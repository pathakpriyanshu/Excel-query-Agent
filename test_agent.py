"""
test_agent.py — Non-interactive smoke test of the full stack.

Runs a few representative questions through the agent and prints the answer plus
the SQL it ran. Use this to sanity-check the whole pipeline (Groq tool-calling →
DuckDB → answer) without launching Chainlit:  python test_agent.py
"""

from agents import create_agent, answer_text, MODEL_PROVIDER


def sql_for_turn(agent, start_idx):
    sqls = []
    for msg in agent.messages[start_idx:]:
        for block in msg.get("content", []) or []:
            if isinstance(block, dict) and "toolUse" in block:
                tu = block["toolUse"]
                if tu.get("name") == "query_tracker":
                    sql = tu.get("input", {}).get("sql")
                    if sql:
                        sqls.append(sql)
    return sqls


# Kept to ONE question so the smoke test stays under Groq's free-tier
# tokens-per-minute limit. Add more once you're on a higher tier / OpenAI.
QUESTIONS = [
    "How many initiatives are currently Live?",
]


def main():
    print(f"Model provider: {MODEL_PROVIDER}\n")
    agent = create_agent()

    for q in QUESTIONS:
        print("=" * 70)
        print(f"Q: {q}")
        start = len(agent.messages)
        result = agent(q)
        print(f"\nA: {answer_text(result)}")
        sqls = sql_for_turn(agent, start)
        if sqls:
            print("\nSQL run:")
            for s in sqls:
                print(f"  {s}")
        print()


if __name__ == "__main__":
    main()
