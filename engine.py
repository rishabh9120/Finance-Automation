import pandas as pd

# Bank statements round-trip through CSV/Excel and can drift by a paisa or two.
# Compare amounts with a small tolerance instead of exact float equality (bug #5).
MATCH_TOLERANCE = 0.01
MATCH_WINDOW_DAYS = 2

BANK_SOURCES = ['HDFC Bank', 'SBI Bank', 'Bank']


def _isclose(a, b, tol=MATCH_TOLERANCE):
    return abs(a - b) <= tol


def _match_pass(df, sw_mask, bank_type, amount_col, transfer_label):
    """
    Generic matcher: for every unmatched Splitwise row selected by sw_mask,
    look for an unmatched bank transaction of `bank_type` (Debit/Credit) on
    the corresponding side, with the same amount (within tolerance) inside
    the matching window, and link the two.
    Mutates df in place and returns it.
    """
    sw_rows = df[sw_mask]
    for sw_idx, sw_row in sw_rows.iterrows():
        if str(sw_row['match_notes']).startswith("🔗"):
            continue  # already linked

        sw_date = sw_row['date']
        sw_amount = sw_row[amount_col]
        if sw_amount <= 0:
            continue

        candidates = df[
            (df['account_source'].isin(BANK_SOURCES)) &
            (df['type'] == bank_type) &
            (df['match_notes'] == "") &
            (df['date'] >= sw_date - pd.Timedelta(days=MATCH_WINDOW_DAYS)) &
            (df['date'] <= sw_date + pd.Timedelta(days=MATCH_WINDOW_DAYS))
        ]

        match_idx = None
        for cand_idx, cand_row in candidates.iterrows():
            if _isclose(cand_row['amount'], sw_amount):
                match_idx = cand_idx
                break
        if match_idx is None:
            continue

        # 1. Update the bank leg — exclude it from spend, tag how it was resolved.
        df.at[match_idx, 'type'] = transfer_label
        df.at[match_idx, 'category'] = 'Excluded'
        df.at[match_idx, 'match_notes'] = f"🔗 Matched SW: {sw_row['description']}"

        # 2. Update the Splitwise leg with an audit trail.
        df.at[sw_idx, 'match_notes'] = f"🔗 Matched Bank: {df.at[match_idx, 'description']}"

    return df


def run_global_reconciliation(df):
    """
    Scans the entire transaction database and matches Splitwise money
    movements against the corresponding bank transaction, in BOTH
    directions:

      1. Bills you fronted for the group (you_paid > 0)  <->  Bank Debit
      2. Settlements a roommate paid back to you (you_received > 0) <-> Bank Credit

    Matched pairs are linked via `match_notes` and the bank leg is
    excluded from spend totals so the same money isn't double-counted
    as both a Splitwise expense and a bank expense.
    """
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])

    for col, default in [('match_notes', ""), ('you_paid', 0.0), ('you_received', 0.0)]:
        if col not in df.columns:
            df[col] = default
    df['match_notes'] = df['match_notes'].fillna("")
    df['you_paid'] = df['you_paid'].fillna(0.0)
    df['you_received'] = df['you_received'].fillna(0.0)

    # Direction 1: group bills you fronted -> your outgoing bank debit.
    df = _match_pass(
        df,
        sw_mask=(df['account_source'] == 'Splitwise') & (df['you_paid'] > 0),
        bank_type='Debit',
        amount_col='you_paid',
        transfer_label='Transfer_Splitwise_Base',
    )

    # Direction 2 (bug #6 fix): settlements paid back to you -> your incoming bank credit.
    df = _match_pass(
        df,
        sw_mask=(df['account_source'] == 'Splitwise') & (df['you_received'] > 0),
        bank_type='Credit',
        amount_col='you_received',
        transfer_label='Transfer_Splitwise_Settlement',
    )

    return df
