from pathlib import Path
from pdp.config import database_path
from pdp.db import Database
from .core import (ProjectsService, SheetsService, ColumnsService, RecordsService, SearchService,
                   AnalyticsService, AuditService, RecycleService, SystemService)
from .imports import ImportService
from .exports import ExportService


class App:
    """Service container: UI code should use this object, never repositories."""
    def __init__(self, data_dir=None):
        root = Path(data_dir).expanduser().resolve() if data_dir else database_path().parent
        self.data_dir = root
        self.db = Database(root / "project_data.sqlite3")
        self.db.initialize()
        self.projects = ProjectsService(self.db)
        self.sheets = SheetsService(self.db)
        self.columns = ColumnsService(self.db)
        self.records = RecordsService(self.db)
        self.search = SearchService(self.db)
        self.analytics = AnalyticsService(self.db)
        self.audit = AuditService(self.db)
        self.system = SystemService(self.db)
        self.recycle = RecycleService(self.db)
        self.imports = ImportService(self)
        self.exports = ExportService(self)


__all__ = ["App"]
