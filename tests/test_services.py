import pytest
from pdp.domain.errors import AppError


def test_project_create_get_and_list(app, project):
    assert app.projects.get(project.id)["name"] == "Test Project"
    assert app.projects.list()[0]["id"] == project.id


def test_project_duplicate_name_case_insensitive(app, project):
    with pytest.raises(AppError) as error:
        app.projects.create("test project")
    assert error.value.code == "conflict"


def test_project_rename_and_delete(app, project):
    assert app.projects.rename(project.id, "Renamed")["name"] == "Renamed"
    app.projects.delete(project.id)
    assert app.projects.list() == []


@pytest.mark.parametrize("name", ["", "x", "a" * 61, "bad\nname"])
def test_project_names_validated(app, name):
    with pytest.raises(AppError):
        app.projects.create(name)


def test_description_limits(app):
    with pytest.raises(AppError):
        app.projects.create("Project One", "x" * 501)


def test_sheet_and_column_lifecycle(app, project):
    sheet = app.sheets.create(project.id, "Data")
    field = app.columns.add(sheet["id"], "Age", "WHOLE")
    assert app.columns.list(sheet["id"])[0]["type"] == "WHOLE"
    app.columns.set_hidden(field["id"], True)
    assert app.columns.list(sheet["id"], include_hidden=False) == []
    app.columns.set_hidden(field["id"], False)
    app.columns.delete(field["id"])
    assert app.columns.list(sheet["id"]) == []


def test_duplicate_sheet_and_column_names_are_case_insensitive(app, project, sheet):
    with pytest.raises(AppError):
        app.sheets.create(project.id, "records")
    app.columns.add(sheet["id"], "Name")
    with pytest.raises(AppError):
        app.columns.add(sheet["id"], "name")


def test_column_move(app, sheet):
    a = app.columns.add(sheet["id"], "A")
    b = app.columns.add(sheet["id"], "B")
    app.columns.move(b["id"], 0)
    assert [x["name"] for x in app.columns.list(sheet["id"])] == ["B", "A"]


def test_record_validation_and_optimistic_lock(app, sheet):
    col = app.columns.add(sheet["id"], "Count", "WHOLE")
    row = app.records.add(sheet["id"], {str(col["id"]): "7"})
    assert row["values"][str(col["id"])] == 7
    updated = app.records.update(row["id"], {str(col["id"]): "8"}, row["version"])
    assert updated["version"] == row["version"] + 1
    with pytest.raises(AppError) as error:
        app.records.update(row["id"], {str(col["id"]): "9"}, row["version"])
    assert error.value.code == "conflict"


def test_record_requires_expected_version_and_rejects_unknown_column(app, sheet):
    row = app.records.add(sheet["id"])
    with pytest.raises(AppError):
        app.records.update(row["id"], {}, None)
    with pytest.raises(AppError):
        app.records.update(row["id"], {"900": "x"}, row["version"])


def test_paging_and_recycle_restore(app, sheet):
    col = app.columns.add(sheet["id"], "Name")
    rows = [app.records.add(sheet["id"], {str(col["id"]): f"Row {i}"}) for i in range(3)]
    page = app.records.list(sheet["id"], page=1, page_size=2)
    assert len(page["items"]) == 2 and page["total"] == 3
    app.records.delete(rows[0]["id"])
    assert app.records.list(sheet["id"])["total"] == 2
    assert len(app.recycle.list(sheet["project_id"] if "project_id" in sheet else 1)) == 1
    app.recycle.restore(rows[0]["id"])
    assert app.records.list(sheet["id"])["total"] == 3


def test_recycle_delete_forever(app, sheet):
    row = app.records.add(sheet["id"])
    app.records.delete(row["id"])
    app.recycle.delete_forever(row["id"])
    with pytest.raises(AppError):
        app.records.get(row["id"])


def test_audit_chain_and_search(app, project, sheet):
    col = app.columns.add(sheet["id"], "Name")
    app.records.add(sheet["id"], {str(col["id"]): "Northwind"})
    assert app.audit.verify()
    assert app.search.search(project.id, "North")[0]["sheet"] == "Records"
    with pytest.raises(AppError):
        app.search.search(project.id, "ab")


def test_analytics_saved_item(app, project):
    item = app.analytics.save(project.id, "report", "Summary", {"group_by": "status"})
    assert app.analytics.list(project.id, "report")[0]["id"] == item["id"]
    with pytest.raises(AppError):
        app.analytics.save(project.id, "other", "x", {})


def test_paste_adds_tsv_rows_and_enforces_paste_limit(app, sheet):
    field = app.columns.add(sheet["id"], "Value")
    rows = app.records.paste(sheet["id"], [{str(field["id"]): "one"}, {str(field["id"]): "two"}])
    assert len(rows) == 2
    with pytest.raises(AppError) as error:
        app.records.paste(sheet["id"], [{}] * 5001, confirmed=True)
    assert error.value.code == "limit_exceeded"
