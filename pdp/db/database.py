from contextlib import contextmanager
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from .models import Base


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.path.as_posix()}", future=True)
        @event.listens_for(self.engine, "connect")
        def _sqlite_pragmas(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            try:
                conn.exec_driver_sql(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS record_search USING fts5(record_id UNINDEXED, project_id UNINDEXED, content)"
                )
            except Exception:
                pass

    @contextmanager
    def transaction(self):
        session = self.Session()
        try:
            with session.begin():
                yield session
        finally:
            session.close()

    def close(self):
        self.engine.dispose()
