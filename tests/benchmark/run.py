"""Run the DataPilot benchmark.

  python -m tests.benchmark.run            ground-truth mode: runs the trusted SQL against the
                                           database (read-only role) and checks cases.json. No Gemini.
  python -m tests.benchmark.run --live     live mode: sends each question through the real app
                                           pipeline (Gemini -> validator -> read-only executor).
                                           Uses Gemini quota; never runs unless asked.

Live mode options: --only id1,id2  --delay SECONDS (default 15)  --output results.json
"""

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.chart_selector import select_visualization  # noqa: E402
from app.db_executor import QueryExecutionError, execute_query  # noqa: E402
from app.query_service import QueryServiceError, run_business_query  # noqa: E402
from app.sql_validator import UnsafeSQLError, validate_sql  # noqa: E402

from .evaluate import PASS, LiveOutcome, Verdict, evaluate_result, fail, judge_live, load_cases  # noqa: E402
from .ground_truth import GROUND_TRUTH_SQL  # noqa: E402

TABLES = ("customers", "categories", "products", "orders", "order_items", "payments")
DEFAULT_DELAY_SECONDS = 15


# --- Ground-truth mode --------------------------------------------------------------------------

def check_ground_truth(case: dict, execute=execute_query) -> Verdict:
    """Run the trusted SQL for a case and check its stored expectation (and chart) still hold."""
    sql = GROUND_TRUTH_SQL.get(case["id"])
    if case["expected_behavior"] != "answer":
        return Verdict("skipped", "judged by behaviour in live mode only")
    if sql is None:
        return fail("no ground-truth SQL")
    try:
        validate_sql(sql)  # the trusted SQL must also pass the app's own safety rules
        result = execute(sql)
    except UnsafeSQLError as error:
        return fail(f"ground-truth SQL rejected by the validator: {error}")
    except QueryExecutionError as error:
        return fail(f"ground-truth SQL failed ({error.kind})")

    verdict = evaluate_result(case["expectation"], result.columns, result.rows)
    if not verdict.passed:
        return verdict
    chart = select_visualization(result.columns, result.rows).type
    if case.get("expected_chart") and chart != case["expected_chart"]:
        return fail(f"chart rule gave {chart}, expected {case['expected_chart']}")
    return PASS


# --- Live mode -------------------------------------------------------------------------------------

def count_rows(execute=execute_query) -> dict[str, int]:
    sql = "SELECT " + ", ".join(f"(SELECT COUNT(*) FROM {table}) AS {table}" for table in TABLES)
    result = execute(sql)
    return dict(zip(result.columns, result.rows[0]))


def run_live_case(case: dict, pipeline=run_business_query, counts=count_rows) -> LiveOutcome:
    """Send one question through the real pipeline. Insights are skipped to save Gemini quota."""
    watch_data = case["expected_behavior"] == "refuse"
    before = counts() if watch_data else None
    try:
        response = pipeline(case["question"], summarize=lambda *args: None)
        outcome = LiveOutcome(sql=response.sql, columns=response.columns, rows=response.rows)
    except QueryServiceError as error:
        outcome = LiveOutcome(error_kind=error.kind)
    if watch_data:
        outcome.data_changed = counts() != before
    return outcome


def run_live(cases: list[dict], delay: float, pipeline=run_business_query, counts=count_rows,
             sleep=time.sleep) -> dict[str, Verdict]:
    """Questions run one at a time with a pause between them. A 429 stops the run: the quota is
    spent, so later questions would only fail for the same reason."""
    verdicts: dict[str, Verdict] = {}
    for index, case in enumerate(cases):
        if index:
            sleep(delay)
        outcome = run_live_case(case, pipeline, counts)
        verdicts[case["id"]] = judge_live(case, outcome)
        print(f"  {case['id']:<40} {verdicts[case['id']].status:<12} {verdicts[case['id']].reason}", flush=True)
        if outcome.error_kind == "rate_limited":
            for remaining in cases[index + 1:]:
                verdicts[remaining["id"]] = Verdict("not_run", "stopped after Gemini rate limit (429)")
            break
    return verdicts


# --- Report ---------------------------------------------------------------------------------------

def summarize(cases: list[dict], verdicts: dict[str, Verdict], mode: str) -> str:
    statuses = Counter(verdict.status for verdict in verdicts.values())
    judged = statuses["pass"] + statuses["fail"]
    lines = [f"DataPilot benchmark ({mode})", f"Total cases: {len(cases)}"]
    for status in ("pass", "fail", "unavailable", "not_run", "skipped"):
        if statuses[status]:
            lines.append(f"  {status.replace('_', ' ').capitalize()}: {statuses[status]}")
    if judged:
        lines.append(f"Score: {statuses['pass']}/{judged} judged cases passed"
                     + (f" ({len(cases) - judged} not judged in this run)" if judged < len(cases) else ""))
    else:
        lines.append("Score: none - no case could be judged")

    by_category = defaultdict(Counter)
    for case in cases:
        by_category[case["category"]][verdicts[case["id"]].status] += 1
    lines.append("By category (passed/judged):")
    for category, counts in by_category.items():
        extra = ", ".join(f"{n} {s.replace('_', ' ')}" for s, n in counts.items() if s not in ("pass", "fail"))
        lines.append(f"  {category:<18} {counts['pass']}/{counts['pass'] + counts['fail']}" + (f"  ({extra})" if extra else ""))

    failures = [(case["id"], verdicts[case["id"]].reason) for case in cases if verdicts[case["id"]].status == "fail"]
    if failures:
        lines.append("Failures:")
        lines.extend(f"  {case_id}: {reason}" for case_id, reason in failures)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the DataPilot benchmark.")
    parser.add_argument("--live", action="store_true", help="send questions through Gemini (uses quota)")
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="seconds between live questions")
    parser.add_argument("--output", type=Path, help="write per-case results to this JSON file")
    args = parser.parse_args(argv)

    cases = load_cases()
    if args.only:
        wanted = set(args.only.split(","))
        unknown = wanted - {case["id"] for case in cases}
        if unknown:
            parser.error(f"unknown case ids: {sorted(unknown)}")
        cases = [case for case in cases if case["id"] in wanted]

    if args.live:
        print(f"Live mode: {len(cases)} questions through Gemini, {args.delay:g} s apart. Insights are skipped.")
        verdicts = run_live(cases, args.delay)
        mode = "live, through Gemini"
    else:
        verdicts = {case["id"]: check_ground_truth(case) for case in cases}
        mode = "ground truth against the database, no Gemini"

    print(summarize(cases, verdicts, mode))
    if args.output:
        results = {case_id: {"status": v.status, "reason": v.reason} for case_id, v in verdicts.items()}
        args.output.write_text(json.dumps({"mode": mode, "results": results}, indent=2) + "\n")
    return 1 if any(v.status == "fail" for v in verdicts.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
