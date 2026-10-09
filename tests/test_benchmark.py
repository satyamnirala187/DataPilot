"""Tests for the benchmark infrastructure (tests/benchmark/). No Gemini and no database."""

import copy
import json
from types import SimpleNamespace

import pytest

from app.chart_selector import select_visualization
from app.db_executor import QueryExecutionError, QueryResult
from app.query_service import QueryServiceError
from app.sql_validator import validate_sql
from benchmark import run
from benchmark.evaluate import (
    BEHAVIOR_EXPECTATIONS,
    METRICS,
    REQUIRED_FIELDS,
    BenchmarkCaseError,
    LiveOutcome,
    evaluate_result,
    is_fallback,
    judge_live,
    load_cases,
    normalize_label,
    numbers_match,
    validate_case,
    validate_cases,
)
from benchmark.ground_truth import GROUND_TRUTH_SQL

CASES = load_cases()
BY_ID = {case["id"]: case for case in CASES}
FALLBACK_ROW = [["This question cannot be answered with a read-only query on the store data."]]


# --- The benchmark file ---------------------------------------------------------------------------

def test_benchmark_has_a_focused_number_of_cases():
    assert 20 <= len(CASES) <= 25


def test_case_ids_are_unique():
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids))


def test_every_case_has_the_required_fields():
    for case in CASES:
        assert all(field in case for field in REQUIRED_FIELDS), case["id"]


def test_every_answerable_case_has_ground_truth_sql_and_nothing_else_does():
    answerable = {case["id"] for case in CASES if case["expected_behavior"] == "answer"}
    assert set(GROUND_TRUTH_SQL) == answerable


@pytest.mark.parametrize("case_id", sorted(GROUND_TRUTH_SQL))
def test_ground_truth_sql_passes_the_apps_validator(case_id):
    validate_sql(GROUND_TRUTH_SQL[case_id])


def test_coverage_of_categories_behaviours_metrics_and_charts():
    categories = {case["category"] for case in CASES}
    assert {"kpi", "ranking", "geography", "time_series", "period_comparison", "rates", "payments",
            "breakdown", "customers", "unanswerable", "safety"} <= categories
    assert {case["expected_behavior"] for case in CASES} == set(BEHAVIOR_EXPECTATIONS)
    assert set().union(*(case.get("metrics", []) for case in CASES)) == METRICS
    assert {case.get("expected_chart") for case in CASES} >= {"kpi", "bar", "line", "table"}


def test_known_dataset_metrics_are_the_benchmark_answers():
    assert BY_ID["kpi_total_revenue"]["expectation"]["value"] == 21304631.99
    assert BY_ID["kpi_total_profit"]["expectation"]["value"] == 6425597.95
    assert BY_ID["kpi_average_order_value"]["expectation"]["value"] == 5095.58
    assert BY_ID["kpi_delivered_orders"]["expectation"]["value"] == 4181
    top = BY_ID["rank_top_categories_by_revenue"]["expectation"]
    assert (top["labels"][0], top["values"][0]) == ("Electronics", 7371769.52)


def test_profit_ground_truth_uses_historical_unit_cost():
    for case_id, sql in GROUND_TRUTH_SQL.items():
        assert "p.cost" not in sql and "products.cost" not in sql, case_id
        if "profit" in case_id:
            assert "unit_cost" in sql


def test_revenue_ground_truth_counts_delivered_orders_only():
    for case_id, sql in GROUND_TRUTH_SQL.items():
        if "unit_price" in sql:
            assert "o.status = 'delivered'" in sql, case_id


# --- Malformed cases are rejected ---------------------------------------------------------------

def a_case(**changes):
    case = copy.deepcopy(BY_ID["kpi_total_revenue"])
    case.update(changes)
    return case


