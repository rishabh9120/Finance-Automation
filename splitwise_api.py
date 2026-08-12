"""
Direct Splitwise API access for the finance tracker.

This replaces the "export a CSV from Splitwise, then upload it" step with a
direct pull from Splitwise's own REST API (https://dev.splitwise.com/),
using a personal API key. It's split into small, pure, testable pieces:

  - fetch_current_user()      : who am I, per Splitwise (id + display name)
  - fetch_raw_expenses()      : paginated raw JSON expenses from the API
  - expenses_to_dataframe()   : pure transform, JSON -> the exact same
                                 DataFrame shape a Splitwise CSV "detailed"
                                 export produces (Date, Description,
                                 Category, Cost, Currency, plus one column
                                 per group member holding their net balance)
  - fetch_splitwise_expenses_df(): the high-level convenience wrapper that
                                 combines the three above

Because expenses_to_dataframe() reproduces the CSV export's exact column
shape, the output plugs directly into logic.standardize_splitwise_data()
and logic.finalize_splitwise_category() with ZERO changes to that pipeline
-- app.py just gets a second way to produce the same "raw Splitwise
DataFrame" it already knows how to handle.

Also included: a tiny on-disk "last synced at" tracker, so repeated syncs
only ever ask Splitwise for expenses added/changed since the last pull
instead of re-fetching your entire multi-year history every time.
"""
import json
import os
from datetime import datetime, timezone

import pandas as pd
import requests

SPLITWISE_API_BASE = "https://secure.splitwise.com/api/v3.0"

# Where the "last synced at" timestamp is persisted. A plain JSON file next
# to the workbook, not a workbook sheet -- this is sync bookkeeping, not
# financial data, and keeping it out of finance_tracker.xlsx means a sync
# failure/retry can never accidentally corrupt real transaction rows.
SYNC_STATE_FILE = "splitwise_sync_state.json"


def _get_headers(api_key=None):
    """
    Build the Authorization header for a Splitwise API request.

    Input:
        api_key (str, optional): a Splitwise personal API key. If omitted,
            falls back to the SPLITWISE_API_KEY environment variable.

    Output:
        dict: headers suitable for requests.get(..., headers=...).

    Edge cases:
        - Raises ValueError (not a silent None) if no key is available from
          either source, so a missing key fails fast and clearly instead of
          producing a confusing 401 from Splitwise several lines later.
    """
    key = api_key or os.environ.get("SPLITWISE_API_KEY")
    if not key:
        raise ValueError(
            "No Splitwise API key provided. Pass api_key= explicitly, or set "
            "the SPLITWISE_API_KEY environment variable. Generate a personal "
            "API key at https://secure.splitwise.com/apps."
        )
    return {"Authorization": f"Bearer {key}"}


def fetch_current_user(api_key=None):
    """
    Look up the Splitwise account these API calls are authenticated as.

    Input:
        api_key (str, optional): see _get_headers().

    Output:
        dict with:
          - 'id' (int): the Splitwise user id.
          - 'name' (str): "First Last" display name, matching exactly what
            appears as that user's column header in a Splitwise CSV export
            -- this is what should be passed as `user_name` to
            logic.standardize_splitwise_data().

    Edge cases:
        - Raises requests.HTTPError if the API key is invalid/expired
          (Splitwise returns 401), via response.raise_for_status().
        - last_name can be None for some accounts; guarded with `or ''` so
          the joined name doesn't literally contain the string "None".
    """
    resp = requests.get(f"{SPLITWISE_API_BASE}/get_current_user", headers=_get_headers(api_key), timeout=15)
    resp.raise_for_status()
    user = resp.json()["user"]
    full_name = f"{user['first_name']} {user.get('last_name') or ''}".strip()
    return {"id": user["id"], "name": full_name}


