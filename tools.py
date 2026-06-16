"""
tools.py — The agent's one tool: run SQL against the New Vision tracker.

A Strands tool is just a Python function decorated with @tool. Two things matter:
1. The TYPE HINTS — they tell the model what arguments to pass (here: one string).
2. The DOCSTRING — the model literally reads it to decide WHEN and HOW to call
   the tool. So the docstring below is written for the model, not for us.

The tool returns a plain string (a small text table or an error message). When
the SQL is wrong, we return the error text instead of raising — that lets the
agent read its own mistake and fix the query on the next turn (self-correction).
"""

from strands import tool
from db import run_sql, TABLE_NAME

# Safety caps so one query can never flood the model's context with huge output.
# (Also keeps us under Groq's free-tier tokens-per-minute limit.)
MAX_ROWS = 25          # rows returned to the agent
MAX_CELL_CHARS = 220   # characters per cell (note columns can be paragraphs)


def _format_dataframe(df) -> str:
    """Turn a result DataFrame into a compact, readable text table for the agent."""
    if df is None or df.empty:
        return "Query ran successfully but returned 0 rows."

    total = len(df)
    shown = df.head(MAX_ROWS).copy()

    # Trim very long cell values (the free-text note columns can be paragraphs).
    for col in shown.columns:
        shown[col] = shown[col].astype(str).apply(
            lambda v: (v[:MAX_CELL_CHARS] + "…") if len(v) > MAX_CELL_CHARS else v
        )

    table = shown.to_string(index=False)
    note = ""
    if total > MAX_ROWS:
        note = f"\n\n(Showing first {MAX_ROWS} of {total} rows.)"
    return f"{total} row(s) returned.\n\n{table}{note}"


@tool
def query_tracker(sql: str) -> str:
    """Run a read-only SQL query against the "New Vision" project tracker and return the rows.

    Use this whenever you need real data to answer a question. The table is named
    new_vision. Column names have spaces, so wrap them in double quotes, e.g.:
        SELECT "Partner", "Status" FROM new_vision WHERE "Status" ILIKE '%delay%'

    All columns are text. Use ILIKE '%...%' for case-insensitive matching,
    TRY_CAST(... AS DOUBLE) for numbers, and '' (not NULL) for empty cells.

    Args:
        sql: A single DuckDB SELECT statement to run against the new_vision table.

    Returns:
        The matching rows as a text table, or an error message if the SQL failed
        (in which case, fix the SQL and call this tool again).
    """
    # Guard rail: this tool is read-only. Reject anything that isn't a plain
    # SELECT/WITH query so a malformed (or unexpected) statement can't mutate
    # anything. The DuckDB connection is in-memory anyway, but defence in depth.
    stripped = sql.strip().rstrip(";").lstrip("(").strip().lower()
    if not (stripped.startswith("select") or stripped.startswith("with")):
        return (
            "ERROR: Only read-only SELECT queries are allowed. "
            "Rewrite your query as a SELECT statement."
        )

    try:
        result_df = run_sql(sql)
        return _format_dataframe(result_df)
    except Exception as e:
        # Hand the exact error back to the model so it can self-correct.
        return f"ERROR running query: {type(e).__name__}: {e}\nFix the SQL and try again."
