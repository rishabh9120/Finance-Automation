"""
Pure, framework-free business logic for the finance tracker: bank-statement
standardization, credit card statement standardization, Splitwise
standardization, manual entry, narration cleanup, and the category-rule
engine. Deliberately has ZERO dependency on Streamlit (or DB_FILE-writing
side effects beyond reading category_rules) so it can be unit-tested
directly, without faking a browser session.

`app.py` imports everything it needs for the UI from this module.
"""
import re
import pandas as pd
import numpy as np
from database import DB_FILE

# --- item #2: narration cleanup -------------------------------------------
# Raw UPI/IMPS/ACH narrations are noisy ("UPI/DR/613858185975/VIKKY BA/YESB/
# paytmq r71o/UPI 0097690162095 AT 30525 IIM CAMPUS INDORE"). Keyword category
# rules need the merchant/payee name, not reference numbers and bank codes.
_NARRATION_NOISE_PREFIXES = [
    r'^WDL\s*TFR\s*', r'^DEP\s*TFR\s*', r'^ME\s*DC\s*SI\s*',
    r'^ACH\s*C-?\s*', r'^ACH\s*D-?\s*',
    r'^IMPS-?\s*', r'^NEFT-?\s*', r'^RTGS-?\s*',
    r'^UPI/(DR|CR)/',
]
_BANK_IFSC_CODES = {
    'HDFC', 'SBIN', 'UTIB', 'ICIC', 'YESB', 'UBIN', 'AXIS', 'KKBK', 'PYTM', 'IDFB', 'IOBA', 'CNRB',
}
_GENERIC_NARRATION_TOKENS = {
    'UPI', 'PAID', 'PAY', 'PA', 'AT', 'SI', 'TFR', 'DR', 'CR', 'VIA', 'REF', 'NO', 'CAMPUS', 'INDORE',
}


def clean_bank_narration(description):
    """
    Best-effort strip of reference numbers, masked card/account digits, bank
    IFSC-style codes, and generic UPI/IMPS boilerplate from a raw bank
    narration, leaving the merchant/payee name so category rules can match
    reliably. Falls back to the original text if nothing meaningful survives.
    """
    text = str(description or "").replace('\n', ' ').replace('\r', ' ').strip()
    if not text:
        return ""

    for pattern in _NARRATION_NOISE_PREFIXES:
        text = re.sub(pattern, ' ', text, flags=re.IGNORECASE)

    # split on slash, hyphen, AND whitespace so a merchant name buried in a
    # space-separated narration (e.g. "...6366 SPOTIFY SI") gets isolated too.
    tokens = re.split(r'[\/\-\s]+', text)
    cleaned_tokens = []
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        upper = tok.upper()
        # drop pure reference numbers / masked card fragments (4+ chars of digits/X)
        if re.fullmatch(r'[\dX]{4,}', upper):
            continue
        # drop tokens that are mostly digits (transaction ids, phone-like ids)
        digits = sum(c.isdigit() for c in upper)
        if len(upper) >= 4 and digits / len(upper) > 0.5:
            continue
        if upper in _BANK_IFSC_CODES or upper in _GENERIC_NARRATION_TOKENS:
            continue
        cleaned_tokens.append(tok)

    result = re.sub(r'\s{2,}', ' ', ' '.join(cleaned_tokens)).strip()
    return result or text


# --- item #1: wire up category_rules for real ------------------------------
# The rules sheet existed in the schema from day one but nothing ever read or
# wrote to it -- every transaction had to be categorized manually, every time.

def load_category_rules():
    """Load the persisted keyword -> category rules as an upper-cased dict."""
    rules_df = pd.read_excel(DB_FILE, sheet_name='category_rules')
    if rules_df.empty:
        return {}
    return dict(zip(rules_df['keyword'].astype(str).str.upper().str.strip(), rules_df['category']))


def suggest_category(description_clean, rules):
    """Return the best matching category for a cleaned description, or None."""
    if not rules:
        return None
    upper = str(description_clean or "").upper()
    if not upper:
        return None
    # longest keyword first, so a specific match wins over a shorter, looser one
    for keyword in sorted(rules.keys(), key=len, reverse=True):
        if keyword and keyword in upper:
            return rules[keyword]
    return None


