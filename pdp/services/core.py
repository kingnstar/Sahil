from datetime import datetime, timezone
from hashlib import sha256
import json
from sqlalchemy import String, func, select
from sqlalchemy.exc import IntegrityError

from pdp.db.models import Project, Dataset, Field, Record, AuditLog, SavedItem, ExportLog, QualityFlag
from pdp.domain.errors import AppError
from pdp.domain.types import FieldType, parse_value, validate_name, ambiguous_slash_date
from pdp.repos.core import Repository
from pdp.config import LIMITS


def _audit(session, action, entity, entity_id, details):
    previous = session.scalar(select(AuditLog.entry_hash).order_by(AuditLog.id.desc()).limit(1)) or "0" * 64
    payload = json.dumps({"action": action, "entity": entity, "id": str(entity_id),
                          "details": details, "previous": previous}, sort_keys=True, default=str)
    digest = sha256(payload.encode("utf-8")).hexdigest()
    session.add(AuditLog(action=action, entity=entity, entity_id=str(entity_id),
                         details=details, previous_hash=previous, entry_hash=digest))


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ProjectsService:
    def __init__(self, db): self.db = db

    def create(self, name, description=""):
        name = validate_name(name, 2, 60)
        if not isinstance(description, str):
            raise AppError("validation_error", "Description must be text")
        if any(ord(c) < 32 or ord(c) == 127 for c in description): raise AppError("validation_error", "Control characters are not allowed")
        if len(description) > 500: raise AppError("validation_error", "Description must be at most 500 characters")
        with self.db.transaction() as s:
            project = Project(name=name, name_key=name.casefold(), description=description)
            s.add(project)
            try: s.flush()
            except IntegrityError: raise AppError("conflict", "A project with that name already exists")
            _audit(s, "project.create", "project", project.id, {"name": name})
            return project

    def get(self, project_id):
        with self.db.transaction() as s:
            p = Repository(s).project(project_id)
            return {"id": p.id, "name": p.name, "description": p.description}

    def list(self):
        with self.db.transaction() as s:
            return [{"id": p.id, "name": p.name, "description": p.description}
                    for p in s.scalars(select(Project).where(Project.deleted_at.is_(None)).order_by(Project.name))]

    def rename(self, project_id, name, description=None):
        name = validate_name(name, 2, 60)
        with self.db.transaction() as s:
            p = Repository(s).project(project_id)
            p.name, p.name_key, p.updated_at = name, name.casefold(), _utcnow()
            if description is not None:
                if not isinstance(description, str):
                    raise AppError("validation_error", "Description must be text")
                if len(description) > 500: raise AppError("validation_error", "Description must be at most 500 characters")
                if any(ord(c) < 32 or ord(c) == 127 for c in description):
                    raise AppError("validation_error", "Control characters are not allowed")
                p.description = description
            try: s.flush()
            except IntegrityError: raise AppError("conflict", "A project with that name already exists")
            _audit(s, "project.rename", "project", p.id, {"name": name})
            return {"id": p.id, "name": p.name, "description": p.description}

    def delete(self, project_id):
        with self.db.transaction() as s:
            p = Repository(s).project(project_id)
            name = p.name
            s.delete(p)
            _audit(s, "project.delete", "project", project_id, {"name": name})


