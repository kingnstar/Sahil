from pathlib import Path
from sqlalchemy import select
from pdp.db.models import ImportJob
from pdp.domain.errors import AppError
from pdp.importers.readers import read_upload
from pdp.domain.types import FieldType, parse_value


def detect_type(values):
    samples = [v for v in values if v is not None and str(v).strip()][:100]
    if not samples:
        return "TEXT"
    for kind in (FieldType.WHOLE, FieldType.NUMBER, FieldType.DATE, FieldType.DATETIME,
                 FieldType.YESNO, FieldType.EMAIL, FieldType.URL):
        try:
            for value in samples:
                parse_value(value, kind)
            return kind.value
        except AppError:
            continue
    return "TEXT"


class ImportService:
    def __init__(self, app):
        self.app = app

    def review(self, filename, content):
        sheets = read_upload(filename, content)
        for sheet in sheets:
            sheet["types"] = {name: detect_type([row.get(name) for row in sheet["rows"]])
                              for name in sheet["columns"]}
        with self.app.db.transaction() as s:
            job = ImportJob(filename=Path(filename).name, state="review",
                            summary={"sheets": sheets, "total_rows": sum(len(x["rows"]) for x in sheets)})
            s.add(job); s.flush()
            return {"id": job.id, "filename": job.filename, "sheets": sheets, "total_rows": job.summary["total_rows"]}

    def commit(self, project_id, job_id):
        self.app.projects.get(project_id)
        with self.app.db.transaction() as s:
            job = s.get(ImportJob, job_id)
            if not job or job.state != "review":
                raise AppError("not_found", "Import review not found")
            snapshot = job.summary["sheets"]
        created = []
        try:
            for sheet_data in snapshot:
                sheet = self.app.sheets.create(project_id, sheet_data["name"])
                cols = {name: self.app.columns.add(sheet["id"], name, sheet.get("types", {}).get(name, "TEXT"))
                        for name in sheet_data["columns"]}
                created.append(sheet["id"])
                batch = [{str(cols[name]["id"]): value for name, value in values.items()} for values in sheet_data["rows"]]
                for start in range(0, len(batch), 5_000):
                    self.app.records.bulk_add(sheet["id"], batch[start:start + 5_000], confirmed=True)
            with self.app.db.transaction() as s:
                job = s.get(ImportJob, job_id); job.state = "completed"
            return {"sheets": created}
        except Exception:
            for sheet_id in created:
                try: self.app.sheets.delete(sheet_id)
                except Exception: pass
            raise
