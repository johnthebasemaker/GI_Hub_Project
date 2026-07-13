"""
ai/safety.py — Safe-SQL gate for AI-generated queries
======================================================
The NL → SQL feature lets an LLM (qwen2.5-coder) emit a SQL string. Before
we hand that string to SQLite, we MUST verify it:

  1. Contains a single statement (no '; DROP …' tail).
  2. Begins with SELECT (after stripping leading SQL comments / whitespace).
  3. Does NOT contain destructive keywords as standalone tokens
     (DROP, DELETE, UPDATE, INSERT, ALTER, ATTACH, PRAGMA, CREATE, REPLACE,
      VACUUM, REINDEX).
  4. Doesn't touch the `users` table — passwords/hashes are off-limits to AI.
  5. Has a LIMIT clause (we inject one if missing).

The validator is intentionally pessimistic. False negatives ("rejected a
legitimate query") are user-visible but recoverable — they just rerun with
a clearer question. False positives ("let destructive SQL through") are
catastrophic.

Both `is_safe_select` and `scrub_sql` are pure functions with no I/O, so
they're trivially unit-testable.
"""

from __future__ import annotations

import re
from typing import Tuple


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
FORBIDDEN_KEYWORDS = (
    "drop", "delete", "update", "insert", "alter", "attach",
    "pragma", "create", "replace", "vacuum", "reindex",
    "truncate", "merge", "grant", "revoke",
)

FORBIDDEN_TABLES = ("users", "pending_users")  # auth/PII surfaces

DEFAULT_ROW_LIMIT = 500
HARD_ROW_LIMIT = 2000


# Token boundary so "Created_At" doesn't match "create".
_KW_PATTERN = re.compile(
    r"(?ix)\b(" + "|".join(FORBIDDEN_KEYWORDS) + r")\b"
)
_TABLE_PATTERN = re.compile(
    r"(?ix)\b(?:from|join|into|update)\s+(" + "|".join(FORBIDDEN_TABLES) + r")\b"
)
_LIMIT_PATTERN = re.compile(r"(?i)\blimit\s+\d+")

# Single left-to-right tokenizer that recognises (in precedence order) a
# single-quoted string literal, a block comment, or a line comment. Whichever
# matches FIRST at a given position wins — so a `--` inside a string is part of
# the string (not a comment), and a `'` inside a comment is part of the comment
# (not a string). This is the correct lexical precedence and is what makes the
# keyword scan immune to false positives like `-- Replace with real Site_ID`.
_SQL_TOKEN = re.compile(
    r"""
      (?P<string>'(?:[^']|'')*')   # '...' with '' as the escaped quote
    | (?P<block>/\*.*?\*/)          # /* block comment */
    | (?P<line>--[^\n]*)            # -- line comment
    """,
    re.DOTALL | re.VERBOSE,
)


def _sanitize_for_scan(sql: str) -> str:
    """
    Return a copy of `sql` safe to scan for keywords/tables/statements:
      - string literals collapsed to ''      (so data values can't trip rules)
      - comments (block + line) replaced with a space

    The result is NEVER executed — only inspected. The original SQL (comments
    and all) is what actually runs; SQLite handles comments fine.
    """
    def _repl(m: "re.Match") -> str:
        return "''" if m.lastgroup == "string" else " "
    return _SQL_TOKEN.sub(_repl, sql)


def is_safe_select(sql: str) -> Tuple[bool, str]:
    """
    Returns (ok, reason). `reason` is "" when ok=True, otherwise a short
    human-readable explanation suitable for showing to the user.

    All structural checks run against a sanitized copy (comments stripped,
    string literals blanked) so neither comment prose nor data values can
    cause a false reject OR a false accept.
    """
    if not sql or not sql.strip():
        return False, "Empty query."

    scan = _sanitize_for_scan(sql)

    # Reject multi-statement payloads. A trailing semicolon is fine — we
    # check whether there's any non-whitespace content AFTER the first ';'.
    parts = scan.split(";")
    significant = [p for p in parts if p.strip()]
    if len(significant) > 1:
        return False, "Multiple statements are not allowed."

    body = significant[0] if significant else scan

    # Must START with SELECT (or WITH … SELECT for CTEs).
    head = body.lstrip().lower()
    if not (head.startswith("select") or head.startswith("with")):
        return False, "Only SELECT queries are allowed."

    # No forbidden keywords as tokens.
    m = _KW_PATTERN.search(body)
    if m:
        return False, f"Query contains forbidden keyword: {m.group(1).upper()}."

    # No reads from sensitive tables.
    m = _TABLE_PATTERN.search(body)
    if m:
        return False, f"Reads from `{m.group(1)}` are not allowed via the AI search."

    return True, ""


def scrub_sql(sql: str, row_limit: int = DEFAULT_ROW_LIMIT) -> str:
    """
    Returns the SQL with a `LIMIT N` clause appended if one isn't already
    present. Caps the limit at HARD_ROW_LIMIT regardless of caller request.
    Trailing semicolons are stripped.

    LIMIT detection runs on the sanitized copy so a stray "limit" word inside
    a comment or string can't fool us into skipping the row cap.
    """
    body = sql.strip().rstrip(";").strip()
    cap = min(max(1, int(row_limit)), HARD_ROW_LIMIT)
    if _LIMIT_PATTERN.search(_sanitize_for_scan(body)):
        return body
    # Append on a NEW LINE: if `body` ends with a `-- line comment`, appending
    # on the same line would make the comment swallow the LIMIT, leaving the
    # query unbounded. A newline terminates the comment first.
    return f"{body}\nLIMIT {cap}"
