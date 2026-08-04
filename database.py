import pandas as pd
import os

DB_FILE = "finance_tracker.xlsx"

def init_db():
    if not os.path.exists(DB_FILE):
        # Create empty dataframes with our target schema
        df_transactions = pd.DataFrame(columns=[
            'date', 'description', 'amount', 'type', 
            'account_source', 'category', 'is_reviewed'
        ])
        df_rules = pd.DataFrame(columns=['keyword', 'category'])
        
        # Write to separate sheets in the same Excel file
        with pd.ExcelWriter(DB_FILE, engine='openpyxl') as writer:
            df_transactions.to_excel(writer, sheet_name='transactions', index=False)
            df_rules.to_excel(writer, sheet_name='category_rules', index=False)
        print(f"Initialized {DB_FILE}")

if __name__ == "__main__":
    init_db()