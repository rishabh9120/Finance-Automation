"""
Unit tests for engine.run_global_reconciliation -- bug #5 (float tolerance),
bug #6 (bidirectional matching), and general matching-engine correctness
(date windows, already-matched rows, ambiguous candidates, idempotency).
"""
import pandas as pd
import pytest
from engine import run_global_reconciliation, MATCH_TOLERANCE, MATCH_WINDOW_DAYS


def txn(date, description, amount, ttype, account_source, category="Uncategorized",
        is_reviewed=False, you_paid=0.0, you_received=0.0, match_notes=""):
    return dict(date=date, description=description, amount=amount, type=ttype,
                account_source=account_source, category=category, is_reviewed=is_reviewed,
                you_paid=you_paid, you_received=you_received, match_notes=match_notes)


class TestToleranceMatching:
    """bug #5: exact float equality is too brittle for Excel round-tripped amounts."""

    def test_exact_amount_matches(self):
        df = pd.DataFrame([
            txn("2026-07-10", "Groceries", 500, "Debit", "Splitwise", you_paid=1500.00),
            txn("2026-07-11", "UPI-DMART", 1500.00, "Debit", "HDFC Bank"),
        ])
        result = run_global_reconciliation(df)
        assert result.iloc[1]["type"] == "Transfer_Splitwise_Base"

    def test_one_paisa_drift_still_matches(self):
        df = pd.DataFrame([
            txn("2026-07-10", "Groceries", 500, "Debit", "Splitwise", you_paid=1500.00),
            txn("2026-07-11", "UPI-DMART", 1499.99, "Debit", "HDFC Bank"),
        ])
        result = run_global_reconciliation(df)
        assert result.iloc[1]["type"] == "Transfer_Splitwise_Base"

    def test_amount_just_outside_tolerance_does_not_match(self):
        df = pd.DataFrame([
            txn("2026-07-10", "Groceries", 500, "Debit", "Splitwise", you_paid=1500.00),
            txn("2026-07-11", "UPI-DMART", 1500.00 - (MATCH_TOLERANCE + 0.05), "Debit", "HDFC Bank"),
        ])
        result = run_global_reconciliation(df)
        assert result.iloc[1]["type"] == "Debit"  # unchanged, not matched
        assert result.iloc[1]["match_notes"] == ""


class TestBidirectionalMatching:
    """bug #6: settlements received must match incoming bank credits, not just
    bills-fronted matching outgoing debits."""

    def test_you_fronted_bill_matches_bank_debit(self):
        df = pd.DataFrame([
            txn("2026-07-10", "Groceries bill", 0, "Debit", "Splitwise", you_paid=1500.00),
            txn("2026-07-10", "UPI-DMART", 1500.00, "Debit", "HDFC Bank"),
        ])
        result = run_global_reconciliation(df)
        assert result.iloc[1]["type"] == "Transfer_Splitwise_Base"
        assert result.iloc[1]["category"] == "Excluded"
        assert "🔗" in result.iloc[0]["match_notes"]

    def test_settlement_received_matches_bank_credit(self):
        df = pd.DataFrame([
            txn("2026-07-15", "Roommate paid me back", 0, "Settlement", "Splitwise", you_received=3000.0),
            txn("2026-07-16", "UPI-CR-ROOMMATE", 3000.0, "Credit", "HDFC Bank"),
        ])
        result = run_global_reconciliation(df)
        assert result.iloc[1]["type"] == "Transfer_Splitwise_Settlement"
        assert result.iloc[1]["category"] == "Excluded"

    def test_settlement_never_matches_a_debit(self):
        # A you_received row must only ever match a Credit, never a Debit,
        # even with a coincidentally identical amount.
        df = pd.DataFrame([
            txn("2026-07-15", "Roommate paid me back", 0, "Settlement", "Splitwise", you_received=3000.0),
            txn("2026-07-16", "Unrelated debit", 3000.0, "Debit", "HDFC Bank"),
        ])
        result = run_global_reconciliation(df)
        assert result.iloc[1]["type"] == "Debit"
        assert result.iloc[1]["match_notes"] == ""

    def test_bill_fronted_never_matches_a_credit(self):
        df = pd.DataFrame([
            txn("2026-07-10", "Groceries bill", 0, "Debit", "Splitwise", you_paid=1500.00),
            txn("2026-07-10", "Unrelated credit", 1500.00, "Credit", "HDFC Bank"),
        ])
        result = run_global_reconciliation(df)
        assert result.iloc[1]["type"] == "Credit"
        assert result.iloc[1]["match_notes"] == ""


