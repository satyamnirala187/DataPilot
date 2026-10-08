"""Tests for the query pipeline (backend/app/query_service.py). No live Gemini or database calls."""

import pytest

from app.db_executor import QueryExecutionError, QueryResult
from app.nl_to_sql import SQLGenerationError
from app.query_service import GEMINI_RETRY_DELAYS, QueryServiceError, run_business_query
from app.sql_validator import UnsafeSQLError, validate_sql

RAW_SQL = "SELECT SUM(oi.quantity * oi.unit_price) AS revenue FROM orders o JOIN order_items oi ON oi.order_id = o.order_id"
SAFE_SQL = RAW_SQL + " LIMIT 500"


class Pipeline:
    """Fake generate / validate / execute / sleep steps that record how they were called."""

    def __init__(self, *, generate_results=None, validate_error=None, execute_result=None, execute_error=None):
        self.generate_results = list(generate_results or [RAW_SQL])  # SQL strings or exceptions, in order
        self.validate_error = validate_error
        self.execute_result = execute_result or QueryResult(columns=["revenue"], rows=[[21304631.99]], truncated=False)
        self.execute_error = execute_error
        self.generated_for, self.validated, self.executed, self.slept = [], [], [], []

    def generate(self, question):
        self.generated_for.append(question)
        outcome = self.generate_results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def validate(self, sql):
        self.validated.append(sql)
        if self.validate_error:
            raise self.validate_error
        return SAFE_SQL

    def execute(self, sql):
        self.executed.append(sql)
        if self.execute_error:
            raise self.execute_error
        return self.execute_result

    def run(self, question="What is our total revenue?"):
        return run_business_query(question, generate=self.generate, validate=self.validate,
                                  execute=self.execute, sleep=self.slept.append)


def failure(pipeline: Pipeline, question="What is our total revenue?") -> QueryServiceError:
    with pytest.raises(QueryServiceError) as caught:
        pipeline.run(question)
    return caught.value


# --- Successful pipeline --------------------------------------------------------------

def test_each_step_receives_the_previous_steps_output():
    p = Pipeline()
    p.run("  What is our total revenue?  ")
    assert p.generated_for == ["What is our total revenue?"]  # trimmed question
    assert p.validated == [RAW_SQL]
    assert p.executed == [SAFE_SQL]


def test_response_contains_validated_sql_and_results():
    response = Pipeline().run()
    assert response.question == "What is our total revenue?"
    assert response.sql == SAFE_SQL
    assert response.columns == ["revenue"]
    assert response.rows == [[21304631.99]]
    assert response.row_count == 1
    assert response.truncated is False


def test_row_count_and_truncated_flag_are_passed_through():
    result = QueryResult(columns=["order_id"], rows=[[1], [2], [3]], truncated=True)
    response = Pipeline(execute_result=result).run()
    assert response.row_count == 3 and response.truncated is True


def test_empty_result_is_a_valid_answer():
    response = Pipeline(execute_result=QueryResult(columns=["city"], rows=[], truncated=False)).run()
    assert response.rows == [] and response.row_count == 0


def test_unanswerable_message_query_flows_through_normally():
    message_sql = "SELECT 'This question cannot be answered with a read-only query on the store data.' AS message"
    executed = []
    response = run_business_query(
        "Delete every customer", generate=lambda q: message_sql, validate=validate_sql,
        execute=lambda sql: executed.append(sql) or QueryResult(["message"], [["This question cannot be answered..."]], False),
    )
    assert executed == [message_sql + " LIMIT 500"] and response.columns == ["message"]


# --- Security: only validator-approved SQL is executed ----------------------------------

def test_executor_is_not_called_when_generation_fails():
    p = Pipeline(generate_results=[SQLGenerationError("rate_limited", "x")])
    failure(p)
    assert p.validated == [] and p.executed == []


def test_executor_is_not_called_when_validation_fails():
    p = Pipeline(validate_error=UnsafeSQLError("Only SELECT queries are allowed, got DELETE."))
    assert failure(p).kind == "unsafe_sql"
    assert p.executed == []


def test_raw_generated_sql_never_reaches_the_executor():
    p = Pipeline()
    p.run()
    assert RAW_SQL not in p.executed and p.executed == [SAFE_SQL]


@pytest.mark.parametrize("injected", [
    "DELETE FROM customers",
    "DROP TABLE orders",
    "SELECT * FROM customers; DELETE FROM customers",
    "WITH gone AS (DELETE FROM orders RETURNING *) SELECT * FROM gone",
    "SELECT * FROM auth.users",
    "SELECT pg_sleep(30)",
])
def test_write_or_unsafe_sql_from_the_generator_is_blocked_by_the_real_validator(injected):
    executed = []
    with pytest.raises(QueryServiceError) as caught:
        run_business_query("anything", generate=lambda q: injected, validate=validate_sql,
                           execute=lambda sql: executed.append(sql))
    assert caught.value.kind == "unsafe_sql" and executed == []


def test_executor_receives_the_validator_modified_sql():
    executed = []
    run_business_query("all orders", generate=lambda q: "SELECT order_id FROM orders LIMIT 100000",
                       validate=validate_sql,
                       execute=lambda sql: executed.append(sql) or QueryResult(["order_id"], [], False))
    assert executed == ["SELECT order_id FROM orders LIMIT 500"]


# --- Errors ---------------------------------------------------------------------------

@pytest.mark.parametrize("question", ["", "   ", "\n\t", None])
def test_empty_question_is_rejected_before_anything_runs(question):
    p = Pipeline()
    assert failure(p, question).kind == "invalid_question"
    assert p.generated_for == [] and p.executed == []


