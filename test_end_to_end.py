"""
End-to-end tests that exercise the full pipeline the way "Process Files" in
the Streamlit app does: standardize bank + Splitwise uploads, auto-categorize,
merge/dedup against the existing DB, reconcile transfers, and persist to Excel.

These are the tests to run when you want confidence the whole system works
together, not just each piece in isolation.
"""
import os
import pandas as pd
import pytest

import database
import logic
from engine import run_global_reconciliation


def make_splitwise_ready(sw_raw_df, fallback_category="Uncategorized"):
    """Mirrors app.py's Splitwise handling exactly: standardize, then resolve
    the final Category via settlement > Splitwise-mapped > fallback."""
    clean_sw = logic.standardize_splitwise_data(sw_raw_df)
    clean_sw = clean_sw.rename(columns={"Cost": "Total Cost"})
    return logic.finalize_splitwise_category(clean_sw, fallback_category)


def run_pipeline(db_file, new_dfs):
    """Mirrors app.py's 'Process Files' button handler exactly."""
    new_data = pd.concat(new_dfs, ignore_index=True)
    new_data["is_reviewed"] = False
    new_data.columns = [c.lower() for c in new_data.columns]

    if "category" not in new_data.columns:
        new_data["category"] = "Uncategorized"
    if "you_paid" not in new_data.columns:
        new_data["you_paid"] = 0.0
    if "you_received" not in new_data.columns:
        new_data["you_received"] = 0.0
    if "match_notes" not in new_data.columns:
        new_data["match_notes"] = ""
    new_data["category"] = new_data["category"].fillna("Uncategorized")

    new_data = logic.apply_category_rules(new_data)

    existing_df = pd.read_excel(db_file, sheet_name="transactions")
    combined_df = logic.merge_and_dedup(existing_df, new_data)

    final_df = run_global_reconciliation(combined_df)

    rules_df = pd.read_excel(db_file, sheet_name="category_rules")
    with pd.ExcelWriter(db_file, engine="openpyxl") as writer:
        final_df.to_excel(writer, sheet_name="transactions", index=False)
        rules_df.to_excel(writer, sheet_name="category_rules", index=False)

    return final_df


class TestFullPipelineWithBankAndSplitwise:

    def test_bank_and_splitwise_combine_and_reconcile(self, isolated_db, hdfc_raw_factory, splitwise_df_factory, splitwise_users):
        hdfc_raw = hdfc_raw_factory([
            dict(date="10/07/26", narration="UPI/DR/111111111111/DMART/HDFC/x/Pa", withdrawal=1500.00, deposit="", balance=8500),
            dict(date="16/07/26", narration="UPI/CR/222222222222/ANURAG P/HDFC/x/Pa", withdrawal="", deposit=3000.00, balance=11500),
        ])
        hdfc_clean = logic.standardize_hdfc_excel(hdfc_raw)

        sw_rows = [
            dict(Date="2026-07-10", Description="Groceries run", Category="Groceries", Cost=1500, Currency="INR",
                 **{"Rishabh Agrawal": 1500, "Geetesh Mokhare": -750, "AC": -750}),
            dict(Date="2026-07-15", Description="AC paid Rishabh A.", Category="Payment", Cost=3000, Currency="INR",
                 **{"Rishabh Agrawal": -3000, "Geetesh Mokhare": 0, "AC": 3000}),
        ]
        sw_df = splitwise_df_factory(sw_rows, splitwise_users)
        sw_clean = make_splitwise_ready(sw_df)

        final_df = run_pipeline(isolated_db, [hdfc_clean, sw_clean])

        # The bill you fronted (1500) should have matched the bank debit
        dmart_row = final_df[final_df["description"].str.contains("DMART", case=False)]
        assert dmart_row.iloc[0]["type"] == "Transfer_Splitwise_Base"
        assert dmart_row.iloc[0]["category"] == "Excluded"

        # The settlement you received (3000) should have matched the bank credit
        credit_row = final_df[final_df["description"].str.contains("ANURAG", case=False)]
        assert credit_row.iloc[0]["type"] == "Transfer_Splitwise_Settlement"
        assert credit_row.iloc[0]["category"] == "Excluded"

        # Neither the settlement nor the transfer should count as spend
        real_expenses = final_df[(final_df["category"] != "Excluded") & (final_df["category"] != "Settlement") & (final_df["amount"] > 0)]
        assert 3000 not in real_expenses["amount"].values  # the settlement amount never appears as an expense

    def test_various_splits_end_to_end(self, isolated_db, splitwise_df_factory, splitwise_users):
        sw_rows = [
            # you fronted, 2-way split
            dict(Date="2026-07-01", Description="Dinner", Category="Dining out", Cost=1000, Currency="INR",
                 **{"Rishabh Agrawal": 500, "Geetesh Mokhare": -500, "AC": 0}),
            # someone else paid, your share
            dict(Date="2026-07-02", Description="Groceries", Category="Groceries", Cost=900, Currency="INR",
                 **{"Rishabh Agrawal": -300, "Geetesh Mokhare": 600, "AC": -300}),
            # perfectly even, net zero -> should disappear entirely
            dict(Date="2026-07-03", Description="Nothing owed", Category="General", Cost=100, Currency="INR",
                 **{"Rishabh Agrawal": 0, "Geetesh Mokhare": 0, "AC": 0}),
            # 3-way uneven, you fronted
            dict(Date="2026-07-04", Description="Road trip fuel", Category="Car", Cost=1200, Currency="INR",
                 **{"Rishabh Agrawal": 600, "Geetesh Mokhare": -400, "AC": -200}),
        ]
        sw_df = splitwise_df_factory(sw_rows, splitwise_users)
        sw_clean = make_splitwise_ready(sw_df)

        final_df = run_pipeline(isolated_db, [sw_clean])

        assert len(final_df) == 3  # the net-zero row never made it in
        # Each row's Splitwise category should now be mapped to our taxonomy
        assert set(final_df["category"]) == {"Eating Out", "Groceries", "Transport"}
        dinner = final_df[final_df["description"] == "Dinner"].iloc[0]
        assert dinner["your_share"] == 500
        fuel = final_df[final_df["description"] == "Road trip fuel"].iloc[0]
        assert fuel["your_share"] == 600


