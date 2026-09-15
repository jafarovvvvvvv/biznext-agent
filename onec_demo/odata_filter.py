"""Subset of the OData v3 $filter grammar used by 1C:Enterprise standard.odata, translated to SQLite.

Supported: eq ne gt ge lt le, and / or / not, parentheses, string '…', numbers, true/false/null,
datetime'YYYY-MM-DDTHH:MM:SS', guid'…'. Identifiers are checked against the table columns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_TOKEN_RE = re.compile(
    r"\s*(?:"
    r"(?P<datetime>datetime'[^']*')|(?P<guid>guid'[^']*')|(?P<string>'(?:[^']|'')*')|"
    r"(?P<number>-?\d+(?:\.\d+)?)|(?P<lparen>\()|(?P<rparen>\))|"
    r"(?P<word>[\w/]+)"
    r")",
    re.UNICODE,
)
_OPERATORS = {"eq": "=", "ne": "<>", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class FilterError(ValueError):
    pass


@dataclass
class Token:
    kind: str
    value: str


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    position = 0
    text = text.strip()
    while position < len(text):
        match = _TOKEN_RE.match(text, position)
        if not match or match.end() == position:
            raise FilterError(f"unexpected character at position {position}")
        kind = match.lastgroup or ""
        tokens.append(Token(kind, match.group(kind)))
        position = match.end()
        while position < len(text) and text[position].isspace():
            position += 1
    return tokens


class _Parser:
    def __init__(self, tokens: list[Token], columns: set[str]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.columns = columns
        self.params: list[Any] = []

    def peek(self) -> Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> Token:
        token = self.peek()
        if token is None:
            raise FilterError("unexpected end of filter")
        self.pos += 1
        return token

    def is_word(self, word: str) -> bool:
        token = self.peek()
        return token is not None and token.kind == "word" and token.value.lower() == word

    def parse(self) -> str:
        sql = self.parse_or()
        if self.peek() is not None:
            raise FilterError(f"unexpected token '{self.peek().value}'")  # type: ignore[union-attr]
        return sql

    def parse_or(self) -> str:
        parts = [self.parse_and()]
        while self.is_word("or"):
            self.take()
            parts.append(self.parse_and())
        return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"

    def parse_and(self) -> str:
        parts = [self.parse_unary()]
        while self.is_word("and"):
            self.take()
            parts.append(self.parse_unary())
        return parts[0] if len(parts) == 1 else "(" + " AND ".join(parts) + ")"

    def parse_unary(self) -> str:
        if self.is_word("not"):
            self.take()
            return f"NOT ({self.parse_unary()})"
        token = self.peek()
        if token is not None and token.kind == "lparen":
            self.take()
            inner = self.parse_or()
            if self.take().kind != "rparen":
                raise FilterError("missing ')'")
            return f"({inner})"
        return self.parse_comparison()

    def parse_comparison(self) -> str:
        field = self.take()
        if field.kind != "word" or field.value not in self.columns:
            raise FilterError(f"unknown field '{field.value}'")
        operator = self.take()
        if operator.kind != "word" or operator.value.lower() not in _OPERATORS:
            raise FilterError(f"unsupported operator '{operator.value}'")
        op = _OPERATORS[operator.value.lower()]
        value = self.take()
        column = f'"{field.value}"'
        if value.kind == "word" and value.value.lower() == "null":
            if op not in ("=", "<>"):
                raise FilterError("null supports only eq/ne")
            return f"{column} IS {'NOT ' if op == '<>' else ''}NULL"
        self.params.append(self.literal(value))
        return f"{column} {op} ?"

    @staticmethod
    def literal(token: Token) -> Any:
        if token.kind == "datetime":
            raw = token.value[len("datetime'"):-1]
            if not _DATETIME_RE.match(raw):
                raise FilterError(f"invalid datetime literal {token.value}")
            return raw
        if token.kind == "guid":
            raw = token.value[len("guid'"):-1]
            if not _GUID_RE.match(raw):
                raise FilterError(f"invalid guid literal {token.value}")
            return raw.lower()
        if token.kind == "string":
            return token.value[1:-1].replace("''", "'")
        if token.kind == "number":
            return float(token.value) if "." in token.value else int(token.value)
        if token.kind == "word" and token.value.lower() in ("true", "false"):
            return 1 if token.value.lower() == "true" else 0
        raise FilterError(f"unsupported literal '{token.value}'")


def to_sql(text: str, columns: set[str]) -> tuple[str, list[Any]]:
    parser = _Parser(tokenize(text), columns)
    return parser.parse(), parser.params
