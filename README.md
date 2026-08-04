# Finance Automation

This project is a lightweight personal finance tracker built with Python and Streamlit.

It lets a user:
- upload bank statements and Splitwise exports,
- clean and standardize the data into a single transaction format,
- review unclassified transactions in a triage queue,
- save the final result into an Excel workbook for ongoing tracking and reconciliation.

## Project structure

- `app.py` — Streamlit UI and file-processing workflow
- `engine.py` — reconciliation logic for matching Splitwise payments to bank debits
- `database.py` — creates and initializes the Excel database workbook
- `finance_tracker.xlsx` — generated workbook used as the backing database
- `Files/` — folder for bank statements and Splitwise uploads

## What the app does

1. User uploads one or more bank statement files and/or Splitwise CSVs.
2. The app standardizes the imported data into a common transaction schema.
3. It merges the new transactions into the workbook.
4. It runs a global reconciliation pass to identify likely Splitwise-related matches.
5. The user reviews remaining transactions in the triage queue and categorizes them.

## Setup

Use Python 3.10+.

Install the required packages:

```powershell
python -m pip install streamlit pandas numpy plotly openpyxl
```

If you are using `uv`, you can also use:

```powershell
uv pip install streamlit pandas numpy plotly openpyxl
```

## Run the app

From the project root:

```powershell
python -m streamlit run app.py
```

## Notes

- The first launch creates `finance_tracker.xlsx` automatically if it does not already exist.
- The workbook contains two sheets:
  - `transactions`
  - `category_rules`
- Bank statement support is currently optimized for standard SBI and HDFC statement layouts.
- Splitwise CSVs are converted into a transaction-style format using the configured user name.

## Suggested workflow

1. Open the Streamlit app.
2. Upload bank statements and Splitwise CSV(s).
3. Click `Process Files`.
4. Review transactions in the `Triage Queue` tab.
5. Save and continue updating the workbook over time.

## Future ideas

- add support for more banks and statement formats,
- improve categorization rules,
- add reporting dashboards for monthly spend analysis,
- move the transaction store from Excel to SQLite or a proper database backend.
