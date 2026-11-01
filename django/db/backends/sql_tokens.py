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
    | (?P<comment>(?:--|\#(?=\s|$))[^\n]*|/\*.*?(?:\*/|\Z))
    | (?P<string>'(?:''|\\'|[^'])*'|'[^']*\Z)
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


_TXN_WORDS = {
    "TRANSACTION",
    "WORK",
    "TRAN",
    "DISTRIBUTED",
    "DEFERRED",
    "IMMEDIATE",
    "EXCLUSIVE",
}
_END_QUALIFIERS = {"IF", "FOR", "WHILE", "LOOP", "CASE"}


def _split_on_semicolons(sql):
    """
    Split on top-level semicolons. Semicolons inside parentheses or inside
    BEGIN ... END blocks (triggers, stored routines, anonymous blocks) don't
    end a statement. As in sqlparse, a backslash escapes a quote inside
    single-quoted strings.
    """
    toks = list(tokenize(sql))
    sig = [i for i, tk in enumerate(toks) if tk.kind not in (WHITESPACE, COMMENT)]
    pos = {ti: n for n, ti in enumerate(sig)}

    def next_word(ti):
        n = pos.get(ti)
        if n is None or n + 1 >= len(sig):
            return None
        tk = toks[sig[n + 1]]
        return (
            tk.value.upper()
            if tk.kind == WORD
            else (";" if is_punctuation(tk, ";") else None)
        )

    statements, current = [], []
    stack, level, is_create, unconfirmed = [], 0, False, None
    closing = False
    for ti, tk in enumerate(toks):
        if tk.kind == COMMENT:
            current.append(" ")
            continue
        current.append(tk.value)
        if tk.kind == PUNCTUATION:
            if tk.value == "(":
                level += 1
            elif tk.value == ")":
                level = max(0, level - 1)
            elif tk.value == ";":
                unconfirmed = None
                if level <= 0 and not stack:
                    statements.append("".join(current))
                    current = []
                    stack, level, is_create = [], 0, False
            continue
        if tk.kind != WORD:
            continue
        w = tk.value.upper()
        if closing:
            # The IF/LOOP/... in "END IF" closes a block, it doesn't open one.
            closing = False
            if w in _END_QUALIFIERS:
                continue
        if w == "CREATE":
            is_create = True
        elif w == "DECLARE" and is_create and not stack:
            stack.append("DECLARE")
        elif w == "BEGIN":
            nw = next_word(ti)
            if nw == ";" or nw in _TXN_WORDS:
                continue
            if stack and stack[-1] == "DECLARE":
                stack[-1] = "BEGIN"
            else:
                stack.append("BEGIN")
        elif "BEGIN" in stack:
            if w in ("FOR", "WHILE"):
                unconfirmed = w
            elif w in ("LOOP", "DO") and (unconfirmed or w == "LOOP"):
                stack.append(unconfirmed or "LOOP")
                unconfirmed = None
            elif w in ("IF", "CASE"):
                stack.append(w)
            elif w == "END":
                nw = next_word(ti)
                if nw in _END_QUALIFIERS:
                    closing = True
                    if stack and (
                        stack[-1] == nw
                        or (nw == "LOOP" and stack[-1] in ("FOR", "WHILE"))
                    ):
                        stack.pop()
                elif stack:
                    stack.pop()
    statements.append("".join(current))
    return [x.strip() for x in statements if x.strip()]


_CLIENT_COMMAND_RE = re.compile(r"\b(?:DELIMITER|GO)\b", re.IGNORECASE)
_DELIMITER_COMMAND_RE = re.compile(
    r"DELIMITER[ \t]+([^\s'\"`\\]+)[ \t]*(?:\r?\n|\Z)", re.IGNORECASE
)
_GO_COMMAND_RE = re.compile(
    r"GO(?:[ \t]+\d+)?[ \t]*(?:--[^\n]*)?(?:\r?\n|\Z)", re.IGNORECASE
)


def _strip_comments(text):
    text = "".join(
        " " if token.kind == COMMENT else token.value for token in tokenize(text)
    ).strip()
    return [text] if text else []


def split_statements(sql):
    """
    Split a script into statements. Comments are replaced by a space and
    statements are stripped; empty ones are skipped.

    Statements end at top-level semicolons (see _split_on_semicolons()). Two
    client commands are also understood, and aren't part of the output:

    * "DELIMITER x" on its own line at the start of a statement (MySQL client):
      statements then end at "x" instead of ";", which is removed.
    * "GO" or "GO n" alone on a line, outside parentheses (SQL Server
      sqlcmd): it ends the current batch. The repeat count is ignored.

    A lone "/" terminator (Oracle) isn't supported.
    """
    if not _CLIENT_COMMAND_RE.search(sql):
        return _split_on_semicolons(sql)
    statements = []
    delimiter = ";"
    pos = chunk_start = 0
    level = 0
    at_start = line_start = True

    def flush(end):
        if delimiter == ";":
            statements.extend(_split_on_semicolons(sql[chunk_start:end]))
        else:
            statements.extend(_strip_comments(sql[chunk_start:end]))

    while pos < len(sql):
        if delimiter != ";" and sql.startswith(delimiter, pos):
            flush(pos)
            pos = chunk_start = pos + len(delimiter)
            at_start, line_start = True, False
            continue
        match = _TOKEN_RE.match(sql, pos)
        value = match.group()
        kind = match.lastgroup
        if kind == WHITESPACE:
            line_start = line_start or "\n" in value
            pos = match.end()
            continue
        if kind == COMMENT:
            line_start = False
            pos = match.end()
            continue
        command = None
        if kind == WORD and line_start:
            upper = value.upper()
            if upper == "DELIMITER" and at_start:
                command = _DELIMITER_COMMAND_RE.match(sql, pos)
            elif upper == "GO" and delimiter == ";" and level == 0:
                command = _GO_COMMAND_RE.match(sql, pos)
        if command:
            flush(pos)
            if value.upper() == "DELIMITER":
                delimiter = command.group(1)
            pos = chunk_start = command.end()
            level = 0
            at_start = line_start = True
            continue
        line_start = False
        at_start = False
        if kind == PUNCTUATION:
            if value == "(":
                level += 1
            elif value == ")":
                level = max(0, level - 1)
            elif value == ";" and level == 0:
                at_start = True
        pos = match.end()
    flush(len(sql))
    return statements


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
