from strands import tool
from db import run_sql, find_entity_candidates

MAX_ROWS = 25
MAX_CELL_CHARS = 220


def _format_dataframe(df) -> str:
    if df is None or df.empty:
        return "Query ran successfully but returned 0 rows."

    total = len(df)
    shown = df.head(MAX_ROWS).copy()

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
        return f"ERROR running query: {type(e).__name__}: {e}\nFix the SQL and try again."


@tool
def find_entity(term: str) -> str:
    """Find the real tracker values that best match a partner, person, or line-of-business the user named.

    Use this whenever the user's wording might be partial, abbreviated, or
    mis-spelled, OR whenever a query returns 0 rows for a name they mentioned.
    It maps things like 'AU merchandise' -> 'AU Bank' and 'madhavi' -> 'Madhvi
    Gupta'. ALWAYS call this before telling the user something "doesn't exist".

    Args:
        term: the name or phrase the user used (e.g. "AU merchandise", "madhavi").

    Returns:
        A ranked list of the closest real values and which column each is in, so
        you can either use the best match (stating your interpretation) or ask
        the user which one they meant.
    """
    matches = find_entity_candidates(term)
    if not matches:
        return (
            f"No close matches for '{term}' in the partner / person / "
            f"line-of-business columns. It may genuinely not be in the tracker."
        )

    top_val, top_col, top_sim, top_cov = matches[0]
    second_cov = matches[1][3] if len(matches) > 1 else -1

    if len(matches) == 1 or top_cov > second_cov:
        msg = [f'Best match for "{term}": "{top_val}" (in "{top_col}"). Use this.']
        if len(matches) > 1:
            others = ", ".join(f'"{v}"' for v, _, _, _ in matches[1:5])
            msg.append(f"(Other, weaker possibilities: {others}.)")
        return "\n".join(msg)

    lines = [f'Several equally close matches for "{term}" - ask the user which:']
    for val, col, score, _cov in matches[:5]:
        lines.append(f'  - "{val}"  (in "{col}")')
    return "\n".join(lines)