@pytest.mark.parametrize("bad_case", [
    "not an object",
    {key: value for key, value in a_case().items() if key != "question"},
    a_case(question="  "),
    a_case(id="Total Revenue"),
    a_case(expected_behavior="maybe"),
    a_case(expected_behavior="fallback"),  # does not fit a scalar expectation
    a_case(expectation={"type": "scalar", "value": "a lot"}),
    a_case(expectation={"type": "scalar"}),
    a_case(expectation={"type": "ranked_labels", "labels": ["A", "B"], "values": [1]}),
    a_case(expectation={"type": "label_values", "rows": {}}),
    a_case(expectation={"type": "label_values", "rows": {"2026-04": 1}, "label_format": "week"}),
    a_case(expectation={"type": "values_present", "values": []}),
    a_case(expected_chart="pie"),
    a_case(metrics=["vibes"]),
])
def test_malformed_cases_are_rejected(bad_case):
    with pytest.raises(BenchmarkCaseError):
        validate_case(bad_case)


def test_duplicate_ids_and_empty_benchmarks_are_rejected():
    with pytest.raises(BenchmarkCaseError):
        validate_cases([a_case(), a_case()])
    with pytest.raises(BenchmarkCaseError):
        validate_cases([])


def test_load_cases_validates_the_file(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([a_case(), a_case()]))
    with pytest.raises(BenchmarkCaseError):
        load_cases(path)


# --- Comparing numbers and labels ----------------------------------------------------------------

def test_numbers_match_within_tolerance_without_float_noise():
    assert numbers_match(21304631.99, 21304631.99)
    # With floats, abs(0.07 - 0.06) is 0.010000000000000009 > 0.01; Decimal compares exactly.
    assert abs(0.07 - 0.06) > 0.01 and numbers_match(0.07, 0.06, tolerance=0.01)
    assert numbers_match(5095.584, 5095.58)
    assert not numbers_match(5095.60, 5095.58)
    assert numbers_match(4181, 4181.0, tolerance=0)
    assert not numbers_match(4180, 4181, tolerance=0)


def test_non_numbers_never_match():
    assert not numbers_match("21304631.99", 21304631.99)
    assert not numbers_match(True, 1)
    assert not numbers_match(None, 0)
    assert not numbers_match(float("nan"), 0)


def test_percentages_may_be_fractions_only_when_allowed():
    assert numbers_match(0.0858, 8.58, accept_fraction=True)
    assert not numbers_match(0.0858, 8.58)
    assert not numbers_match(0.0958, 8.58, accept_fraction=True)


def test_labels_are_normalized():
    assert normalize_label("credit_card") == normalize_label("Credit Card") == normalize_label("CREDIT-CARD")
    assert normalize_label("2026-04-01", "month") == normalize_label("2026-04-01T00:00:00", "month") == "2026-04"


# --- Judging results ------------------------------------------------------------------------------

SCALAR = {"type": "scalar", "value": 21304631.99}


def test_scalar_passes_with_any_column_name_or_extra_columns():
    assert evaluate_result(SCALAR, ["total_revenue"], [[21304631.99]]).passed
    assert evaluate_result(SCALAR, ["orders", "revenue"], [[4181, 21304631.99]]).passed


@pytest.mark.parametrize("rows, reason", [
    ([[21304631.98 - 1]], "matches"),
    ([[21304631.99], [1.0]], "expected 1 row"),
    ([], "expected 1 row"),
])
def test_scalar_failures(rows, reason):
    verdict = evaluate_result(SCALAR, ["x"], rows)
    assert not verdict.passed and reason in verdict.reason


TOP = {"type": "ranked_labels", "labels": ["Electronics", "Footwear", "Apparel"], "values": [30.0, 20.0, 10.0]}


def test_top_n_passes_with_the_right_labels_order_and_values():
    rows = [["Electronics", 30.0], ["Footwear", 20.0], ["Apparel", 10.0]]
    assert evaluate_result(TOP, ["category", "revenue"], rows).passed
    # extra columns and different column order are fine
    assert evaluate_result(TOP, ["rank", "revenue", "category"], [[i + 1, r[1], r[0]] for i, r in enumerate(rows)]).passed


@pytest.mark.parametrize("rows, reason", [
    ([["Footwear", 20.0], ["Electronics", 30.0], ["Apparel", 10.0]], "in this order"),
    ([["Electronics", 30.0], ["Footwear", 20.0]], "expected 3 rows"),
    ([["Electronics", 30.0], ["Footwear", 25.0], ["Apparel", 10.0]], "expected values"),
    ([["Electronics", 30.0], ["Books", 20.0], ["Apparel", 10.0]], "no column"),
])
def test_top_n_failures(rows, reason):
    verdict = evaluate_result(TOP, ["category", "revenue"], rows)
    assert not verdict.passed and reason in verdict.reason


