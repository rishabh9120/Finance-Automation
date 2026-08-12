"""
Database initialization layer for the finance tracker.

This module owns exactly one responsibility: making sure the Excel workbook
that acts as the app's "database" (DB_FILE) exists before the rest of the
app (app.py, logic.py, engine.py) tries to read from or write to it. It does
NOT read, update, or query the workbook after creation -- that happens
elsewhere (mainly app.py and logic.py).

The workbook has two sheets:
  - 'transactions'   : the main transaction ledger (starts empty)
  - 'category_rules'  : keyword -> category rules used to auto-categorize
                         future transactions (starts pre-seeded, see
                         DEFAULT_CATEGORY_RULES below)
"""
import pandas as pd
import os

# Path/filename of the Excel workbook used as the app's persistence layer.
# Tests monkeypatch this (see conftest.py's `isolated_db` fixture) to point
# at a throwaway file so nothing in the test suite touches real user data.
DB_FILE = "finance_tracker.xlsx"

# item #1: seed a handful of high-confidence keyword -> category rules so
# the app isn't a completely cold start. Deliberately conservative -- only
# merchants with one obvious category (no Travel/Party guesses, since those
# depend on personal judgment) are seeded here. Everything else is learned
# from your own triage edits over time.
DEFAULT_CATEGORY_RULES = {
    "SWIGGY": "Eating Out", "ZOMATO": "Eating Out", "DOMINOS": "Eating Out",
    "MCDONALD": "Eating Out", "STARBUCKS": "Eating Out", "CAFE COFFEE": "Eating Out",
    "BIGBASKET": "Groceries", "BLINKIT": "Groceries", "ZEPTO": "Groceries",
    "DMART": "Groceries", "GROFERS": "Groceries",
    "UBER": "Transport", "OLA": "Transport", "RAPIDO": "Transport",
    "IRCTC": "Transport", "PETROL": "Transport", "FUEL": "Transport",
    "AMAZON": "Shopping", "FLIPKART": "Shopping", "MYNTRA": "Shopping",
    "AJIO": "Shopping", "NYKAA": "Shopping",
    "ELECTRICITY": "Utilities", "BESCOM": "Utilities", "BROADBAND": "Utilities",
    "JIO FIBER": "Utilities", "JIOFIBER": "Utilities", "AIRTEL": "Utilities",
    "CRUNCHYROLL": "Party", "BOOKMYSHOW": "Party", "BOOK MY SHOW": "Party", "NETFLIX": "Party",
    "SPOTIFY": "Party", "HOTSTAR": "Party", "PRIME VIDEO": "Party",
}


def init_db():
    """
    Ensure the Excel workbook at DB_FILE exists, creating it (with the two
    expected sheets and starter category rules) if it doesn't.

    Input:
        None (reads the module-level DB_FILE path).

    Output:
        None. Side effect only: writes a new .xlsx file to disk at DB_FILE
        if one is not already present.

    Behavior / edge cases:
        - This function is idempotent by design -- if DB_FILE already
          exists, it does nothing at all (no overwrite, no schema
          migration). This means if the schema below is changed later,
          existing workbooks will NOT automatically pick up the new
          columns/rules; only brand-new workbooks get them.
        - Called once at the top of app.py on every app startup, so in
          practice this only ever does real work the very first time the
          app is run in a given directory.
        - The 'transactions' sheet is created EMPTY (columns only, zero
          rows) -- real data only appears once the user uploads and
          processes their first file.
        - The 'category_rules' sheet is pre-seeded from
          DEFAULT_CATEGORY_RULES so the app isn't a completely cold start.
    """
    if not os.path.exists(DB_FILE):
        # Create an empty transactions table with our target schema. No
        # rows yet -- this just defines the columns future uploads will be
        # merged into (see logic.merge_and_dedup).
        df_transactions = pd.DataFrame(columns=[
            'date', 'description', 'description_clean', 'amount', 'type',
            'account_source', 'category', 'is_reviewed',
            'you_paid', 'you_received', 'match_notes', '_dup_seq'
        ])
        # Seed the rules table from the starter keyword -> category map
        # defined above, so basic auto-categorization works immediately.
        df_rules = pd.DataFrame(
            list(DEFAULT_CATEGORY_RULES.items()), columns=['keyword', 'category']
        )

        # Write both tables into the same .xlsx file as two separate sheets.
        with pd.ExcelWriter(DB_FILE, engine='openpyxl') as writer:
            df_transactions.to_excel(writer, sheet_name='transactions', index=False)
            df_rules.to_excel(writer, sheet_name='category_rules', index=False)
        print(f"Initialized {DB_FILE} with {len(df_rules)} starter category rules")

# Allows the workbook to be initialized standalone via `python database.py`,
# without needing to launch the full Streamlit app first.
if __name__ == "__main__":
    init_db()
