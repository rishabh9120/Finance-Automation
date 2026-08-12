# CLAUDE.md

This file is intended to give a new AI coding assistant full project context with no prior repository knowledge.

## Repository purpose

This repository is a personal finance automation app that converts uploaded financial data into a normalized transaction database and helps reconcile expenses across bank statements and Splitwise.

The tool is primarily a Streamlit-based dashboard for:
- importing statements,
- cleaning and standardizing them,
- auto-suggesting categories and learning from corrections,
- storing transactions in an Excel workbook,
- reviewing unmatched or unclassified records,
- running a reconciliation engine for Splitwise payment matching (bills fronted **and** settlements received).

A pytest suite (`tests/`) covers the business logic directly — see "Testing" below before changing anything in `logic.py` or `engine.py`.

## High-level architecture

### 1. User interface layer
File: `app.py`

This is the main entry point. As of the `logic.py` extraction, **`app.py` contains only Streamlit UI and orchestration** — no standalone business logic lives here anymore.

Responsibilities:
- initialize the workbook if missing,
- present a Streamlit UI with tabs for upload, triage, and analysis,
- accept bank files and Splitwise CSVs,
- call the standardization/categorization functions from `logic.py`,
- merge the new data with existing workbook data via `logic.merge_and_dedup`,
- call the reconciliation logic from `engine.py`, and
- save the final transaction set back to the Excel file.

### 2. Data processing / normalization / categorization layer
File: `logic.py`

**This module has zero Streamlit dependency on purpose**, so it can be unit-tested directly without faking a browser session (`from logic import ...` just works in a plain Python/pytest process). `app.py` imports everything it needs from here.

Standardizers (raw upload → common transaction schema):
- `standardize_sbi_excel(df)`
- `standardize_hdfc_excel(df)`
- `standardize_splitwise_data(df, user_name="Rishabh Agrawal")`
- `finalize_splitwise_category(df, fallback_category)` — resolves the final Splitwise `Category` (settlement → Splitwise-mapped → fallback); must be called after `standardize_splitwise_data`, not skipped, or rows keep an internal `_settlement_category`/`_category_hint` pair instead of a real category.
- `parse_sbi_card_lines(lines)` / `standardize_sbi_card_pdf(pdf_file)` — credit card statement support. `parse_sbi_card_lines` is the pure, unit-testable parser (takes a list of text lines); `standardize_sbi_card_pdf` is a thin `pdfplumber`-based I/O wrapper around it. Currently covers SBI Card's PDF layout only. A "PAYMENT RECEIVED..." line becomes `you_paid` (a transfer to match against a bank Debit, `Category` forced to `'Settlement'`), any other Credit-flagged line is a refund/reversal (`Type='Credit'`, a real credit but not a bank transfer), and Debit-flagged lines are real purchases (`Type='Debit'`).

Narration cleanup:
- `clean_bank_narration(description)` — strips UPI/IMPS/ACH/card-mandate boilerplate down to a merchant/payee name.

Category rules engine:
- `load_category_rules()` — reads the `category_rules` sheet into a `{keyword: category}` dict.
- `suggest_category(description_clean, rules)` — longest-keyword-wins substring match.
- `extract_rule_keyword(description_clean)` — reduces a description to a short, reusable key for learning. **Precondition: expects already-cleaned text** (i.e. the output of `clean_bank_narration`); it does not itself strip embedded digits, so feeding it raw narrations with reference numbers will not produce stable keys across transactions.
- `apply_category_rules(df)` — fills `Uncategorized` rows from saved rules and marks matched rows `is_reviewed = True` (so the Triage queue only surfaces genuinely new merchants).
- `map_splitwise_category(raw_category)` / `SPLITWISE_CATEGORY_MAP` — maps Splitwise's own per-expense category onto this app's taxonomy. Note: `"Payment"` is deliberately **not** in this map — settlement rows are categorized via the separate `is_settlement` branch inside `standardize_splitwise_data`, not through this map.

Dedup:
- `DEDUP_COLUMNS`, `ensure_dup_seq(frame)`, `merge_and_dedup(existing_df, new_data)` — see "Deduplication" below.

