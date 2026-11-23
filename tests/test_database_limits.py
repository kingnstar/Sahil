import pytest
from sqlalchemy import text
from pdp.config import LIMITS, data_path, database_path
from pdp.db.models import QualityFlag
from pdp.domain.errors import AppError
from pdp.domain.types import ambiguous_slash_date
from pdp.services.exports import safe_filename
from flask_app import is_loopback_host


def test_configured_limits():
    assert LIMITS.upload_bytes == 25 * 1024 * 1024
    assert LIMITS.import_rows == 200_000
    assert LIMITS.columns == 200
    assert LIMITS.sheets == 100
    assert LIMITS.grid_page == 200
    assert LIMITS.paste_rows == 5_000
    assert LIMITS.bulk_rows == 100_000
    assert LIMITS.bulk_confirm_threshold == 1_000
    assert LIMITS.search_min_chars == 3
    assert LIMITS.recycle_days == 30
    assert LIMITS.pick_list_options == 200


def test_data_directory_environment_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PDP_DATA_DIR", str(tmp_path / "custom"))
    assert data_path() == (tmp_path / "custom").resolve()
    assert database_path().parent == (tmp_path / "custom").resolve()


def test_sqlite_foreign_keys_and_wal(app):
    with app.db.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"


def test_fts_index_is_initialized(app):
    with app.db.engine.connect() as connection:
        names = {row[0] for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "record_search" in names


@pytest.mark.parametrize("value,expected", [
    ("01/02/2026", True), ("12/12/2026", False), ("13/02/2026", False),
    ("2026-02-01", False), ("1/2/26", True), (None, False),
])
def test_ambiguous_slash_date_detection(value, expected):
    assert ambiguous_slash_date(value) is expected


def test_ambiguous_date_creates_quality_flag(app, project, sheet):
    field = app.columns.add(sheet["id"], "Date", "DATE")
    row = app.records.add(sheet["id"], {str(field["id"]): "01/02/2026"})
    with app.db.transaction() as s:
        flag = s.query(QualityFlag).filter_by(record_id=row["id"]).one()
        assert "Ambiguous date" in flag.message


def test_bulk_confirmation_and_limits(app, sheet):
    with pytest.raises(AppError) as error:
        app.records.bulk_add(sheet["id"], [{}] * 1001)
    assert error.value.code == "confirmation_required"
    with pytest.raises(AppError):
        app.records.bulk_add(sheet["id"], [{}] * (LIMITS.bulk_rows + 1), confirmed=True)


def test_bulk_delete_checks_sheet_ownership(app, project, sheet):
    other = app.sheets.create(project.id, "Other")
    row = app.records.add(other["id"])
    with pytest.raises(AppError):
        app.records.bulk_delete(sheet["id"], [row["id"]])


def test_choice_options_are_bounded_and_unique(app, sheet):
    with pytest.raises(AppError):
        app.columns.add(sheet["id"], "Status", "CHOICE")
    with pytest.raises(AppError):
        app.columns.add(sheet["id"], "Status", "CHOICE", ["Open", "open"])
    with pytest.raises(AppError):
        app.columns.add(sheet["id"], "Status", "CHOICE", [str(i) for i in range(201)])
    field = app.columns.add(sheet["id"], "Status", "CHOICE", ["Open", "Done"])
    row = app.records.add(sheet["id"], {str(field["id"]): "open"})
    assert row["values"][str(field["id"])] == "Open"


@pytest.mark.parametrize("value,expected", [
    ("../../secret.txt", "secret.txt"), ("..", "export"), ("my report.xlsx", "my_report.xlsx"),
    ("folder/name", "name"), ("", "export"),
])
def test_download_filename_is_traversal_safe(value, expected):
    assert safe_filename(value) == expected


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True), ("0.0.0.0", False), ("localhost", False),
    ("::", False), ("192.168.1.4", False), ("", False),
])
def test_bind_host_validation(host, expected):
    assert is_loopback_host(host) is expected


def test_migration_upgrade_and_downgrade(tmp_path):
    from sqlalchemy import create_engine
    from pdp.db.migrations.versions.v0001_initial import upgrade, downgrade
    engine = create_engine(f"sqlite:///{(tmp_path / 'migration.db').as_posix()}")
    with engine.begin() as conn:
        upgrade(conn)
        assert conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE name='projects'").scalar() == "projects"
        downgrade(conn)
        assert conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE name='projects'").scalar() is None
