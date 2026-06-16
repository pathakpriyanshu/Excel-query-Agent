# Vision Assistant

Ask anything about the **New Vision** project tracker.

This assistant reads the live Google Sheet, runs SQL against it with DuckDB, and
answers in plain language. It never makes numbers up — every answer comes from a
real query, and you can see the exact SQL it ran under **"SQL I ran"**.

**Try:**
- How many projects are delayed, and what are the blockers?
- Which initiatives go live this month?
- AU Bank ka status kya hai?
- Which P0 items are still in QA?

Type `refresh` to pull the latest data from the sheet.
