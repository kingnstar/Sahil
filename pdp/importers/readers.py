import csv
from datetime import date, datetime, time
from pathlib import Path
import zipfile

from pdp.config import LIMITS
from pdp.domain.errors import AppError


def read_upload(filename, content):
    if not isinstance(filename, str) or not isinstance(content, bytes):
        raise AppError("validation_error", "File name and content are required")
    name = Path(filename or "").name
    suffix = Path(name).suffix.casefold()
    if suffix not in {".csv", ".xlsx", ".xls"} or suffix == ".xlsm":
        raise AppError("validation_error", "Choose a CSV, XLSX, or XLS file")
    if not content or len(content) > LIMITS.upload_bytes:
        raise AppError("limit_exceeded", "File must be between 1 byte and 25 MB")
    if suffix == ".csv":
        try:
            text = content.decode("utf-8-sig")
            rows = list(csv.reader(text.splitlines()))
        except (UnicodeDecodeError, csv.Error):
            raise AppError("validation_error", "CSV file is invalid or not UTF-8 encoded")
        return _normalize([{"name": Path(name).stem or "Sheet 1", "rows": rows}])
    if suffix == ".xlsx":
        try:
            import io
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = archive.namelist()
                if any("vbaproject.bin" in n.casefold() for n in names):
                    raise AppError("validation_error", "Macro-enabled workbooks are not supported")
                if "xl/workbook.xml" not in names:
                    raise AppError("validation_error", "Workbook is corrupt")
                if sum(item.file_size for item in archive.infolist()) > 150 * 1024 * 1024:
                    raise AppError("limit_exceeded", "Uncompressed workbook size exceeds the safety limit")
            from openpyxl import load_workbook
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            if len(workbook.worksheets) > LIMITS.sheets:
                workbook.close()
                raise AppError("limit_exceeded", "Workbook has too many sheets")
            sheets = []
            remaining = LIMITS.import_rows + 1
            for ws in workbook.worksheets:
                current = []
                for row in ws.iter_rows(values_only=True):
                    current.append(list(row))
                    remaining -= 1
                    if remaining <= 0:
                        workbook.close()
                        raise AppError("limit_exceeded", "Import has too many rows")
                sheets.append({"name": ws.title, "rows": current})
            workbook.close()
            return _normalize(sheets)
        except AppError:
            raise
        except Exception:
            raise AppError("validation_error", "Workbook is corrupt or password protected")
    try:
        import io, xlrd
        book = xlrd.open_workbook(file_contents=content, on_demand=True)
        if len(book.sheet_names()) > LIMITS.sheets:
            book.release_resources()
            raise AppError("limit_exceeded", "Workbook has too many sheets")
        total_rows = 0
        sheets = []
        for sheet in book.sheets():
            if sheet.ncols > LIMITS.columns:
                book.release_resources()
                raise AppError("limit_exceeded", "Sheet has too many columns")
            total_rows += sheet.nrows
            if total_rows > LIMITS.import_rows + LIMITS.sheets:
                book.release_resources()
                raise AppError("limit_exceeded", "Import has too many rows")
            sheets.append({"name": sheet.name, "rows": [sheet.row_values(i) for i in range(sheet.nrows)]})
        book.release_resources()
        return _normalize(sheets)
    except AppError:
        raise
    except Exception:
        raise AppError("validation_error", "Legacy workbook is corrupt or password protected")


def _normalize(sheets):
    result = []
    total = 0
    if len(sheets) > LIMITS.sheets:
        raise AppError("limit_exceeded", "Workbook has too many sheets")
    for sheet in sheets:
        rows = sheet["rows"]
        rows = [list(row) for row in rows if any(v is not None and str(v).strip() for v in row)]
        if len(rows) < 2:
            continue
        if len(rows[0]) > LIMITS.columns:
            raise AppError("limit_exceeded", "Sheet has too many columns")
        total += len(rows) - 1
        if total > LIMITS.import_rows:
            raise AppError("limit_exceeded", "Import has too many rows")
        headers = [str(x).strip() for x in rows[0]]
        if any(not x for x in headers):
            raise AppError("validation_error", "Column names cannot be blank")
        if len({x.casefold() for x in headers}) != len(headers):
            raise AppError("validation_error", "Duplicate column names are not allowed")
        if any(len(row) > len(headers) for row in rows[1:]):
            raise AppError("validation_error", "A data row has more cells than the header")
        normalized_rows = []
        for row in rows[1:]:
            padded = row + [None] * (len(headers) - len(row))
            normalized_rows.append({header: _json_value(value) for header, value in zip(headers, padded)})
        result.append({"name": sheet["name"][:60], "columns": headers, "rows": normalized_rows})
    return result


def _json_value(value):
    if isinstance(value, datetime) and value.time() == time.min:
        return value.date().isoformat()
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
