import re
import difflib
import duckdb
from loader import get_new_vision_df

TABLE_NAME = "new_vision"

ENTITY_COLUMNS = [
    "Partner", "Line of Business",
    "VG SPOC", "Product SPOC", "Tech Lead", "QA SPOC",
]
_JUNK_VALUES = {"", "-", "na", "n/a", "tbd"}

_NOTE_COL_PATTERN = re.compile(
    r"^\s*(review|comments?|fin review|finance weekly)\b", re.IGNORECASE
)


def _is_note_col(col: str) -> bool:
    return bool(_NOTE_COL_PATTERN.match(col))


_con = duckdb.connect(database=":memory:")
_loaded_df_id = None


def _ensure_table():
    global _loaded_df_id

    df = get_new_vision_df()

    if id(df) != _loaded_df_id:
        _con.register("_incoming_df", df)
        _con.execute(
            f"CREATE OR REPLACE TABLE {TABLE_NAME} AS SELECT * FROM _incoming_df"
        )
        _con.unregister("_incoming_df")
        _loaded_df_id = id(df)


def run_sql(sql: str):
    _ensure_table()
    return _con.execute(sql).df()


def _sample_str(df, col: str, n: int) -> str:
    vals = df[col].astype(str)
    samples = []
    for v in vals.unique():
        v = " ".join(str(v).split())
        if v:
            samples.append(v[:40])
        if len(samples) >= n:
            break
    return ", ".join(f"'{s}'" for s in samples) if samples else "(mostly empty)"


def get_schema_text(sample_values: int = 3) -> str:
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
    return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if len(t) >= 2]


def find_entity_candidates(term: str, limit: int = 8):
    _ensure_table()
    df = get_new_vision_df()

    term_l = " ".join(term.lower().split())
    term_tokens = set(_tokens(term_l))

    best = {}
    for col in ENTITY_COLUMNS:
        if col not in df.columns:
            continue
        for raw in df[col].astype(str).unique():
            raw = raw.strip()
            if raw.lower() in _JUNK_VALUES:
                continue
            parts = {raw} | {
                p.strip() for p in re.split(r"[,/&]|\band\b", raw) if p.strip()
            }
            for cand in parts:
                cl = " ".join(cand.lower().split())
                cand_tokens = set(_tokens(cl))

                whole = difflib.SequenceMatcher(None, term_l, cl).ratio()
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
    return [
        (v, c, round(sim, 2), key[0])
        for v, (key, c, sim) in ranked
        if sim >= 0.6
    ][:limit]
