"""Initial SQLite schema migration."""
from pdp.db.models import Base


revision = "0001_initial"
down_revision = None


def upgrade(connection):
    Base.metadata.create_all(bind=connection)
    try:
        connection.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS record_search USING fts5(record_id UNINDEXED, project_id UNINDEXED, content)"
        )
    except Exception:
        # FTS5 is optional; SearchService retains its SQLite JSON fallback.
        pass


def downgrade(connection):
    Base.metadata.drop_all(bind=connection)
    connection.exec_driver_sql("DROP TABLE IF EXISTS record_search")
