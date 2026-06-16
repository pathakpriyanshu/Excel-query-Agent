"""
db.py — The DuckDB query layer.

Think of DuckDB as "SQLite for analytics": an in-memory SQL engine, no server,
no install. We load the cleaned 'New Vision' DataFrame into a table called
`new_vision`, and the agent answers questions by running SQL against it.

Why SQL instead of the old project's exec()'d pandas?
- A read-only DuckDB connection is genuinely sandboxed — it can ONLY run SQL,
  it can't touch the filesystem or run arbitrary Python (the old exec() could).
- LLMs write correct SQL more reliably than correct pandas.

How freshness works:
- loader.get_new_vision_df() owns the 30-minute cache. We just ask it for the
  current DataFrame on every query. If loader hands us a NEW DataFrame object
  (because its cache expired and it refetched), we rebuild the table. Rebuilding
  a ~100-row table takes milliseconds, so this is cheap.
"""

import duckdb
from loader import get_new_vision_df

TABLE_NAME = "new_vision"

# One in-memory connection for the whole process. ":memory:" means the database
# lives in RAM and vanishes when the process exits — perfect for a cache/query
# layer whose source of truth is always Google Sheets.
_con = duckdb.connect(database=":memory:")

# Tracks WHICH DataFrame we last loaded, by Python object identity. If loader
# returns the same object (cache still fresh), we skip the reload entirely.
_loaded_df_id = None


def _ensure_table():
    """
    Make sure the `new_vision` table reflects the latest DataFrame from loader.
    Reloads only when loader hands us a different DataFrame object.
    """
    global _loaded_df_id

    df = get_new_vision_df()   # cached by loader; may refetch if 30 min passed

    if id(df) != _loaded_df_id:
        # CREATE OR REPLACE so a refresh cleanly swaps the old data out.
        # We register the DataFrame under a temp name, copy it into a real
        # table, then drop the registration.
        _con.register("_incoming_df", df)
        _con.execute(
            f"CREATE OR REPLACE TABLE {TABLE_NAME} AS SELECT * FROM _incoming_df"
        )
        _con.unregister("_incoming_df")
        _loaded_df_id = id(df)


def run_sql(sql: str):
    """
    Run a read-only SQL query against the `new_vision` table and return the
    result as a pandas DataFrame.

    Raises on bad SQL — the caller (the tool) catches it and feeds the error
    back to the agent so it can fix its own query.
    """
    _ensure_table()
    return _con.execute(sql).df()


def get_schema_text(sample_values: int = 4) -> str:
    """
    Build a human-readable description of the table for the system prompt:
    every column name plus a few example values. The agent reads this to know
    what columns exist and what the data looks like BEFORE writing any SQL.

    Example output line:
        - "Status" (text) — e.g. 'UAT', 'Live', 'Delayed', 'In Progress'
    """
    _ensure_table()
    df = get_new_vision_df()

    lines = [f'Table name: "{TABLE_NAME}"  ({len(df)} rows)', "", "Columns:"]
    for col in df.columns:
        # A few distinct, non-empty example values so the agent writes filters
        # that match the REAL data (e.g. 'UAT' not 'In UAT').
        vals = df[col].astype(str)
        samples = [v for v in vals.unique() if v.strip()][:sample_values]
        sample_str = ", ".join(f"'{s}'" for s in samples) if samples else "(empty)"
        lines.append(f'  - "{col}" (text) — e.g. {sample_str}')
    return "\n".join(lines)


# ── Quick test: run `python db.py` to verify DuckDB loads + queries the tab ────
if __name__ == "__main__":
    print("Schema:\n")
    print(get_schema_text())
    print("\nSample query — first 5 rows:")
    print(run_sql(f"SELECT * FROM {TABLE_NAME} LIMIT 5").to_string())
