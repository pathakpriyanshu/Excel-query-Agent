import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
import os
import json
import base64
import time

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

TAB_NAME = os.getenv("SHEET_TAB", "New Vision")
CACHE_TTL_SECONDS = 30 * 60
HEADER_SEARCH_DEPTH = 10

_CACHE = {
    "data": None,
    "fetched_at": 0.0,
}


def _load_credentials() -> Credentials:
    # Production: full service-account JSON passed as an env var
    # (raw JSON or base64-encoded). Preferred for Docker / DigitalOcean.
    raw = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if raw:
        raw = raw.strip()
        try:
            info = json.loads(raw)
        except json.JSONDecodeError:
            info = json.loads(base64.b64decode(raw).decode("utf-8"))
        return Credentials.from_service_account_info(info, scopes=SCOPES)

    # Local dev: path to the JSON file on disk.
    creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH")
    if creds_path:
        return Credentials.from_service_account_file(creds_path, scopes=SCOPES)

    raise RuntimeError(
        "No Google credentials found. Set GOOGLE_CREDENTIALS_JSON (production) "
        "or GOOGLE_CREDENTIALS_PATH (local dev)."
    )


def get_google_client():
    creds = _load_credentials()
    return gspread.authorize(creds)


def _find_header_row(rows: list) -> int:
    best_row = 0
    best_count = -1
    for i, row in enumerate(rows[:HEADER_SEARCH_DEPTH]):
        filled = sum(1 for cell in row if str(cell).strip())
        if filled > best_count:
            best_count = filled
            best_row = i
    return best_row


def _clean_headers(raw_header: list, width: int) -> list:
    cleaned = []
    seen = {}
    for i in range(width):
        raw = raw_header[i] if i < len(raw_header) else ""
        name = " ".join(str(raw).split())
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
    if not values:
        return pd.DataFrame()

    header_idx = _find_header_row(values)
    header_raw = values[header_idx]
    data_rows = values[header_idx + 1:]

    width = max(len(r) for r in values)
    columns = _clean_headers(header_raw, width)

    padded = [row + [""] * (width - len(row)) for row in data_rows]
    df = pd.DataFrame(padded, columns=columns)

    mask_nonempty = df.apply(lambda row: any(str(v).strip() for v in row), axis=1)
    df = df[mask_nonempty].reset_index(drop=True)

    for col in list(df.columns):
        if col.startswith("Column_") and not df[col].astype(str).str.strip().any():
            df = df.drop(columns=[col])

    return df


def _find_worksheet(spreadsheet, wanted: str):
    worksheets = spreadsheet.worksheets()

    for ws in worksheets:
        if ws.title == wanted:
            return ws

    norm_wanted = " ".join(wanted.lower().split())
    for ws in worksheets:
        norm_title = " ".join(ws.title.lower().split())
        if norm_wanted in norm_title or norm_title in norm_wanted:
            return ws

    available = [ws.title for ws in worksheets]
    raise ValueError(f"Tab '{wanted}' not found. Available tabs: {available}")


def get_new_vision_df(force_refresh: bool = False) -> pd.DataFrame:
    now = time.time()

    if (
        not force_refresh
        and _CACHE["data"] is not None
        and (now - _CACHE["fetched_at"]) < CACHE_TTL_SECONDS
    ):
        return _CACHE["data"]

    try:
        client = get_google_client()
        spreadsheet = client.open_by_url(os.getenv("SHEET_URL"))
        worksheet = _find_worksheet(spreadsheet, TAB_NAME)

        values = worksheet.get_all_values()
        df = _values_to_dataframe(values)

        _CACHE["data"] = df
        _CACHE["fetched_at"] = now
        return df

    except Exception:
        if _CACHE["data"] is not None:
            return _CACHE["data"]
        raise
