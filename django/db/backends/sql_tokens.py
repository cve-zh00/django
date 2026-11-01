"""
A minimal SQL tokenizer used by database backends.

It only knows enough SQL lexical structure (strings, quoted identifiers,
comments, words, punctuation) to split scripts into statements and to
introspect simple definitions. It doesn't validate or build a syntax tree.
"""

import re
from collections import namedtuple

WHITESPACE = "whitespace"
COMMENT = "comment"
STRING = "string"
QUOTED_NAME = "quoted_name"
WORD = "word"
NUMBER = "number"
PUNCTUATION = "punctuation"
OTHER = "other"

Token = namedtuple("Token", ["kind", "value"])

_TOKEN_RE = re.compile(
    r"""
      (?P<whitespace>\s+)
    | (?P<comment>--[^\n]*|/\*.*?(?:\*/|\Z))
    | (?P<string>'(?:[^']|'')*(?:'|\Z))
    | (?P<dollar>\$(?P<tag>[^\W\d]\w*|)\$.*?(?:\$(?P=tag)\$|\Z))
    | (?P<quoted_name>"(?:[^"]|"")*(?:"|\Z)|`(?:[^`]|``)*(?:`|\Z)|\[[^\]]*(?:\]|\Z))
    | (?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
    | (?P<word>[^\W\d]\w*)
    | (?P<punctuation>[(),;.])
    | (?P<other>.)
    """,
    re.VERBOSE | re.DOTALL,
)


def tokenize(sql):
    """Yield Token(kind, value) pairs; joining the values restores `sql`."""
    for match in _TOKEN_RE.finditer(sql):
        kind = match.lastgroup
        if kind in {"tag", "dollar"}:
            kind = STRING
        yield Token(kind, match.group())


def significant_tokens(sql):
    """Yield tokens that are neither whitespace nor comments."""
    for token in tokenize(sql):
        if token.kind not in {WHITESPACE, COMMENT}:
            yield token


def is_punctuation(token, value):
    return token.kind == PUNCTUATION and token.value == value


def is_keyword(token, value):
    return token.kind == WORD and token.value.upper() == value


def name_value(token):
    """
    Return the identifier held by a word or quoted name token, or None for any
    other token.
    """
    if token.kind == WORD:
        return token.value
    if token.kind == QUOTED_NAME:
        quote = token.value[0]
        closing = "]" if quote == "[" else quote
        inner = token.value[1:-1]
        return inner.replace(closing * 2, closing) if quote != "[" else inner
    return None


def split_statements(sql):
    """
    Split a script into statements on top-level semicolons. Comments are
    removed and statements are stripped; empty statements are skipped.
    """
    statements = []
    current = []
    for token in tokenize(sql):
        if token.kind == COMMENT:
            current.append(" ")
        elif is_punctuation(token, ";"):
            current.append(token.value)
            statements.append("".join(current))
            current = []
        else:
            current.append(token.value)
    statements.append("".join(current))
    return [statement.strip() for statement in statements if statement.strip()]


_CLAUSE_KEYWORDS = frozenset(
    {"SELECT", "FROM", "WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "OFFSET"}
    | {"UNION", "INSERT", "UPDATE", "DELETE", "SET", "VALUES", "RETURNING"}
)
_UPPERCASE_KEYWORDS = _CLAUSE_KEYWORDS | {
    "AS", "AND", "OR", "NOT", "NULL", "IN", "IS", "ON", "JOIN", "LEFT", "RIGHT",
    "INNER", "OUTER", "CROSS", "BY", "DISTINCT", "INTO", "ASC", "DESC", "BETWEEN",
    "LIKE", "EXISTS", "CASE", "WHEN", "THEN", "ELSE", "END", "ALL",
}  # fmt: skip


def format_sql(sql):
    """
    Return `sql` with keywords uppercased and a line break before each major
    clause. Meant for human-readable debug output only.
    """
    out = []
    for token in tokenize(sql):
        value = token.value
        if token.kind == WORD:
            upper = value.upper()
            if upper in _UPPERCASE_KEYWORDS:
                value = upper
            if upper in _CLAUSE_KEYWORDS and out:
                while out and out[-1].isspace():
                    out.pop()
                out.append("\n")
        out.append(value)
    return "".join(out).strip()


def parenthesized_groups(sql):
    """
    Return the text between each top-level pair of parentheses, in order,
    without the parentheses themselves.
    """
    groups = []
    depth = 0
    current = []
    for token in tokenize(sql):
        if is_punctuation(token, "("):
            if depth:
                current.append(token.value)
            depth += 1
        elif is_punctuation(token, ")") and depth:
            depth -= 1
            if depth:
                current.append(token.value)
            else:
                groups.append("".join(current))
                current = []
        elif depth:
            current.append(token.value)
    return groups