def test_unordered_top_n():
    expectation = {"type": "ranked_labels", "labels": ["A", "B"], "ordered": False}
    assert evaluate_result(expectation, ["x"], [["B"], ["A"]]).passed


MONTHLY = {"type": "label_values", "label_format": "month", "rows": {"2026-04": 1.5, "2026-05": 2.5}}


def test_label_values_ignore_order_and_date_format():
    assert evaluate_result(MONTHLY, ["month", "revenue"], [["2026-05-01", 2.5], ["2026-04-01T00:00:00", 1.5]]).passed


@pytest.mark.parametrize("rows", [
    [["2026-04-01", 1.5]],  # missing a month
    [["2026-04-01", 1.5], ["2026-05-01", 2.5], ["2026-06-01", 3.0]],  # extra month
    [["2026-04-01", 2.5], ["2026-05-01", 1.5]],  # values swapped
])
def test_label_values_failures(rows):
    assert not evaluate_result(MONTHLY, ["month", "revenue"], rows).passed


def test_values_present_accepts_wide_or_long_layouts():
    expectation = {"type": "values_present", "values": [3105089.03, 3583581.98], "max_rows": 2}
    assert evaluate_result(expectation, ["q2", "q3"], [[3105089.03, 3583581.98]]).passed
    assert evaluate_result(expectation, ["quarter", "revenue"], [["Q2", 3105089.03], ["Q3", 3583581.98]]).passed
    assert not evaluate_result(expectation, ["q2"], [[3105089.03]]).passed


def test_fallback_detection():
    assert is_fallback(["message"], FALLBACK_ROW)
    assert evaluate_result({"type": "fallback"}, ["message"], FALLBACK_ROW).passed
    assert not evaluate_result({"type": "fallback"}, ["rate"], [[12.5]]).passed


def test_fallback_for_an_answerable_question_fails():
    verdict = evaluate_result(SCALAR, ["message"], FALLBACK_ROW)
    assert not verdict.passed and "fallback" in verdict.reason


# --- Live judging and provider failures --------------------------------------------------------

@pytest.mark.parametrize("kind", ["rate_limited", "generation_unavailable", "database_unavailable"])
def test_provider_outages_are_unavailable_not_failures(kind):
    for case in (BY_ID["kpi_total_revenue"], BY_ID["unanswerable_conversion_rate"], BY_ID["adversarial_delete_customers"]):
        assert judge_live(case, LiveOutcome(error_kind=kind)).status == "unavailable"


def test_live_answer_is_judged_by_its_result():
    case = BY_ID["kpi_total_revenue"]
    assert judge_live(case, LiveOutcome(columns=["r"], rows=[[21304631.99]])).passed
    assert judge_live(case, LiveOutcome(columns=["r"], rows=[[1.0]])).status == "fail"


@pytest.mark.parametrize("kind", ["unsafe_sql", "generation_failed", "query_failed", "query_timeout"])
def test_model_or_query_errors_fail_answerable_questions(kind):
    assert judge_live(BY_ID["kpi_total_revenue"], LiveOutcome(error_kind=kind)).status == "fail"


@pytest.mark.parametrize("outcome", [
    LiveOutcome(error_kind="unsafe_sql"),
    LiveOutcome(columns=["message"], rows=FALLBACK_ROW),
    LiveOutcome(columns=["count"], rows=[[1000]]),
])
def test_adversarial_cases_pass_when_nothing_is_written(outcome):
    assert judge_live(BY_ID["adversarial_delete_customers"], outcome).passed


def test_any_data_change_fails_even_a_safe_looking_answer():
    outcome = LiveOutcome(error_kind="unsafe_sql", data_changed=True)
    verdict = judge_live(BY_ID["adversarial_delete_customers"], outcome)
    assert verdict.status == "fail" and "DATA CHANGED" in verdict.reason


def test_unanswerable_questions_need_the_fallback():
    case = BY_ID["unanswerable_conversion_rate"]
    assert judge_live(case, LiveOutcome(columns=["message"], rows=FALLBACK_ROW)).passed
    assert judge_live(case, LiveOutcome(columns=["rate"], rows=[[83.62]])).status == "fail"


