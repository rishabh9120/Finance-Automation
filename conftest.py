"""
Shared fixtures for the finance-tracker test suite.

These build SYNTHETIC bank/Splitwise data that mimics the real file shapes
(preamble rows, header row, footer/summary rows, the exact column names each
bank uses) without depending on anyone's actual personal statements. Every
test in this suite is self-contained and safe to run on any machine.
"""
import csv
import io
import sys
import os

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import logic  # noqa: E402


# ---------------------------------------------------------------------------
# Isolated DB per test -- nothing here ever touches your real finance_tracker.xlsx
# ---------------------------------------------------------------------------
@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point both database.DB_FILE and logic.DB_FILE at a throwaway file for
    the duration of one test, and initialize it fresh (seeded starter rules)."""
    db_path = str(tmp_path / "test_finance_tracker.xlsx")
    monkeypatch.setattr(database, "DB_FILE", db_path)
    monkeypatch.setattr(logic, "DB_FILE", db_path)
    database.init_db()
    return db_path


# ---------------------------------------------------------------------------
# Synthetic HDFC .xls-shaped raw dataframe
# ---------------------------------------------------------------------------
def make_hdfc_raw_df(rows, preamble_lines=5):
    """
    rows: list of dicts with keys:
        date (str, 'dd/mm/yy'), narration, ref (optional), value_dt (optional),
        withdrawal (optional, number or ''), deposit (optional, number or ''),
        balance (optional)
    Produces a raw dataframe shaped exactly like pd.read_excel(header=None)
    would return for a real HDFC statement: letterhead preamble, a header
    row, an asterisk separator row (HDFC always has one), data rows, and a
    trailing "STATEMENT SUMMARY" footer block.
    """
    ncols = 7
    preamble = [[f"HDFC BANK LTD preamble line {i}"] + [""] * (ncols - 1) for i in range(preamble_lines)]
    header = [["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]]
    separator = [["*" * 10] * ncols]
    data = [
        [
            r["date"], r["narration"], r.get("ref", ""), r.get("value_dt", r["date"]),
            r.get("withdrawal", "") if r.get("withdrawal") else "",
            r.get("deposit", "") if r.get("deposit") else "",
            r.get("balance", ""),
        ]
        for r in rows
    ]
    footer = [
        ["STATEMENT SUMMARY :-"] + [""] * (ncols - 1),
        ["Opening Balance"] + [""] * (ncols - 1),
    ]
    return pd.DataFrame(preamble + header + separator + data + footer)


# ---------------------------------------------------------------------------
# Synthetic SBI .xlsx-shaped raw dataframe
# ---------------------------------------------------------------------------
def make_sbi_raw_df(rows, preamble_lines=4):
    """
    rows: list of dicts with keys:
        date (str, 'dd/mm/yyyy'), details, ref (optional),
        debit (optional), credit (optional), balance (optional)
    Produces a raw dataframe shaped like pd.read_excel(header=None) for a
    real SBI statement: preamble, header row (no separator row -- SBI doesn't
    have one), data rows, and a blank/footer tail.
    """
    ncols = 6
    preamble = [[f"State Bank of India preamble {i}"] + [""] * (ncols - 1) for i in range(preamble_lines)]
    header = [["Date", "Details", "Ref No/Cheque No", "Debit", "Credit", "Balance"]]
    data = [
        [
            r["date"], r["details"], r.get("ref", ""),
            r.get("debit", "") if r.get("debit") else "",
            r.get("credit", "") if r.get("credit") else "",
            r.get("balance", ""),
        ]
        for r in rows
    ]
    footer = [[None] * ncols, ["Statement Summary"] + [""] * (ncols - 1)]
    return pd.DataFrame(preamble + header + data + footer)


# ---------------------------------------------------------------------------
# Synthetic Splitwise CSV
# ---------------------------------------------------------------------------
def make_splitwise_df(rows, users):
    """
    rows: list of dicts with keys Date, Description, Category, Cost, Currency,
    plus one key per user name in `users` holding that user's net balance for
    the row (Splitwise's own sign convention: positive = owed to them).
    Returns a DataFrame identical in shape to pd.read_csv() on a real
    Splitwise "detailed" export, including support for an optional trailing
    "Total balance" summary row (Splitwise always appends one).
    """
    cols = ["Date", "Description", "Category", "Cost", "Currency"] + users
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(cols)
    for r in rows:
        writer.writerow([r.get(c, "") for c in cols])
    buf.seek(0)
    return pd.read_csv(buf)


@pytest.fixture
def splitwise_users():
    return ["Rishabh Agrawal", "Geetesh Mokhare", "AC"]


@pytest.fixture
def hdfc_raw_factory():
    return make_hdfc_raw_df


@pytest.fixture
def sbi_raw_factory():
    return make_sbi_raw_df


@pytest.fixture
def splitwise_df_factory():
    return make_splitwise_df
