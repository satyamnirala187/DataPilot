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


def test_year_month_strings_are_a_line_chart():
    # Live benchmark: Gemini grouped months with TO_CHAR(order_date, 'YYYY-MM').
    rows = [["2026-04", 784130.39], ["2026-05", 801234.10], ["2026-06", 799999.99]]
    assert select_visualization(["month", "revenue"], rows) == \
        Visualization(type="line", x_key="month", y_key="revenue")


@pytest.mark.parametrize("value", ["2026-13-45", "2026-13", "2026-00", "2026-9", "Sept 2026", "2026/09/01"])
def test_invalid_or_non_iso_dates_are_not_line_charts(value):
    # Not a parseable ISO date, so it is treated as text: a bar chart of labels, never a line.
    assert kind(["period", "revenue"], [[value, 1.0], ["2026-09-01", 2.0]]) != "line"


# --- Identifier columns are ignored when choosing bar or line -----------------------------

def test_product_id_name_and_profit_is_a_bar_chart():
    rows = [[4, "Orvexa Bluetooth Speaker", 560801.88], [45, "Nuvaro Kolhapuri Chappals", 390832.52]]
    assert select_visualization(["product_id", "product_name", "profit"], rows) == \
        Visualization(type="bar", x_key="product_name", y_key="profit")


def test_category_id_name_and_revenue_is_a_bar_chart():
    rows = [[1, "Electronics", 7371769.52], [5, "Home & Kitchen", 2788099.81], [4, "Footwear", 2655074]]
    assert select_visualization(["category_id", "category_name", "revenue"], rows) == \
        Visualization(type="bar", x_key="category_name", y_key="revenue")


def test_id_and_month_and_revenue_is_a_line_chart():
    rows = [[1, "2026-04-01", 100.0], [2, "2026-05-01", 200.0]]
    assert select_visualization(["id", "month", "revenue"], rows) == \
        Visualization(type="line", x_key="month", y_key="revenue")


def test_customer_id_name_email_and_spending_is_still_a_table():
    # Two text columns remain once the id is ignored, so there is no single category.
    rows = [[318, "Girik Menon", "girik@example.com", 370418.49], [687, "Tanish Om", "tanish@example.org", 267573.19]]
    assert kind(["customer_id", "full_name", "email", "total_spending"], rows) == "table"


def test_identifier_columns_stay_in_the_result():
    columns = ["product_id", "product_name", "profit"]
    rows = [[4, "Speaker", 10.0], [45, "Chappals", 5.0]]
    select_visualization(columns, rows)
    assert columns == ["product_id", "product_name", "profit"]
    assert rows == [[4, "Speaker", 10.0], [45, "Chappals", 5.0]]


@pytest.mark.parametrize("columns, rows", [
    (["product_id", "profit"], [[4, 10.0]]),
    (["product_id", "product_name", "profit"], [[4, "Speaker", 10.0]]),
])
def test_one_row_with_an_identifier_is_not_a_new_kpi(columns, rows):
    # Identifiers are ignored only on the 2+ row path, so these stay tables, as before.
    assert kind(columns, rows) == "table"


def test_kpi_rule_is_unchanged_for_a_single_id_named_column():
    # One row, one number was a KPI before this rule and still is, whatever the column is called.
    assert select_visualization(["id"], [[7]]) == Visualization(type="kpi", y_key="id")


def test_numeric_year_is_not_treated_as_an_identifier():
    assert kind(["year", "revenue"], [[2025, 100.0], [2026, 200.0]]) == "table"
    assert kind(["year", "category_name", "revenue"], [[2026, "Electronics", 1.0], [2026, "Books", 2.0]]) == "table"


@pytest.mark.parametrize("column, values", [
    ("product_id", [4.5, 45.0]),  # not integers
    ("product_id", ["P-4", "P-45"]),  # text codes
    ("paid", [4, 45]),  # ends in "id" but is not "id" or "*_id"
])
def test_only_integer_id_or_underscore_id_columns_are_ignored(column, values):
    rows = [[values[0], "Speaker", 10.0], [values[1], "Chappals", 5.0]]
    assert kind([column, "product_name", "profit"], rows) == "table"


def test_identifier_with_nulls_is_still_ignored():
    rows = [[None, "Speaker", 10.0], [45, "Chappals", 5.0]]
    assert kind(["product_id", "product_name", "profit"], rows) == "bar"


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
