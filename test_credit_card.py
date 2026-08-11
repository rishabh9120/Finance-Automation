"""
Tests for logic.parse_sbi_card_lines / standardize_sbi_card_pdf (credit card
statement support), and for the generalized engine matching that now covers
credit card bill payments alongside Splitwise.
"""
import pandas as pd
import pytest
from logic import parse_sbi_card_lines, standardize_sbi_card_pdf
from engine import run_global_reconciliation


def cc_line(date, desc, amount, flag):
    """Build a line exactly as pdfplumber extracts it from an SBI Card statement."""
    return f"{date} {desc} {amount} {flag}"


class TestParseSBICardLines:

    def test_real_statement_lines_parse_correctly(self):
        # Exact lines from a real SBI Card AURUM statement
        lines = [
            "24 Jul 26 PAYMENT RECEIVED 000DP016205124952bcq9GO 9,240.00 C",
            "05 Aug 26 PAYMENT RECEIVED 000DP016217150516zsqqmM 17,000.00 C",
            "TRANSACTIONS FOR RISHABH M AGRAWAL",
            "23 Jul 26 RAZ*Crunchyroll Mumbai MH IN 475.00 D",
            "30 Jul 26 BOOK MY SHOW SMART G MUMBAI IN 53.10 D",
            "01 Aug 26 ASSPL Bangalore IN (Pay in EMIs) 8,158.00 D",
        ]
        result = parse_sbi_card_lines(lines)
        assert len(result) == 5  # the section header line is correctly ignored

    def test_payment_received_becomes_you_paid_not_an_expense(self):
        lines = ["24 Jul 26 PAYMENT RECEIVED 000DP016205124952bcq9GO 9,240.00 C"]
        result = parse_sbi_card_lines(lines)
        row = result.iloc[0]
        assert row["Amount"] == 0
        assert row["you_paid"] == 9240.00
        assert row["Type"] == "Settlement"
        assert row["Category"] == "Settlement"

    def test_purchase_becomes_a_real_debit_expense(self):
        lines = ["23 Jul 26 RAZ*Crunchyroll Mumbai MH IN 475.00 D"]
        result = parse_sbi_card_lines(lines)
        row = result.iloc[0]
        assert row["Amount"] == 475.00
        assert row["Type"] == "Debit"
        assert row["you_paid"] == 0

    def test_non_payment_credit_is_a_refund_not_a_transfer(self):
        # A merchant refund, not a "PAYMENT RECEIVED" bill payment
        lines = ["10 Jul 26 REFUND RAZORPAY SOFTWARE 200.00 C"]
        result = parse_sbi_card_lines(lines)
        row = result.iloc[0]
        assert row["Type"] == "Credit"
        assert row["Amount"] == 200.00
        assert row["you_paid"] == 0  # not treated as a bank transfer

    def test_account_source_tagged(self):
        lines = ["23 Jul 26 RAZ*Crunchyroll Mumbai MH IN 475.00 D"]
        result = parse_sbi_card_lines(lines)
        assert result.iloc[0]["Account_Source"] == "SBI Credit Card"

    def test_description_is_cleaned(self):
        lines = ["23 Jul 26 RAZ*Crunchyroll Mumbai MH IN 475.00 D"]
        result = parse_sbi_card_lines(lines)
        # Should not choke on/mangle plain merchant text
        assert "Crunchyroll" in result.iloc[0]["Description"]

    def test_thousands_separator_amount_parsed_correctly(self):
        lines = ["01 Aug 26 ASSPL Bangalore IN (Pay in EMIs) 8,158.00 D"]
        result = parse_sbi_card_lines(lines)
        assert result.iloc[0]["Amount"] == 8158.00

    def test_date_parsed_correctly(self):
        lines = ["04 Aug 26 BOOK MY SHOW SMART G MUMBAI IN 1,205.44 D"]
        result = parse_sbi_card_lines(lines)
        d = pd.Timestamp(result.iloc[0]["Date"])
        assert (d.day, d.month, d.year) == (4, 8, 2026)

    def test_non_transaction_lines_ignored(self):
        lines = [
            "GSTIN of SBI Card : 06AAECS5981K1ZV",
            "ACCOUNT SUMMARY",
            "Previous Balance 9,240.17",
            "for Statement Period: 07 Jul 26 to 06 Aug 26",
        ]
        with pytest.raises(ValueError, match="No recognizable SBI Card transactions"):
            parse_sbi_card_lines(lines)

    def test_finance_charge_illustration_table_not_mistaken_for_transactions(self):
        # These lines resemble transaction lines (dates + rupee amounts) but
        # come from the T&C illustration section, not the transaction table.
        lines = [
            "Finance Charge on `1,200 from 30th April to 20th May (21 Days) ` 31.07",
            "On 21st May, the Cardholder pays Minimum Amount Due of ` 200.",
        ]
        with pytest.raises(ValueError):
            parse_sbi_card_lines(lines)

    def test_empty_input_raises(self):
        with pytest.raises(ValueError):
            parse_sbi_card_lines([])

    def test_multiple_payments_and_purchases_mixed(self):
        lines = [
            "24 Jul 26 PAYMENT RECEIVED ABC123 9,240.00 C",
            "23 Jul 26 RAZ*Crunchyroll Mumbai MH IN 475.00 D",
            "05 Aug 26 PAYMENT RECEIVED XYZ456 17,000.00 C",
            "04 Aug 26 BOOK MY SHOW SMART G MUMBAI IN 1,205.44 D",
        ]
        result = parse_sbi_card_lines(lines)
        assert len(result) == 4
        assert (result["Type"] == "Settlement").sum() == 2
        assert (result["Type"] == "Debit").sum() == 2