The goal is to turn raw external formats into a consistent transaction structure with columns such as:
- `date`
- `description` / `description_clean`
- `amount`
- `type`
- `account_source`
- `category`
- `is_reviewed`
- `you_paid`
- `you_received`
- `your_share`
- `match_notes`
- `_dup_seq`

### 3. Reconciliation engine
File: `engine.py`

This module contains `run_global_reconciliation(df)`.

Its purpose is to scan the combined transactions table and match money movements from any **external ledger** (Splitwise, credit card statements, and any future ledger type) against bank transactions, **in both directions**:

1. Rows where `you_paid > 0` (a Splitwise bill you fronted, or a credit card bill payment) are matched against a bank **Debit** of the same amount.
2. Rows where `you_received > 0` (a Splitwise settlement paid back to you, or a credit card refund routed to your bank) are matched against a bank **Credit** of the same amount.

The mask for "external ledger" is simply `~account_source.isin(BANK_SOURCES)` — anything that isn't a recognized bank source. This means a new ledger type (e.g. Demat) automatically participates in matching the moment its standardizer sets `you_paid`/`you_received`, with no `engine.py` changes required. Per-source transfer labels come from `_transfer_label(account_source, kind)`: `Transfer_Splitwise_Base`/`Transfer_Splitwise_Settlement` for Splitwise, `Transfer_CreditCard_Payment`/`Transfer_CreditCard_Refund` for anything with `"Credit Card"` in its account source, and a generic `Transfer_{source}_{kind}` fallback otherwise.

Current behavior:
- amounts are compared with a small tolerance (`MATCH_TOLERANCE = 0.01`), not exact float equality — Excel/CSV round-tripping can drift by a paisa,
- the search window is `MATCH_WINDOW_DAYS = 2` around the Splitwise date,
- a row already carrying a `match_notes` value starting with `🔗` is never re-matched, so re-running reconciliation is idempotent,
- matching is per-direction via the shared `_match_pass()` helper — a `you_paid` row can only match a `Debit`, a `you_received` row can only match a `Credit`; they are never cross-wired even if amounts coincide.

Important implementation detail:
- the matching is heuristic, not a full financial ledger engine,
- it relies on amount (within tolerance), date proximity, `type`, and `account_source`,
- it is intentionally simple and meant to support human review rather than fully autonomous reconciliation.

### 4. Database initialization layer
File: `database.py`

This module defines:
- `DB_FILE = "finance_tracker.xlsx"`
- `DEFAULT_CATEGORY_RULES` — a conservative starter set of high-confidence keyword → category rules (unambiguous merchants only; no Travel/Party guessing) seeded into `category_rules` on first run.
- `init_db()`

`init_db()` ensures the workbook exists before the app starts. If the workbook is missing, it creates a new Excel file with two sheets:
- `transactions` (empty, with the target schema — actual saved columns can exceed this once real data flows through, since `to_excel` persists whatever columns the pipeline produced)
- `category_rules` (pre-seeded with `DEFAULT_CATEGORY_RULES`)

The workbook is the central persistence layer for the current MVP.

### 5. Test suite
Directory: `tests/` (+ `pytest.ini`, `requirements-test.txt`, `TESTING.md`)

Covers every layer above using **synthetic fixtures** (`tests/conftest.py` builds fake HDFC/SBI/Splitwise files shaped like the real exports) — the suite never depends on anyone's actual bank data. There is one optional golden-master test (`TestGoldenMasterRealFiles` in `test_end_to_end.py`) that runs against real sample files if they happen to be present at a hardcoded path, and auto-skips otherwise.

Run `pytest` before and after any change to `logic.py` or `engine.py`.

## Data flow

1. The user starts the Streamlit app with `python -m streamlit run app.py`.
2. The app imports `init_db()` from `database.py`.
3. `init_db()` creates or confirms the existence of `finance_tracker.xlsx`, seeding starter category rules if new.
4. The user uploads bank statements and/or Splitwise CSVs.
5. `app.py` calls `logic.standardize_hdfc_excel` / `standardize_sbi_excel` / `standardize_splitwise_data` + `finalize_splitwise_category` on each file. Unsupported file types (CSV/PDF bank statements) or unrecognized bank layouts are collected into a `skipped_files` list and surfaced via `st.warning`/`st.error` instead of silently vanishing.
6. `logic.apply_category_rules` auto-fills categories for anything still `Uncategorized` (mainly bank rows — Splitwise rows already got a category from step 5) and marks confidently-matched rows as reviewed.
7. `logic.merge_and_dedup` combines the new data with the existing workbook, using a stable per-batch occurrence index (`_dup_seq`) so re-uploading an overlapping/refreshed statement only adds genuinely new rows.
8. `engine.run_global_reconciliation()` is called on the combined data.
9. The final dataframe (transactions) and the rules dataframe (category_rules, possibly updated with newly learned rules from a Triage save) are written back to the workbook.