class ColumnsService:
    def __init__(self, db): self.db = db

    def add(self, dataset_id, name, field_type="TEXT", options=None):
        name = validate_name(name)
        try: kind = FieldType(field_type.upper())
        except (ValueError, AttributeError): raise AppError("validation_error", "Unknown field type")
        if options is None:
            options = []
        if not isinstance(options, list):
            raise AppError("validation_error", "Choice options must be a list")
        if len(options) > LIMITS.pick_list_options: raise AppError("validation_error", "Too many choice options")
        if any(not isinstance(option, str) or not option.strip() or len(option) > LIMITS.text_chars
               for option in options):
            raise AppError("validation_error", "Choice options must be non-empty text up to 4,000 characters")
        if len({option.casefold() for option in options}) != len(options):
            raise AppError("validation_error", "Choice options must be unique")
        if kind == FieldType.CHOICE and not options:
            raise AppError("validation_error", "Add at least one choice option")
        with self.db.transaction() as s:
            d = Repository(s).dataset(dataset_id)
            if len(d.fields) >= LIMITS.columns: raise AppError("limit_exceeded", "Sheet has reached the column limit")
            field = Field(dataset_id=d.id, name=name, name_key=name.casefold(), field_type=kind.value,
                          options=options, position=len(d.fields))
            s.add(field)
            try: s.flush()
            except IntegrityError: raise AppError("conflict", "A column with that name already exists")
            _audit(s, "column.add", "column", field.id, {"sheet_id": d.id, "name": name})
            return self._serialize(field)

    @staticmethod
    def _serialize(f):
        return {"id": f.id, "dataset_id": f.dataset_id, "name": f.name, "type": f.field_type,
                "options": f.options, "position": f.position, "hidden": f.hidden}

    def list(self, dataset_id, include_hidden=True):
        with self.db.transaction() as s:
            d = Repository(s).dataset(dataset_id)
            return [self._serialize(f) for f in sorted(d.fields, key=lambda x:x.position) if include_hidden or not f.hidden]

    def set_hidden(self, field_id, hidden):
        if not isinstance(hidden, bool):
            raise AppError("validation_error", "Column visibility must be true or false")
        with self.db.transaction() as s:
            f = Repository(s).field(field_id)
            f.hidden = bool(hidden)
            _audit(s, "column.visibility", "column", f.id, {"hidden": f.hidden})
            return self._serialize(f)

    def move(self, field_id, position):
        if isinstance(position, bool) or (isinstance(position, float) and not position.is_integer()):
            raise AppError("validation_error", "Column position must be an integer")
        try:
            position = int(position)
        except (TypeError, ValueError):
            raise AppError("validation_error", "Column position must be an integer")
        with self.db.transaction() as s:
            f = Repository(s).field(field_id)
            fields = sorted(s.scalars(select(Field).where(Field.dataset_id == f.dataset_id)), key=lambda x:x.position)
            fields.remove(f)
            fields.insert(max(0, min(position, len(fields))), f)
            for i, item in enumerate(fields): item.position = i
            _audit(s, "column.move", "column", f.id, {"position": f.position})
            return self._serialize(f)

    def delete(self, field_id):
        with self.db.transaction() as s:
            f = Repository(s).field(field_id)
            dataset_id, key = f.dataset_id, str(f.id)
            for row in s.scalars(select(Record).where(Record.dataset_id == dataset_id)):
                row.values.pop(key, None)
            s.delete(f)
            _audit(s, "column.delete", "column", key, {"sheet_id": dataset_id})


class SheetsService:
    def __init__(self, db): self.db = db

    def create(self, project_id, name):
        name = validate_name(name)
        with self.db.transaction() as s:
            p = Repository(s).project(project_id)
            if len(p.datasets) >= LIMITS.sheets: raise AppError("limit_exceeded", "Project has reached the sheet limit")
            d = Dataset(project_id=p.id, name=name, name_key=name.casefold(), position=len(p.datasets))
            s.add(d)
            try: s.flush()
            except IntegrityError: raise AppError("conflict", "A sheet with that name already exists")
            _audit(s, "sheet.create", "sheet", d.id, {"project_id": p.id, "name": name})
            return {"id": d.id, "project_id": p.id, "name": d.name}

    def list(self, project_id):
        with self.db.transaction() as s:
            p = Repository(s).project(project_id)
            return [{"id": d.id, "name": d.name, "position": d.position,
                     "row_count": len([r for r in d.records if r.deleted_at is None])}
                    for d in sorted(p.datasets, key=lambda x:x.position)]

    def delete(self, dataset_id):
        with self.db.transaction() as s:
            d = Repository(s).dataset(dataset_id)
            key = str(d.id)
            s.delete(d)
            _audit(s, "sheet.delete", "sheet", key, {})


