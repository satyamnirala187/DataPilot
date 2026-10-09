"""Tests for the deterministic chart selection rules (backend/app/chart_selector.py)."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.chart_selector import MAX_BAR_CATEGORIES, Visualization, select_visualization


def kind(columns, rows):
    return select_visualization(columns, rows).type


# --- KPI: one row, one numeric value ---------------------------------------------------

@pytest.mark.parametrize("column, value", [
    ("total_revenue", 21304631.99),
    ("average_order_value", 5095.58),
    ("customer_count", 1000),
    ("total_profit", Decimal("6789012.34")),
    ("refund_total", 0),
])
def test_single_numeric_aggregate_is_a_kpi(column, value):
    assert select_visualization([column], [[value]]) == Visualization(type="kpi", y_key=column)


def test_single_null_aggregate_is_a_table():
    # e.g. SUM over no rows: there is no value to show.
    assert kind(["total_revenue"], [[None]]) == "table"


@pytest.mark.parametrize("columns, rows", [
    (["message"], [["This question cannot be answered with a read-only query on the store data."]]),
    (["revenue", "profit"], [[100.0, 40.0]]),
    (["category", "revenue"], [["Electronics", 100.0]]),  # one bar is not a chart
    (["is_active"], [[True]]),
])
def test_single_row_that_is_not_one_number_is_a_table(columns, rows):
    assert kind(columns, rows) == "table"


# --- Bar: text category + numeric value -------------------------------------------------

def test_category_and_revenue_is_a_bar_chart():
    rows = [["Electronics", 7371769.52], ["Apparel", 4012345.25], ["Home", 2500000.00]]
    assert select_visualization(["category_name", "revenue"], rows) == \
        Visualization(type="bar", x_key="category_name", y_key="revenue")


def test_city_and_customer_count_is_a_bar_chart():
    rows = [["Mumbai", 120], ["Delhi", 110], ["Pune", 75]]
    assert select_visualization(["city", "customer_count"], rows) == \
        Visualization(type="bar", x_key="city", y_key="customer_count")


def test_product_and_units_sold_with_decimal_values_is_a_bar_chart():
    rows = [["Phone", Decimal("10")], ["Laptop", Decimal("7.5")]]
    assert kind(["product_name", "units_sold"], rows) == "bar"


def test_numeric_column_first_still_finds_the_category():
    rows = [[7371769.52, "Electronics"], [4012345.25, "Apparel"]]
    assert select_visualization(["revenue", "category"], rows) == \
        Visualization(type="bar", x_key="category", y_key="revenue")


def test_null_measure_values_are_allowed_in_a_bar_chart():
    assert kind(["category", "revenue"], [["Electronics", 100.0], ["Toys", None]]) == "bar"


def test_null_category_label_falls_back_to_a_table():
    assert kind(["category", "revenue"], [["Electronics", 100.0], [None, 50.0]]) == "table"


def test_too_many_categories_fall_back_to_a_table():
    rows = [[f"Product {i}", i] for i in range(MAX_BAR_CATEGORIES + 1)]
    assert kind(["product", "units"], rows) == "table"
    assert kind(["product", "units"], rows[:MAX_BAR_CATEGORIES]) == "bar"


# --- Line: date + numeric value ----------------------------------------------------------

def test_month_and_revenue_is_a_line_chart():
    rows = [["2026-04-01", 784130.39], ["2026-05-01", 801234.10], ["2026-06-01", 799999.99]]
    assert select_visualization(["month", "revenue"], rows) == \
        Visualization(type="line", x_key="month", y_key="revenue")


@pytest.mark.parametrize("dates", [
    ["2026-09-01", "2026-09-02"],
    ["2026-09-01T00:00:00", "2026-09-02T00:00:00"],
    ["2026-09-01T00:00:00+05:30", "2026-09-02T00:00:00+05:30"],
    ["2026-09-01 10:30:00", "2026-09-02 10:30:00.123"],
    ["2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z"],
])
def test_iso_date_and_datetime_strings_are_dates(dates):
    assert kind(["order_date", "order_count"], [[d, 5] for d in dates]) == "line"


def test_date_and_datetime_objects_are_dates():
    assert kind(["day", "orders"], [[date(2026, 9, 1), 3], [date(2026, 9, 2), 4]]) == "line"
    assert kind(["ts", "orders"], [[datetime(2026, 9, 1, 10), 3], [datetime(2026, 9, 2, 10), 4]]) == "line"


@pytest.mark.parametrize("value", ["2026-13-45", "2026-09", "Sept 2026", "2026/09/01"])
def test_invalid_or_non_iso_dates_are_not_line_charts(value):
    # Not a parseable ISO date, so it is treated as text: a bar chart of labels, never a line.
    assert kind(["period", "revenue"], [[value, 1.0], ["2026-09-01", 2.0]]) != "line"


# --- Table: everything else ---------------------------------------------------------------

def test_zero_rows_is_a_table():
    assert select_visualization(["category", "revenue"], []) == Visualization(type="table")


def test_multiple_text_columns_is_a_table():
    assert kind(["full_name", "city"], [["A", "Pune"], ["B", "Delhi"]]) == "table"


def test_multiple_numeric_columns_is_a_table():
    assert kind(["revenue", "profit"], [[100.0, 40.0], [200.0, 90.0]]) == "table"
    assert kind(["year", "revenue"], [[2025, 100.0], [2026, 200.0]]) == "table"


def test_multi_dimensional_result_is_a_table():
    rows = [["2026-04-01", "Electronics", 100.0], ["2026-04-01", "Apparel", 50.0]]
    assert kind(["month", "category", "revenue"], rows) == "table"


def test_mixed_types_in_a_column_is_a_table():
    assert kind(["label", "value"], [["A", 1], [2, 3]]) == "table"


def test_all_null_measure_column_is_a_table():
    assert kind(["category", "revenue"], [["A", None], ["B", None]]) == "table"


def test_boolean_values_are_not_numbers():
    assert kind(["category", "is_active"], [["A", True], ["B", False]]) == "table"


def test_duplicate_column_names_are_a_table():
    assert kind(["revenue", "revenue"], [[1.0, 2.0], [3.0, 4.0]]) == "table"


def test_ragged_rows_are_a_table():
    assert kind(["category", "revenue"], [["A", 1.0], ["B"]]) == "table"


def test_integer_and_float_values_mix_in_one_numeric_column():
    assert kind(["category", "revenue"], [["A", 1], ["B", 2.5], ["C", Decimal("3")]]) == "bar"


def test_selection_is_deterministic():
    rows = [["Electronics", 7371769.52], ["Apparel", 4012345.25]]
    assert select_visualization(["category", "revenue"], rows) == select_visualization(["category", "revenue"], rows)
