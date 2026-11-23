import pytest
from pdp.services import App


@pytest.fixture
def app(tmp_path):
    instance = App(tmp_path)
    yield instance
    instance.db.close()


@pytest.fixture
def project(app):
    return app.projects.create("Test Project")


@pytest.fixture
def sheet(app, project):
    return app.sheets.create(project.id, "Records")