class RecordsService:
    def __init__(self, db): self.db = db

    def add(self, dataset_id, values=None):
        if values is None:
            values = {}
        if not isinstance(values, dict):
            raise AppError("validation_error", "Row values must be an object")
        with self.db.transaction() as s:
            d = Repository(s).dataset(dataset_id)
            row = self._validated(values, d.fields)
            max_order = s.scalar(select(func.max(Record.row_order)).where(Record.dataset_id == d.id)) or 0
            record = Record(dataset_id=d.id, values=row, row_order=max_order + 1)
            s.add(record); s.flush()
            self._flag_ambiguous_dates(s, record, values, d.fields)
            _audit(s, "record.create", "record", record.id, {"sheet_id": d.id})
            self._index(s, record)
            return self._serialize(record)

    def update(self, record_id, values, expected_version):
        if expected_version is None: raise AppError("validation_error", "expected_version is required")
        if isinstance(expected_version, bool):
            raise AppError("validation_error", "expected_version must be an integer")
        if not isinstance(values, dict):
            raise AppError("validation_error", "Row values must be an object")
        try:
            expected_version = int(expected_version)
        except (TypeError, ValueError):
            raise AppError("validation_error", "expected_version must be an integer")
        if expected_version < 1:
            raise AppError("validation_error", "expected_version must be positive")
        with self.db.transaction() as s:
            record = Repository(s).record(record_id)
            if record.version != expected_version: raise AppError("conflict", "This row changed; reload before saving")
            d = Repository(s).dataset(record.dataset_id)
            record.values = self._validated(values, d.fields)
            record.version += 1; record.updated_at = _utcnow()
            for flag in s.scalars(select(QualityFlag).where(QualityFlag.record_id == record.id, QualityFlag.resolved.is_(False))):
                flag.resolved = True
            self._flag_ambiguous_dates(s, record, values, d.fields)
            _audit(s, "record.update", "record", record.id, {"version": record.version})
            self._index(s, record)
            return self._serialize(record)

    @staticmethod
    def _flag_ambiguous_dates(session, record, values, fields):
        for field in fields:
            raw = values.get(str(field.id), values.get(field.name))
            if field.field_type == "DATE" and ambiguous_slash_date(raw):
                session.add(QualityFlag(record_id=record.id, field_id=field.id,
                                        message=f"Ambiguous date interpreted as day/month: {raw}"))

    def _validated(self, values, fields):
        if not isinstance(values, dict):
            raise AppError("validation_error", "Row values must be an object")
        result = {}
        field_ids = {str(f.id) for f in fields}
        for key in values:
            if str(key) not in field_ids: raise AppError("validation_error", "Unknown column")
        for f in fields:
            if f.hidden:
                pass
            key = str(f.id)
            if key in values or f.name in values:
                value = values.get(key, values.get(f.name))
                result[key] = parse_value(value, f.field_type, f.options)
        return result

    @staticmethod
    def _serialize(r):
        return {"id": r.id, "dataset_id": r.dataset_id, "values": r.values, "version": r.version,
                "row_order": r.row_order, "updated_at": r.updated_at.isoformat()}

    @staticmethod
    def _index(s, record):
        try:
            s.connection().exec_driver_sql("DELETE FROM record_search WHERE record_id=?", (record.id,))
            s.connection().exec_driver_sql("INSERT INTO record_search(record_id,project_id,content) SELECT ?,d.project_id,? FROM datasets d WHERE d.id=?",
                                           (record.id, " ".join(map(str, record.values.values())), record.dataset_id))
        except Exception:
            pass

    def get(self, record_id):
        with self.db.transaction() as s: return self._serialize(Repository(s).record(record_id))

    def list(self, dataset_id, page=1, page_size=LIMITS.grid_page):
        try:
            page = max(1, int(page)); page_size = min(LIMITS.grid_page, max(1, int(page_size)))
        except (TypeError, ValueError):
            raise AppError("validation_error", "Page and page size must be integers")
        with self.db.transaction() as s:
            d = Repository(s).dataset(dataset_id)
            rows = list(s.scalars(select(Record).where(Record.dataset_id == d.id, Record.deleted_at.is_(None))
                                  .order_by(Record.row_order).offset((page-1)*page_size).limit(page_size)))
            count = s.scalar(select(func.count()).select_from(Record).where(Record.dataset_id == d.id, Record.deleted_at.is_(None)))
            return {"items": [self._serialize(r) for r in rows], "page": page, "page_size": page_size, "total": count}

    def delete(self, record_id):
        with self.db.transaction() as s:
            r = Repository(s).record(record_id)
            r.deleted_at = _utcnow(); r.version += 1
            _audit(s, "record.recycle", "record", r.id, {"deleted_at": r.deleted_at.isoformat()})

    def bulk_add(self, dataset_id, rows, confirmed=False):
        if not isinstance(rows, list) or not rows or len(rows) > LIMITS.bulk_rows:
            raise AppError("limit_exceeded", f"Bulk operation must contain 1–{LIMITS.bulk_rows} rows")
        if len(rows) > LIMITS.bulk_confirm_threshold and not confirmed:
            raise AppError("confirmation_required", "Confirm bulk changes above 1,000 rows")
        if not isinstance(confirmed, bool):
            raise AppError("validation_error", "Bulk confirmation must be true or false")
        with self.db.transaction() as s:
            d = Repository(s).dataset(dataset_id)
            existing = s.scalar(select(func.count()).select_from(Record).where(Record.dataset_id == d.id)) or 0
            if existing + len(rows) > LIMITS.bulk_rows:
                raise AppError("limit_exceeded", f"Bulk operation cannot exceed {LIMITS.bulk_rows} rows")
            start = s.scalar(select(func.max(Record.row_order)).where(Record.dataset_id == d.id)) or 0
            created = []
            for offset, values in enumerate(rows, 1):
                if not isinstance(values, dict):
                    raise AppError("validation_error", "Each row must be an object")
                record = Record(dataset_id=d.id, values=self._validated(values, d.fields), row_order=start + offset)
                s.add(record)
                created.append(record)
            s.flush()
            for record, values in zip(created, rows):
                self._flag_ambiguous_dates(s, record, values, d.fields)
                self._index(s, record)
            _audit(s, "record.bulk_create", "sheet", d.id, {"count": len(created)})
            return [self._serialize(row) for row in created]

    def paste(self, dataset_id, rows, confirmed=False):
        if not isinstance(rows, list) or not rows or len(rows) > LIMITS.paste_rows:
            raise AppError("limit_exceeded", f"Paste must contain 1–{LIMITS.paste_rows} rows")
        return self.bulk_add(dataset_id, rows, confirmed=confirmed)

    def bulk_delete(self, dataset_id, record_ids, confirmed=False):
        if not isinstance(record_ids, list) or not record_ids or len(record_ids) > LIMITS.bulk_rows:
            raise AppError("limit_exceeded", f"Bulk operation must contain 1–{LIMITS.bulk_rows} rows")
        if len(record_ids) > LIMITS.bulk_confirm_threshold and not confirmed:
            raise AppError("confirmation_required", "Confirm bulk changes above 1,000 rows")
        if not isinstance(confirmed, bool):
            raise AppError("validation_error", "Bulk confirmation must be true or false")
        with self.db.transaction() as s:
            d = Repository(s).dataset(dataset_id)
            rows = []
            try:
                if any(isinstance(record_id, bool) for record_id in record_ids):
                    raise ValueError()
                normalized_ids = {int(record_id) for record_id in record_ids}
            except (TypeError, ValueError):
                raise AppError("validation_error", "Row identifiers must be integers")
            for record_id in normalized_ids:
                row = Repository(s).record(record_id)
                if row.dataset_id != d.id:
                    raise AppError("not_found", "Row does not belong to this sheet")
                rows.append(row)
            for row in rows:
                row.deleted_at = _utcnow()
                row.version += 1
            _audit(s, "record.bulk_recycle", "sheet", d.id, {"count": len(rows)})
            return len(rows)