class TestDateWindow:

    def test_match_within_window_succeeds(self):
        df = pd.DataFrame([
            txn("2026-07-10", "Bill", 0, "Debit", "Splitwise", you_paid=500.0),
            txn(f"2026-07-{10 + MATCH_WINDOW_DAYS}", "Bank txn", 500.0, "Debit", "HDFC Bank"),
        ])
        result = run_global_reconciliation(df)
        assert result.iloc[1]["type"] == "Transfer_Splitwise_Base"

    def test_match_outside_window_fails(self):
        df = pd.DataFrame([
            txn("2026-07-10", "Bill", 0, "Debit", "Splitwise", you_paid=500.0),
            txn(f"2026-07-{10 + MATCH_WINDOW_DAYS + 1}", "Bank txn", 500.0, "Debit", "HDFC Bank"),
        ])
        result = run_global_reconciliation(df)
        assert result.iloc[1]["type"] == "Debit"
        assert result.iloc[1]["match_notes"] == ""


class TestAlreadyMatchedRowsAreSkipped:

    def test_row_with_link_prefix_is_not_rematched(self):
        df = pd.DataFrame([
            txn("2026-07-10", "Bill", 0, "Debit", "Splitwise", you_paid=500.0, match_notes="🔗 Matched Bank: old"),
            txn("2026-07-10", "Bank txn", 500.0, "Debit", "HDFC Bank"),
        ])
        result = run_global_reconciliation(df)
        # Should NOT create a second match since the Splitwise row is already linked
        assert result.iloc[1]["match_notes"] == ""
        assert result.iloc[1]["type"] == "Debit"

    def test_idempotent_on_second_run(self):
        df = pd.DataFrame([
            txn("2026-07-10", "Bill", 0, "Debit", "Splitwise", you_paid=500.0),
            txn("2026-07-10", "Bank txn", 500.0, "Debit", "HDFC Bank"),
        ])
        once = run_global_reconciliation(df)
        twice = run_global_reconciliation(once)
        pd.testing.assert_frame_equal(
            once.reset_index(drop=True), twice.reset_index(drop=True), check_dtype=False
        )


class TestAmbiguousCandidates:

    def test_picks_one_candidate_without_double_matching(self):
        # Two bank debits of the same amount on the same day -- only one
        # should get consumed by the Splitwise match, not both.
        df = pd.DataFrame([
            txn("2026-07-10", "Bill", 0, "Debit", "Splitwise", you_paid=500.0),
            txn("2026-07-10", "Bank txn A", 500.0, "Debit", "HDFC Bank"),
            txn("2026-07-10", "Bank txn B", 500.0, "Debit", "HDFC Bank"),
        ])
        result = run_global_reconciliation(df)
        matched_count = (result["type"] == "Transfer_Splitwise_Base").sum()
        assert matched_count == 1


class TestNoOpCases:

    def test_pure_personal_expense_never_matched(self):
        # you_paid = 0, you_received = 0 -- nothing to reconcile
        df = pd.DataFrame([
            txn("2026-07-10", "Personal share of dinner", 300, "Debit", "Splitwise"),
            txn("2026-07-10", "Unrelated bank txn", 300.0, "Debit", "HDFC Bank"),
        ])
        result = run_global_reconciliation(df)
        assert result.iloc[1]["match_notes"] == ""

    def test_empty_dataframe_does_not_crash(self):
        df = pd.DataFrame(columns=["date", "description", "amount", "type", "account_source", "category",
                                    "is_reviewed", "you_paid", "you_received", "match_notes"])
        result = run_global_reconciliation(df)
        assert len(result) == 0

    def test_bank_only_no_splitwise_does_not_crash(self):
        df = pd.DataFrame([txn("2026-07-10", "ATM", 500.0, "Debit", "HDFC Bank")])
        result = run_global_reconciliation(df)
        assert len(result) == 1
        assert result.iloc[0]["match_notes"] == ""
