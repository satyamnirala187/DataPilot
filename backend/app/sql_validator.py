"""SQL safety validator.

AI-generated SQL is untrusted input. validate_sql() parses it with SQLGlot and checks the syntax
tree (not the raw text), then either returns safe SQL or raises UnsafeSQLError. It has no
database, network or LLM access, so it can be tested on its own.
"""

import sqlglot
from sqlglot import exp

MAX_LIMIT = 500

# Rule 3: the only tables a query may read. Unqualified or public.<table> only.
ALLOWED_TABLES = frozenset({"customers", "categories", "products", "orders", "order_items", "payments"})
ALLOWED_SCHEMAS = frozenset({"", "public"})

# Rule 2: anything that writes, changes the schema, locks rows or is not a plain read.
# SELECT ... INTO creates a table and FOR UPDATE/SHARE locks rows, so both are blocked too.
FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge,
    exp.Create, exp.Drop, exp.Alter, exp.TruncateTable,
    exp.Grant, exp.Revoke, exp.Copy,
    exp.Into, exp.Lock,
    exp.Set, exp.Transaction, exp.Commit, exp.Rollback,
    exp.Analyze, exp.LoadData, exp.Use, exp.Pragma,
    exp.Command,  # statements SQLGlot cannot fully parse (EXPLAIN, VACUUM, SHOW, ...)
)

# Rule 4: functions that sleep, read files or the server's state, change settings,
# run SQL from a string, or reach outside the database.
BLOCKED_FUNCTIONS = frozenset({
    "pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "lo_import", "lo_export", "dblink", "dblink_exec", "dblink_connect",
    "query_to_xml", "query_to_xml_and_xmlschema", "table_to_xml", "cursor_to_xml",
    "set_config", "current_setting", "nextval", "setval", "currval",
})
BLOCKED_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink")

# SQLGlot has a class for almost every common SQL function. Functions it does not recognise
# are rejected (fail closed) unless they are listed here as known-safe PostgreSQL functions.
SAFE_UNRECOGNISED_FUNCTIONS = frozenset({"age", "make_date", "every"})

# The argument key SQLGlot uses for a query's WITH clause ("with_" in recent versions).
WITH_KEY = "with_" if "with_" in exp.Select.arg_types else "with"


class UnsafeSQLError(ValueError):
    """The SQL was rejected. The message names the rule that failed (for logs, not end users)."""


def validate_sql(sql: str, max_limit: int = MAX_LIMIT) -> str:
    """Return a safe, normalised version of sql, or raise UnsafeSQLError."""
    statement = _parse_single_statement(sql)  # rules 6 and 1
    _check_read_only(statement)  # rule 2
    _check_tables(statement)  # rule 3
    _check_functions(statement)  # rule 4
    _enforce_limit(statement, max_limit)  # rule 5
    return statement.sql(dialect="postgres", comments=False)


def _parse_single_statement(sql: str) -> exp.Expression:
    if not sql or not sql.strip():
        raise UnsafeSQLError("Empty SQL.")
    try:
        statements = sqlglot.parse(sql, read="postgres")
        # Ignore empty statements (";;") and comment-only ones ("SELECT 1; -- done").
        statements = [s for s in statements if s is not None and not isinstance(s, exp.Semicolon)]
    except Exception as error:  # rule 6: anything the parser cannot handle is rejected
        raise UnsafeSQLError("SQL could not be parsed.") from error
    if len(statements) != 1:
        raise UnsafeSQLError(f"Expected exactly one SQL statement, found {len(statements)}.")
    return statements[0]


def _check_read_only(statement: exp.Expression) -> None:
    # The statement itself must be a SELECT (a WITH ... SELECT is a Select with a WITH clause),
    # or SELECTs combined with UNION / INTERSECT / EXCEPT.
    if not isinstance(statement, (exp.Select, exp.SetOperation)):
        raise UnsafeSQLError(f"Only SELECT queries are allowed, got {statement.key.upper()}.")
    # Nothing anywhere inside it (CTEs, subqueries) may write or lock.
    for node in statement.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeSQLError(f"{node.key.upper()} is not allowed in a read-only query.")


