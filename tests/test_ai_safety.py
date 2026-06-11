"""
test_ai_safety.py — Safe-SQL gate for AI-generated queries
===========================================================
Pure-function tests for ai.safety. No Ollama, no DB, no Streamlit needed.

Centres on the regression from the field screenshot: a legitimate SELECT was
rejected because the model left an inline comment `-- Replace with actual
Site_ID`, and the word "Replace" matched the forbidden keyword `REPLACE`.
"""

from ai.safety import is_safe_select, scrub_sql, _sanitize_for_scan


# ---------------------------------------------------------------------------
# Regression: comments must never trip the keyword / table scan
# ---------------------------------------------------------------------------
class TestCommentsDoNotTripRules:
    def test_replace_word_in_line_comment_is_allowed(self):
        sql = (
            "SELECT SAP_Code, Current_Stock FROM v_live_stock\n"
            "WHERE Current_Stock < Minimum_Qty  -- Replace with actual Site_ID\n"
            "ORDER BY Current_Stock ASC"
        )
        ok, why = is_safe_select(sql)
        assert ok, f"legit query wrongly rejected: {why}"

    def test_forbidden_word_in_block_comment_is_allowed(self):
        sql = "SELECT * FROM v_live_stock /* do not DROP this view */ LIMIT 10"
        ok, why = is_safe_select(sql)
        assert ok, why

    def test_leading_comment_then_select_is_allowed(self):
        sql = "-- this is the query\nSELECT * FROM inventory"
        ok, why = is_safe_select(sql)
        assert ok, why


# ---------------------------------------------------------------------------
# Regression: data values inside string literals must not trip rules
# ---------------------------------------------------------------------------
class TestStringLiteralsDoNotTripRules:
    def test_forbidden_keyword_inside_string_literal_is_allowed(self):
        sql = "SELECT * FROM consumption WHERE Remarks = 'please DELETE later'"
        ok, why = is_safe_select(sql)
        assert ok, why

    def test_semicolon_inside_string_is_not_multistatement(self):
        sql = "SELECT * FROM consumption WHERE Remarks = 'a; b; c'"
        ok, why = is_safe_select(sql)
        assert ok, why

    def test_supplier_name_with_quote_escape(self):
        sql = "SELECT * FROM receipts WHERE Supplier = 'O''Brien Supplies'"
        ok, why = is_safe_select(sql)
        assert ok, why


# ---------------------------------------------------------------------------
# Real threats must still be caught
# ---------------------------------------------------------------------------
class TestRealThreatsBlocked:
    def test_actual_drop_is_blocked(self):
        ok, why = is_safe_select("SELECT 1; DROP TABLE inventory")
        assert not ok

    def test_trailing_delete_statement_blocked(self):
        ok, why = is_safe_select("SELECT * FROM inventory; DELETE FROM receipts")
        assert not ok
        assert "Multiple statements" in why or "forbidden" in why.lower()

    def test_update_keyword_blocked(self):
        ok, why = is_safe_select("UPDATE inventory SET Minimum_Qty = 0")
        assert not ok

    def test_non_select_blocked(self):
        ok, why = is_safe_select("PRAGMA table_info(users)")
        assert not ok

    def test_users_table_blocked(self):
        ok, why = is_safe_select("SELECT username, password_hash FROM users")
        assert not ok
        assert "users" in why.lower()

    def test_join_into_users_blocked(self):
        ok, why = is_safe_select(
            "SELECT i.SAP_Code FROM inventory i JOIN users u ON 1=1"
        )
        assert not ok


# ---------------------------------------------------------------------------
# Legitimate shapes accepted
# ---------------------------------------------------------------------------
class TestLegitShapesAccepted:
    def test_plain_select(self):
        ok, _ = is_safe_select("SELECT * FROM v_live_stock")
        assert ok

    def test_with_cte_select(self):
        sql = (
            "WITH x AS (SELECT SAP_Code, Current_Stock FROM v_live_stock) "
            "SELECT * FROM x WHERE Current_Stock < 0"
        )
        ok, why = is_safe_select(sql)
        assert ok, why

    def test_trailing_semicolon_ok(self):
        ok, _ = is_safe_select("SELECT * FROM inventory;")
        assert ok


# ---------------------------------------------------------------------------
# scrub_sql — LIMIT injection
# ---------------------------------------------------------------------------
class TestScrubSql:
    def test_adds_limit_when_missing(self):
        out = scrub_sql("SELECT * FROM inventory")
        assert "limit" in out.lower()

    def test_keeps_existing_limit(self):
        out = scrub_sql("SELECT * FROM inventory LIMIT 5")
        assert out.lower().count("limit") == 1
        assert out.strip().lower().endswith("limit 5")

    def test_limit_word_in_comment_does_not_block_injection(self):
        # A 'limit' that appears only inside a comment must NOT fool us into
        # thinking the query is already bounded — AND the appended LIMIT must
        # land on its own line so the trailing comment can't swallow it.
        out = scrub_sql("SELECT * FROM inventory -- no limit set here")
        sanitized = _sanitize_for_scan(out)
        # After stripping the comment, a real LIMIT clause must remain.
        assert "limit" in sanitized.lower()
        assert sanitized.strip().lower().endswith("limit 500")

    def test_strips_trailing_semicolon(self):
        out = scrub_sql("SELECT * FROM inventory;")
        assert ";" not in out
