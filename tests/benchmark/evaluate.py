"""Load benchmark cases and judge query results against them. Pure Python: no database or Gemini.

Correctness is judged by the RESULT, never by comparing SQL text: many different queries are
equally correct, and column names are up to the model. Expectation types:

  scalar          one row containing a number equal to "value" (within "tolerance")
  values_present  every number in "values" appears somewhere in at most "max_rows" rows
  ranked_labels   exactly len("labels") rows; some column holds these labels (in order unless
                  "ordered" is false); if "values" is given, some numeric column matches them row by row
  label_values    exactly len("rows") rows; each label maps to its number ("label_format": text or month)
  fallback        the app's safe "cannot be answered" message
  safe_refusal    judged by behaviour (no data changed), not by a result

"accept_fraction": true also accepts a percentage written as a fraction (8.58% as 0.0858).
"""

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

CASES_PATH = Path(__file__).with_name("cases.json")

# The answer the NL-to-SQL prompt asks for when a question cannot be answered from the schema.
FALLBACK_MARKER = "cannot be answered"

REQUIRED_FIELDS = ("id", "question", "category", "expected_behavior", "expectation", "notes")
CHART_TYPES = {"kpi", "bar", "line", "table"}
METRICS = {"revenue", "profit", "aov", "completed_order", "successful_payment"}
# expected_behavior -> the expectation types that fit it
BEHAVIOR_EXPECTATIONS = {
    "answer": {"scalar", "values_present", "ranked_labels", "label_values"},
    "fallback": {"fallback"},
    "refuse": {"safe_refusal"},
}
DEFAULT_TOLERANCE = Decimal("0.01")
DEFAULT_MAX_ROWS = 10

# Pipeline error kinds (app.query_service) that mean an outside service was unavailable.
# They say nothing about whether DataPilot's answer would have been right.
UNAVAILABLE_KINDS = {"rate_limited", "generation_unavailable", "database_unavailable"}
REJECTED_KINDS = {"unsafe_sql", "query_not_allowed"}


class BenchmarkCaseError(ValueError):
    """A benchmark case is malformed."""


@dataclass
class Verdict:
    status: str  # pass, fail or unavailable
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "pass"


PASS = Verdict("pass")


def fail(reason: str) -> Verdict:
    return Verdict("fail", reason)


# --- Loading and checking cases ---------------------------------------------------------------

def load_cases(path: Path = CASES_PATH) -> list[dict]:
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_cases(cases)
    return cases


def validate_cases(cases: Any) -> None:
    if not isinstance(cases, list) or not cases:
        raise BenchmarkCaseError("The benchmark must be a non-empty list of cases.")
    seen = set()
    for case in cases:
        validate_case(case)
        if case["id"] in seen:
            raise BenchmarkCaseError(f"Duplicate case id: {case['id']}")
        seen.add(case["id"])


def validate_case(case: Any) -> None:
    if not isinstance(case, dict):
        raise BenchmarkCaseError("Each case must be an object.")
    missing = [field for field in REQUIRED_FIELDS if field not in case]
    label = case.get("id", "<no id>")
    if missing:
        raise BenchmarkCaseError(f"{label}: missing fields {missing}")
    for field in ("id", "question", "category", "notes"):
        if not isinstance(case[field], str) or not case[field].strip():
            raise BenchmarkCaseError(f"{label}: {field} must be a non-empty string")
    if not re.fullmatch(r"[a-z0-9_]+", case["id"]):
        raise BenchmarkCaseError(f"{label}: id must be snake_case")

    behavior, expectation = case["expected_behavior"], case["expectation"]
    if behavior not in BEHAVIOR_EXPECTATIONS:
        raise BenchmarkCaseError(f"{label}: unknown expected_behavior {behavior!r}")
    if not isinstance(expectation, dict) or expectation.get("type") not in BEHAVIOR_EXPECTATIONS[behavior]:
        raise BenchmarkCaseError(f"{label}: expectation type does not fit expected_behavior {behavior!r}")
    _validate_expectation(label, expectation)

    if "expected_chart" in case and case["expected_chart"] not in CHART_TYPES:
        raise BenchmarkCaseError(f"{label}: expected_chart must be one of {sorted(CHART_TYPES)}")
    if not set(case.get("metrics", [])) <= METRICS:
        raise BenchmarkCaseError(f"{label}: unknown metric in {case['metrics']}")


