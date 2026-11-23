from io import BytesIO
from pathlib import Path
import re
from sqlalchemy import select
from pdp.db.models import ExportLog, Dataset, Record
from pdp.domain.errors import AppError
from pdp.services.core import Repository, RecordsService, _audit


def safe_filename(value):
    value = Path(str(value or "export")).name
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return (value or "export")[:100]


class ExportService:
    def __init__(self, app): self.app = app

    def generate(self, project_id, fmt="xlsx"):
        if not isinstance(fmt, str):
            raise AppError("validation_error", "Unsupported export format")
        fmt = fmt.casefold()
        if fmt not in {"xlsx", "pdf", "png"}: raise AppError("validation_error", "Unsupported export format")
        with self.app.db.transaction() as s:
            project = Repository(s).project(project_id)
            sheets = list(s.scalars(select(Dataset).where(Dataset.project_id == project_id).order_by(Dataset.position)))
            payload = [(d.name, [(f.id, f.name) for f in sorted(d.fields, key=lambda x:x.position)],
                        [RecordsService._serialize(r) for r in s.scalars(select(Record).where(
                            Record.dataset_id == d.id, Record.deleted_at.is_(None)).order_by(Record.row_order))])
                       for d in sheets]
            project_name = project.name
        output = BytesIO()
        if fmt == "xlsx":
            import xlsxwriter
            book = xlsxwriter.Workbook(output, {"in_memory": True})
            for name, fields, rows in payload:
                ws = book.add_worksheet(safe_filename(name)[:31])
                for col, (_, header) in enumerate(fields): ws.write_string(0, col, header)
                for i, row in enumerate(rows, 1):
                    for j, (field_id, _) in enumerate(fields):
                        value = row["values"].get(str(field_id))
                        if value is None:
                            ws.write_blank(i, j, None)
                        elif isinstance(value, bool):
                            ws.write_boolean(i, j, value)
                        elif isinstance(value, (int, float)):
                            ws.write_number(i, j, value)
                        else:
                            ws.write_string(i, j, str(value))
            book.close()
        elif fmt == "pdf":
            from reportlab.lib.pagesizes import landscape, letter
            from reportlab.pdfgen import canvas
            c = canvas.Canvas(output, pagesize=landscape(letter))
            y = 560
            c.setFont("Helvetica-Bold", 14); c.drawString(35, y, project_name[:80]); y -= 24
            for name, fields, rows in payload:
                c.setFont("Helvetica-Bold", 11); c.drawString(35, y, name[:80]); y -= 18
                c.setFont("Helvetica", 8)
                for row in rows[:100]:
                    line = " | ".join(str(v)[:35] for v in row["values"].values())
                    c.drawString(35, y, line[:150]); y -= 12
                    if y < 35: c.showPage(); y = 560
            c.save()
        else:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.bar([name for name, _, _ in payload], [len(rows) for _, _, rows in payload])
            ax.set_ylabel("Rows"); fig.tight_layout(); fig.savefig(output, format="png"); plt.close(fig)
        filename = safe_filename(project_name) + "." + fmt
        with self.app.db.transaction() as s:
            s.add(ExportLog(project_id=project_id, format=fmt, filename=filename))
            _audit(s, "export.create", "project", project_id, {"format": fmt})
        return filename, output.getvalue()

    def history(self, project_id):
        with self.app.db.transaction() as s:
            Repository(s).project(project_id)
            return [{"id": x.id, "format": x.format, "filename": x.filename, "created_at": x.created_at.isoformat()}
                    for x in s.scalars(select(ExportLog).where(ExportLog.project_id == project_id).order_by(ExportLog.id.desc()))]
