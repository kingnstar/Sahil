from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Limits:
    upload_bytes: int = 25 * 1024 * 1024
    import_rows: int = 200_000
    columns: int = 200
    sheets: int = 100
    text_chars: int = 4_000
    grid_page: int = 200
    paste_rows: int = 5_000
    bulk_rows: int = 100_000
    bulk_confirm_threshold: int = 1_000
    search_min_chars: int = 3
    min_year: int = 1900
    max_year: int = 2100
    recycle_days: int = 30
    pick_list_options: int = 200


LIMITS = Limits()


def data_path(override=None) -> Path:
    return Path(override or os.environ.get("PDP_DATA_DIR", "./data")).expanduser().resolve()


def database_path(data_dir=None) -> Path:
    return data_path(data_dir) / "project_data.sqlite3"
