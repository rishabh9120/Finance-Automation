"""
Unit tests for logic.merge_and_dedup (bug #8) -- including the specific
scenario the user asked about: re-uploading a bank statement whose date
range overlaps a previous upload (a "refreshed" export that includes some
already-seen transactions plus new ones).
"""
import pandas as pd
from logic import merge_and_dedup, ensure_dup_seq, DEDUP_COLUMNS


def make_txn(date, desc, amount, account="Splitwise", **kw):
    row = dict(date=date, description=desc, amount=amount, type=kw.pop("type", "Debit"),
               account_source=account, category=kw.pop("category", "Uncategorized"),
               is_reviewed=kw.pop("is_reviewed", False))
    row.update(kw)
    return row


class TestGenuineDuplicatesPreserved:

    def test_two_identical_lookalike_rows_in_one_batch_both_kept(self):
        batch = pd.DataFrame([
            make_txn("2026-07-06", "Auto", 121.0),
            make_txn("2026-07-06", "Auto", 121.0),
        ])
        result = merge_and_dedup(pd.DataFrame(), batch)
        assert len(result) == 2

    def test_three_identical_lookalike_rows_all_kept(self):
        batch = pd.DataFrame([make_txn("2026-07-06", "Auto", 121.0)] * 3)
        result = merge_and_dedup(pd.DataFrame(), batch)
        assert len(result) == 3


class TestReuploadDeduplication:

    def test_exact_reupload_adds_zero_new_rows(self):
        first = pd.DataFrame([
            make_txn("2026-07-06", "Auto", 121.0),
            make_txn("2026-07-06", "Auto", 121.0),
            make_txn("2026-07-07", "Groceries", 500.0),
        ])
        existing = merge_and_dedup(pd.DataFrame(), first)

        reupload = pd.DataFrame([
            make_txn("2026-07-06", "Auto", 121.0),
            make_txn("2026-07-06", "Auto", 121.0),
            make_txn("2026-07-07", "Groceries", 500.0),
        ])
        result = merge_and_dedup(existing, reupload)
        assert len(result) == 3  # not 6

    def test_updated_bank_statement_with_overlap_only_adds_new_rows(self):
        """
        Simulates downloading an "updated" statement that covers a longer date
        range: 3 transactions the user already imported, plus 2 genuinely new
        ones at the end (as a real bank export would look after a week).
        """
        original_upload = pd.DataFrame([
            make_txn("2026-07-01", "ATM WDL", 500.0, account="HDFC Bank"),
            make_txn("2026-07-02", "SWIGGY ORDER", 350.0, account="HDFC Bank"),
            make_txn("2026-07-03", "SALARY", 55000.0, account="HDFC Bank", type="Credit"),
        ])
        existing = merge_and_dedup(pd.DataFrame(), original_upload)

        refreshed_statement = pd.DataFrame([
            make_txn("2026-07-01", "ATM WDL", 500.0, account="HDFC Bank"),          # already seen
            make_txn("2026-07-02", "SWIGGY ORDER", 350.0, account="HDFC Bank"),     # already seen
            make_txn("2026-07-03", "SALARY", 55000.0, account="HDFC Bank", type="Credit"),  # already seen
            make_txn("2026-07-08", "AMAZON SHOPPING", 1200.0, account="HDFC Bank"),  # NEW
            make_txn("2026-07-09", "ATM WDL", 500.0, account="HDFC Bank"),           # NEW (coincidentally same amount as day 1)
        ])
        result = merge_and_dedup(existing, refreshed_statement)

        assert len(result) == 5  # 3 old + 2 genuinely new
        assert (result["description"] == "AMAZON SHOPPING").sum() == 1
        # merge_and_dedup preserves whatever date type it's given (date
        # normalization to Timestamp happens later, in run_global_reconciliation)
        assert (result["date"].astype(str) == "2026-07-09").sum() == 1

    def test_reviewed_status_is_preserved_across_reupload(self):
        """A transaction the user already categorized/reviewed shouldn't be
        reset to unreviewed just because it appears again in a refreshed export."""
        original = pd.DataFrame([make_txn("2026-07-01", "ATM WDL", 500.0, account="HDFC Bank")])
        existing = merge_and_dedup(pd.DataFrame(), original)
        existing.loc[0, "is_reviewed"] = True
        existing.loc[0, "category"] = "General"

        reupload = pd.DataFrame([make_txn("2026-07-01", "ATM WDL", 500.0, account="HDFC Bank", is_reviewed=False)])
        result = merge_and_dedup(existing, reupload)

        assert len(result) == 1
        assert result.iloc[0]["is_reviewed"] == True  # noqa: E712 -- kept, not clobbered by the unreviewed re-upload


class TestDupSeqMigration:

    def test_missing_dup_seq_column_is_backfilled(self):
        # Simulates an existing DB saved before the _dup_seq column existed
        legacy_existing = pd.DataFrame([
            make_txn("2026-07-01", "ATM WDL", 500.0, account="HDFC Bank"),
        ])
        assert "_dup_seq" not in legacy_existing.columns
        backfilled = ensure_dup_seq(legacy_existing)
        assert "_dup_seq" in backfilled.columns
        assert backfilled.iloc[0]["_dup_seq"] == 0

    def test_backfill_does_not_break_existing_data_merge(self):
        legacy_existing = pd.DataFrame([make_txn("2026-07-01", "ATM WDL", 500.0, account="HDFC Bank")])
        new_upload = pd.DataFrame([make_txn("2026-07-02", "SWIGGY", 300.0, account="HDFC Bank")])
        result = merge_and_dedup(legacy_existing, new_upload)
        assert len(result) == 2


class TestDifferentAccountsNeverCollide:

    def test_same_date_desc_amount_different_account_both_kept(self):
        batch = pd.DataFrame([
            make_txn("2026-07-06", "Settlement", 500.0, account="Splitwise"),
            make_txn("2026-07-06", "Settlement", 500.0, account="HDFC Bank"),
        ])
        result = merge_and_dedup(pd.DataFrame(), batch)
        assert len(result) == 2