class TestUpdatedBankStatementReupload:

    def test_second_upload_with_overlapping_range_only_adds_new_and_still_reconciles(
        self, isolated_db, hdfc_raw_factory, splitwise_df_factory, splitwise_users
    ):
        # --- First upload: covers July 1-10 ---
        first_hdfc = hdfc_raw_factory([
            dict(date="01/07/26", narration="ATM WDL", withdrawal=500, deposit="", balance=9500),
            dict(date="10/07/26", narration="UPI/DR/111111111111/DMART/HDFC/x/Pa", withdrawal=1500.00, deposit="", balance=8000),
        ])
        first_clean = logic.standardize_hdfc_excel(first_hdfc)
        sw_rows = [dict(Date="2026-07-10", Description="Groceries run", Category="Groceries", Cost=1500, Currency="INR",
                         **{"Rishabh Agrawal": 1500, "Geetesh Mokhare": -750, "AC": -750})]
        sw_clean = make_splitwise_ready(splitwise_df_factory(sw_rows, splitwise_users))

        after_first = run_pipeline(isolated_db, [first_clean, sw_clean])
        assert len(after_first) == 3  # ATM + DMART(now matched) + Splitwise row
        matched_after_first = (after_first["type"] == "Transfer_Splitwise_Base").sum()
        assert matched_after_first == 1

        # --- User downloads an "updated" statement 5 days later: July 1-15,
        # which re-includes the July 1 and July 10 rows PLUS two new ones. ---
        updated_hdfc = hdfc_raw_factory([
            dict(date="01/07/26", narration="ATM WDL", withdrawal=500, deposit="", balance=9500),
            dict(date="10/07/26", narration="UPI/DR/111111111111/DMART/HDFC/x/Pa", withdrawal=1500.00, deposit="", balance=8000),
            dict(date="12/07/26", narration="SWIGGY ORDER", withdrawal=350, deposit="", balance=7650),
            dict(date="14/07/26", narration="SALARY CREDIT", withdrawal="", deposit=55000, balance=62650),
        ])
        updated_clean = logic.standardize_hdfc_excel(updated_hdfc)

        after_second = run_pipeline(isolated_db, [updated_clean])

        # 3 from before + 2 genuinely new = 5. The overlapping ATM/DMART rows
        # must NOT be duplicated.
        assert len(after_second) == 5
        assert (after_second["description"].str.contains("SALARY", case=False)).sum() == 1
        assert (after_second["description"].str.contains("SWIGGY", case=False)).sum() == 1
        # The DMART row is still correctly matched/excluded, not reset or duplicated
        dmart_rows = after_second[after_second["description"].str.contains("DMART", case=False)]
        assert len(dmart_rows) == 1
        assert dmart_rows.iloc[0]["type"] == "Transfer_Splitwise_Base"

    def test_reviewed_transactions_survive_a_reupload(self, isolated_db, hdfc_raw_factory):
        first = hdfc_raw_factory([dict(date="01/07/26", narration="ATM WDL", withdrawal=500, deposit="", balance=9500)])
        after_first = run_pipeline(isolated_db, [logic.standardize_hdfc_excel(first)])

        # user manually reviews and categorizes it
        idx = after_first.index[0]
        after_first.loc[idx, "is_reviewed"] = True
        after_first.loc[idx, "category"] = "General"
        rules_df = pd.read_excel(isolated_db, sheet_name="category_rules")
        with pd.ExcelWriter(isolated_db, engine="openpyxl") as writer:
            after_first.to_excel(writer, sheet_name="transactions", index=False)
            rules_df.to_excel(writer, sheet_name="category_rules", index=False)

        # updated statement re-includes the same transaction
        updated = hdfc_raw_factory([
            dict(date="01/07/26", narration="ATM WDL", withdrawal=500, deposit="", balance=9500),
            dict(date="02/07/26", narration="NEW TXN", withdrawal=100, deposit="", balance=9400),
        ])
        after_second = run_pipeline(isolated_db, [logic.standardize_hdfc_excel(updated)])

        assert len(after_second) == 2
        atm_row = after_second[after_second["description"].str.contains("ATM", case=False)].iloc[0]
        assert atm_row["is_reviewed"] == True  # noqa: E712
        assert atm_row["category"] == "General"


