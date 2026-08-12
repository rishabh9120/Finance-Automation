# Finance Automation

This project is a lightweight personal finance tracker built with Python and Streamlit.

It lets a user:
- upload bank statements (HDFC/SBI), Splitwise exports, and credit card statements (SBI Card PDF),
- clean and standardize the data into a single transaction format,
- get categories auto-suggested from Splitwise's own tags and learned keyword rules,
- review unclassified transactions in a triage queue,
- automatically (and manually) reconcile Splitwise bills-fronted/settlements and credit card bill payments against the matching bank transaction, so the same money is never double-counted as an expense,
- save the final result into an Excel workbook for ongoing tracking and reconciliation.

## Project structure

- `app.py` — Streamlit UI and file-processing workflow (UI/orchestration only)
- `logic.py` — standardization, narration cleanup, category-rule engine, and dedup logic (framework-free, unit-tested)
- `engine.py` — reconciliation logic matching Splitwise money movements to bank transactions, in both directions
- `database.py` — creates and initializes the Excel database workbook, seeds starter category rules
- `finance_tracker.xlsx` — generated workbook used as the backing database
- `Files/` — folder for bank statements and Splitwise uploads
- `tests/` — pytest suite covering every layer above with synthetic fixtures (see **Testing** below)
- `pytest.ini`, `requirements-test.txt`, `TESTING.md` — test configuration and instructions

## What the app does

1. User uploads one or more bank statement files and/or Splitwise CSVs.
2. The app standardizes the imported data into a common transaction schema, cleaning up raw UPI/IMPS/ACH narrations into readable merchant names along the way.
3. Categories get auto-suggested: Splitwise rows use Splitwise's own category (mapped onto this app's taxonomy), bank rows use learned/seeded keyword rules — anything it's confident about is pre-filled and marked reviewed; anything new is left for you in the Triage queue.
4. It merges the new transactions into the workbook, safely deduplicating even when a "refreshed" export overlaps a previous upload.
5. It runs a global reconciliation pass to identify likely Splitwise-related matches — both bills you fronted (matched to a bank debit) and settlements paid back to you (matched to a bank credit) — and excludes those from spend totals.
6. The user reviews remaining transactions in the triage queue, categorizes them (teaching the app for next time), and can manually link any transfer/settlement pairs the automatic pass missed.

## Setup

Use Python 3.10+.

Install the required packages:

```powershell
python -m pip install streamlit pandas numpy plotly openpyxl xlrd pdfplumber
```

If you are using `uv`, you can also use:

```powershell
uv pip install streamlit pandas numpy plotly openpyxl xlrd pdfplumber
```

## Run the app

From the project root:

```powershell
python -m streamlit run app.py
```

## Testing

The business logic (`logic.py`, `engine.py`) is covered by a pytest suite that runs entirely against synthetic fixtures — it never needs your real bank statements to pass.

```powershell
pip install -r requirements-test.txt
pytest
```

See `TESTING.md` for a breakdown of what each test file covers and how to run a single layer or scenario (e.g. `pytest tests/test_end_to_end.py -k updated_bank_statement -v`).

## Notes

- The first launch creates `finance_tracker.xlsx` automatically if it does not already exist, pre-seeded with a small set of high-confidence category rules.
- The workbook contains two sheets:
  - `transactions`
  - `category_rules` — grows over time as you correct categories in the Triage tab; corrections are remembered so you only ever categorize a given merchant once.
- Bank statement support is currently optimized for standard SBI and HDFC `.xlsx`/`.xls` statement layouts. CSV and PDF bank statements aren't supported yet — uploading one shows a clear message instead of silently doing nothing, and an unrecognized `.xlsx`/`.xls` layout is called out the same way.
- Credit card statement support currently covers SBI Card PDF statements. Real purchases are imported as expenses directly; the card's own "PAYMENT RECEIVED" bill-payment line is never counted as spend — it's matched against the corresponding bank debit and excluded, same as a Splitwise settlement.
- Splitwise CSVs are converted into a transaction-style format using the configured user name. Splitwise's own per-expense category is used automatically where possible; settlement ("Payment") rows are tracked separately and never counted as spend.
- Bank ↔ Splitwise / Credit Card matching allows a small rounding tolerance and works in both directions: money you fronted (bank debit) and money paid back to you (bank credit).

## Suggested workflow

1. Open the Streamlit app.
2. Upload bank statements and Splitwise CSV(s).
3. Click `Process Files`. Check any warnings for skipped files.
4. Review transactions in the `Triage Queue` tab — much of it may already be pre-categorized.
5. Correct anything wrong; it's remembered for next time.
6. Save and continue updating the workbook over time.

## Future ideas

Roadmap for making bulk/backdated import easier and automating recurring statement ingestion (see `CLAUDE.md` for the full phased breakdown):

**Near-term (backfill pain):**
- **automate the free Splitwise CSV export** rather than the official API — Splitwise's API terms allow them to require an active Splitwise Pro subscription, so it's not a reliably free path. A scheduled browser-automation script downloading the same CSV export you'd download by hand (worth knowing: more fragile than an API, and a light ToS gray area for any site) is the practical free alternative, and it slots into the same "watched folder" automation as bank/card statements below.
- a command-line ingestion script so a whole folder of historical bank/card statements can be imported in one run instead of clicking through the uploader file-by-file,
- bulk actions in the Triage queue (multi-select + apply category, accept-all for rule-matched rows),
- support for more banks and credit card issuers.

**Medium-term (automate recurring imports):**
- a watched-folder auto-importer that picks up newly downloaded bank/card statements automatically,
- email-based auto-fetch for statements issuers send every cycle (needs careful credential/security handling before it's built),
- a visible "last synced" status once ingestion runs unattended,
- move the transaction store from Excel to SQLite/PostgreSQL (more important once something other than you is writing to it).

**Long-term:**
- Account Aggregator / open banking API integration for consent-based automatic transaction pull,
- reporting dashboards, budget tracking, and anomaly detection,
- CI automation for the test suite.
