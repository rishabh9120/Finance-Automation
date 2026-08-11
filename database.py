import pandas as pd
import os

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
    if not os.path.exists(DB_FILE):
        # Create empty dataframes with our target schema
        df_transactions = pd.DataFrame(columns=[
            'date', 'description', 'description_clean', 'amount', 'type',
            'account_source', 'category', 'is_reviewed',
            'you_paid', 'you_received', 'match_notes', '_dup_seq'
        ])
        df_rules = pd.DataFrame(
            list(DEFAULT_CATEGORY_RULES.items()), columns=['keyword', 'category']
        )

        # Write to separate sheets in the same Excel file
        with pd.ExcelWriter(DB_FILE, engine='openpyxl') as writer:
            df_transactions.to_excel(writer, sheet_name='transactions', index=False)
            df_rules.to_excel(writer, sheet_name='category_rules', index=False)
        print(f"Initialized {DB_FILE} with {len(df_rules)} starter category rules")

if __name__ == "__main__":
    init_db()