class TestStandardizeSBICardPDF:

    def test_wires_pdfplumber_extraction_to_the_parser(self, monkeypatch):
        """Verify the PDF I/O wrapper correctly feeds extracted text into the
        parser, without needing a real PDF file."""
        import logic

        class FakePage:
            def extract_text(self):
                return "23 Jul 26 RAZ*Crunchyroll Mumbai MH IN 475.00 D"

        class FakePDF:
            pages = [FakePage()]
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class FakePdfplumber:
            @staticmethod
            def open(f):
                return FakePDF()

        monkeypatch.setitem(__import__("sys").modules, "pdfplumber", FakePdfplumber)
        result = logic.standardize_sbi_card_pdf("fake_path.pdf")
        assert len(result) == 1
        assert result.iloc[0]["Amount"] == 475.00

    def test_real_pdf_file_parses_correctly(self):
        """Uses the actual sample SBI Card PDF if present; skipped otherwise."""
        import os
        real_pdf = "/mnt/user-data/uploads/CardStatement_2026-08-06.pdf"
        if not os.path.exists(real_pdf):
            pytest.skip("Real sample PDF not present in this environment")
        result = standardize_sbi_card_pdf(real_pdf)
        assert len(result) == 7
        assert (result["Type"] == "Settlement").sum() == 2
        assert (result["Type"] == "Debit").sum() == 5
        # Cross-check against the statement's own printed totals
        assert result[result["Type"] == "Debit"]["Amount"].sum() == pytest.approx(16595.54)
        assert result[result["Type"] == "Settlement"]["you_paid"].sum() == pytest.approx(26240.00)


def txn(date, description, amount, ttype, account_source, category="Uncategorized",
        is_reviewed=False, you_paid=0.0, you_received=0.0, match_notes=""):
    return dict(date=date, description=description, amount=amount, type=ttype,
                account_source=account_source, category=category, is_reviewed=is_reviewed,
                you_paid=you_paid, you_received=you_received, match_notes=match_notes)


class TestCreditCardReconciliation:
    """The engine must treat a credit card bill payment exactly like a
    Splitwise bill fronted: match to a bank Debit, exclude from spend."""

    def test_credit_card_bill_payment_matches_bank_debit(self):
        df = pd.DataFrame([
            txn("2026-08-05", "PAYMENT RECEIVED", 0, "Settlement", "SBI Credit Card", you_paid=17000.00),
            txn("2026-08-05", "SBI CARD BILL PAYMENT", 17000.00, "Debit", "HDFC Bank"),
        ])
        result = run_global_reconciliation(df)
        assert result.iloc[1]["type"] == "Transfer_CreditCard_Payment"
        assert result.iloc[1]["category"] == "Excluded"

    def test_credit_card_purchases_are_never_matched(self):
        # A real purchase (you_paid=0) must never be swept into a transfer match
        df = pd.DataFrame([
            txn("2026-07-23", "Crunchyroll", 475.00, "Debit", "SBI Credit Card"),
            txn("2026-07-23", "Unrelated bank debit", 475.00, "Debit", "HDFC Bank"),
        ])
        result = run_global_reconciliation(df)
        assert result.iloc[1]["match_notes"] == ""  # not matched -- no you_paid on the card row

    def test_splitwise_and_credit_card_matching_coexist(self):
        df = pd.DataFrame([
            txn("2026-07-10", "Groceries bill", 0, "Debit", "Splitwise", you_paid=1500.00),
            txn("2026-07-10", "UPI-DMART", 1500.00, "Debit", "HDFC Bank"),
            txn("2026-08-05", "PAYMENT RECEIVED", 0, "Settlement", "SBI Credit Card", you_paid=17000.00),
            txn("2026-08-05", "SBI CARD BILL PAYMENT", 17000.00, "Debit", "HDFC Bank"),
        ])
        result = run_global_reconciliation(df)
        assert result.iloc[1]["type"] == "Transfer_Splitwise_Base"
        assert result.iloc[3]["type"] == "Transfer_CreditCard_Payment"

    def test_credit_card_tolerance_matching(self):
        df = pd.DataFrame([
            txn("2026-08-05", "PAYMENT RECEIVED", 0, "Settlement", "SBI Credit Card", you_paid=17000.00),
            txn("2026-08-05", "SBI CARD BILL PAYMENT", 16999.99, "Debit", "HDFC Bank"),
        ])
        result = run_global_reconciliation(df)
        assert result.iloc[1]["type"] == "Transfer_CreditCard_Payment"

    def test_two_card_payments_in_one_statement_both_match_independently(self):
        df = pd.DataFrame([
            txn("2026-07-24", "PAYMENT RECEIVED", 0, "Settlement", "SBI Credit Card", you_paid=9240.00),
            txn("2026-08-05", "PAYMENT RECEIVED", 0, "Settlement", "SBI Credit Card", you_paid=17000.00),
            txn("2026-07-24", "SBI CARD BILL PAYMENT", 9240.00, "Debit", "HDFC Bank"),
            txn("2026-08-05", "SBI CARD BILL PAYMENT", 17000.00, "Debit", "HDFC Bank"),
        ])
        result = run_global_reconciliation(df)
        matched = result[result["type"] == "Transfer_CreditCard_Payment"]
        assert len(matched) == 2
