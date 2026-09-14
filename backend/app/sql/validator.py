from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType

# Node types that modify data, change schema/privileges or control the session/transaction.
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name)
    for name in (
        "Insert",
        "Update",
        "Delete",
        "Merge",
        "Create",
        "Drop",
        "Alter",
        "TruncateTable",
        "Grant",
        "Revoke",
        "Command",
        "Copy",
        "Set",
        "Transaction",
        "Commit",
        "Rollback",
        "Lock",
        "Into",
        "LoadData",
        "Pragma",
        "Use",
        "Kill",
        "Execute",
        "Declare",
        "Refresh",
        "Cache",
        "Uncache",
        "Analyze",
        "Comment",
        "Attach",
        "Detach",
        "Install",
    )
    if hasattr(exp, name)
)

_FORBIDDEN_FUNCTION_PATTERNS = [
    r"pg_sleep\w*",
    r"pg_read_\w+",
    r"pg_ls_\w+",
    r"pg_stat_file",
    r"lo_\w+",
    r"dblink\w*",
    r"pg_terminate_backend",
    r"pg_cancel_backend",
    r"set_config",
    r"nextval",
    r"setval",
    r"pg_advisory\w*",
    r"pg_try_advisory\w*",
    r"txid_\w+",
    r"pg_notify",
    r"pg_reload_conf",
    r"pg_rotate_logfile",
    r"pg_promote",
    r"pg_switch_wal",
    r"pg_create_\w+",
    r"pg_drop_\w+",
    r"pg_logical_\w+",
    r"pg_replication_\w+",
    r"pg_file_\w+",
    r"pg_export_snapshot",
    r"query_to_xml\w*",
    r"table_to_xml\w*",
    r"cursor_to_xml\w*",
    r"database_to_xml\w*",
    r"schema_to_xml\w*",
]
_FORBIDDEN_FUNCTION_RE = re.compile(r"^(?:" + "|".join(_FORBIDDEN_FUNCTION_PATTERNS) + r")$", re.IGNORECASE)

_SYSTEM_TABLE_PREFIX = "pg_"


@dataclass
class ValidationResult:
    ok: bool
    reason_code: str | None = None
    message: str | None = None
    normalized_sql: str | None = None
    tables: list[str] = field(default_factory=list)

    def public(self) -> dict[str, object]:
        return {"ok": self.ok, "reason_code": self.reason_code, "message": self.message}


def _reject(code: str, message: str) -> ValidationResult:
    return ValidationResult(ok=False, reason_code=code, message=message)


class SqlValidator:
    """First line of defence: only a single read-only SELECT over the allowed schema passes (fail closed)."""

    def __init__(self, *, allowed_schema: str, max_rows: int, max_length: int = 20000, dialect: str = "postgres") -> None:
        self.allowed_schema = allowed_schema.lower()
        self.max_rows = max_rows
        self.max_length = max_length
        self.dialect = dialect

    def validate(self, sql: str, *, max_rows: int | None = None) -> ValidationResult:
        limit_rows = max_rows if max_rows is not None else self.max_rows
        if sql is None or not sql.strip():
            return _reject("empty", "SQL query is empty")
        if len(sql) > self.max_length:
            return _reject("too_long", f"SQL query is longer than {self.max_length} characters")

        try:
            statements = [s for s in sqlglot.parse(sql, read=self.dialect) if s is not None]
        except SqlglotError as exc:
            first_line = str(exc).splitlines()[0] if str(exc) else "parse error"
            return _reject("parse_error", f"SQL could not be parsed: {first_line[:300]}")
        except Exception:  # noqa: BLE001 - any parser failure must reject
            return _reject("parse_error", "SQL could not be parsed")

        if not statements:
            return _reject("empty", "SQL query is empty")
        if len(statements) > 1:
            return _reject("multiple_statements", "only one SQL statement is allowed")

        root = statements[0]
        while isinstance(root, exp.Subquery) and not root.alias and not root.args.get("limit"):
            root = root.this
        if not isinstance(root, (exp.Select, exp.SetOperation)):
            return _reject("not_select", "only SELECT queries (optionally with WITH / UNION) are allowed")

        for node in root.walk():
            if isinstance(node, _FORBIDDEN_NODES):
                name = type(node).__name__
                if isinstance(node, exp.Into):
                    name = "SELECT INTO"
                elif isinstance(node, exp.Lock):
                    name = "FOR UPDATE/SHARE"
                return _reject("forbidden_operation", f"forbidden operation in query: {name}")

        forbidden_function = self._find_forbidden_function(sql, root)
        if forbidden_function:
            return _reject("forbidden_function", f"function '{forbidden_function}' is not allowed")

        tables_or_error = self._check_tables(root)
        if isinstance(tables_or_error, ValidationResult):
            return tables_or_error

        self._apply_limit(root, limit_rows)
        try:
            normalized = root.sql(dialect=self.dialect, comments=False)
        except SqlglotError:
            return _reject("parse_error", "SQL could not be normalized")
        return ValidationResult(ok=True, normalized_sql=normalized, tables=tables_or_error)

    def _find_forbidden_function(self, sql: str, root: exp.Expression) -> str | None:
        for func in root.find_all(exp.Func):
            name = func.name if isinstance(func, exp.Anonymous) else func.sql_name()
            if name and _FORBIDDEN_FUNCTION_RE.match(name):
                return name
        # Token scan catches calls that the AST maps to typed nodes or quoted identifiers.
        try:
            tokens = sqlglot.tokenize(sql, read=self.dialect)
        except SqlglotError:
            return "unparseable"
        for current, following in zip(tokens, tokens[1:], strict=False):
            if following.token_type == TokenType.L_PAREN and current.token_type not in (TokenType.STRING,):
                if _FORBIDDEN_FUNCTION_RE.match(current.text.strip('"')):
                    return current.text
        return None

    def _check_tables(self, root: exp.Expression) -> list[str] | ValidationResult:
        cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
        tables: list[str] = []
        for table in root.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier):
                # table-valued function in FROM, e.g. generate_series(...)
                continue
            name = table.name
            schema = (table.db or "").lower()
            if table.catalog:
                return _reject("forbidden_schema", f"cross-database reference '{table.sql(self.dialect)}' is not allowed")
            if not schema:
                if name.lower() in cte_names:
                    continue
                if name.lower().startswith(_SYSTEM_TABLE_PREFIX):
                    return _reject("forbidden_schema", f"system catalog table '{name}' is not allowed")
            elif schema != self.allowed_schema:
                return _reject(
                    "forbidden_schema",
                    f"schema '{table.db}' is not allowed; only '{self.allowed_schema}' tables can be queried",
                )
            if name not in tables:
                tables.append(name)
        return tables

    @staticmethod
    def _apply_limit(root: exp.Expression, max_rows: int) -> None:
        cap = max_rows + 1
        current = root.args.get("limit")
        value: int | None = None
        if isinstance(current, exp.Limit):
            limit_expr = current.expression
            if isinstance(limit_expr, exp.Literal) and not limit_expr.is_string:
                try:
                    value = int(limit_expr.this)
                except ValueError:
                    value = None
        elif isinstance(current, exp.Fetch):
            count = current.args.get("count")
            if isinstance(count, exp.Literal) and not count.is_string:
                try:
                    value = int(count.this)
                except ValueError:
                    value = None
        if value is not None and 0 <= value <= max_rows:
            if isinstance(current, exp.Fetch):
                root.set("limit", exp.Limit(expression=exp.Literal.number(value)))
            return
        root.set("limit", exp.Limit(expression=exp.Literal.number(cap)))