## Supported inputs

### Bank statements
The current normalization logic is specifically tailored to these statement structures:
- HDFC export layouts (`.xlsx`/`.xls`) containing `Date`, `Narration`, `Withdrawal Amt.`, `Deposit Amt.`
- SBI export layouts (`.xlsx`/`.xls`) containing `Date`, `Details`, `Debit`, `Credit`

The code attempts to locate the real header row dynamically (scanning the first 30 rows) rather than assuming a fixed position.

**Not yet supported, and explicitly surfaced as such** (not silently dropped):
- CSV bank statements
- PDF bank statements (planned; deferred to a future update)
- Any bank layout that doesn't match the HDFC/SBI header signature

### Splitwise exports
The current implementation expects a Splitwise CSV that contains a user-name column, such as `Rishabh Agrawal`, and a `Cost` column.

Splitwise's own trailing "Total balance" running-summary row is explicitly filtered out (never treated as a transaction), as is any row with a blank `Category`.

Three kinds of rows come out of a Splitwise export and are handled differently:
- **Group expense, you fronted it** → `you_paid = Cost`, `your_share = Cost - net` (matched against a bank Debit)
- **Group expense, someone else fronted it** → `your_share = abs(net)`, `you_paid = 0` (a real personal expense, not matched to a bank transaction)
- **Settlement (`Category == "Payment"`)** → never a real expense (`Amount = 0` always); becomes either `you_paid` (you repaid someone → matched against a bank Debit) or `you_received` (someone repaid you → matched against a bank Credit)

Each expense also carries its own Splitwise category (`Groceries`, `Car`, `Dining out`, etc.), mapped via `map_splitwise_category` onto this app's taxonomy — the per-file "fallback" category selected at upload time is only used when that mapping comes back empty.

### Credit card statements
Currently supports **SBI Card PDF statements** only, via `parse_sbi_card_lines`/`standardize_sbi_card_pdf` in `logic.py`. Uses `pdfplumber` to extract page text, then a regex matches lines shaped like `DD Mon YY  <description>  <amount>  C|D` (verified against a real 7-page statement with zero false positives from the T&C/fee-schedule pages).

Three kinds of line, handled differently — mirroring the Splitwise settlement pattern:
- **`D` (Debit)** → a real purchase. `Type='Debit'`, categorized like any other transaction.
- **`C` line containing "PAYMENT RECEIVED"** → money paid from your bank account to settle the bill. Never a real expense (`Amount=0`); becomes `you_paid` so `engine.py` matches it against the corresponding bank Debit and excludes that bank-side transaction from spend, exactly like a Splitwise bill fronted.
- **any other `C` line** → a merchant refund/reversal credited back to the card. `Type='Credit'`, a real credit but not a bank transfer (`you_paid=0`).

Account source is tagged `"SBI Credit Card"`. Adding another issuer means writing a new line-format parser and giving its account source a `"Credit Card"` substring so `engine._transfer_label` picks the right label automatically.

## Current workbook schema