@pytest.mark.parametrize("generation_kind, service_kind", [
    ("rate_limited", "rate_limited"),
    ("not_configured", "generation_unavailable"),
    ("model_unavailable", "generation_unavailable"),
    ("invalid_response", "generation_failed"),
    ("empty_response", "generation_failed"),
    ("request_failed", "generation_failed"),
    ("invalid_question", "invalid_question"),
])
def test_generation_errors_are_mapped(generation_kind, service_kind):
    p = Pipeline(generate_results=[SQLGenerationError(generation_kind, "internal detail")])
    error = failure(p)
    assert error.kind == service_kind
    assert "internal detail" not in str(error)


@pytest.mark.parametrize("execution_kind, service_kind", [
    ("unavailable", "database_unavailable"),
    ("timeout", "query_timeout"),
    ("permission_denied", "query_not_allowed"),
    ("query_error", "query_failed"),
    ("something_new", "query_failed"),
])
def test_execution_errors_are_mapped(execution_kind, service_kind):
    p = Pipeline(execute_error=QueryExecutionError(execution_kind, 'column "x" does not exist'))
    error = failure(p)
    assert error.kind == service_kind
    assert "does not exist" not in str(error)  # database detail stays internal


def test_service_errors_do_not_chain_the_original_exception():
    p = Pipeline(execute_error=QueryExecutionError("unavailable", "secret detail"))
    error = failure(p)
    assert error.__cause__ is None and error.__suppress_context__


# --- Gemini retry policy ----------------------------------------------------------------

def test_unavailable_gemini_is_retried_and_can_succeed():
    p = Pipeline(generate_results=[SQLGenerationError("unavailable", "503"), RAW_SQL])
    response = p.run()
    assert response.sql == SAFE_SQL
    assert len(p.generated_for) == 2 and p.slept == [GEMINI_RETRY_DELAYS[0]]


def test_succeeds_on_the_last_allowed_attempt():
    p = Pipeline(generate_results=[SQLGenerationError("unavailable", "503")] * 2 + [RAW_SQL])
    assert p.run().sql == SAFE_SQL
    assert len(p.generated_for) == 3 and p.slept == list(GEMINI_RETRY_DELAYS)


def test_repeated_unavailable_gives_up_after_two_retries():
    p = Pipeline(generate_results=[SQLGenerationError("unavailable", "503")] * 5)
    assert failure(p).kind == "generation_unavailable"
    assert len(p.generated_for) == 3  # 1 attempt + 2 retries
    assert p.slept == [0.5, 1.0]  # short exponential backoff
    assert p.executed == []


@pytest.mark.parametrize("kind", ["rate_limited", "not_configured", "model_unavailable", "request_failed",
                                  "invalid_response", "empty_response"])
def test_non_transient_generation_errors_are_not_retried(kind):
    p = Pipeline(generate_results=[SQLGenerationError(kind, "x"), RAW_SQL])
    failure(p)
    assert len(p.generated_for) == 1 and p.slept == []


def test_validation_failures_are_not_retried():
    p = Pipeline(validate_error=UnsafeSQLError("nope"))
    failure(p)
    assert len(p.generated_for) == 1 and len(p.validated) == 1


# --- Retry decisions by real Gemini HTTP status, through the real generate_sql ----------

from types import SimpleNamespace  # noqa: E402

from google.genai import errors as genai_errors  # noqa: E402

from app.nl_to_sql import GeneratedSQL, generate_sql  # noqa: E402


class FailingGemini:
    """A fake genai.Client whose calls fail with the given errors, then succeed."""

    def __init__(self, *failures):
        self.failures = list(failures)
        self.calls = 0
        self.models = SimpleNamespace(generate_content=self._generate_content)

    def _generate_content(self, **kwargs):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return SimpleNamespace(parsed=GeneratedSQL(sql=RAW_SQL), text=None, model_version=None)


def http_error(code: int) -> genai_errors.APIError:
    cls = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return cls(code, {"error": {"code": code, "message": "upstream detail", "status": "X"}})


def run_with(gemini: FailingGemini, slept: list):
    return run_business_query("Total revenue?", generate=lambda q: generate_sql(q, client=gemini),
                              validate=lambda sql: SAFE_SQL,
                              execute=lambda sql: QueryResult(["revenue"], [[1]], False), sleep=slept.append)


@pytest.mark.parametrize("code, kind", [
    (400, "generation_failed"),
    (401, "generation_unavailable"),
    (403, "generation_unavailable"),
    (404, "generation_unavailable"),
    (422, "generation_failed"),
    (429, "rate_limited"),
])
def test_gemini_4xx_errors_are_not_retried(code, kind):
    gemini, slept = FailingGemini(http_error(code)), []
    with pytest.raises(QueryServiceError) as caught:
        run_with(gemini, slept)
    assert caught.value.kind == kind
    assert gemini.calls == 1 and slept == []


@pytest.mark.parametrize("failure", [http_error(500), http_error(503), ConnectionError("network down"), TimeoutError()])
def test_gemini_5xx_and_network_errors_are_retried_then_succeed(failure):
    gemini, slept = FailingGemini(failure), []
    assert run_with(gemini, slept).sql == SAFE_SQL
    assert gemini.calls == 2 and slept == [0.5]


@pytest.mark.parametrize("failure", [http_error(500), http_error(503), ConnectionError("network down")])
def test_gemini_transient_errors_stop_after_two_retries(failure):
    gemini, slept = FailingGemini(failure, failure, failure, failure), []
    with pytest.raises(QueryServiceError) as caught:
        run_with(gemini, slept)
    assert caught.value.kind == "generation_unavailable"
    assert gemini.calls == 3 and slept == [0.5, 1.0]
    assert "upstream detail" not in str(caught.value)
