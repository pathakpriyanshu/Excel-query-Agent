"""
loader.py — Fetches and cleans the "New Vision" tab from Google Sheets.

This is the ONLY place that talks to Google. Everything downstream (DuckDB,
the agent) reads the clean pandas DataFrame this module produces.

Difference from the old project's sheets_reader.py:
- That one fetched ALL 49 tabs (fetch_all_tabs). We only need ONE tab.
- Cache TTL is 30 minutes here (a PM tracker barely changes within 30 min).

The messy-sheet cleaning helpers (_find_header_row, _clean_headers,
_values_to_dataframe) are ported almost verbatim from the old project — they
are pure utilities that solve real problems in THIS sheet (merged title cells,
duplicate column names, trailing-space headers, blank padding rows).
"""

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
import os
import time

load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────────────

# Minimum permissions: read sheet cells + locate the file by URL. Never write.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Which tab to read. Configurable via .env so you can repoint without code edits.
TAB_NAME = os.getenv("SHEET_TAB", "New Vision")

# How long cached data stays "fresh", in seconds. 30 minutes (per your call).
# Tradeoff: longer = fewer Google API calls but staler answers. Lower this if
# the CEO ever needs near-live data.
CACHE_TTL_SECONDS = 30 * 60

# When hunting for the header row, only look at the first N rows of the tab.
HEADER_SEARCH_DEPTH = 10

# ── Module-level cache ────────────────────────────────────────────────────────
# Lives at MODULE level (not inside the function) so it survives between calls
# for the whole life of the running process. If it were inside the function it
# would reset to None on every call and the cache would be useless.
_CACHE = {
    "data": None,        # the cleaned DataFrame from the last successful fetch
    "fetched_at": 0.0,   # time.time() of that fetch
}


def get_google_client():
    """
    Logs in as the service account (the 'robot' user) and returns a gspread
    client we can use for all sheet reads.

    Credentials.from_service_account_file reads the JSON key file and produces
    an auth token Google will accept — like showing an ID card at the door.
    """
    creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH")
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    return gspread.authorize(creds)


# ── Cleaning helpers (ported from old project — see its sheets_reader.py) ──────

def _find_header_row(rows: list) -> int:
    """
    Find which row is the real header. Many tabs have a merged title in row 1
    ("New Vision — Tracker") and the actual column names in row 2 or 3.
    Heuristic: within the first few rows, the header is the row with the MOST
    non-empty cells (titles span 1-2 cells, headers span many). Ties → earliest.
    """
    best_row = 0
    best_count = -1
    for i, row in enumerate(rows[:HEADER_SEARCH_DEPTH]):
        filled = sum(1 for cell in row if str(cell).strip())
        if filled > best_count:   # strictly greater → earliest row wins on a tie
            best_count = filled
            best_row = i
    return best_row


def _clean_headers(raw_header: list, width: int) -> list:
    """
    Turn a messy raw header row into clean, unique column names. Fixes:
    1. Whitespace junk — 'Comments \\n ' or names padded with spaces → collapsed.
    2. Empty names — a blank header cell → 'Column_<position>' so its data stays
       reachable.
    3. Duplicates — two 'Status' columns would collide as dict keys / SQL cols →
       renamed 'Status_2', 'Status_3', ...
    """
    cleaned = []
    seen = {}
    for i in range(width):
        raw = raw_header[i] if i < len(raw_header) else ""
        name = " ".join(str(raw).split())   # collapse all whitespace runs to one
        if not name:
            name = f"Column_{i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        cleaned.append(name)
    return cleaned


def _values_to_dataframe(values: list) -> pd.DataFrame:
    """
    Convert a raw grid (list of lists of strings) into a clean DataFrame:
    find header → clean names → rows below it are data → drop fully-empty rows
    (sheets are full of blank padding rows that would pollute counts).

    Every value stays a STRING — Google sends them that way, and uniform types
    mean string operations never crash on a stray number. The agent's SQL is
    responsible for CASTing to numbers/dates when it needs to.
    """
    if not values:
        return pd.DataFrame()

    header_idx = _find_header_row(values)
    header_raw = values[header_idx]
    data_rows = values[header_idx + 1:]

    width = max(len(r) for r in values)   # widest row defines table width
    columns = _clean_headers(header_raw, width)

    # Pad every data row to full width so each row has a value per column.
    padded = [row + [""] * (width - len(row)) for row in data_rows]
    df = pd.DataFrame(padded, columns=columns)

    # Drop rows where every cell is empty/whitespace — visual padding, not data.
    mask_nonempty = df.apply(lambda row: any(str(v).strip() for v in row), axis=1)
    df = df[mask_nonempty].reset_index(drop=True)

    # Drop columns that have no header AND no data — pure dead space.
    for col in list(df.columns):
        if col.startswith("Column_") and not df[col].astype(str).str.strip().any():
            df = df.drop(columns=[col])

    return df


def _find_worksheet(spreadsheet, wanted: str):
    """
    Find the worksheet by name, forgivingly.

    First try an exact title match. If that fails (the real tab might be
    'New Vision ' with a trailing space, or 'Brands and Aggregators - New
    Vision'), fall back to a normalized 'contains' match so a small naming
    drift in the sheet doesn't break the app.
    """
    worksheets = spreadsheet.worksheets()

    # Exact match first.
    for ws in worksheets:
        if ws.title == wanted:
            return ws

    # Normalized contains match (lowercase, whitespace-collapsed).
    norm_wanted = " ".join(wanted.lower().split())
    for ws in worksheets:
        norm_title = " ".join(ws.title.lower().split())
        if norm_wanted in norm_title or norm_title in norm_wanted:
            return ws

    available = [ws.title for ws in worksheets]
    raise ValueError(f"Tab '{wanted}' not found. Available tabs: {available}")


def get_new_vision_df(force_refresh: bool = False) -> pd.DataFrame:
    """
    Return the cleaned 'New Vision' tab as a DataFrame.

    Order of preference:
    1. Fresh cache (< 30 min old) → instant, zero API calls.
    2. Live fetch from Google.
    3. Stale cache, if the live fetch failed → old data beats no data.

    Pass force_refresh=True to bypass the cache (e.g. a 'refresh' chat command).
    """
    now = time.time()

    # Case 1: serve fresh cache.
    if (
        not force_refresh
        and _CACHE["data"] is not None
        and (now - _CACHE["fetched_at"]) < CACHE_TTL_SECONDS
    ):
        return _CACHE["data"]

    # Case 2: fetch live.
    try:
        client = get_google_client()
        spreadsheet = client.open_by_url(os.getenv("SHEET_URL"))
        worksheet = _find_worksheet(spreadsheet, TAB_NAME)

        # get_all_values returns the raw grid (list of lists of strings) with no
        # assumptions about the header — exactly what our cleaning layer wants.
        values = worksheet.get_all_values()
        df = _values_to_dataframe(values)

        _CACHE["data"] = df
        _CACHE["fetched_at"] = now
        return df

    except Exception:
        # Case 3: fall back to stale cache if we have any; else re-raise so the
        # caller can surface the real connection error.
        if _CACHE["data"] is not None:
            return _CACHE["data"]
        raise


# ── Quick test: run `python loader.py` to verify the connection + cleaning ─────
if __name__ == "__main__":
    print(f"Fetching '{TAB_NAME}' tab...")
    df = get_new_vision_df()
    print(f"Loaded {len(df)} rows, {len(df.columns)} columns.\n")
    print("Columns:")
    for c in df.columns:
        print(f"  - {c}")
    print("\nFirst 3 rows:")
    print(df.head(3).to_string())
