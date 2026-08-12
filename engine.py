"""
Reconciliation engine for the finance tracker.

This module's only job is to look at the combined transactions table
(bank rows + external "ledger" rows -- Splitwise and credit card statements,
already standardized into one schema by logic.py) and automatically link
each ledger money movement to its matching bank transaction, so the same
real-world payment doesn't get counted twice (once as a ledger expense,
once as a bank debit/credit).

It is intentionally a heuristic "best guess" matcher, not a full ledger
engine: it matches on amount (within a small tolerance) and date proximity
only. It's meant to catch the obvious/common cases and leave the rest for
either the app's manual-matcher UI or human review in the Triage queue.
"""
import pandas as pd

# Bank statements round-trip through CSV/Excel and can drift by a paisa or two
# (e.g. rounding when a bank exports 1500.00 as 1499.99). Compare amounts with
# a small tolerance instead of exact float equality (bug #5).
MATCH_TOLERANCE = 0.01

# How many days before/after the ledger transaction date to look for a
# matching bank transaction. A Splitwise expense (or a credit card bill
# payment) and its corresponding bank debit/credit rarely land on the exact
# same calendar day, so a small window is used instead of requiring an exact
# date match.
MATCH_WINDOW_DAYS = 2

# The set of `account_source` values that count as "a real bank account" for
# matching purposes. Anything NOT in this list (Splitwise, any credit card
# source, or any future ledger type) is treated as an external ledger whose
# money movements need reconciling against one of these.
BANK_SOURCES = ['HDFC Bank', 'SBI Bank', 'Bank']


def _isclose(a, b, tol=MATCH_TOLERANCE):
    """
    Compare two transaction amounts for equality within MATCH_TOLERANCE.

    Input:
        a, b (float): the two amounts to compare.
        tol (float, optional): tolerance in rupees. Defaults to
            MATCH_TOLERANCE (0.01, i.e. one paisa).

    Output:
        bool: True if the two amounts are within `tol` of each other.

    Edge cases:
        - Exists specifically so reconciliation doesn't fail on tiny
          rounding drift between how an amount was recorded in an external
          ledger vs. how it appears in an exported bank statement (bug #5).
          Using plain `a == b` here would cause otherwise-correct matches
          to be silently missed.
    """
    return abs(a - b) <= tol


def _transfer_label(account_source, kind):
    """
    Decide what `type` value to write into a matched bank row, based on
    which external ledger it was matched against and which direction the
    money moved.

    Input:
        account_source (str): the `account_source` of the ledger row that
            was matched (e.g. 'Splitwise', 'SBI Credit Card').
        kind (str): 'paid' (a you_paid row matched to a bank Debit) or
            'received' (a you_received row matched to a bank Credit).

    Output:
        str: a label like 'Transfer_Splitwise_Base',
        'Transfer_CreditCard_Payment', etc.

    Edge cases / rationale:
        - Per-source labels keep the Triage table meaningful ("this was a
          Splitwise bill" vs "this was a card payment") while staying
          generic enough that a future ledger type (e.g. a Demat/brokerage
          feed) gets a sensible label automatically, without any engine.py
          changes -- it falls through to the generic
          f'Transfer_{src}_{kind.capitalize()}' pattern.
        - 'Credit Card' is matched as a substring of account_source rather
          than an exact value, since different card issuers could each
          have their own Account_Source (e.g. 'SBI Credit Card', 'HDFC
          Credit Card') and should all resolve to the same
          Transfer_CreditCard_* labels.
    """
    src = str(account_source)
    if src == 'Splitwise':
        return 'Transfer_Splitwise_Base' if kind == 'paid' else 'Transfer_Splitwise_Settlement'
    if 'Credit Card' in src:
        return 'Transfer_CreditCard_Payment' if kind == 'paid' else 'Transfer_CreditCard_Refund'
    return f'Transfer_{src}_{kind.capitalize()}'


