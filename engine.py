import pandas as pd

# Bank statements round-trip through CSV/Excel and can drift by a paisa or two.
# Compare amounts with a small tolerance instead of exact float equality (bug #5).
MATCH_TOLERANCE = 0.01
MATCH_WINDOW_DAYS = 2

BANK_SOURCES = ['HDFC Bank', 'SBI Bank', 'Bank']


def _isclose(a, b, tol=MATCH_TOLERANCE):
    return abs(a - b) <= tol


def _transfer_label(account_source, kind):
    """
    kind: 'paid' (you_paid -> matched to a Debit) or 'received' (you_received
    -> matched to a Credit). Per-source labels keep the Triage table
    meaningful ("this was a Splitwise bill" vs "this was a card payment")
    while staying generic enough that a future ledger type (e.g. Demat) gets
    a sensible label automatically without engine changes.
    """
    src = str(account_source)
    if src == 'Splitwise':
        return 'Transfer_Splitwise_Base' if kind == 'paid' else 'Transfer_Splitwise_Settlement'
    if 'Credit Card' in src:
        return 'Transfer_CreditCard_Payment' if kind == 'paid' else 'Transfer_CreditCard_Refund'
    return f'Transfer_{src}_{kind.capitalize()}'


def _match_pass(df, ledger_mask, bank_type, amount_col, kind):
    """
    Generic matcher: for every unmatched external-ledger row selected by
    ledger_mask (a Splitwise bill fronted, a credit card bill payment, etc.),
    look for an unmatched bank transaction of `bank_type` (Debit/Credit) on
    the corresponding side, with the same amount (within tolerance) inside
    the matching window, and link the two.
    Mutates df in place and returns it.
    """
    ledger_rows = df[ledger_mask]
    for ledger_idx, ledger_row in ledger_rows.iterrows():
        if str(ledger_row['match_notes']).startswith("🔗"):
            continue  # already linked

        ledger_date = ledger_row['date']
        ledger_amount = ledger_row[amount_col]
        if ledger_amount <= 0:
            continue

        candidates = df[
            (df['account_source'].isin(BANK_SOURCES)) &
            (df['type'] == bank_type) &
            (df['match_notes'] == "") &
            (df['date'] >= ledger_date - pd.Timedelta(days=MATCH_WINDOW_DAYS)) &
            (df['date'] <= ledger_date + pd.Timedelta(days=MATCH_WINDOW_DAYS))
        ]

        match_idx = None
        for cand_idx, cand_row in candidates.iterrows():
            if _isclose(cand_row['amount'], ledger_amount):
                match_idx = cand_idx
                break
        if match_idx is None:
            continue

        transfer_label = _transfer_label(ledger_row['account_source'], kind)

        # 1. Update the bank leg — exclude it from spend, tag how it was resolved.
        df.at[match_idx, 'type'] = transfer_label
        df.at[match_idx, 'category'] = 'Excluded'
        df.at[match_idx, 'match_notes'] = f"🔗 Matched: {ledger_row['description']}"

        # 2. Update the ledger leg with an audit trail.
        df.at[ledger_idx, 'match_notes'] = f"🔗 Matched Bank: {df.at[match_idx, 'description']}"

    return df


def run_global_reconciliation(df):
    """
    Scans the entire transaction database and matches external-ledger money
    movements (Splitwise, credit card bill payments, and any future ledger
    account) against the corresponding bank transaction, in BOTH directions:

      1. Money you fronted for another ledger (you_paid > 0) -- a Splitwise
         bill fronted for the group, or a credit card bill payment -- is
         matched against your outgoing bank Debit.
      2. Money paid back to you (you_received > 0) -- a Splitwise settlement,
         or a credit card refund routed back to your bank -- is matched
         against your incoming bank Credit.

    Matched pairs are linked via `match_notes` and the bank leg is excluded
    from spend totals so the same money isn't double-counted as both a
    ledger expense and a bank expense.
    """
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])

    for col, default in [('match_notes', ""), ('you_paid', 0.0), ('you_received', 0.0)]:
        if col not in df.columns:
            df[col] = default
    df['match_notes'] = df['match_notes'].fillna("")
    df['you_paid'] = df['you_paid'].fillna(0.0)
    df['you_received'] = df['you_received'].fillna(0.0)

    is_external_ledger = ~df['account_source'].isin(BANK_SOURCES)

    # Direction 1: money fronted for another ledger -> your outgoing bank debit.
    df = _match_pass(
        df,
        ledger_mask=is_external_ledger & (df['you_paid'] > 0),
        bank_type='Debit',
        amount_col='you_paid',
        kind='paid',
    )

    # Direction 2: money paid back to you -> your incoming bank credit.
    df = _match_pass(
        df,
        ledger_mask=is_external_ledger & (df['you_received'] > 0),
        bank_type='Credit',
        amount_col='you_received',
        kind='received',
    )

    return df
