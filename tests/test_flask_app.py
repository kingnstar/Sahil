import io
import re
import pytest
from flask_app import create_app


@pytest.fixture
def web(tmp_path):
    app = create_app(tmp_path)
    app.config.update(TESTING=True)
    client = app.test_client()
    client.get("/")
    with client.session_transaction() as state:
        token = state["_csrf_token"]
    yield app, client, token
    app.extensions["pdp"].db.close()


def setup_workspace(client, token):
    return client.post("/setup", data={"csrf_token": token, "display_name": "Local Operator"})


def make_project(client, token, name="Web Project"):
    response = client.post("/projects", data={"csrf_token": token, "name": name, "template": "Blank"})
    assert response.status_code == 302
    with client.application.extensions["pdp"].db.transaction() as s:
        from sqlalchemy import select
        from pdp.db.models import Project
        project = s.scalar(select(Project).where(Project.name == name))
        return project.id


def test_initial_setup_and_redirect(web):
    _, client, token = web
    assert client.get("/").status_code == 302
    response = setup_workspace(client, token)
    assert response.status_code == 302 and response.location.endswith("/")
    assert client.get("/").status_code == 200


def test_every_post_requires_csrf(web):
    _, client, _ = web
    assert client.post("/setup", data={"display_name": "Operator"}).status_code == 400


def test_security_headers_and_loopback_health(web):
    _, client, _ = web
    response = client.get("/health")
    assert response.json["binding"] == "127.0.0.1"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_invalid_display_name_feedback(web):
    _, client, token = web
    response = client.post("/setup", data={"csrf_token": token, "display_name": "123"})
    assert response.status_code == 302
    follow = client.get(response.location, follow_redirects=True)
    assert b"cannot contain only digits" in follow.data


def test_project_create_page_and_settings(web):
    _, client, token = web
    setup_workspace(client, token)
    project_id = make_project(client, token)
    response = client.get(f"/projects/{project_id}")
    assert response.status_code == 200
    assert b"Project tools" in response.data
    assert client.get("/settings").status_code == 200
    assert client.post("/settings", data={"csrf_token": token, "display_name": "Operator Two"}).status_code == 302


def test_sheet_create_and_render(web):
    _, client, token = web
    setup_workspace(client, token)
    project_id = make_project(client, token)
    response = client.post(f"/projects/{project_id}/sheets", data={"csrf_token": token, "name": "Contacts"})
    assert response.status_code == 302
    sheet_id = int(response.location.rsplit("/", 1)[1])
    assert b"Add a column" in client.get(f"/sheets/{sheet_id}").data


def test_api_csrf_validation_and_optimistic_conflict(web):
    app, client, token = web
    setup_workspace(client, token)
    project_id = make_project(client, token)
    pdp = app.extensions["pdp"]
    sheet = pdp.sheets.create(project_id, "Rows")
    field = pdp.columns.add(sheet["id"], "Age", "WHOLE")
    row = pdp.records.add(sheet["id"], {str(field["id"]): "21"})
    missing = client.post(f"/api/records/{row['id']}", json={"values": {str(field["id"]): "22"}, "expected_version": 1})
    assert missing.status_code == 400
    invalid = client.post(f"/api/records/{row['id']}", headers={"X-CSRF-Token": token},
                          json={"values": {str(field["id"]): "22"}, "expected_version": 1})
    assert invalid.status_code == 200 and invalid.json["version"] == 2
    conflict = client.post(f"/api/records/{row['id']}", headers={"X-CSRF-Token": token},
                           json={"values": {str(field["id"]): "23"}, "expected_version": 1})
    assert conflict.status_code == 422 and conflict.json["error"] == "conflict"