def extract_rule_keyword(description_clean):
    """Turn a cleaned description into a short, reusable keyword for learning."""
    text = str(description_clean or "").upper()
    tokens = [t for t in re.split(r'\s+', text) if len(t) > 2]
    return ' '.join(tokens[:3]).strip()


def apply_category_rules(df):
    """
    Auto-fill category for rows still sitting at 'Uncategorized' using saved
    rules, matched against a cleaned version of the description. Rows a rule
    matches are marked reviewed, so the Triage queue only surfaces genuinely
    new merchants -- not ones you've already taught the app.
    """
    rules = load_category_rules()
    if 'description_clean' not in df.columns:
        df['description_clean'] = df['description'].apply(clean_bank_narration)

    needs_category = df['category'].astype(str).str.strip().isin(['', 'Uncategorized', 'nan'])
    if needs_category.any() and rules:
        suggested = df.loc[needs_category, 'description_clean'].apply(lambda d: suggest_category(d, rules))
        matched = suggested.notna()
        df.loc[needs_category & df.index.isin(suggested[matched].index), 'category'] = suggested[matched]
        df.loc[suggested[matched].index, 'is_reviewed'] = True
    return df

# Splitwise tags every expense with its own category. Map those onto this app's
# taxonomy so each row gets a sensible category automatically instead of every
# row in a file sharing one blanket category picked at upload time (item #4).
SPLITWISE_CATEGORY_MAP = {
    "groceries": "Groceries",
    "dining out": "Eating Out",
    "food and drink": "Eating Out",
    "car": "Transport",
    "taxi": "Transport",
    "parking": "Transport",
    "bus/train": "Transport",
    "gas/fuel": "Transport",
    "electricity": "Utilities",
    "heat/gas": "Utilities",
    "water": "Utilities",
    "trash": "Utilities",
    "tv/phone/internet": "Utilities",
    "household supplies": "Home Setup",
    "cleaning": "Home Setup",
    "furniture": "Home Setup",
    "electronics": "Home Setup",
    "rent": "Rent",
    "mortgage": "Rent",
    "clothing": "Shopping",
    "liquor": "Party",
    "entertainment": "Party",
    "movies": "Party",
    "music": "Party",
    "games": "Party",
    "hotel": "Travel - Weekend",
    "flight": "Travel - Major",
    "vacation": "Travel - Major",
    "medical expenses": "General",
    "life insurance": "General",
    "general": "General",
}


def map_splitwise_category(raw_category):
    """Look up this app's category for a Splitwise-native category label. Returns
    None (not '' ) when there's no mapping, so callers can fall back cleanly."""
    key = str(raw_category or "").strip().lower()
    return SPLITWISE_CATEGORY_MAP.get(key)


# --- bug #8 fix: stable dedup key -------------------------------------------
# A plain (date, description, amount, account) key collapses two *genuinely
# different* same-day transactions with identical text/amount (e.g. two ₹121
# "Auto" rides) into one. Tagging each row with its occurrence index within
# its own upload batch -- computed BEFORE merging/sorting -- means a re-upload
# of the same file reproduces the same indices and still dedupes correctly,
# while distinct look-alike rows keep their own identity.
DEDUP_COLUMNS = ['date', 'description', 'amount', 'account_source']


def ensure_dup_seq(frame):
    """Attach a stable per-batch occurrence index used for deduplication."""
    frame = frame.copy()
    if '_dup_seq' not in frame.columns or frame['_dup_seq'].isna().any():
        frame['_dup_seq'] = frame.groupby(DEDUP_COLUMNS).cumcount()
    else:
        frame['_dup_seq'] = frame['_dup_seq'].astype(int)
    return frame