def _match_pass(df, ledger_mask, bank_type, amount_col, kind):
    """
    Run one direction of matching: for every unmatched external-ledger row
    selected by `ledger_mask` (a Splitwise bill fronted, a credit card bill
    payment, etc.), look for an unmatched bank transaction of `bank_type`
    (Debit/Credit) with the same amount (within tolerance) inside the
    date-matching window, and link the two together.

    This is a shared helper used by run_global_reconciliation() to run the
    same matching logic for every external ledger and in both directions
    (money fronted / money received back) without duplicating the matching
    code itself.

    Input:
        df (pd.DataFrame): the full combined transactions table. Must
            already have 'date' as a datetime column and 'match_notes',
            'you_paid'/'you_received' columns present (run_global_
            reconciliation guarantees this before calling here).
        ledger_mask (pd.Series[bool]): boolean mask selecting which rows of
            df are the ledger-side candidates for this pass (e.g. rows
            where account_source is not a bank and you_paid > 0).
        bank_type (str): the `type` value a candidate bank row must have,
            e.g. 'Debit' or 'Credit'.
        amount_col (str): which column on the ledger row holds the amount
            to match against (e.g. 'you_paid' or 'you_received').
        kind (str): 'paid' or 'received' -- passed through to
            _transfer_label() to pick the right label for the matched bank
            row.

    Output:
        pd.DataFrame: the same `df`, mutated in place AND returned, with
        `type`, `category`, and `match_notes` updated on any rows that got
        matched this pass.

    Matching rule / edge cases:
        - Skips ledger rows already linked (match_notes starts with the 🔗
          marker), so re-running reconciliation doesn't create duplicate or
          conflicting links (idempotent).
        - Skips ledger rows with a non-positive amount in `amount_col`
          (nothing to match against).
        - A "candidate" bank row must: belong to a real bank account
          (BANK_SOURCES), have the exact `bank_type` (Debit vs Credit are
          never cross-matched), have no existing match_notes, and fall
          within MATCH_WINDOW_DAYS of the ledger row's date.
        - Among multiple same-amount candidates on/near the same date, the
          FIRST one encountered (in df's current row order) is taken --
          there's no "best" match logic, just first-fit. This is why the
          docstring on run_global_reconciliation calls this a heuristic,
          not a full ledger engine.
        - Once a bank row is matched, its `type` is overwritten with the
          label from _transfer_label() and its `category` forced to
          'Excluded' so it stops counting as ordinary spend/income (it
          would otherwise be double-counted alongside the ledger expense
          it corresponds to). This is a real mutation of the row's
          meaning, not just a note -- by design, since the whole point is
          to remove the duplicate from spend totals.
    """
    ledger_rows = df[ledger_mask]
    for ledger_idx, ledger_row in ledger_rows.iterrows():
        if str(ledger_row['match_notes']).startswith("🔗"):
            continue  # already linked

        ledger_date = ledger_row['date']
        ledger_amount = ledger_row[amount_col]
        if ledger_amount <= 0:
            continue

        # Narrow down to bank rows that are plausible matches: right
        # account type, right debit/credit direction, not already matched,
        # and within the allowed date window either side of ledger_date.
        candidates = df[
            (df['account_source'].isin(BANK_SOURCES)) &
            (df['type'] == bank_type) &
            (df['match_notes'] == "") &
            (df['date'] >= ledger_date - pd.Timedelta(days=MATCH_WINDOW_DAYS)) &
            (df['date'] <= ledger_date + pd.Timedelta(days=MATCH_WINDOW_DAYS))
        ]

        # Take the first candidate whose amount is close enough. First-fit,
        # not best-fit -- see docstring above for why that's acceptable here.
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
    account) against the corresponding bank transaction, in BOTH
    directions:

      1. Money you fronted for another ledger (you_paid > 0) -- a Splitwise
         bill fronted for the group, or a credit card bill payment -- is
         matched against your outgoing bank Debit.
      2. Money paid back to you (you_received > 0) -- a Splitwise
         settlement, or a credit card refund routed back to your bank --
         is matched against your incoming bank Credit.

    Matched pairs are linked via `match_notes` and the bank leg is
    excluded from spend totals so the same money isn't double-counted
    as both a ledger expense and a bank expense.

    Input:
        df (pd.DataFrame): the combined transactions table (bank rows +
            all external ledger rows), as read from the workbook /
            produced by merge_and_dedup. Does not need to already have
            'date' parsed as datetime, or the match_notes/you_paid/
            you_received columns present -- this function normalizes all
            of that itself.

    Output:
        pd.DataFrame: a COPY of `df` (the input is never mutated) with any
        newly-discovered matches applied.

    Edge cases:
        - Safe to call on an empty DataFrame or one with no bank rows at
          all -- both matching passes simply find zero candidates and the
          frame passes through unchanged.
        - Idempotent: calling this twice in a row on the same data
          produces the same result, since already-linked rows (🔗 prefix
          in match_notes) are always skipped.
        - "External ledger" is defined as anything NOT in BANK_SOURCES, so
          adding a new bank format only requires adding it to
          BANK_SOURCES; every other account_source (Splitwise, any credit
          card issuer, or a manual-entry account) is automatically treated
          as a ledger to reconcile.
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
