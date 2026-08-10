"""
Unit tests for logic.standardize_hdfc_excel and logic.standardize_sbi_excel.
Uses synthetic raw dataframes shaped exactly like a real export (preamble,
header row, separator/footer rows) so these tests never depend on anyone's
actual bank statement.
"""
import pandas as pd
import pytest
from logic import standardize_hdfc_excel, standardize_sbi_excel


class TestHDFCStandardize:

    def test_finds_header_amid_preamble_and_parses_rows(self, hdfc_raw_factory):
        raw = hdfc_raw_factory([
            dict(date="14/05/26", narration="UPI/DR/123456789012/SWIGGY LTD/UTIB/x/Pa", withdrawal=450, deposit="", balance=10000),
            dict(date="15/05/26", narration="SALARY CREDIT NEFT", withdrawal="", deposit=55000, balance=65000),
        ])
        result = standardize_hdfc_excel(raw)
        assert len(result) == 2
        assert result["Account_Source"].eq("HDFC Bank").all()

    def test_debit_credit_merged_into_amount_and_type(self, hdfc_raw_factory):
        raw = hdfc_raw_factory([
            dict(date="14/05/26", narration="ATM WDL", withdrawal=500, deposit="", balance=9500),
            dict(date="15/05/26", narration="SALARY", withdrawal="", deposit=50000, balance=59500),
        ])
        result = standardize_hdfc_excel(raw)
        debit_row = result[result["Type"] == "Debit"].iloc[0]
        credit_row = result[result["Type"] == "Credit"].iloc[0]
        assert debit_row["Amount"] == 500
        assert credit_row["Amount"] == 50000

    def test_dates_parsed_as_ddmmyy(self, hdfc_raw_factory):
        raw = hdfc_raw_factory([dict(date="01/12/26", narration="Test txn", withdrawal=100, deposit="", balance=900)])
        result = standardize_hdfc_excel(raw)
        d = pd.Timestamp(result.iloc[0]["Date"])
        assert (d.day, d.month, d.year) == (1, 12, 2026)

    def test_statement_summary_footer_excluded(self, hdfc_raw_factory):
        raw = hdfc_raw_factory([dict(date="14/05/26", narration="ATM WDL", withdrawal=500, deposit="", balance=9500)])
        result = standardize_hdfc_excel(raw)
        assert not result["Description"].str.contains("STATEMENT SUMMARY", case=False, na=False).any()

    def test_description_is_cleaned(self, hdfc_raw_factory):
        raw = hdfc_raw_factory([
            dict(date="14/05/26", narration="WDL TFR   UPI/DR/613858185975/VIKKY BA/YESB/x/Pa   9999999999 AT 30525 IIM CAMPUS INDORE",
                 withdrawal=179, deposit="", balance=9821)
        ])
        result = standardize_hdfc_excel(raw)
        desc = result.iloc[0]["Description"]
        assert "613858185975" not in desc
        assert "VIKKY BA" in desc

    def test_missing_header_raises_value_error(self):
        # A file with no recognizable HDFC header at all
        junk = pd.DataFrame([["not", "a", "statement"], ["just", "random", "text"]])
        with pytest.raises(ValueError, match="Narration"):
            standardize_hdfc_excel(junk)

    def test_rows_with_missing_amount_are_dropped(self, hdfc_raw_factory):
        raw = hdfc_raw_factory([
            dict(date="14/05/26", narration="Valid txn", withdrawal=100, deposit="", balance=900),
            dict(date="", narration="Malformed row with no date", withdrawal="", deposit="", balance=""),
        ])
        result = standardize_hdfc_excel(raw)
        assert len(result) == 1


class TestSBIStandardize:

    def test_finds_header_amid_preamble_and_parses_rows(self, sbi_raw_factory):
        raw = sbi_raw_factory([
            dict(date="18/05/2026", details="UPI/DR/123456789012/DMART/HDFC/x/Pa", debit=250, credit="", balance=5000),
            dict(date="19/05/2026", details="DEP TFR UPI/CR/987654321098/FRIEND/HDFC/x/Pa", debit="", credit=1000, balance=6000),
        ])
        result = standardize_sbi_excel(raw)
        assert len(result) == 2
        assert result["Account_Source"].eq("SBI Bank").all()

    def test_debit_credit_merged_into_amount_and_type(self, sbi_raw_factory):
        raw = sbi_raw_factory([
            dict(date="18/05/2026", details="Withdrawal", debit=250, credit="", balance=5000),
            dict(date="19/05/2026", details="Deposit", debit="", credit=1000, balance=6000),
        ])
        result = standardize_sbi_excel(raw)
        debit_row = result[result["Type"] == "Debit"].iloc[0]
        credit_row = result[result["Type"] == "Credit"].iloc[0]
        assert debit_row["Amount"] == 250
        assert credit_row["Amount"] == 1000

    def test_dates_parsed_dayfirst(self, sbi_raw_factory):
        raw = sbi_raw_factory([dict(date="05/03/2026", details="Test", debit=100, credit="", balance=900)])
        result = standardize_sbi_excel(raw)
        d = pd.Timestamp(result.iloc[0]["Date"])
        assert (d.day, d.month, d.year) == (5, 3, 2026)

    def test_blank_footer_rows_excluded(self, sbi_raw_factory):
        raw = sbi_raw_factory([dict(date="18/05/2026", details="Withdrawal", debit=250, credit="", balance=5000)])
        result = standardize_sbi_excel(raw)
        assert result["Description"].notna().all()
        assert len(result) == 1

    def test_description_is_cleaned(self, sbi_raw_factory):
        raw = sbi_raw_factory([
            dict(date="18/05/2026",
                 details="WDL TFR   UPI/DR/657804158042/Swiggy Ltd/UTIB/x/Pa   9999999999 AT 30525 IIM CAMPUS INDORE",
                 debit=450, credit="", balance=5000)
        ])
        result = standardize_sbi_excel(raw)
        desc = result.iloc[0]["Description"]
        assert "657804158042" not in desc
        assert "Swiggy" in desc

    def test_missing_header_raises_value_error(self):
        junk = pd.DataFrame([["not", "a", "statement"], ["just", "random", "text"]])
        with pytest.raises(ValueError, match="Details"):
            standardize_sbi_excel(junk)
