"""Shared test setup."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app import auth, main
from app.config import settings
from app.history_store import (AlreadySaved, AnalysisDetail, AnalysisSummary, HistoryResult, SavedReport,
                               SavedReportDetail, SavedReportRef, SavedReportSummary, StoredAnalysis)
from app.rate_limiter import DailyLimit


@pytest.fixture(autouse=True)
def no_real_gemini_key(monkeypatch):
    """Tests never call the real Gemini API: any code path that would build a real client
    sees no key and fails with not_configured. Tests that need a key set a fake one."""
    monkeypatch.setattr(settings, "gemini_api_key", None)


@pytest.fixture(autouse=True)
def fresh_daily_limit(monkeypatch):
    """The global daily cap is process-wide state: give every test its own, generous counter so
    counts never leak between tests. Tests of the cap itself install their own."""
    monkeypatch.setattr(main, "daily_limit", DailyLimit(limit=10_000))


SIGNED_IN = auth.Session(session_id="test-session-override", expires_at=2**31)


@pytest.fixture(autouse=True)
def signed_in():
    """Most tests exercise the query pipeline, not the login gate, so they run as an already
    signed-in user: this overrides the require_session dependency for the test only. Production
    code is unchanged and always requires a session. tests/test_auth.py removes this override
    (its `real_auth` fixture) and tests the real check."""
    main.app.dependency_overrides[auth.require_session] = lambda: SIGNED_IN
    yield
    main.app.dependency_overrides.pop(auth.require_session, None)


class FakeHistory:
    """Stands in for app.history_store in every test, so no test ever reads or writes the real
    History database (the local .env may hold a real APP_DATABASE_URL).

    Called directly, it is record_analysis (POST /query): it records each call and returns .result,
    by default "saved" with ANALYSIS_ID, without storing anything. Its other methods are the History
    and Saved Reports API operations, over an in-memory store with the same rules as the real one:
    data belongs to an account, a report's account is its analysis's, one report per analysis.
    Seed it with add_analysis(); set .fail_with to an exception that every API operation raises.
    tests/test_history_store.py tests the real store against a fake connection."""

    ANALYSIS_ID = "00000000-0000-4000-8000-00000000c0de"
    START = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)

    def __init__(self):
        self.calls = []  # (response, account_id)
        self.result = HistoryResult("saved", analysis_id=self.ANALYSIS_ID)
        self.analyses, self.reports = {}, {}  # id -> (account_id, StoredAnalysis); id -> SavedReport
        self.api_calls = []  # (operation, account_id)
        self.fail_with = None
        self._tick = 0

    def __call__(self, response, *, account_id):
        self.calls.append((response, account_id))
        return self.result

    def add_analysis(self, account_id="demo", **fields):
        defaults = dict(id=uuid4(), question="Total revenue?", sql="SELECT 1 AS revenue", columns=["revenue"],
                        rows=[[1]], row_count=1, truncated=False, visualization={"type": "kpi", "y_key": "revenue"},
                        insight="Revenue is 1.", created_at=self._now())
        analysis = StoredAnalysis(**(defaults | fields))
        self.analyses[analysis.id] = (account_id, analysis)
        return analysis

    def list_history(self, account_id, limit):
        mine = self._mine("list_history", account_id)
        newest = sorted(mine.values(), key=lambda a: a.created_at, reverse=True)[:limit]
        return [AnalysisSummary(id=a.id, question=a.question, visualization_type=a.visualization.type,
                                row_count=a.row_count, truncated=a.truncated, created_at=a.created_at,
                                saved_report_id=self._report_for(a.id) and self._report_for(a.id).id) for a in newest]

    def get_analysis(self, account_id, analysis_id):
        analysis = self._mine("get_analysis", account_id).get(analysis_id)
        if analysis is None:
            return None
        report = self._report_for(analysis_id)
        ref = report and SavedReportRef(id=report.id, title=report.title)
        return AnalysisDetail(**analysis.model_dump(), saved_report=ref)

    def save_report(self, account_id, analysis_id, title):
        if analysis_id not in self._mine("save_report", account_id):
            return None
        if self._report_for(analysis_id):
            raise AlreadySaved()
        report = SavedReport(id=uuid4(), analysis_id=analysis_id, title=title, saved_at=self._now())
        self.reports[report.id] = report
        return report

    def list_saved_reports(self, account_id, limit):
        mine = self._mine("list_saved_reports", account_id)
        reports = sorted((r for r in self.reports.values() if r.analysis_id in mine),
                         key=lambda r: r.saved_at, reverse=True)[:limit]
        return [SavedReportSummary(id=r.id, analysis_id=r.analysis_id, title=r.title,
                                   question=mine[r.analysis_id].question,
                                   visualization_type=mine[r.analysis_id].visualization.type,
                                   row_count=mine[r.analysis_id].row_count, truncated=mine[r.analysis_id].truncated,
                                   saved_at=r.saved_at, analysis_created_at=mine[r.analysis_id].created_at)
                for r in reports]

    def get_saved_report(self, account_id, report_id):
        mine = self._mine("get_saved_report", account_id)
        report = self.reports.get(report_id)
        if report is None or report.analysis_id not in mine:
            return None
        return SavedReportDetail(id=report.id, title=report.title, saved_at=report.saved_at,
                                 analysis=mine[report.analysis_id])

    def _mine(self, operation, account_id):
        self.api_calls.append((operation, account_id))
        if self.fail_with is not None:
            raise self.fail_with
        return {aid: analysis for aid, (owner, analysis) in self.analyses.items() if owner == account_id}

    def _report_for(self, analysis_id):
        return next((r for r in self.reports.values() if r.analysis_id == analysis_id), None)

    def _now(self):
        self._tick += 1
        return self.START + timedelta(minutes=self._tick)


API_OPERATIONS = ("list_history", "get_analysis", "save_report", "list_saved_reports", "get_saved_report")


@pytest.fixture(autouse=True)
def fake_history(monkeypatch):
    """main uses app.history_store's functions by name; every test gets the fake instead."""
    fake = FakeHistory()
    monkeypatch.setattr(main, "record_analysis", fake)
    for operation in API_OPERATIONS:
        monkeypatch.setattr(main, operation, getattr(fake, operation))
    return fake
