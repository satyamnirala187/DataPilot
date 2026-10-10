"""Tests for Phase 17's optional backend hardening: API docs off by default, HSTS, and the split
between runtime and development dependencies. (Metadata-function blocking is in
test_sql_validator.py.) No Gemini calls.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.config import Settings

ROOT = Path(__file__).resolve().parents[1]
DOCS_PATHS = ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"]


# --- API docs ---------------------------------------------------------------------------------

def test_api_docs_are_off_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
    assert Settings(_env_file=None).enable_api_docs is False


@pytest.mark.parametrize("path", DOCS_PATHS)
def test_docs_routes_are_not_served_by_default(path):
    response = TestClient(main.app).get(path)
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "This endpoint does not exist."}}


def test_docs_can_be_switched_on_for_local_development():
    app = FastAPI(**main.api_docs_urls(True))
    client = TestClient(app)
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    assert client.get("/openapi.json").status_code == 200


# --- HSTS ----------------------------------------------------------------------------------------

HSTS = "max-age=31536000"


def test_hsts_is_sent_without_subdomains_or_preload():
    response = TestClient(main.app).get("/health")
    assert response.headers["strict-transport-security"] == HSTS


@pytest.mark.parametrize("method, path, kwargs", [
    ("get", "/nope", {}),  # 404
    ("post", "/query", {"json": {}}),  # 400
    ("put", "/query", {}),  # 405
])
def test_hsts_is_sent_on_error_responses_too(method, path, kwargs):
    response = getattr(TestClient(main.app), method)(path, **kwargs)
    assert response.status_code >= 400 and response.headers["strict-transport-security"] == HSTS


# --- Dependencies --------------------------------------------------------------------------------

def requirement_names(path):
    lines = (line.split("#")[0].strip() for line in (ROOT / path).read_text().splitlines())
    return {line.split("==")[0].split("[")[0].lower() for line in lines if line and not line.startswith("-r")}


def test_production_requirements_hold_only_runtime_packages():
    runtime = requirement_names("backend/requirements.txt")
    assert runtime == {"fastapi", "starlette", "pydantic", "pydantic-settings", "uvicorn", "psycopg", "sqlglot",
                       "google-genai", "httpx"}
    assert not runtime & {"pytest", "faker"}


def test_every_package_the_app_imports_is_declared_for_production():
    import_to_package = {"fastapi": "fastapi", "starlette": "starlette", "pydantic": "pydantic",
                         "pydantic_settings": "pydantic-settings", "psycopg": "psycopg", "sqlglot": "sqlglot",
                         "google": "google-genai", "httpx": "httpx"}
    imported = set()
    for path in (ROOT / "backend" / "app").glob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(("import ", "from ")):
                module = line.split()[1].split(".")[0]
                if module not in sys.stdlib_module_names and module != "app":
                    imported.add(module)
    assert imported == set(import_to_package)  # a new third-party import must be added here and below
    assert {import_to_package[m] for m in imported} <= requirement_names("backend/requirements.txt")


def test_development_requirements_add_tests_and_seeding_on_top():
    text = (ROOT / "backend/requirements-dev.txt").read_text()
    assert "-r requirements.txt" in text
    assert requirement_names("backend/requirements-dev.txt") == {"pytest", "faker"}


def test_render_installs_only_the_runtime_requirements():
    assert "buildCommand: pip install -r requirements.txt" in (ROOT / "render.yaml").read_text()


def test_the_app_imports_without_development_packages():
    # As in production: pytest and Faker are not installed, so importing them must not be needed.
    script = ("import sys\n"
              "sys.modules['pytest'] = sys.modules['faker'] = sys.modules['_pytest'] = None\n"
              "import app.main, app.query_service, app.db_executor, app.nl_to_sql, app.insight_service\n")
    done = subprocess.run([sys.executable, "-c", script], cwd=ROOT / "backend", capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr.splitlines()[-1:]
