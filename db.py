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

import re
import difflib
import duckdb
from loader import get_new_vision_df

TABLE_NAME = "new_vision"

# Columns that hold names a user is most likely to mis-spell or abbreviate:
# partners, people (owners), and lines of business. The fuzzy matcher below
# searches these to turn "AU merchandise" -> "AU Bank", "madhavi" -> "Madhvi".
ENTITY_COLUMNS = [
    "Partner", "Line of Business",
    "VG SPOC", "Product SPOC", "Tech Lead", "QA SPOC",
]
_JUNK_VALUES = {"", "-", "na", "n/a", "tbd"}

# This sheet has ~90 dated "weekly review" columns (e.g. 'Review 9th June',
# 'Comments 24 March'). They're free-text meeting notes, mostly empty per row.
# Listing all of them in the prompt would bury the ~30 columns that actually
# matter. We detect them by name and summarize them as a group instead.
_NOTE_COL_PATTERN = re.compile(
    r"^\s*(review|comments?|fin review|finance weekly)\b", re.IGNORECASE
)


def _is_note_col(col: str) -> bool:
    """True if a column is one of the dated weekly review/comment note columns."""
    return bool(_NOTE_COL_PATTERN.match(col))

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


def _sample_str(df, col: str, n: int) -> str:
    """A few distinct, non-empty example values for a column, single-lined."""
    vals = df[col].astype(str)
    samples = []
    for v in vals.unique():
        v = " ".join(str(v).split())          # collapse newlines/whitespace
        if v:
            samples.append(v[:40])            # trim very long note cells
        if len(samples) >= n:
            break
    return ", ".join(f"'{s}'" for s in samples) if samples else "(mostly empty)"


def get_schema_text(sample_values: int = 3) -> str:
    """
    Build a human-readable description of the table for the system prompt.

    Splits columns into two groups so the prompt stays focused:
    1. CORE columns — listed in full with example values. These are what most
       questions are about (status, partner, dates, owners, efforts, risk).
    2. DATED NOTE columns — ~90 'Review <date>' / 'Comments <date>' free-text
       columns. We don't list each one (it would bury the core columns); we
       summarize them and list their names compactly so the agent knows they
       exist and can query a specific date when asked.
    """
    _ensure_table()
    df = get_new_vision_df()

    core_cols = [c for c in df.columns if not _is_note_col(c)]
    note_cols = [c for c in df.columns if _is_note_col(c)]

    lines = [
        f'Table name: "{TABLE_NAME}"  ({len(df)} rows, {len(df.columns)} columns total)',
        "",
        "CORE columns (use these for almost all questions):",
    ]
    for col in core_cols:
        lines.append(f'  - "{col}" - e.g. {_sample_str(df, col, sample_values)}')

    if note_cols:
        # We deliberately DO NOT list all ~90 names here — that bloats every LLM
        # call. We give a couple of examples and tell the agent how to discover
        # the exact name on demand (via information_schema) when a question
        # actually needs a specific dated note column.
        examples = ", ".join(f'"{c}"' for c in note_cols[:4])
        lines += [
            "",
            f"DATED NOTE columns: there are also {len(note_cols)} free-text weekly "
            f"review/meeting-note columns (e.g. {examples}), one per review date, "
            "mostly empty per row. Only use them when the user asks what was "
            "discussed/updated on a SPECIFIC date. To find the exact column name "
            "for a date, run: SELECT column_name FROM information_schema.columns "
            f"WHERE table_name = '{TABLE_NAME}' AND column_name ILIKE '%<date>%'.",
        ]

    return "\n".join(lines)


def _tokens(s: str):
    """Split a string into lowercase word tokens of length >= 2."""
    return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if len(t) >= 2]


def find_entity_candidates(term: str, limit: int = 8):
    """
    Return the real tracker values that best match a (possibly misspelled,
    abbreviated, or partial) term the user typed.

    This is what powers the "did you mean…?" behaviour. It scores every distinct
    value in the entity columns against the term three ways and keeps the best:
    (a) whole-string fuzzy similarity (difflib),
    (b) best token-to-token fuzzy similarity (catches 'madhavi' vs 'madhvi'),
    (c) an exact shared token (catches 'au' inside 'AU Bank').

    Returns a ranked list of (value, column, score) for matches >= 0.6.
    """
    _ensure_table()
    df = get_new_vision_df()

    term_l = " ".join(term.lower().split())
    term_tokens = set(_tokens(term_l))

    best = {}  # candidate value -> (sort_key, column, sim) ; sort_key=(coverage, sim)
    for col in ENTITY_COLUMNS:
        if col not in df.columns:
            continue
        for raw in df[col].astype(str).unique():
            raw = raw.strip()
            if raw.lower() in _JUNK_VALUES:
                continue
            # A cell may list several people ("Vikas, Rajkumar"); treat the whole
            # cell AND each split part as separate candidates.
            parts = {raw} | {
                p.strip() for p in re.split(r"[,/&]|\band\b", raw) if p.strip()
            }
            for cand in parts:
                cl = " ".join(cand.lower().split())
                cand_tokens = set(_tokens(cl))

                whole = difflib.SequenceMatcher(None, term_l, cl).ratio()
                # For each term token, its best fuzzy match against a candidate
                # token. "coverage" = how many of the user's words matched well —
                # this is what makes "Madhvi Gupta" (2 words) beat "Anjali Gupta"
                # (1 word) for the query "madhavi gupta".
                coverage = 0
                best_tok = 0.0
                for tt in term_tokens:
                    m = max(
                        (difflib.SequenceMatcher(None, tt, ct).ratio() for ct in cand_tokens),
                        default=0.0,
                    )
                    best_tok = max(best_tok, m)
                    if m >= 0.8:
                        coverage += 1

                sim = max(whole, best_tok)
                key = (coverage, sim)
                if cand not in best or key > best[cand][0]:
                    best[cand] = (key, col, sim)

    ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)
    # Return (value, column, similarity, coverage); keep only close-enough matches.
    return [
        (v, c, round(sim, 2), key[0])
        for v, (key, c, sim) in ranked
        if sim >= 0.6
    ][:limit]


# ── Quick test: run `python db.py` to verify DuckDB loads + queries the tab ────
if __name__ == "__main__":
    print("Schema:\n")
    print(get_schema_text())
    print("\nSample query — first 5 rows:")
    print(run_sql(f"SELECT * FROM {TABLE_NAME} LIMIT 5").to_string())