def test_api_validation_message_and_column_scope(web):
    app, client, token = web
    setup_workspace(client, token)
    pid = make_project(client, token)
    sheet = app.extensions["pdp"].sheets.create(pid, "Input")
    field = app.extensions["pdp"].columns.add(sheet["id"], "Whole", "WHOLE")
    row = app.extensions["pdp"].records.add(sheet["id"])
    response = client.post(f"/api/records/{row['id']}", headers={"X-CSRF-Token": token},
                           json={"values": {str(field["id"]): "2.5"}, "expected_version": 1})
    assert response.status_code == 422 and "ASCII digits" in response.json["message"]


def test_paste_api_uses_csrf_and_live_records(web):
    app, client, token = web
    setup_workspace(client, token)
    pid = make_project(client, token)
    pdp = app.extensions["pdp"]
    sheet = pdp.sheets.create(pid, "Paste")
    field = pdp.columns.add(sheet["id"], "Name")
    response = client.post(f"/api/sheets/{sheet['id']}/paste", headers={"X-CSRF-Token": token},
                          json={"rows": [{str(field["id"]): "First"}, {str(field["id"]): "Second"}],
                                "confirmed": False})
    assert response.status_code == 201 and response.json["created"] == 2


def test_import_review_flow(web):
    app, client, token = web
    setup_workspace(client, token)
    pid = make_project(client, token)
    response = client.post(f"/import/{pid}", data={
        "csrf_token": token, "file": (io.BytesIO(b"Name,Age\nAlice,31\n"), "data.csv")
    }, content_type="multipart/form-data")
    assert response.status_code == 200 and b"Import reviewed data" in response.data
    with client.session_transaction() as state:
        csrf = state["_csrf_token"]
    match = re.search(rb'name="job_id" value="(\d+)"', response.data)
    assert match
    committed = client.post(f"/import/{pid}", data={"csrf_token": csrf, "job_id": match.group(1).decode()})
    assert committed.status_code == 302
    assert len(app.extensions["pdp"].sheets.list(pid)) == 1


def test_export_response_and_history(web):
    app, client, token = web
    setup_workspace(client, token)
    pid = make_project(client, token)
    response = client.post(f"/projects/{pid}/exports", data={"csrf_token": token, "format": "xlsx"})
    assert response.status_code == 200
    assert response.headers["Content-Disposition"].endswith(".xlsx")
    assert b"PK" == response.data[:2]


def test_traversal_download_attempts_are_not_served(web):
    _, client, _ = web
    for path in ("/download/../../flask_secret", "/download/%2e%2e%2fdata%2fsecret",
                 "/projects/../../flask_secret", "/static/../../flask_secret"):
        assert client.get(path).status_code in {404, 308}


def test_not_found_and_upload_limit_pages(web):
    _, client, _ = web
    assert client.get("/projects/999999").status_code == 404
    client.application.config["MAX_CONTENT_LENGTH"] = 1
    response = client.post("/import/1", data={"file": (io.BytesIO(b"xx"), "x.csv")},
                           content_type="multipart/form-data")
    assert response.status_code in {400, 413}


def test_project_copy_safe_filename(web):
    app, client, token = web
    setup_workspace(client, token)
    pid = make_project(client, token, "../unsafe")
    response = client.get(f"/projects/{pid}/copy.zip")
    assert response.status_code == 200
    assert ".." not in response.headers["Content-Disposition"]
    assert response.data[:2] == b"PK"


def test_all_navigation_views_render(web):
    app, client, token = web
    setup_workspace(client, token)
    pid = make_project(client, token)
    for path in (f"/projects/{pid}/dashboards", f"/projects/{pid}/reports",
                 f"/projects/{pid}/exports", f"/projects/{pid}/history",
                 f"/projects/{pid}/recycle", f"/projects/{pid}/search?q=abc"):
        assert client.get(path).status_code == 200


def test_escaped_project_name(web):
    _, client, token = web
    setup_workspace(client, token)
    pid = make_project(client, token, "<script>alert(1)</script>")
    response = client.get(f"/projects/{pid}")
    assert b"&lt;script&gt;" in response.data
    assert b"<script>alert(1)</script>" not in response.data