def _validate_expectation(label: str, expectation: dict) -> None:
    kind = expectation["type"]
    if kind == "scalar":
        _require_number(label, expectation.get("value"))
    elif kind == "values_present":
        _require_list(label, expectation.get("values"), _require_number)
    elif kind == "ranked_labels":
        _require_list(label, expectation.get("labels"), _require_label)
        if "values" in expectation:
            _require_list(label, expectation["values"], _require_number)
            if len(expectation["values"]) != len(expectation["labels"]):
                raise BenchmarkCaseError(f"{label}: labels and values must have the same length")
    elif kind == "label_values":
        rows = expectation.get("rows")
        if not isinstance(rows, dict) or not rows:
            raise BenchmarkCaseError(f"{label}: rows must be a non-empty object")
        for value in rows.values():
            _require_number(label, value)
        if expectation.get("label_format", "text") not in ("text", "month"):
            raise BenchmarkCaseError(f"{label}: label_format must be text or month")
    if "tolerance" in expectation:
        _require_number(label, expectation["tolerance"])


def _require_number(label: str, value: Any) -> None:
    if to_decimal(value) is None:
        raise BenchmarkCaseError(f"{label}: expected a number, got {value!r}")


def _require_label(label: str, value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise BenchmarkCaseError(f"{label}: labels must be non-empty strings")


def _require_list(label: str, values: Any, check) -> None:
    if not isinstance(values, list) or not values:
        raise BenchmarkCaseError(f"{label}: expected a non-empty list")
    for value in values:
        check(label, value)


# --- Comparing values ------------------------------------------------------------------------------

def to_decimal(value: Any) -> Decimal | None:
    """Numbers only (bools and strings are not numbers here). Decimal avoids float rounding noise."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def numbers_match(actual: Any, expected: Any, tolerance: Any = DEFAULT_TOLERANCE, accept_fraction: bool = False) -> bool:
    actual_number, expected_number = to_decimal(actual), to_decimal(expected)
    if actual_number is None or expected_number is None:
        return False
    tolerance = Decimal(str(tolerance))
    if abs(actual_number - expected_number) <= tolerance:
        return True
    # A percentage may also be returned as a fraction: 8.58 -> 0.0858 (tolerance scales too).
    return accept_fraction and abs(actual_number - expected_number / 100) <= tolerance / 100


def normalize_label(value: Any, label_format: str = "text") -> str:
    text = str(value)
    if label_format == "month":
        match = re.search(r"(\d{4})-(\d{2})", text)  # 2026-04-01, 2026-04, 2026-04-01T00:00:00
        return f"{match.group(1)}-{match.group(2)}" if match else text
    # credit_card, Credit Card and CREDIT-CARD all compare equal.
    return " ".join(text.replace("_", " ").replace("-", " ").split()).casefold()


def is_fallback(columns: list[str], rows: list[list[Any]]) -> bool:
    return (len(rows) == 1 and len(rows[0]) == 1 and isinstance(rows[0][0], str)
            and FALLBACK_MARKER in rows[0][0].casefold())


# --- Judging a result -----------------------------------------------------------------------------

def evaluate_result(expectation: dict, columns: list[str], rows: list[list[Any]]) -> Verdict:
    """Does this query result answer the question correctly?"""
    kind = expectation["type"]
    tolerance = expectation.get("tolerance", DEFAULT_TOLERANCE)
    fraction = expectation.get("accept_fraction", False)

    if kind == "fallback":
        return PASS if is_fallback(columns, rows) else fail("expected the safe 'cannot be answered' fallback")

    if is_fallback(columns, rows):
        return fail("returned the 'cannot be answered' fallback for an answerable question")

    if kind == "scalar":
        if len(rows) != 1:
            return fail(f"expected 1 row, got {len(rows)}")
        if any(numbers_match(cell, expectation["value"], tolerance, fraction) for cell in rows[0]):
            return PASS
        return fail(f"no value in {rows[0]} matches {expectation['value']}")

    if kind == "values_present":
        max_rows = expectation.get("max_rows", DEFAULT_MAX_ROWS)
        if len(rows) > max_rows:
            return fail(f"expected at most {max_rows} rows, got {len(rows)}")
        cells = [cell for row in rows for cell in row]
        missing = [v for v in expectation["values"] if not any(numbers_match(c, v, tolerance, fraction) for c in cells)]
        return fail(f"missing values {missing}") if missing else PASS

    if kind == "ranked_labels":
        return _evaluate_ranked_labels(expectation, rows, tolerance, fraction)

    if kind == "label_values":
        return _evaluate_label_values(expectation, rows, tolerance, fraction)

    return fail(f"cannot judge expectation type {kind!r} from a result")


def _columns(rows: list[list[Any]]) -> list[list[Any]]:
    return [list(column) for column in zip(*rows)] if rows else []


def _evaluate_ranked_labels(expectation, rows, tolerance, fraction) -> Verdict:
    labels = [normalize_label(label) for label in expectation["labels"]]
    if len(rows) != len(labels):
        return fail(f"expected {len(labels)} rows, got {len(rows)}")
    ordered = expectation.get("ordered", True)

    def labels_fit(column):
        found = [normalize_label(value) for value in column]
        return found == labels if ordered else sorted(found) == sorted(labels)

    label_columns = [i for i, column in enumerate(_columns(rows)) if labels_fit(column)]
    if not label_columns:
        return fail(f"no column lists {expectation['labels']}" + (" in this order" if ordered else ""))
    if "values" not in expectation:
        return PASS

    # Values are checked row by row against the expected label order.
    label_index = label_columns[0]
    expected_by_label = dict(zip(labels, expectation["values"]))
    for column in _columns(rows):
        if all(numbers_match(value, expected_by_label[normalize_label(row[label_index])], tolerance, fraction)
               for value, row in zip(column, rows)):
            return PASS
    return fail("labels are right but no column has the expected values")


def _evaluate_label_values(expectation, rows, tolerance, fraction) -> Verdict:
    label_format = expectation.get("label_format", "text")
    expected = {normalize_label(label, label_format): value for label, value in expectation["rows"].items()}
    if len(rows) != len(expected):
        return fail(f"expected {len(expected)} rows, got {len(rows)}")

    for label_index, column in enumerate(_columns(rows)):
        found = [normalize_label(value, label_format) for value in column]
        if sorted(found) != sorted(expected):
            continue
        for value_index in range(len(rows[0])):
            if value_index != label_index and all(
                numbers_match(row[value_index], expected[label], tolerance, fraction)
                for row, label in zip(rows, found)
            ):
                return PASS
        return fail("labels are right but no column has the expected values")
    return fail(f"no column has exactly the labels {sorted(expectation['rows'])}")


# --- Judging a live run through the app ------------------------------------------------------------

@dataclass
class LiveOutcome:
    """What the real pipeline did with one question."""

    error_kind: str | None = None  # QueryServiceError kind, or None on success
    sql: str | None = None
    columns: list[str] | None = None
    rows: list[list[Any]] | None = None
    data_changed: bool = False


def judge_live(case: dict, outcome: LiveOutcome) -> Verdict:
    if outcome.data_changed:
        return fail("DATA CHANGED: a write reached the database")
    if outcome.error_kind in UNAVAILABLE_KINDS:
        # Quota or outage: not a semantic failure, and not a pass either.
        return Verdict("unavailable", outcome.error_kind)

    behavior = case["expected_behavior"]
    if behavior == "refuse":
        # Rejected, a fallback or a harmless read are all safe; only a write would be a failure.
        return PASS
    if outcome.error_kind in REJECTED_KINDS:
        return fail(f"the validator rejected the generated SQL ({outcome.error_kind})")
    if outcome.error_kind is not None:
        return fail(f"pipeline error: {outcome.error_kind}")
    return evaluate_result(case["expectation"], outcome.columns or [], outcome.rows or [])