def _identifier_name(identifier: exp.Expression | None) -> str:
    """Name as PostgreSQL resolves it: unquoted names are lowercased, quoted names keep their case."""
    if identifier is None:
        return ""
    return identifier.name if identifier.args.get("quoted") else identifier.name.lower()


def _check_tables(statement: exp.Expression) -> None:
    # Everything in FROM / JOIN must be a table or a subquery (optionally LATERAL). This rejects
    # row-producing functions such as unnest(...), LATERAL generate_series(...) and VALUES lists.
    for clause in [*statement.find_all(exp.From), *statement.find_all(exp.Join)]:
        source = clause.this
        if isinstance(source, exp.Lateral):
            source = source.this
        if not isinstance(source, (exp.Table, exp.Subquery)):
            raise UnsafeSQLError(f"Only tables and subqueries are allowed in FROM, got {source.key.upper()}.")

    for table in statement.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise UnsafeSQLError("Functions are not allowed as a table source.")
        name = _identifier_name(table.this)
        schema = _identifier_name(table.args.get("db"))
        if table.catalog or schema not in ALLOWED_SCHEMAS:
            raise UnsafeSQLError(f"Schema '{table.catalog or table.db}' is not allowed.")
        if name in ALLOWED_TABLES:
            continue
        if not schema and name in _visible_cte_names(table):
            continue
        raise UnsafeSQLError(f"Table '{table.name}' is not allowed.")


def _visible_cte_names(table: exp.Table) -> set[str]:
    """CTE names that a table reference can actually resolve to, following PostgreSQL scoping.

    A CTE is visible in the main query of its WITH clause and in the CTEs defined after it
    (and inside itself only for WITH RECURSIVE). Anything else with that name is a real table.
    """
    names: set[str] = set()
    child, node = table, table.parent
    while node is not None:
        if isinstance(node, exp.With):
            ctes = node.expressions
            position = next(i for i, cte in enumerate(ctes) if cte is child)
            visible = ctes[: position + 1] if node.args.get("recursive") else ctes[:position]
            names.update(_cte_name(cte) for cte in visible)
        else:
            with_clause = node.args.get(WITH_KEY)
            if with_clause is not None and with_clause is not child:
                names.update(_cte_name(cte) for cte in with_clause.expressions)
        child, node = node, node.parent
    return names


def _cte_name(cte: exp.CTE) -> str:
    return _identifier_name(cte.args["alias"].this)


def _check_functions(statement: exp.Expression) -> None:
    for function in statement.find_all(exp.Func):
        unrecognised = isinstance(function, exp.Anonymous)
        name = (function.name if unrecognised else function.sql_name()).lower()
        if name in BLOCKED_FUNCTIONS or name.startswith(BLOCKED_FUNCTION_PREFIXES):
            raise UnsafeSQLError(f"Function '{name}' is not allowed.")
        if unrecognised and name not in SAFE_UNRECOGNISED_FUNCTIONS:
            raise UnsafeSQLError(f"Function '{name}' is not recognised as safe.")


def _enforce_limit(statement: exp.Expression, max_limit: int) -> None:
    limit = statement.args.get("limit")
    if limit is None:  # no LIMIT (LIMIT ALL also parses as none)
        statement.set("limit", exp.Limit(expression=exp.Literal.number(max_limit)))
        return
    if not isinstance(limit, exp.Limit):
        raise UnsafeSQLError("Use LIMIT to restrict rows (FETCH is not supported).")
    value = limit.expression
    if not (isinstance(value, exp.Literal) and value.is_int):
        raise UnsafeSQLError("LIMIT must be a whole number.")
    if int(value.name) > max_limit:
        statement.set("limit", exp.Limit(expression=exp.Literal.number(max_limit)))
