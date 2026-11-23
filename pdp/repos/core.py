from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from pdp.db.models import Project, Dataset, Field, Record, AuditLog, SavedItem, SystemSetting
from pdp.domain.errors import AppError


class Repository:
    """SQL access only; callers own transactions."""
    def __init__(self, session: Session):
        self.s = session

    def project(self, project_id, active=True):
        stmt = select(Project).where(Project.id == project_id)
        if active:
            stmt = stmt.where(Project.deleted_at.is_(None))
        value = self.s.scalar(stmt)
        if value is None:
            raise AppError("not_found", "Project not found")
        return value

    def dataset(self, dataset_id):
        value = self.s.get(Dataset, dataset_id)
        if value is None or self.project(value.project_id).deleted_at is not None:
            raise AppError("not_found", "Sheet not found")
        return value

    def field(self, field_id):
        value = self.s.get(Field, field_id)
        if value is None:
            raise AppError("not_found", "Column not found")
        self.dataset(value.dataset_id)
        return value

    def record(self, record_id, include_deleted=False):
        value = self.s.get(Record, record_id)
        if value is None or (value.deleted_at and not include_deleted):
            raise AppError("not_found", "Row not found")
        self.dataset(value.dataset_id)
        return value

    def audit(self, action, entity, entity_id, details, previous_hash, entry_hash):
        row = AuditLog(action=action, entity=entity, entity_id=str(entity_id), details=details,
                       previous_hash=previous_hash, entry_hash=entry_hash)
        self.s.add(row)
        return row
