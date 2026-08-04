# CLAUDE.md

This file is intended to give a new AI coding assistant full project context with no prior repository knowledge.

## Repository purpose

This repository is a personal finance automation app that converts uploaded financial data into a normalized transaction database and helps reconcile expenses across bank statements and Splitwise.

The tool is primarily a Streamlit-based dashboard for:
- importing statements,
- cleaning and standardizing them,
- storing transactions in an Excel workbook,
- reviewing unmatched or unclassified records,
- running a simple reconciliation engine for Splitwise payment matching.

## High-level architecture

### 1. User interface layer
File: `app.py`

This is the main entry point.

Responsibilities:
- initialize the workbook if missing,
- present a Streamlit UI with tabs for upload, triage, and analysis,
- accept bank files and Splitwise CSVs,
- run transformation functions on uploaded data,
- merge the new data with existing workbook data,
- call the reconciliation logic from `engine.py`, and
- save the final transaction set back to the Excel file.

### 2. Data processing / normalization layer
File: `app.py`

There are helper functions inside `app.py` that standardize external data into a common schema:

- `standardize_sbi_excel(df)`
- `standardize_hdfc_excel(df)`
- `standardize_splitwise_data(df, user_name="Rishabh Agrawal")`

The goal is to turn raw external formats into a consistent transaction structure with columns such as:
- `date`
- `description`
- `amount`
- `type`
- `account_source`
- `category`
- `is_reviewed`
- `you_paid`
- `your_share`
- `match_notes`

### 3. Reconciliation engine
File: `engine.py`

This module contains `run_global_reconciliation(df)`.

Its purpose is to scan the combined transactions table and match Splitwise payments to potential bank debit transactions.

Current behavior:
- it identifies Splitwise rows where `you_paid > 0`,
- searches for a debit bank row with the same amount,
- restricts the search window to a few days around the Splitwise date,
- marks both rows as matched by writing a `match_notes` string.

Important implementation detail:
- the matching is heuristic, not a full financial ledger engine,
- it relies on amount, date proximity, and the `account_source` type,
- it is intentionally simple and meant to support human review rather than fully autonomous reconciliation.

### 4. Database initialization layer
File: `database.py`

This module defines:
- `DB_FILE = "finance_tracker.xlsx"`
- `init_db()`

`init_db()` ensures the workbook exists before the app starts. If the workbook is missing, it creates a new Excel file with two sheets:
- `transactions`
- `category_rules`

The workbook is the central persistence layer for the current MVP.

## Data flow

1. The user starts the Streamlit app with `python -m streamlit run app.py`.
2. The app imports `init_db()` from `database.py`.
3. `init_db()` creates or confirms the existence of `finance_tracker.xlsx`.
4. The user uploads bank statements and/or Splitwise CSVs.
5. `app.py` loads the uploaded files and normalizes them into consistent dataframes.
6. The normalized results are concatenated and written back into the `transactions` sheet.
7. The `run_global_reconciliation()` function is called.
8. The final dataframe is saved back to the workbook.

## Supported inputs

### Bank statements
The current normalization logic is specifically tailored to these statement structures:
- HDFC export layouts containing `Date`, `Narration`, `Withdrawal Amt.`, `Deposit Amt.`
- SBI export layouts containing `Date`, `Details`, `Debit`, `Credit`

The code attempts to locate the real header row dynamically rather than assuming a fixed position.

### Splitwise exports
The current implementation expects a Splitwise CSV that contains a user-name column, such as `Rishabh Agrawal`, and a `Cost` column.

The code derives:
- `you_paid`
- `your_share`
- `Amount`
- `Type = Debit`

The Splitwise rows are not full bank transactions; they represent the user's share or payment contribution.

## Current workbook schema

### transactions sheet
Expected columns include:
- `date`
- `description`
- `amount`
- `type`
- `account_source`
- `category`
- `is_reviewed`
- `you_paid`
- `your_share`
- `match_notes`

### category_rules sheet
This sheet stores category keyword rules.

Typical structure:
- `keyword`
- `category`

The user can review and categorize transactions manually in the UI.

## Important implementation notes

### Case normalization
The code lowercases all incoming column names before merging data:

```python
new_data.columns = [c.lower() for c in new_data.columns]
```

This means the code must consistently expect lowercase column names when reading/writing the workbook.

### Excel persistence
The app uses pandas with `openpyxl` to write the workbook, not a database engine.

This makes the project simple and human-editable, but it also means there are limitations around data integrity, scaling, and robust query capabilities.

### Heuristic matching
The reconciliation is intentionally narrow and based on assumptions:
- the same amount appears on both sides,
- transactions are within a small date window,
- the transaction has not already been matched.

This is best treated as a suggestion engine, not a final source of truth.

## Expected user workflow

A typical run looks like this:

1. Launch the app.
2. Upload SBI/HDFC statements.
3. Upload Splitwise CSVs.
4. Click `Process Files`.
5. Review the `Triage Queue` tab.
6. Mark transactions as reviewed and categorize them.
7. Keep the Excel workbook as the long-term record.

## Current limitations

The current codebase is intentionally a minimum viable personal finance workflow. It has several gaps:

- only a few bank formats are normalized,
- reconciliation is based on simple heuristics,
- no robust error recovery for malformed statement layouts,
- no clean persistence model beyond Excel,
- no true account ledger or reporting engine,
- Splitwise user-name matching is hardcoded to one display name.

## Future scope

This project has strong potential to expand into a more complete finance automation system.

### Near-term improvements
- support more bank formats (ICICI, Axis, etc.),
- support PDF and OCR-based bank statement ingestion,
- add more flexible category rules,
- improve duplicate detection and review flags,
- add a monthly/quarterly analytics dashboard.

### Medium-term improvements
- migrate from Excel to SQLite or PostgreSQL,
- store raw statement metadata, ingestion history, and audit trails,
- build a durable rule engine for automatic categorization,
- add forecasting and budget tracking.

### Long-term vision
- create a full personal finance assistant that can:
  - classify transactions automatically,
  - reconcile multiple accounts and payment sources,
  - generate expense summaries and month-end reports,
  - support recurring budgets, savings goals, and anomaly detection.

## Working assumptions for future AI edits

When modifying this project, assume the following:

1. The app is a Streamlit dashboard, not a web API.
2. The persistence layer is the workbook file `finance_tracker.xlsx`.
3. `app.py` contains both UI and some data normalization logic.
4. `engine.py` contains reconciliation heuristics and should remain fairly simple.
5. `database.py` is responsible only for workbook initialization.
6. Many parsing functions are currently format-specific and may need sensitivity to exact statement layout.

## Implementation guidance for future contributors

If you are asked to extend the project, prefer changes that:
- keep the UI lightweight and human-reviewable,
- preserve the existing workbook format for continuity,
- avoid breaking the normalization pipeline for current bank formats,
- add clear test or validation steps before changing reconciliation logic.

The codebase is best thought of as a small personal finance automation workflow rather than a polished production SaaS product.