def merge_and_dedup(existing_df, new_data):
    """
    Combine already-saved transactions with newly uploaded ones, dropping
    true duplicates (e.g. re-uploading the same statement, or a "refreshed"
    export whose date range overlaps a previous upload) while preserving
    genuinely distinct look-alike transactions.
    """
    new_data = ensure_dup_seq(new_data)
    if existing_df is not None and not existing_df.empty:
        existing_df = ensure_dup_seq(existing_df)
        combined_df = pd.concat([existing_df, new_data], ignore_index=True)
        combined_df = combined_df.sort_values(by='is_reviewed', ascending=False)
        combined_df = combined_df.drop_duplicates(subset=DEDUP_COLUMNS + ['_dup_seq'], keep='first')
    else:
        combined_df = new_data
    return combined_df.reset_index(drop=True)


def standardize_sbi_excel(df):
    """
    Cleans and standardizes SBI Excel bank statements.
    """
    # 1. Find the actual header row (look for "Details" or "Debit" in any column)
    header_idx = None
    for idx, row in df.iterrows():
        row_str = row.astype(str).str.lower()
        if 'details' in row_str.values and 'debit' in row_str.values:
            header_idx = idx
            break

    if header_idx is None:
        raise ValueError("Could not find the header row containing 'Details' and 'Debit'. Ensure this is a valid SBI statement.")

    # 2. Rebuild the dataframe starting from the actual header
    new_cols = df.iloc[header_idx].astype(str).str.strip().tolist()
    clean_df = df.iloc[header_idx + 1:].copy()
    clean_df.columns = new_cols

    # 3. Drop rows that are entirely NaN or footer metadata
    clean_df = clean_df.dropna(how='all')
    # Filter out empty dates or summary lines at the bottom
    clean_df = clean_df.dropna(subset=['Date'])
    clean_df = clean_df[clean_df['Date'].astype(str).str.strip() != '']

    # 4. Standardize column names
    column_mapping = {
        "Date": "Date",
        "Details": "Description",
        "Ref No/Cheque No": "Reference",
        "Debit": "Debit_Amount",
        "Credit": "Credit_Amount",
        "Balance": "Balance"
    }
    clean_df = clean_df.rename(columns=column_mapping)

    # 5. Clean up description text -- item #2: extract the merchant/payee name
    # out of raw UPI/IMPS/ACH boilerplate so category rules have something to match.
    clean_df['Description'] = clean_df['Description'].astype(str).apply(clean_bank_narration)

    # 6. Clean and Merge Amounts
    if 'Debit_Amount' in clean_df.columns and 'Credit_Amount' in clean_df.columns:
        # Convert amounts to numeric, handle missing values
        clean_df['Debit_Amount'] = pd.to_numeric(clean_df['Debit_Amount'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        clean_df['Credit_Amount'] = pd.to_numeric(clean_df['Credit_Amount'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)

        # Unified Amount and Type columns
        clean_df['Amount'] = np.where(clean_df['Debit_Amount'] > 0, clean_df['Debit_Amount'], clean_df['Credit_Amount'])
        clean_df['Type'] = np.where(clean_df['Debit_Amount'] > 0, 'Debit', 'Credit')

    # 7. Final cleanup
    # SBI dates are usually DD/MM/YYYY or DD-MM-YYYY
    clean_df['Date'] = pd.to_datetime(clean_df['Date'], dayfirst=True, errors='coerce')
    clean_df = clean_df.dropna(subset=['Date', 'Amount'])
    clean_df['Account_Source'] = 'SBI Bank'

    return clean_df


# --- Credit card statements ------------------------------------------------
# A credit card bill payment shows up in the bank statement as one lump debit
# with no spending detail. Importing the card's own statement gives real,
# itemized, categorizable transactions -- and the bank-side "bill payment"
# becomes a transfer to exclude (same shape as the Splitwise you_paid <->
# bank Debit match), not a second copy of the expense.
_SBI_CARD_TXN_LINE_RE = re.compile(
    r'^(\d{1,2}\s+[A-Za-z]{3}\s+\d{2})\s+(.+?)\s+([\d,]+\.\d{2})\s+(C|D)\s*$'
)


def parse_sbi_card_lines(lines):
    """
    Pure parser: turns a list of text lines (as extracted from an SBI Card
    PDF statement, e.g. one page's text split on '\\n') into standardized
    transaction rows. Kept separate from PDF I/O so it's unit-testable with
    plain strings, no PDF file required.

    Three kinds of line appear on an SBI Card statement:
      - "...PAYMENT RECEIVED... <amount> C" -- money paid from your bank
        account to settle the bill. Never a real expense; becomes you_paid
        so it can be matched against the corresponding bank Debit.
      - any other "...<amount> C" line -- a refund/reversal credited back
        to the card. Reduces spend, but isn't a bank transfer.
      - "...<amount> D" -- a real purchase/expense.
    """
    rows = []
    for raw_line in lines:
        line = raw_line.strip()
        m = _SBI_CARD_TXN_LINE_RE.match(line)
        if not m:
            continue

        date_str, desc, amount_str, flag = m.groups()
        date = pd.to_datetime(date_str, format='%d %b %y', errors='coerce')
        if pd.isna(date):
            continue

        amount = float(amount_str.replace(',', ''))
        desc_clean = clean_bank_narration(desc)
        is_bill_payment = (flag == 'C') and ('PAYMENT RECEIVED' in desc.upper())

        if is_bill_payment:
            rows.append(dict(
                Date=date, Description=desc_clean, Amount=0.0, Type='Settlement',
                Account_Source='SBI Credit Card', Category='Settlement',
                you_paid=amount, you_received=0.0, Match_Notes="",
            ))
        elif flag == 'C':
            # refund / reversal -- a real credit, not a transfer
            rows.append(dict(
                Date=date, Description=desc_clean, Amount=amount, Type='Credit',
                Account_Source='SBI Credit Card', Category=None,
                you_paid=0.0, you_received=0.0, Match_Notes="",
            ))
        else:
            rows.append(dict(
                Date=date, Description=desc_clean, Amount=amount, Type='Debit',
                Account_Source='SBI Credit Card', Category=None,
                you_paid=0.0, you_received=0.0, Match_Notes="",
            ))

    if not rows:
        raise ValueError(
            "No recognizable SBI Card transactions found in this statement. "
            "Expected lines like 'DD Mon YY <description> <amount> C|D'."
        )

    return pd.DataFrame(rows)


def standardize_sbi_card_pdf(pdf_file):
    """
    Extracts and standardizes transactions from an SBI Card credit card PDF
    statement. `pdf_file` can be a path or a file-like object (e.g. a
    Streamlit UploadedFile) -- anything pdfplumber.open() accepts.
    """
    import pdfplumber
    lines = []
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines.extend(text.split("\n"))
    return parse_sbi_card_lines(lines)


def standardize_hdfc_excel(df):
    """
    Cleans and standardizes HDFC Excel bank statements.
    """
    # 1. Find the actual header row (look for "Narration" or "Date" in any column)
    header_idx = None
    for idx, row in df.iterrows():
        row_str = row.astype(str).str.lower()
        if 'narration' in row_str.values and 'date' in row_str.values:
            header_idx = idx
            break

    if header_idx is None:
        raise ValueError("Could not find the header row containing 'Date' and 'Narration'. Ensure this is a valid HDFC statement.")

    # 2. Rebuild the dataframe starting from the actual header
    new_cols = df.iloc[header_idx].astype(str).str.strip().tolist()
    clean_df = df.iloc[header_idx + 2:].copy()
    clean_df.columns = new_cols

    # 3. Drop rows that are entirely NaN or just trailing metadata
    clean_df = clean_df.dropna(how='all')
    clean_df = clean_df[~clean_df['Date'].astype(str).str.contains('Statement', case=False, na=False)]

    # 4. Standardize the column names
    column_mapping = {
        "Date": "Date",
        "Narration": "Description",
        "Chq./Ref.No.": "Reference",
        "Value Dt": "Value_Date",
        "Withdrawal Amt.": "Debit_Amount",
        "Deposit Amt.": "Credit_Amount",
        "Closing Balance": "Balance"
    }

    clean_df = clean_df.rename(columns=column_mapping)

    # 5. Clean up description text -- item #2: extract the merchant/payee name
    # out of raw UPI/IMPS/ACH boilerplate so category rules have something to match.
    clean_df['Description'] = clean_df['Description'].astype(str).apply(clean_bank_narration)

    # 6. Clean and Merge Amounts
    if 'Debit_Amount' in clean_df.columns and 'Credit_Amount' in clean_df.columns:
        clean_df['Debit_Amount'] = pd.to_numeric(clean_df['Debit_Amount'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        clean_df['Credit_Amount'] = pd.to_numeric(clean_df['Credit_Amount'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)

        clean_df['Amount'] = np.where(clean_df['Debit_Amount'] > 0, clean_df['Debit_Amount'], clean_df['Credit_Amount'])
        clean_df['Type'] = np.where(clean_df['Debit_Amount'] > 0, 'Debit', 'Credit')

    # 7. Final cleanup of essential columns
    clean_df['Date'] = pd.to_datetime(clean_df['Date'], format='%d/%m/%y', errors='coerce')
    clean_df = clean_df.dropna(subset=['Date', 'Amount'])

    # Explicitly tag this data as coming from the Bank
    clean_df['Account_Source'] = 'HDFC Bank'

    return clean_df


def standardize_splitwise_data(df, user_name="Rishabh Agrawal"):
    """
    Converts the Splitwise net balance matrix into standardized transaction rows.

    Three kinds of money movement come out of a Splitwise export, and they
    need to be treated differently:
      - A group bill you fronted -> 'you_paid' (matched to a bank Debit)
      - A settlement paid back to you -> 'you_received' (matched to a bank Credit)
      - Your real personal share of an expense -> 'your_share' / 'Amount'
    Settlements ("Payment" rows) are never a real expense (bug #2 fix) -- they're
    just cash moving between people and must not inflate category spend.

    This same row shape (Date, Description, Category, Cost, Currency, plus
    one column per group member holding their net balance for that expense)
    is what both a Splitwise CSV "detailed" export produces AND what
    splitwise_api.expenses_to_dataframe() builds from the live API -- so
    this function doesn't care which source `df` came from.
    """
    if user_name not in df.columns:
        raise ValueError(f"User '{user_name}' not found in Splitwise columns. Please check your exact Splitwise display name.")

    df = df.copy()

    # --- bug #1 fix: drop Splitwise's own running-balance summary row and any
    # blank/footer rows before doing any arithmetic on them. ---
    df['Description'] = df['Description'].astype(str).str.strip()
    df = df[df['Description'].str.lower() != 'total balance']
    df = df[df['Category'].notna() & (df['Category'].astype(str).str.strip() != '')]
    df = df[df['Date'].notna()]

    df['Cost'] = pd.to_numeric(df['Cost'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)

    you_paid, your_share, you_received, txn_type, settlement_category, category_hint = [], [], [], [], [], []

    for _, row in df.iterrows():
        cost = row['Cost']
        net = row[user_name]
        if pd.isna(net):
            net = 0
        is_settlement = str(row['Category']).strip().lower() == 'payment'

        if is_settlement:
            # bug #2 fix: settlements move cash, they are not an expense.
            paid = cost if net > 0 else 0.0       # you paid a roommate back
            received = cost if net < 0 else 0.0   # a roommate paid you back
            share = 0.0
            ttype = 'Settlement'
            cat = 'Settlement'
        else:
            received = 0.0
            if net == 0:
                paid, share = 0.0, 0.0
            elif net > 0:
                # You paid the bill, you are owed the difference
                paid, share = cost, cost - net
            else:
                # Someone else paid, you owe them this amount
                paid, share = 0.0, abs(net)
            ttype = 'Debit'
            cat = None  # keep the group-level default category chosen at upload

        you_paid.append(paid)
        your_share.append(share)
        you_received.append(received)
        txn_type.append(ttype)
        settlement_category.append(cat)
        # item #4: map Splitwise's own per-row category into our taxonomy.
        category_hint.append(map_splitwise_category(row['Category']))

    df['you_paid'] = you_paid
    df['your_share'] = your_share
    df['you_received'] = you_received
    df['Type'] = txn_type
    df['_settlement_category'] = settlement_category  # None unless it's a settlement row
    df['_category_hint'] = category_hint  # None unless Splitwise's category mapped cleanly

    # 'Amount' is always your true personal expense share -- 0 for settlements.
    df['Amount'] = your_share
    df['Account_Source'] = 'Splitwise'
    df['Match_Notes'] = ""

    # Keep: real personal expenses, bills you fronted, or settlements -- all three
    # need to either show up in Analysis or be matched against the bank leg.
    df = df[(df['Amount'] > 0) | (df['you_paid'] > 0) | (df['you_received'] > 0)].copy()

    return df


def finalize_splitwise_category(df, fallback_category="Uncategorized"):
    """
    Resolve the final 'Category' column for standardized Splitwise rows
    (output of standardize_splitwise_data), in priority order:
      1. Forced 'Settlement' category (bug #2) for Payment rows
      2. Splitwise's own category mapped to our taxonomy (item #4)
      3. The fallback category chosen at upload time for this file
    """
    df = df.copy()
    df['Category'] = (
        df['_settlement_category']
        .fillna(df['_category_hint'])
        .fillna(fallback_category)
    )
    return df.drop(columns=['_settlement_category', '_category_hint'])


# --- Manual entry ------------------------------------------------------------
# Backdated cash spends, or anything with no statement/export to upload at
# all, need a way in that doesn't go through the standardize_* pipeline.
# A manual entry skips narration cleanup and header-detection entirely --
# the user is typing an already-readable description -- but still flows
# through the exact same apply_category_rules -> merge_and_dedup ->
# run_global_reconciliation -> save pipeline as every other source, so it
# benefits from learned category rules and safe re-entry deduplication.
MANUAL_ENTRY_ACCOUNT_SOURCE = "Manual Entry"


def create_manual_entry_row(date, description, amount, txn_type, category="Uncategorized",
                             account_source=MANUAL_ENTRY_ACCOUNT_SOURCE):
    """
    Build a single standardized transaction row for a manually entered
    transaction (typically a backdated cash expense, or anything with no
    statement/CSV to upload).

    Input:
        date: str or datetime-like -- the transaction date.
        description (str): free-text description. Cannot be blank.
        amount (float): must be a positive number.
        txn_type (str): 'Debit' or 'Credit'.
        category (str, optional): defaults to 'Uncategorized' so it still
            gets a chance to pick up a learned category rule via
            apply_category_rules() during the normal upload pipeline.
        account_source (str, optional): defaults to MANUAL_ENTRY_ACCOUNT_
            SOURCE ('Manual Entry') so these rows are visibly
            distinguishable in the Triage queue / Analysis, and are
            automatically excluded from BANK_SOURCES in engine.py -- they
            can never accidentally be treated as a bank leg, and (being
            outside BANK_SOURCES) they're technically eligible to be
            reconciled as an "external ledger" too, though in practice a
            manual entry has no you_paid/you_received amount set, so
            run_global_reconciliation has nothing to match for it.

    Output:
        pd.DataFrame with exactly one row, in the same column shape the
        other standardize_* functions produce (Date, Description, Amount,
        Type, Account_Source, Category) -- ready to be lower-cased and fed
        into merge_and_dedup exactly like any file-derived DataFrame.

    Edge cases:
        - Raises ValueError if description is blank, amount is missing or
          not strictly positive, or txn_type isn't 'Debit'/'Credit' -- a
          zero/blank manual entry is never meaningful (mirrors the
          net == 0 filtering already done for Splitwise rows).
        - Does NOT run clean_bank_narration() on the description, since
          there's no UPI/IMPS boilerplate to strip from user-typed text.
    """
    description = str(description or "").strip()
    if not description:
        raise ValueError("Description cannot be blank for a manual entry.")
    if amount is None or amount <= 0:
        raise ValueError("Amount must be a positive number for a manual entry.")
    if txn_type not in ("Debit", "Credit"):
        raise ValueError("txn_type must be 'Debit' or 'Credit'.")

    parsed_date = pd.to_datetime(date, errors='coerce')
    if pd.isna(parsed_date):
        raise ValueError(f"Could not parse manual entry date: {date!r}")

    return pd.DataFrame([{
        "Date": parsed_date,
        "Description": description,
        "Amount": float(amount),
        "Type": txn_type,
        "Account_Source": account_source,
        "Category": category or "Uncategorized",
    }])
