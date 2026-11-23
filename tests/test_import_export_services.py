import csv
import io
import pytest
from pdp.domain.errors import AppError
from pdp.importers.readers import read_upload


def test_csv_review_and_commit(app, project):
    review = app.imports.review("sample.csv", b"Name,Age\nAlice,31\nBob,28\n")
    assert review["total_rows"] == 2
    result = app.imports.commit(project.id, review["id"])
    sheet = app.sheets.list(project.id)[0]
    assert result["sheets"] == [sheet["id"]]
    assert app.records.list(sheet["id"])["total"] == 2


def test_xlsx_review_handles_excel_dates_and_type_detection(app):
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Dates"
    sheet.append(["Name", "Start date"])
    sheet.append(["Example", __import__("datetime").date(2026, 3, 14)])
    output = io.BytesIO()
    workbook.save(output)
    review = app.imports.review("dates.xlsx", output.getvalue())
    assert review["sheets"][0]["types"]["Start date"] == "DATE"
    assert review["sheets"][0]["rows"][0]["Start date"] == "2026-03-14"


@pytest.mark.parametrize("filename,content", [
    ("bad.exe", b"abc"), ("macro.xlsm", b"abc"), ("", b"abc"), ("x.csv", b"\xff"),
])
def test_import_rejects_invalid_files(filename, content):
    with pytest.raises(AppError):
        read_upload(filename, content)


@pytest.mark.parametrize("payload", [
    b"Name,Name\nA,B", b",Age\nA,1", b"Name,Age\n",
])
def test_csv_rejects_duplicate_blank_or_empty_columns(payload):
    if payload == b"Name,Age\n":
        assert read_upload("x.csv", payload) == []
    else:
        with pytest.raises(AppError):
            read_upload("x.csv", payload)


def test_xlsx_export_and_history(app, project, sheet):
    col = app.columns.add(sheet["id"], "Name")
    app.records.add(sheet["id"], {str(col["id"]): "A"})
    filename, content = app.exports.generate(project.id, "xlsx")
    assert filename.endswith(".xlsx") and content.startswith(b"PK")
    assert app.exports.history(project.id)[0]["format"] == "xlsx"


@pytest.mark.parametrize("fmt", ["pdf", "png"])
def test_other_export_formats(app, project, fmt):
    filename, content = app.exports.generate(project.id, fmt)
    assert filename.endswith("." + fmt) and content


def test_export_rejects_unknown_format(app, project):
    with pytest.raises(AppError):
        app.exports.generate(project.id, "exe")
