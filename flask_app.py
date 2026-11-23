from __future__ import annotations

from io import BytesIO
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import zipfile

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from sqlalchemy import select

from pdp.config import LIMITS, data_path
from pdp.db.models import Dataset, Field, Project, Record, SystemSetting
from pdp.domain.errors import AppError
from pdp.domain.types import validate_display_name
from pdp.services import App
from pdp.services.exports import safe_filename


def create_app(data_dir=None):
    root = data_path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    secret_file = root / "flask_secret"
    try:
        secret = secret_file.read_bytes()
        if len(secret) < 32:
            raise OSError("Invalid secret file")
    except OSError:
        secret = secrets.token_bytes(48)
        secret_file.write_bytes(secret)
        try:
            secret_file.chmod(0o600)
        except OSError:
            pass

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.update(
        SECRET_KEY=secret,
        MAX_CONTENT_LENGTH=LIMITS.upload_bytes + 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=False,
        JSON_SORT_KEYS=False,
    )
    container = App(root)
    app.extensions["pdp"] = container
    app.extensions["pdp_data_dir"] = root

    @app.before_request
    def protect():
        if request.method == "POST":
            supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
            expected = session.get("_csrf_token", "")
            if not expected or not hmac.compare_digest(str(supplied), str(expected)):
                return render_template("error.html", code=400, message="Your form expired. Reload and try again."), 400
        if request.method in {"POST", "PUT", "PATCH"} and request.content_length is not None:
            if request.content_length > LIMITS.upload_bytes + 1024 * 1024:
                return render_template("error.html", code=413, message="The submitted request is too large."), 413

    @app.after_request
    def security_headers(response):
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        if "_csrf_token" not in session:
            session["_csrf_token"] = secrets.token_urlsafe(32)
        return response

    @app.context_processor
    def template_context():
        return {"csrf_token": session.get("_csrf_token", ""), "app_limits": LIMITS}

    def service():
        return app.extensions["pdp"]

    def setting(key):
        return service().system.get(key)

    def ensure_setup():
        return bool(setting("display_name"))

    def owned_sheet(sheet_id, project_id=None):
        with service().db.transaction() as s:
            sheet = s.get(Dataset, int(sheet_id))
            if sheet is None:
                abort(404)
            project = s.get(Project, sheet.project_id)
            if project is None or project.deleted_at is not None or (project_id is not None and sheet.project_id != int(project_id)):
                abort(404)
            return {"id": sheet.id, "project_id": sheet.project_id, "name": sheet.name,
                    "fields": [{"id": f.id, "name": f.name, "type": f.field_type, "options": f.options,
                                "hidden": f.hidden, "position": f.position} for f in sorted(sheet.fields, key=lambda x:x.position)]}

    def owned_record(record_id):
        with service().db.transaction() as s:
            row = s.get(Record, int(record_id))
            if row is None or row.deleted_at is not None:
                abort(404)
            sheet = s.get(Dataset, row.dataset_id)
            if sheet is None:
                abort(404)
            return row.dataset_id, sheet.project_id

    @app.get("/")
    def index():
        if not ensure_setup():
            return redirect(url_for("setup"))
        projects = service().projects.list()
        return render_template("start.html", projects=projects, templates=[
            ("Blank", []), ("Inventory", [("Items", ["Item", "Quantity", "Location"])]),
            ("Supplier", [("Suppliers", ["Supplier", "Contact", "Email"])]),
            ("Asset", [("Assets", ["Asset", "Serial Number", "Status"])]),
            ("School", [("Students", ["Student", "Year", "Email"])]),
            ("Custom", [])])

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if ensure_setup():
            return redirect(url_for("index"))
        if request.method == "POST":
            display_name = validate_display_name(request.form.get("display_name", ""))
            service().system.set("display_name", display_name)
            flash("Welcome. Your local workspace is ready.", "success")
            return redirect(url_for("index"))
        return render_template("setup.html")

    @app.post("/projects")
    def create_project():
        if not ensure_setup():
            return redirect(url_for("setup"))
        project = service().projects.create(request.form.get("name", ""), request.form.get("description", ""))
        template = request.form.get("template", "Blank")
        for sheet_name, headers in {"Inventory": [("Items", ["Item", "Quantity", "Location"])],
                                    "Supplier": [("Suppliers", ["Supplier", "Contact", "Email"])],
                                    "Asset": [("Assets", ["Asset", "Serial Number", "Status"])],
                                    "School": [("Students", ["Student", "Year", "Email"])]}.get(template, []):
            sheet = service().sheets.create(project.id, sheet_name)
            for header in headers:
                service().columns.add(sheet["id"], header)
        flash("Project created.", "success")
        return redirect(url_for("project_home", project_id=project.id))

    @app.get("/projects/<int:project_id>")
    def project_home(project_id):
        project = service().projects.get(project_id)
        sheets = service().sheets.list(project_id)
        audit = [a for a in service().audit.list(100) if a["entity"] in {"project", "sheet", "record"}][:8]
        return render_template("project.html", project=project, sheets=sheets, audit=audit,
                               total_rows=sum(x["row_count"] for x in sheets))

    @app.post("/projects/<int:project_id>/sheets")
    def create_sheet(project_id):
        sheet = service().sheets.create(project_id, request.form.get("name", ""))
        return redirect(url_for("sheet_view", sheet_id=sheet["id"]))

    @app.get("/sheets/<int:sheet_id>")
    def sheet_view(sheet_id):
        sheet = owned_sheet(sheet_id)
        project = service().projects.get(sheet["project_id"])
        page = request.args.get("page", 1, type=int)
        data = service().records.list(sheet_id, page)
        return render_template("sheet.html", sheet=sheet, project=project, data=data,
                               sheets=service().sheets.list(sheet["project_id"]))

    @app.post("/sheets/<int:sheet_id>/rows")
    def add_row(sheet_id):
        owned_sheet(sheet_id)
        row = service().records.add(sheet_id, {})
        return redirect(url_for("sheet_view", sheet_id=sheet_id) + f"#row-{row['id']}")

    @app.post("/api/sheets/<int:sheet_id>/rows")
    def api_add_row(sheet_id):
        owned_sheet(sheet_id)
        row = service().records.add(sheet_id, {})
        return jsonify(row), 201

    @app.post("/api/sheets/<int:sheet_id>/paste")
    def api_paste_rows(sheet_id):
        owned_sheet(sheet_id)
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            raise AppError("validation_error", "Request body must be an object")
        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise AppError("validation_error", "Paste rows must be a list")
        results = service().records.paste(sheet_id, rows, confirmed=payload.get("confirmed", False))
        return jsonify({"created": len(results), "rows": results}), 201

    @app.post("/api/records/<int:record_id>")
    def api_update_record(record_id):
        dataset_id, _ = owned_record(record_id)
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise AppError("validation_error", "Request body must be an object")
        if not isinstance(body.get("values"), dict):
            raise AppError("validation_error", "A values object is required")
        fields = {str(f["id"]) for f in owned_sheet(dataset_id)["fields"]}
        if not set(map(str, body["values"])).issubset(fields):
            raise AppError("validation_error", "Column does not belong to this sheet")
        result = service().records.update(record_id, body["values"], body.get("expected_version"))
        return jsonify(result)

    @app.post("/api/records/<int:record_id>/delete")
    def api_delete_record(record_id):
        owned_record(record_id)
        service().records.delete(record_id)
        return jsonify({"ok": True})

    @app.post("/api/sheets/<int:sheet_id>/rows/delete")
    def api_bulk_delete_rows(sheet_id):
        owned_sheet(sheet_id)
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            raise AppError("validation_error", "Request body must be an object")
        count = service().records.bulk_delete(sheet_id, payload.get("record_ids"),
                                              confirmed=bool(payload.get("confirmed")))
        return jsonify({"deleted": count})

    @app.post("/projects/<int:project_id>/sheets/<int:sheet_id>/columns")
    def add_column(project_id, sheet_id):
        owned_sheet(sheet_id, project_id)
        options = [item.strip() for item in request.form.get("options", "").split(",") if item.strip()]
        service().columns.add(sheet_id, request.form.get("name", ""), request.form.get("field_type", "TEXT"), options)
        return redirect(url_for("sheet_view", sheet_id=sheet_id))

    @app.post("/api/columns/<int:field_id>/visibility")
    def column_visibility(field_id):
        field = service().columns.list(owned_field_sheet(field_id))
        target = next(x for x in field if x["id"] == field_id)
        value = request.get_json(silent=True) or {}
        if not isinstance(value, dict):
            raise AppError("validation_error", "Request body must be an object")
        result = service().columns.set_hidden(field_id, value.get("hidden", not target["hidden"]))
        return jsonify(result)

    def owned_field_sheet(field_id):
        with service().db.transaction() as s:
            f = s.get(Field, int(field_id))
            if f is None: abort(404)
            d = s.get(Dataset, f.dataset_id)
            if d is None: abort(404)
            p = s.get(Project, d.project_id)
            if p is None or p.deleted_at is not None: abort(404)
            return d.id

    @app.post("/api/columns/<int:field_id>/move")
    def column_move(field_id):
        owned_field_sheet(field_id)
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            raise AppError("validation_error", "Request body must be an object")
        return jsonify(service().columns.move(field_id, payload.get("position", 0)))

    @app.post("/api/columns/<int:field_id>/delete")
    def column_delete(field_id):
        owned_field_sheet(field_id)
        service().columns.delete(field_id)
        return jsonify({"ok": True})

    @app.post("/projects/<int:project_id>/sheets/<int:sheet_id>/delete")
    def delete_sheet(project_id, sheet_id):
        owned_sheet(sheet_id, project_id)
        service().sheets.delete(sheet_id)
        flash("Sheet deleted.", "success")
        return redirect(url_for("project_home", project_id=project_id))

    @app.get("/projects/<int:project_id>/search")
    def search(project_id):
        project = service().projects.get(project_id)
        query = request.args.get("q", "")
        results = service().search.search(project_id, query) if len(query.strip()) >= LIMITS.search_min_chars else []
        return render_template("search.html", project=project, query=query, results=results)

    @app.get("/projects/<int:project_id>/dashboards")
    def dashboards(project_id):
        project = service().projects.get(project_id)
        saved = service().analytics.list(project_id, "dashboard")
        sheets = service().sheets.list(project_id)
        return render_template("dashboard.html", project=project, saved=saved, sheets=sheets)

    @app.post("/projects/<int:project_id>/dashboards")
    def save_dashboard(project_id):
        service().analytics.save(project_id, "dashboard", request.form.get("name", "Dashboard"),
                                 {"metric": "row_count"})
        return redirect(url_for("dashboards", project_id=project_id))

    @app.route("/projects/<int:project_id>/reports", methods=["GET", "POST"])
    def reports(project_id):
        project = service().projects.get(project_id)
        if request.method == "POST":
            service().analytics.save(project_id, "report", request.form.get("name", "Report"),
                                     {"sheet_id": request.form.get("sheet_id", "")})
            return redirect(url_for("reports", project_id=project_id))
        return render_template("reports.html", project=project,
                               reports=service().analytics.list(project_id, "report"),
                               sheets=service().sheets.list(project_id))

    @app.route("/projects/<int:project_id>/exports", methods=["GET", "POST"])
    def exports(project_id):
        project = service().projects.get(project_id)
        if request.method == "POST":
            fmt = request.form.get("format", "xlsx")
            filename, content = service().exports.generate(project_id, fmt)
            return send_file(BytesIO(content), as_attachment=True, download_name=filename,
                             mimetype={"xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                       "pdf":"application/pdf", "png":"image/png"}.get(fmt, "application/octet-stream"))
        return render_template("exports.html", project=project, history=service().exports.history(project_id))

    @app.get("/projects/<int:project_id>/history")
    def history(project_id):
        project = service().projects.get(project_id)
        entries = [x for x in service().audit.list(200) if x["entity"] != "system"]
        return render_template("history.html", project=project, entries=entries)

    @app.get("/projects/<int:project_id>/recycle")
    def recycle(project_id):
        project = service().projects.get(project_id)
        return render_template("recycle.html", project=project, rows=service().recycle.list(project_id))

    @app.post("/api/recycle/<int:record_id>/restore")
    def restore_record(record_id):
        with service().db.transaction() as s:
            row = s.get(Record, record_id)
            if row is None or row.deleted_at is None: abort(404)
            project_id = s.get(Dataset, row.dataset_id).project_id
        result = service().recycle.restore(record_id)
        return jsonify({"ok": True, "project_id": project_id, "record": result})

    @app.post("/api/recycle/<int:record_id>/delete")
    def delete_forever(record_id):
        with service().db.transaction() as s:
            row = s.get(Record, record_id)
            if row is None or row.deleted_at is None: abort(404)
        service().recycle.delete_forever(record_id)
        return jsonify({"ok": True})

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        if request.method == "POST":
            name = validate_display_name(request.form.get("display_name", ""))
            service().system.set("display_name", name)
            flash("Settings saved.", "success")
            return redirect(url_for("settings"))
        return render_template("settings.html", display_name=setting("display_name"))

    @app.post("/projects/<int:project_id>/rename")
    def rename_project(project_id):
        service().projects.rename(project_id, request.form.get("name", ""), request.form.get("description", ""))
        flash("Project updated.", "success")
        return redirect(url_for("project_home", project_id=project_id))

    @app.get("/projects/<int:project_id>/copy.zip")
    def copy_project(project_id):
        project = service().projects.get(project_id)
        output = BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for sheet in service().sheets.list(project_id):
                schema = owned_sheet(sheet["id"], project_id)
                data = []
                page = 1
                while True:
                    result = service().records.list(sheet["id"], page=page, page_size=LIMITS.grid_page)
                    data.extend(result["items"])
                    if len(data) >= result["total"]:
                        break
                    page += 1
                archive.writestr(safe_filename(sheet["name"]) + ".json",
                                 json.dumps({"sheet": {"name": sheet["name"],
                                                       "columns": schema["fields"]},
                                             "records": data}, ensure_ascii=False))
            archive.writestr("project.json", json.dumps(project, ensure_ascii=False))
        output.seek(0)
        return send_file(output, mimetype="application/zip", as_attachment=True,
                         download_name=safe_filename(project["name"]) + "-copy.zip")

    @app.post("/projects/<int:project_id>/delete")
    def delete_project(project_id):
        service().projects.delete(project_id)
        flash("Project moved out of the active workspace.", "success")
        return redirect(url_for("index"))

    @app.route("/import/<int:project_id>", methods=["GET", "POST"])
    def import_wizard(project_id):
        project = service().projects.get(project_id)
        if request.method == "POST":
            if "job_id" in request.form:
                result = service().imports.commit(project_id, int(request.form["job_id"]))
                flash(f"Imported {len(result['sheets'])} sheet(s).", "success")
                return redirect(url_for("project_home", project_id=project_id))
            upload = request.files.get("file")
            if not upload:
                raise AppError("validation_error", "Choose a file to import")
            review = service().imports.review(upload.filename, upload.read(LIMITS.upload_bytes + 1))
            return render_template("import.html", project=project, review=review)
        return render_template("import.html", project=project)

    @app.errorhandler(AppError)
    def app_error(error):
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"error": error.code, "message": error.message}), 422
        if error.code == "not_found":
            return render_template("error.html", code=404, message="That page or item could not be found."), 404
        flash(error.message, "error")
        return redirect(request.referrer or url_for("index"))

    @app.errorhandler(400)
    def bad_request(_):
        return render_template("error.html", code=400, message="The request could not be understood."), 400

    @app.errorhandler(404)
    def not_found(_):
        return render_template("error.html", code=404, message="That page or item could not be found."), 404

    @app.errorhandler(413)
    def too_large(_):
        return render_template("error.html", code=413, message="The uploaded file exceeds the 25 MB limit."), 413

    @app.errorhandler(500)
    def server_error(_):
        return render_template("error.html", code=500, message="Something went wrong. Your local data remains unchanged."), 500

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "binding": "127.0.0.1"})

    return app


def main():
    host = os.environ.get("PDP_HOST", "127.0.0.1")
    if not is_loopback_host(host):
        raise SystemExit("Project Data Platform only binds to 127.0.0.1")
    app = create_app()
    app.run(host="127.0.0.1", port=5000, debug=False)


def is_loopback_host(host):
    return host == "127.0.0.1"


if __name__ == "__main__":
    main()
