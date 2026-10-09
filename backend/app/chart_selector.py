"""Choose how to display a query result, using fixed rules on the result's shape.

The LLM is never involved: the same columns and rows always give the same choice.

  one row, one numeric column              -> kpi
  2+ rows, one text + one numeric column   -> bar
  2+ rows, one date + one numeric column   -> line
  anything else, or anything unclear       -> table

The table is always shown as well; this only decides what is drawn above it.
"""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

# More bars than this are unreadable, so the table is clearer.
MAX_BAR_CATEGORIES = 50

# 2026-04-01, optionally followed by a time such as T10:30:00, 10:30:00.123+05:30 or Z.
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")


class Visualization(BaseModel):
    type: Literal["kpi", "bar", "line", "table"]
    x_key: str | None = None  # category or date column (bar, line)
    y_key: str | None = None  # numeric column (kpi, bar, line)


TABLE = Visualization(type="table")


def select_visualization(columns: list[str], rows: list[list[Any]]) -> Visualization:
    """Return the display for this result. When in doubt, the answer is table."""
    if not rows or not columns or len(set(columns)) != len(columns):
        return TABLE
    if any(len(row) != len(columns) for row in rows):
        return TABLE

    kinds = [_column_kind([row[i] for row in rows]) for i in range(len(columns))]

    if len(rows) == 1 and kinds == ["numeric"] and rows[0][0] is not None:
        return Visualization(type="kpi", y_key=columns[0])

    if len(rows) < 2 or len(columns) != 2 or kinds.count("numeric") != 1:
        return TABLE

    x_index = 1 - kinds.index("numeric")
    x_kind = kinds[x_index]
    if any(row[x_index] is None for row in rows):  # every point needs a label
        return TABLE

    x_key, y_key = columns[x_index], columns[1 - x_index]
    if x_kind == "date":
        return Visualization(type="line", x_key=x_key, y_key=y_key)
    if x_kind == "text" and len(rows) <= MAX_BAR_CATEGORIES:
        return Visualization(type="bar", x_key=x_key, y_key=y_key)
    return TABLE


def _column_kind(values: list[Any]) -> str:
    """numeric, date, text, or unknown, judged from the column's non-null values."""
    present = [value for value in values if value is not None]
    if not present:
        return "unknown"
    if all(_is_number(value) for value in present):
        return "numeric"
    if all(_is_date(value) for value in present):
        return "date"
    if all(isinstance(value, str) for value in present):
        return "text"
    return "unknown"


def _is_number(value: Any) -> bool:
    # bool is a subclass of int, but True/False is not a measure.
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _is_date(value: Any) -> bool:
    if isinstance(value, (date, datetime)):
        return True
    if not isinstance(value, str) or not ISO_DATE.match(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:  # e.g. 2026-13-45
        return False
    return True