def fetch_raw_expenses(api_key=None, group_id=None, dated_after=None, limit=200):
    """
    Fetch every expense visible to this account from Splitwise's
    /get_expenses endpoint, paginating automatically.

    Input:
        api_key (str, optional): see _get_headers().
        group_id (int, optional): restrict to one Splitwise group. Omit to
            fetch across all groups/friends the account has access to.
        dated_after (str, optional): ISO-8601 timestamp (e.g.
            '2026-07-01T00:00:00Z'). Only expenses dated on/after this are
            returned. This is how incremental sync avoids re-fetching your
            entire history every time -- see get_last_sync_time().
        limit (int, optional): page size per request. Splitwise defaults to
            20 if unspecified; 200 keeps large histories to a handful of
            requests instead of dozens.

    Output:
        list[dict]: raw Splitwise expense objects, exactly as the API
        returns them (each has keys like 'cost', 'description', 'date',
        'category', 'payment', 'deleted_at', 'users', ...).

    Edge cases:
        - Pagination stops as soon as a page comes back with fewer than
          `limit` rows OR completely empty -- both signal "no more pages"
          without needing a separate total-count call.
        - Does NOT filter out deleted expenses here; that's left to
          expenses_to_dataframe() so this function stays a thin,
          faithful mirror of what the API actually returned.
    """
    headers = _get_headers(api_key)
    all_expenses = []
    offset = 0
    while True:
        params = {"limit": limit, "offset": offset}
        if group_id is not None:
            params["group_id"] = group_id
        if dated_after is not None:
            params["dated_after"] = dated_after

        resp = requests.get(f"{SPLITWISE_API_BASE}/get_expenses", headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        batch = resp.json().get("expenses", [])
        if not batch:
            break

        all_expenses.extend(batch)
        if len(batch) < limit:
            break
        offset += limit

    return all_expenses


def expenses_to_dataframe(expenses):
    """
    Pure transform: a list of raw Splitwise expense dicts (as returned by
    fetch_raw_expenses) -> a DataFrame shaped exactly like a Splitwise CSV
    "detailed" export -- Date, Description, Category, Cost, Currency, plus
    one column per group member holding THAT member's net balance for the
    expense (Splitwise's own sign convention: positive = owed to them,
    negative = they owe). This is the same shape
    logic.standardize_splitwise_data() already knows how to consume, so no
    changes are needed there.

    Input:
        expenses (list[dict]): raw expense objects from the Splitwise API.

    Output:
        pd.DataFrame. Empty (zero rows, but still with the base columns) if
        `expenses` is empty, so callers don't need a special case for "no
        new expenses since last sync".

    Edge cases:
        - Skips soft-deleted expenses (deleted_at is set) -- Splitwise
          keeps deleted expenses in the API response rather than omitting
          them, so this must be filtered explicitly or a deleted expense
          would reappear as a transaction on every sync.
        - Payment/settlement rows come back from Splitwise with
          `payment: true` and their own 'category' object still present
          (usually "General") -- category is forced to the literal string
          "Payment" for these, matching what a real Splitwise CSV export
          uses to mark settlements, since standardize_splitwise_data()
          keys its settlement detection off exactly that string.
        - A user's last_name can be None; guarded the same way as
          fetch_current_user() so column headers never contain "None".
    """
    rows = []
    for exp in expenses:
        if exp.get("deleted_at"):
            continue

        is_payment = bool(exp.get("payment"))
        category_name = "Payment" if is_payment else (exp.get("category") or {}).get("name", "")

        row = {
            "Date": exp.get("date"),
            "Description": exp.get("description", ""),
            "Category": category_name,
            "Cost": exp.get("cost"),
            "Currency": exp.get("currency_code", ""),
        }

        for share in exp.get("users", []):
            u = share.get("user", {}) or {}
            member_name = f"{u.get('first_name', '')} {u.get('last_name') or ''}".strip()
            if not member_name:
                continue
            row[member_name] = float(share.get("net_balance", 0) or 0)

        rows.append(row)

    if not rows:
        return pd.DataFrame(columns=["Date", "Description", "Category", "Cost", "Currency"])
    return pd.DataFrame(rows)


def get_last_sync_time(state_file=SYNC_STATE_FILE):
    """
    Read the persisted "last synced at" timestamp, if any.

    Input:
        state_file (str, optional): path to the sync-state JSON file.
            Defaults to SYNC_STATE_FILE; tests override this to a tmp path.

    Output:
        str or None: an ISO-8601 timestamp string suitable for passing as
        `dated_after` to fetch_raw_expenses(), or None if no sync has
        happened yet (i.e. "fetch everything").

    Edge cases:
        - Returns None (not an error) if the file doesn't exist yet, or if
          it exists but is unreadable/corrupt JSON -- a broken sync-state
          file should fail open into "sync everything again", not block
          the user from syncing at all.
    """
    if not os.path.exists(state_file):
        return None
    try:
        with open(state_file, "r") as f:
            return json.load(f).get("last_synced_at")
    except (json.JSONDecodeError, OSError):
        return None


def set_last_sync_time(when=None, state_file=SYNC_STATE_FILE):
    """
    Persist the "last synced at" timestamp after a successful sync.

    Input:
        when (datetime, optional): the timestamp to record. Defaults to
            now (UTC) if omitted.
        state_file (str, optional): see get_last_sync_time().

    Output:
        None. Side effect: writes/overwrites `state_file` with
        {"last_synced_at": "<iso timestamp>"}.

    Edge cases:
        - Should only be called AFTER a sync's fetched expenses have been
          successfully merged and saved to the workbook -- calling it
          before that risks marking a batch as "synced" when it was never
          actually persisted, permanently losing those expenses from
          future syncs.
    """
    when = when or datetime.now(timezone.utc)
    with open(state_file, "w") as f:
        json.dump({"last_synced_at": when.strftime("%Y-%m-%dT%H:%M:%SZ")}, f)


def fetch_splitwise_expenses_df(api_key=None, group_id=None, dated_after=None):
    """
    High-level convenience wrapper: authenticate, resolve the account's own
    display name, fetch expenses (optionally only those dated on/after
    `dated_after`), and return them as a ready-to-standardize DataFrame.

    Input:
        api_key (str, optional): see _get_headers().
        group_id (int, optional): see fetch_raw_expenses().
        dated_after (str, optional): see fetch_raw_expenses(). Callers
            doing incremental sync should pass get_last_sync_time() here.

    Output:
        tuple(pd.DataFrame, str): (expenses_df, user_name). `expenses_df`
        is ready to pass straight into
        logic.standardize_splitwise_data(expenses_df, user_name=user_name)
        -- callers never need to hardcode the Splitwise display name
        separately, since it's resolved from the API key itself.

    Edge cases:
        - If there are zero new expenses (e.g. an incremental sync with
          nothing new since last time), `expenses_df` is a valid but
          empty DataFrame -- callers should check `expenses_df.empty`
          before feeding it to standardize_splitwise_data(), which expects
          at least the base columns to be present (which they are) but has
          nothing meaningful to do with zero rows.
    """
    user = fetch_current_user(api_key)
    raw = fetch_raw_expenses(api_key, group_id=group_id, dated_after=dated_after)
    df = expenses_to_dataframe(raw)
    return df, user["name"]