### transactions sheet
Columns actually produced by the pipeline (beyond `init_db`'s minimal starting schema):
- `date`
- `description`, `description_clean`
- `amount`
- `type` — `Debit` | `Credit` | `Settlement` | `Transfer_Splitwise_Base` | `Transfer_Splitwise_Settlement`
- `account_source` — `HDFC Bank` | `SBI Bank` | `Splitwise` | `SBI Credit Card`
- `category`
- `is_reviewed`
- `you_paid`
- `you_received`
- `your_share`
- `match_notes`
- `_dup_seq` — internal, used only for deduplication; not meant for display

### category_rules sheet
This sheet stores category keyword rules, pre-seeded with `DEFAULT_CATEGORY_RULES` from `database.py` and grown over time as the user corrects categories in the Triage tab (see `apply_category_rules` / the Triage "Save" handler).

Structure:
- `keyword`
- `category`

The user can still review and (re)categorize transactions manually in the UI at any point — auto-suggestion never removes that option, it just pre-fills it.

## Important implementation notes

### Case normalization
The code lowercases all incoming column names before merging data:

```python
new_data.columns = [c.lower() for c in new_data.columns]
```

This means the code must consistently expect lowercase column names when reading/writing the workbook. This happens in `app.py`, before `logic.apply_category_rules` / `logic.merge_and_dedup` are called.

### Excel persistence
The app uses pandas with `openpyxl` to write the workbook, not a database engine.

This makes the project simple and human-editable, but it also means there are limitations around data integrity, scaling, and robust query capabilities.

### Deduplication
`logic.merge_and_dedup` (bug #8 fix) does **not** dedup on `(date, description, amount, account_source)` alone — that would silently collapse two genuinely different same-day transactions with identical amount/description (e.g. two ₹121 "Auto" rides) into one. Instead, `ensure_dup_seq` tags each row with its occurrence index within its own upload batch, computed *before* merging/sorting, and the dedup key includes that index. This means:
- A batch containing two identical-looking rows keeps both.
- Re-uploading the exact same file (same internal row order) produces matching indices and correctly collapses to zero new rows.
- A "refreshed" statement whose date range overlaps a previous upload only adds the genuinely new trailing rows.
- Existing data saved before `_dup_seq` existed is backfilled transparently (`ensure_dup_seq` computes it if missing).

### Heuristic matching
The reconciliation is intentionally narrow and based on assumptions:
- the same amount (within ±0.01) appears on both sides, on the correct side (`you_paid`↔Debit, `you_received`↔Credit — never crossed),
- transactions are within a small date window,
- the transaction has not already been matched.

This is best treated as a suggestion engine, not a final source of truth.

### Testability boundary
`logic.py` and `engine.py` must remain free of Streamlit imports/side effects. This is what makes the test suite possible without mocking a browser session — preserve this boundary when adding new logic.

## Expected user workflow

A typical run looks like this:

1. Launch the app.
2. Upload SBI/HDFC statements and/or Splitwise CSVs.
3. Click `Process Files`. Any skipped files (unsupported type, unrecognized layout) are shown with a reason.
4. Review the `Triage Queue` tab — many rows may already be pre-categorized and marked reviewed by learned/seeded rules; only genuinely new merchants need attention.
5. Correct or confirm categories; corrections are learned as new rules for next time.
6. Use the "Manual Matcher" tools in the Triage tab for any transfer/settlement pairs the automatic engine missed, in either direction.
7. Keep the Excel workbook as the long-term record.

## Current limitations

The current codebase is intentionally a minimum viable personal finance workflow. Known gaps that remain (see "Recent fixes" below for what's already been addressed):

- only HDFC and SBI bank formats are normalized (no ICICI, Axis, Kotak, etc.),
- PDF and CSV bank statement ingestion are not implemented (explicitly surfaced as unsupported rather than silently dropped),
- reconciliation is based on simple heuristics, not a full ledger engine,
- keyword category matching is substring-based and can produce false positives on overly broad keywords (mitigated by keeping seeded rules conservative, but a risk for any new rule — see the `"JIO"` → `"JIO FIBER"` narrowing as a precedent),
- `extract_rule_keyword` requires pre-cleaned input; feeding it raw narrations directly does not produce stable keys (see its docstring/tests),
- no clean persistence model beyond Excel,
- no true account ledger or reporting engine,
- Splitwise user-name matching is hardcoded to one display name.

### Recent fixes (for context — don't re-introduce these)
- Splitwise's trailing "Total balance" row is explicitly filtered, not relying on sign coincidence.
- Splitwise settlement ("Payment") rows never inflate expense categories; they're tracked via `you_paid`/`you_received` and excluded from spend.
- Reconciliation uses a float tolerance (±0.01) instead of exact equality.
- Reconciliation matches in both directions (bills fronted ↔ debits, **and** settlements received ↔ credits).
- Deduplication uses a stable per-batch occurrence index, not just `(date, description, amount, account)`.
- Unsupported/unrecognized bank files produce an explicit warning/error instead of vanishing silently.
- Each Splitwise expense keeps its own mapped category instead of one blanket category per uploaded file.
- Bank narrations are cleaned into merchant names before category matching.
- The `category_rules` sheet is actually read, applied, and learned from — categorization is no longer 100% manual every time.
- Credit card statements (SBI Card PDF) can be imported directly: itemized purchases are real expenses, and the bill payment shown in the bank statement is recognized as a transfer and excluded, instead of double-counting or requiring the bank's lump-sum debit to stand in for the actual spend detail. Reconciliation was generalized from "Splitwise-only" to "any external ledger" to support this without duplicating the matching logic.

## Future scope

This project has strong potential to expand into a more complete finance automation system. The roadmap below is organized around the two concrete pain points that motivated it: **bulk-importing historical (backdated) data is tedious today**, and **there's no way to get new statements in without manually downloading and clicking through the uploader every time**.

### Phase A — Make bulk/backdated import painless (near-term, pure software, no new infra)
The pipeline already tolerates re-uploads and overlapping date ranges cleanly (`merge_and_dedup`), so the real friction in a large backfill is UI mechanics, not data correctness. Target that directly:
- **Splitwise API sync** (the flagship item here — see below).
- **A CLI ingestion script** (`ingest.py`) that calls the exact same `logic.py`/`engine.py` functions the Streamlit app uses, so a whole folder of historical statements (`ingest.py --bank hdfc statements/*.xls`) can be backfilled in one command instead of clicking through the uploader file-by-file. This is the highest-leverage, lowest-risk next step for bank/card statements, which don't have a public API to fall back on.
- **Bulk actions in the Triage queue** — multi-row select + "apply this category to all selected," and a one-click "accept all rule-matched suggestions" so backfilling months of history doesn't mean scrolling through hundreds of individual dropdowns.
- **A backfill-aware categorization pass** — when importing a large historical batch, run `apply_category_rules` iteratively so a rule learned from row 50 of the batch can auto-apply to a similar row 800 later in the same batch, not just on the *next* upload.
- Support more bank/card formats (ICICI, Axis, HDFC Credit Card, etc.) and PDF/OCR bank statement ingestion, since backfilling often means pulling from more than one institution.

#### Splitwise sync — the official API isn't reliably free, so use one of these instead
Splitwise's own API Terms of Use state: *"Splitwise may impose conditions on the use of the Self-Serve API, including, for example, maintaining an active Splitwise Pro subscription."* So the API route from the earlier draft of this roadmap isn't a safe assumption — don't build against `GET /get_expenses` expecting free access. Two free alternatives, in order of how much they're worth building:

1. **Automate the download of the existing free CSV export, not the API.** Splitwise's CSV export (what `standardize_splitwise_data` already parses) is a normal, free, no-subscription web feature. A small browser-automation script (Playwright/Selenium) can log into Splitwise on a schedule, click the group's "Export as CSV" link, and drop the file straight into the Phase B watched-folder pipeline below — so Splitwise ends up on the *exact same* automated path as bank/card statements, no separate infrastructure needed. Worth being upfront about the tradeoffs before building this: it's more fragile than an API (breaks if Splitwise changes their page layout), and scripted/automated access to a web app's UI for personal use — while a very common pattern — sits in the kind of gray area most consumer web ToS technically discourage, even when no one enforces it for individual accounts. That's a judgment call for you to make, not a blocker on my end.
2. **Parse Splitwise's own notification emails.** If email notifications are enabled, Splitwise emails you on every new expense with the amount/description in the body. This reuses the exact same IMAP-polling infrastructure already planned for bank statement emails in Phase B, so it's close to free to add once that exists — but it's less structured to parse reliably than a CSV row, and won't catch retroactive edits/deletes the way a true API sync could.

Given the ToS uncertainty, **the pragmatic default for now is: keep the free CSV export as the source of truth, and automate the download step (option 1) rather than the ingestion logic** — the parsing/categorization/reconciliation side is already done and already free.

### Phase B — Automate recurring ingestion (medium-term, needs scheduling + credentials handling)
This is what actually removes the "download the statement, then remember to upload it" loop:
- **Watched-folder auto-import** — a background script (run via cron / Task Scheduler / `launchd`) watches a designated folder and runs any new file through the standard pipeline automatically the moment it lands there (e.g. from a browser's default download folder, or from the Splitwise CSV-download automation above). No email or bank integration required, and it's the simplest version of "automate it."
- **Email-based auto-fetch** — most banks/card issuers (and optionally Splitwise, per above) email something every cycle. An IMAP-based script can poll a mailbox (or a dedicated forwarding rule/label), recognize known statement/notification emails by sender/subject, download attachments, decrypt password-protected PDFs (issuer passwords are usually a deterministic formula like DOB+PAN), and feed it straight into the pipeline. This is the real fix for "get frequent statements automatically" for banks/cards — but **it means storing IMAP credentials and statement-decryption secrets locally**, which needs a clear-eyed security design (e.g. an OS keychain, not a config file in plaintext) before it's built, not after.
- **Scheduled runs with a visible sync status** — nightly/weekly cron job (which can now also re-run the Phase A Splitwise sync on a timer), plus a "last synced: X new transactions, Y need review" indicator in the dashboard so automation doesn't mean losing visibility into what happened.
- Once ingestion can run unattended, **migrate off Excel to SQLite/PostgreSQL** becomes higher priority (a background script and an open Streamlit session both writing to the same `.xlsx` file is a real corruption/locking risk that a proper DB avoids).

### Phase C — Long-term
- **Bank Account Aggregator (AA) / open banking API integration** (India's RBI-regulated AA framework, or a developer-friendly aggregator like Setu) for consent-based, near-real-time transaction pull — removes statement parsing entirely for participating institutions, but is a much bigger compliance/integration lift than Phases A/B and is realistically a "if this grows beyond personal use" step, not a next sprint.
- Store raw statement metadata, ingestion history, and audit trails (which becomes essential once ingestion is automatic and unattended — you want to know *when* and *from what* every row arrived).
- Expand the rule engine beyond substring matching (fuzzy matching, per-account rules).
- Forecasting, budget tracking, recurring-expense detection, anomaly detection.
- Create a full personal finance assistant that can classify transactions automatically, reconcile multiple accounts and payment sources, generate expense summaries and month-end reports, and support recurring budgets, savings goals, and anomaly detection.

**Suggested order:** Phase A first — it's a few hours of low-risk work reusing code that's already tested, and it directly solves "backdated entries are tedious" without touching credentials or scheduling. Phase B (specifically the watched-folder version before the email version) is the natural next step once A is in place. Phase C is a different scale of project and shouldn't block either.

## Working assumptions for future AI edits

When modifying this project, assume the following:

1. The app is a Streamlit dashboard, not a web API.
2. The persistence layer is the workbook file `finance_tracker.xlsx`.
3. `app.py` contains **only** UI and orchestration — standardization, categorization, and dedup logic live in `logic.py`; reconciliation heuristics live in `engine.py`.
4. `engine.py` should remain fairly simple, and must stay Streamlit-free (bidirectional matching is about as complex as it should get without a real design discussion).
5. `database.py` is responsible only for workbook initialization and seeding starter category rules.
6. Many parsing functions are currently format-specific and may need sensitivity to exact statement layout.
7. `logic.py` and `engine.py` must remain framework-free (no Streamlit imports) — this is what makes the test suite possible.
8. New business logic belongs in `logic.py` (or a new equally testable module), not embedded directly in `app.py`'s Streamlit callbacks.
9. Run `pytest` before and after any change to `logic.py` or `engine.py`; add/update tests alongside the change. See `TESTING.md`.

## Implementation guidance for future contributors

If you are asked to extend the project, prefer changes that:
- keep the UI lightweight and human-reviewable,
- preserve the existing workbook format for continuity,
- avoid breaking the normalization pipeline for current bank formats,
- extract new pure logic into `logic.py` rather than inlining it in Streamlit callbacks, so it stays unit-testable,
- add or update tests under `tests/` alongside any logic change — synthetic fixtures in `conftest.py` are the pattern to follow; don't require real personal financial data for tests to pass,
- add clear test or validation steps before changing reconciliation logic.

The codebase is best thought of as a small personal finance automation workflow rather than a polished production SaaS product.