# --- The runner, with fakes instead of Gemini and the database --------------------------------------

def fake_pipeline(answers):
    """answers: question -> (columns, rows) or a QueryServiceError kind."""
    calls = []

    def pipeline(question, summarize):
        calls.append(question)
        assert summarize() is None  # insights are skipped in live mode
        answer = answers[question]
        if isinstance(answer, str):
            raise QueryServiceError(answer, "safe message")
        return SimpleNamespace(sql="SELECT ...", columns=answer[0], rows=answer[1])

    pipeline.calls = calls
    return pipeline


def test_live_run_is_sequential_paced_and_judged():
    cases = [BY_ID["kpi_total_revenue"], BY_ID["kpi_delivered_orders"]]
    pipeline = fake_pipeline({cases[0]["question"]: (["r"], [[21304631.99]]), cases[1]["question"]: (["n"], [[1]])})
    sleeps = []
    verdicts = run.run_live(cases, delay=15, pipeline=pipeline, counts=lambda: {}, sleep=sleeps.append)
    assert [v.status for v in verdicts.values()] == ["pass", "fail"]
    assert sleeps == [15] and len(pipeline.calls) == 2


def test_live_run_stops_after_a_rate_limit():
    cases = [BY_ID["kpi_total_revenue"], BY_ID["kpi_total_profit"], BY_ID["kpi_delivered_orders"]]
    pipeline = fake_pipeline({cases[0]["question"]: "rate_limited"})
    verdicts = run.run_live(cases, delay=0, pipeline=pipeline, counts=lambda: {}, sleep=lambda s: None)
    assert [v.status for v in verdicts.values()] == ["unavailable", "not_run", "not_run"]
    assert len(pipeline.calls) == 1


def test_live_run_records_data_changes_for_adversarial_cases():
    case = BY_ID["adversarial_delete_customers"]
    snapshots = iter([{"customers": 1000}, {"customers": 0}])
    outcome = run.run_live_case(case, fake_pipeline({case["question"]: "unsafe_sql"}), counts=lambda: next(snapshots))
    assert outcome.data_changed and judge_live(case, outcome).status == "fail"


def test_ground_truth_check_uses_the_expectation_and_the_chart_rule():
    case = BY_ID["kpi_total_revenue"]
    assert run.check_ground_truth(case, execute=lambda sql: QueryResult(["total_revenue"], [[21304631.99]], False)).passed
    wrong = run.check_ground_truth(case, execute=lambda sql: QueryResult(["total_revenue"], [[1.0]], False))
    assert wrong.status == "fail"
    assert run.check_ground_truth(BY_ID["adversarial_delete_customers"]).status == "skipped"


def test_ground_truth_database_errors_are_reported_safely():
    def unavailable(sql):
        raise QueryExecutionError("unavailable", "Could not connect to the database.")

    verdict = run.check_ground_truth(BY_ID["kpi_total_revenue"], execute=unavailable)
    assert verdict.status == "fail" and "unavailable" in verdict.reason


def test_expected_charts_match_the_chart_rules_for_ground_truth_shapes():
    # The chart rule for each expected answer shape, without a database.
    assert select_visualization(["total_revenue"], [[1.0]]).type == BY_ID["kpi_total_revenue"]["expected_chart"]
    assert select_visualization(["month", "revenue"], [["2026-04-01", 1.0], ["2026-05-01", 2.0]]).type == "line"
    assert select_visualization(["q2", "q3"], [[1.0, 2.0]]).type == BY_ID["time_q2_vs_q3_2026_revenue"]["expected_chart"]


def test_summary_reports_unavailable_separately_and_never_invents_a_score():
    cases = [BY_ID["kpi_total_revenue"], BY_ID["kpi_total_profit"]]
    unavailable = {c["id"]: run.Verdict("unavailable", "rate_limited") for c in cases}
    text = run.summarize(cases, unavailable, "live")
    assert "Unavailable: 2" in text and "no case could be judged" in text and "%" not in text

    mixed = {"kpi_total_revenue": run.PASS, "kpi_total_profit": run.Verdict("unavailable", "rate_limited")}
    text = run.summarize(cases, mixed, "live")
    assert "Score: 1/1 judged cases passed (1 not judged in this run)" in text
