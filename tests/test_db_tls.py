"""Tests that every PostgreSQL connection requires TLS (Phase 17).

The unit tests use fake URLs and a fake psycopg.connect; nothing is printed. The database-backed
test checks that the real connection to the hosted database is encrypted.
"""

import importlib
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app import db_executor
from app.config import settings
from app.db_executor import QueryExecutionError, execute_query, tls_sslmode

BASE = "postgresql://app_user:s3cret@db.example.test:5432/postgres"
DATABASE_DIR = Path(__file__).resolve().parents[1] / "database"


@pytest.mark.parametrize("url, expected", [
    (BASE, "require"),  # no sslmode: libpq would default to "prefer", which can fall back to plain text
    (BASE + "?sslmode=require", "require"),
    (BASE + "?sslmode=disable", "require"),
    (BASE + "?sslmode=allow", "require"),
    (BASE + "?sslmode=prefer", "require"),
    (BASE + "?sslmode=verify-ca", "verify-ca"),  # stricter modes are kept, never downgraded
    (BASE + "?sslmode=verify-full", "verify-full"),
    ("host=db.example.test user=app_user sslmode=disable", "require"),  # key=value form too
])
def test_tls_is_always_required(url, expected):
    assert tls_sslmode(url) == expected


@pytest.fixture
def connect_calls(monkeypatch):
    calls = []

    def fake_connect(url, **kwargs):
        calls.append((url, kwargs))
        raise psycopg.OperationalError(f'connection to server at "{url}" failed')

    monkeypatch.setattr(db_executor.psycopg, "connect", fake_connect)
    return calls


@pytest.mark.parametrize("url", [BASE, BASE + "?sslmode=disable", BASE + "?sslmode=prefer"])
def test_executor_connects_with_tls_required(connect_calls, url):
    with pytest.raises(QueryExecutionError):
        execute_query("SELECT 1", database_url=url)
    [(passed_url, kwargs)] = connect_calls
    assert kwargs["sslmode"] == "require"
    assert passed_url == url  # the URL itself is passed on unchanged, never rewritten
    assert kwargs["connect_timeout"] == db_executor.CONNECT_TIMEOUT_SECONDS


def test_other_url_parameters_and_special_characters_survive(connect_calls):
    password = "p@ss:w/rd#%?&=word"
    url = make_conninfo("", host="db.example.test", user="app_user", password=password, dbname="postgres",
                        application_name="datapilot", options="-c search_path=public")
    with pytest.raises(QueryExecutionError):
        execute_query("SELECT 1", database_url=url)
    [(passed_url, kwargs)] = connect_calls
    effective = conninfo_to_dict(make_conninfo(passed_url, **kwargs))  # what libpq will use
    assert effective["sslmode"] == "require" and effective["password"] == password
    assert effective["application_name"] == "datapilot" and effective["options"] == "-c search_path=public"


@pytest.mark.parametrize("url", [BASE + "?sslmode=disable", "not a url ===", "postgresql://u:hunter2@db.example.test/x?bad"])
def test_connection_errors_never_reveal_the_url(connect_calls, url):
    with pytest.raises(QueryExecutionError) as caught:
        execute_query("SELECT 1", database_url=url)
    assert caught.value.kind == "unavailable"
    for secret in ("s3cret", "hunter2", "db.example.test", "app_user", "postgresql://"):
        assert secret not in str(caught.value)


def test_admin_scripts_require_tls_too(monkeypatch):
    monkeypatch.syspath_prepend(str(DATABASE_DIR))
    apply_schema = importlib.import_module("apply_schema")
    calls = []
    monkeypatch.setattr(apply_schema.psycopg, "connect", lambda url, **kwargs: calls.append(kwargs))
    apply_schema.connect(BASE + "?sslmode=disable", autocommit=True)
    apply_schema.connect(BASE + "?sslmode=verify-full")
    assert calls == [{"sslmode": "require", "autocommit": True}, {"sslmode": "verify-full"}]
    sys.modules.pop("apply_schema", None)


def test_every_connection_in_the_repository_goes_through_a_tls_path():
    # Only the two helpers (and the test fixture that reuses the app's rule) call psycopg.connect.
    root = Path(__file__).resolve().parents[1]
    callers = sorted(
        str(path.relative_to(root)) for path in [*root.glob("backend/app/*.py"), *root.glob("database/*.py")]
        if "psycopg.connect(" in path.read_text(encoding="utf-8")
    )
    assert callers == ["backend/app/db_executor.py", "database/apply_schema.py"]


needs_database = pytest.mark.skipif(settings.readonly_database_url is None,
                                    reason="READONLY_DATABASE_URL is not configured")


@needs_database
def test_the_real_connection_is_encrypted():
    url = settings.readonly_database_url.get_secret_value()
    try:
        conn = psycopg.connect(url, connect_timeout=db_executor.CONNECT_TIMEOUT_SECONDS, sslmode=tls_sslmode(url))
    except psycopg.Error as error:
        pytest.fail(f"Could not connect as the read-only role ({type(error).__name__})", pytrace=False)
    with conn:
        assert conn.pgconn.ssl_in_use
        assert execute_query("SELECT COUNT(*) AS n FROM categories").rows == [[10]]
