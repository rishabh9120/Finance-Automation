# Running the test suite

## Setup (one-time)

```bash
pip install -r requirements-test.txt
```

## Run everything

```bash
pytest
```

You should see something like `96 passed`. No real bank/Splitwise data needed
— everything runs against synthetic fixtures built to match the real file
shapes (see `tests/conftest.py`).

## Run one layer at a time

```bash
pytest tests/test_narration_cleanup.py       # UPI/IMPS/ACH -> merchant name
pytest tests/test_splitwise_standardize.py   # settlements, "various splits", bug #1/#2
pytest tests/test_bank_standardize.py        # HDFC + SBI parsing
pytest tests/test_credit_card.py             # SBI Card PDF parsing + reconciliation
pytest tests/test_category_rules.py          # learning/suggesting categories
pytest tests/test_engine_reconciliation.py   # bank <-> Splitwise matching, bug #5/#6
pytest tests/test_dedup.py                   # re-uploads, "updated statement" scenario, bug #8
pytest tests/test_end_to_end.py              # everything together
```

## Run one specific scenario

```bash
pytest tests/test_end_to_end.py -k updated_bank_statement -v
pytest tests/test_splitwise_standardize.py -k VariousSplits -v
```

## What's covered

| File | What it checks |
|---|---|
| `test_narration_cleanup.py` | Raw UPI/IMPS/ACH/card-mandate text → clean merchant name, for every pattern seen in real statements, plus garbage/empty/None inputs |
| `test_splitwise_standardize.py` | The "Total balance" summary row is always dropped (both balance signs); settlements never count as expenses; a wide range of split shapes (you-fronted, you-owe, perfectly-even/zero-net, 3-way uneven); Splitwise's own category → app taxonomy mapping |
| `test_bank_standardize.py` | HDFC and SBI header detection amid preamble/footer noise, Debit/Credit → unified Amount/Type, date parsing, malformed-file error handling |
| `test_credit_card.py` | SBI Card PDF line parsing (payments vs refunds vs purchases), no false positives from T&C/fee-schedule pages, and reconciliation of credit card bill payments against bank debits (including alongside Splitwise matching) |
| `test_category_rules.py` | Loading/matching/learning keyword rules, longest-match priority, the "teach it once, it remembers" loop |
| `test_engine_reconciliation.py` | ±1 paisa tolerance matching, both match directions (bills fronted ↔ debits, settlements received ↔ credits), date-window boundaries, no double-matching, idempotency, sign safety (never matches Debit against Credit) |
| `test_dedup.py` | Genuine look-alike duplicates survive; exact re-uploads add zero rows; **the "updated/refreshed bank statement" scenario** — a new export overlapping a previous one only adds the genuinely new rows; reviewed/categorized status survives a re-upload |
| `test_end_to_end.py` | Full pipeline (standardize → auto-categorize → dedup → reconcile → persist) for bank+Splitwise together, various splits end-to-end, updated-statement re-upload end-to-end, multi-bank upload, Excel persistence round-trip, and an optional golden-master check against the real sample files (auto-skipped if they're not present on your machine) |

## Adding a new test

Use the fixtures in `conftest.py` — `hdfc_raw_factory`, `sbi_raw_factory`,
`splitwise_df_factory` — to build synthetic data shaped like your real
statements without needing to attach real files. `isolated_db` gives you a
throwaway `finance_tracker.xlsx` so nothing you run in tests ever touches
your real data.