class SearchService:
    def __init__(self, db): self.db = db
    def search(self, project_id, query):
        if not isinstance(query, str):
            raise AppError("validation_error", "Search text must be text")
        if len((query or "").strip()) < LIMITS.search_min_chars: raise AppError("validation_error", "Search needs at least 3 characters")
        if len(query) > LIMITS.text_chars or any(ord(c) < 32 or ord(c) == 127 for c in query):
            raise AppError("validation_error", "Search text is invalid or too long")
        needle = f"%{query.strip()}%"
        with self.db.transaction() as s:
            Repository(s).project(project_id)
            rows = s.execute(select(Record, Dataset.name).join(Dataset).where(
                Dataset.project_id == project_id, Record.deleted_at.is_(None),
                Record.values.cast(String).ilike(needle)).limit(200)).all()
            return [{"record": RecordsService._serialize(r), "sheet": name} for r, name in rows]


class AnalyticsService:
    def __init__(self, db): self.db = db
    def save(self, project_id, kind, name, definition):
        if not isinstance(kind, str) or kind not in {"filter", "report", "dashboard"}: raise AppError("validation_error", "Invalid saved item type")
        if not isinstance(definition, dict): raise AppError("validation_error", "Saved definition must be an object")
        with self.db.transaction() as s:
            Repository(s).project(project_id)
            item = SavedItem(project_id=project_id, kind=kind, name=validate_name(name), definition=definition)
            s.add(item); s.flush()
            _audit(s, f"{kind}.save", kind, item.id, {"name": item.name})
            return {"id": item.id, "kind": item.kind, "name": item.name}
    def list(self, project_id, kind=None):
        with self.db.transaction() as s:
            Repository(s).project(project_id)
            stmt = select(SavedItem).where(SavedItem.project_id == project_id)
            if kind: stmt = stmt.where(SavedItem.kind == kind)
            return [{"id": i.id, "kind": i.kind, "name": i.name, "definition": i.definition} for i in s.scalars(stmt)]


