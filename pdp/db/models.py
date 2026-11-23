from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    name_key: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    datasets: Mapped[list["Dataset"]] = relationship(cascade="all, delete-orphan")


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("project_id", "name_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    name_key: Mapped[str] = mapped_column(String(60), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    fields: Mapped[list["Field"]] = relationship(cascade="all, delete-orphan")
    records: Mapped[list["Record"]] = relationship(cascade="all, delete-orphan")


class Field(Base):
    __tablename__ = "fields"
    __table_args__ = (UniqueConstraint("dataset_id", "name_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    name_key: Mapped[str] = mapped_column(String(60), nullable=False)
    field_type: Mapped[str] = mapped_column(String(16), default="TEXT")
    options: Mapped[list] = mapped_column(JSON, default=list)
    position: Mapped[int] = mapped_column(Integer, default=0)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)


class Record(Base):
    __tablename__ = "records"
    __table_args__ = (Index("ix_records_dataset_order", "dataset_id", "row_order"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    values: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    row_order: Mapped[int] = mapped_column(Integer, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


class SavedItem(Base):
    __tablename__ = "saved_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    definition: Mapped[dict] = mapped_column(JSON, default=dict)


class QualityFlag(Base):
    __tablename__ = "quality_flags"
    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"))
    field_id: Mapped[int | None] = mapped_column(ForeignKey("fields.id", ondelete="SET NULL"))
    message: Mapped[str] = mapped_column(String(300))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class ImportJob(Base):
    __tablename__ = "import_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(30), default="review")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ExportLog(Base):
    __tablename__ = "export_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    format: Mapped[str] = mapped_column(String(12))
    filename: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