class TestMultiBankUpload:

    def test_hdfc_and_sbi_together(self, isolated_db, hdfc_raw_factory, sbi_raw_factory):
        hdfc_clean = logic.standardize_hdfc_excel(hdfc_raw_factory([
            dict(date="01/07/26", narration="HDFC txn", withdrawal=100, deposit="", balance=900),
        ]))
        sbi_clean = logic.standardize_sbi_excel(sbi_raw_factory([
            dict(date="02/07/2026", details="SBI txn", debit=200, credit="", balance=800),
        ]))
        final_df = run_pipeline(isolated_db, [hdfc_clean, sbi_clean])
        assert len(final_df) == 2
        assert set(final_df["account_source"]) == {"HDFC Bank", "SBI Bank"}


class TestPersistenceRoundTrip:

    def test_data_survives_excel_round_trip(self, isolated_db, hdfc_raw_factory):
        raw = hdfc_raw_factory([dict(date="01/07/26", narration="ATM WDL", withdrawal=500, deposit="", balance=9500)])
        run_pipeline(isolated_db, [logic.standardize_hdfc_excel(raw)])

        reloaded = pd.read_excel(isolated_db, sheet_name="transactions")
        assert len(reloaded) == 1
        assert reloaded.iloc[0]["amount"] == 500
        assert pd.notna(reloaded.iloc[0]["date"])


# ---------------------------------------------------------------------------
# Optional golden-master test against the real sample files used during
# development. Skipped automatically if those files aren't present -- this
# suite never requires anyone's personal statements to pass.
# ---------------------------------------------------------------------------
REAL_FILES_DIR = "/mnt/user-data/uploads"
REAL_HDFC = os.path.join(REAL_FILES_DIR, "HDFC_Acct_Statement_6312_03082026_21_48_07.xls")
REAL_SBI = os.path.join(REAL_FILES_DIR, "AccountStatement_03082026_23336.xlsx")
REAL_SW = os.path.join(REAL_FILES_DIR, "Splitwise_expenses_Aug_3.csv")


@pytest.mark.skipif(
    not (os.path.exists(REAL_HDFC) and os.path.exists(REAL_SBI) and os.path.exists(REAL_SW)),
    reason="Real sample statements not present in this environment",
)
class TestGoldenMasterRealFiles:

    def test_known_row_count_and_no_phantom_settlement_expense(self, isolated_db):
        hdfc_clean = logic.standardize_hdfc_excel(pd.read_excel(REAL_HDFC, header=None))
        sbi_clean = logic.standardize_sbi_excel(pd.read_excel(REAL_SBI, header=None))
        sw_clean = make_splitwise_ready(pd.read_csv(REAL_SW))

        final_df = run_pipeline(isolated_db, [hdfc_clean, sbi_clean, sw_clean])

        assert len(final_df) == 238
        settlement_rows = final_df[final_df["category"] == "Settlement"]
        assert (settlement_rows["amount"] == 0).all()
        assert not (final_df["description"].str.lower() == "total balance").any()