class AuditService:
    def __init__(self, db): self.db = db
    def list(self, limit=100):
        with self.db.transaction() as s:
            return [{"id": a.id, "timestamp": a.timestamp.isoformat(), "action": a.action,
                     "entity": a.entity, "entity_id": a.entity_id, "details": a.details,
                     "previous_hash": a.previous_hash, "entry_hash": a.entry_hash}
                    for a in s.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(max(1, min(int(limit), 500))))]
    def verify(self):
        with self.db.transaction() as s:
            entries = [{"id": a.id, "timestamp": a.timestamp.isoformat(), "action": a.action,
                        "entity": a.entity, "entity_id": a.entity_id, "details": a.details,
                        "previous_hash": a.previous_hash, "entry_hash": a.entry_hash}
                       for a in s.scalars(select(AuditLog).order_by(AuditLog.id))]
        previous = "0" * 64
        for row in entries:
            payload = json.dumps({"action": row["action"], "entity": row["entity"], "id": row["entity_id"],
                                  "details": row["details"], "previous": previous}, sort_keys=True, default=str)
            digest = sha256(payload.encode()).hexdigest()
            if row["previous_hash"] != previous or row["entry_hash"] != digest: return False
            previous = digest
        return True


class SystemService:
    def __init__(self, db): self.db = db
    def get(self, key):
        from pdp.db.models import SystemSetting
        with self.db.transaction() as s:
            row = s.get(SystemSetting, key)
            return row.value if row else None
    def set(self, key, value):
        from pdp.db.models import SystemSetting
        with self.db.transaction() as s:
            row = s.get(SystemSetting, key)
            if row is None:
                row = SystemSetting(key=key, value=str(value))
                s.add(row)
            else:
                row.value = str(value)
            _audit(s, "system.setting", "system", key, {"key": key})
            return row.value


class RecycleService:
    def __init__(self, db): self.db = db
    def list(self, project_id):
        with self.db.transaction() as s:
            Repository(s).project(project_id)
            return [RecordsService._serialize(r) for r in s.scalars(select(Record).join(Dataset).where(
                Dataset.project_id == project_id, Record.deleted_at.is_not(None)))]
    def restore(self, record_id):
        with self.db.transaction() as s:
            r = Repository(s).record(record_id, include_deleted=True)
            if not r.deleted_at: raise AppError("validation_error", "Row is not in the recycle bin")
            from datetime import timedelta
            if r.deleted_at < _utcnow() - timedelta(days=LIMITS.recycle_days): raise AppError("not_found", "Recycle retention has expired")
            r.deleted_at = None; r.version += 1
            _audit(s, "record.restore", "record", r.id, {"version": r.version})
            return RecordsService._serialize(r)
    def delete_forever(self, record_id):
        with self.db.transaction() as s:
            r = Repository(s).record(record_id, include_deleted=True)
            if not r.deleted_at: raise AppError("validation_error", "Row must be recycled before permanent deletion")
            key = str(r.id); s.delete(r); _audit(s, "record.delete_forever", "record", key, {})
