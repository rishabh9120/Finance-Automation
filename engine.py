import pandas as pd

def run_global_reconciliation(df):
    """
    Scans the entire transaction database to match Splitwise payments 
    with unlinked Bank debits.
    """
    df['date'] = pd.to_datetime(df['date'])
    if 'match_notes' not in df.columns: 
        df['match_notes'] = ""
    if 'you_paid' not in df.columns: 
        df['you_paid'] = 0.0
    
    df['match_notes'] = df['match_notes'].fillna("")
    df['you_paid'] = df['you_paid'].fillna(0.0)
    
    sw_payments = df[(df['account_source'] == 'Splitwise') & (df['you_paid'] > 0)]
    
    for sw_idx, sw_row in sw_payments.iterrows():
        # Skip if this Splitwise row is already matched (manually or previously)
        if str(sw_row['match_notes']).startswith("🔗"):
            continue
            
        sw_date = sw_row['date']
        sw_amount = sw_row['you_paid']
        
        mask = (
            (df['account_source'].isin(['HDFC Bank', 'SBI Bank', 'Bank'])) &
            (df['type'] == 'Debit') &
            (df['amount'] == sw_amount) &
            (df['date'] >= sw_date - pd.Timedelta(days=2)) &
            (df['date'] <= sw_date + pd.Timedelta(days=2)) &
            (df['match_notes'] == "") 
        )
        
        if mask.any():
            match_idx = df[mask].index[0]
            
            # 1. Update Bank Row
            df.at[match_idx, 'type'] = 'Transfer_Splitwise_Base'
            df.at[match_idx, 'category'] = 'Excluded'
            df.at[match_idx, 'match_notes'] = f"🔗 Matched SW: {sw_row['description']}"
            
            # 2. Update Splitwise Row
            df.at[sw_idx, 'match_notes'] = f"🔗 Matched Bank: {df.at[match_idx, 'description']}"
            
    return df