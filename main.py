"""
main.py — A simple terminal chat loop for quick testing.

Same brain as the Chainlit app, no UI. Use this to test the agent + your Groq
key fast:  python main.py

One agent instance is created up front and reused for the whole session, so the
agent remembers earlier turns (ask "how many delayed?" then "show me their
partners" and it knows what "their" means).
"""

from agents import create_agent, answer_text, MODEL_PROVIDER
from loader import get_new_vision_df


def run_chat():
    print("=" * 64)
    print("  Vision Assistant  (terminal mode)")
    print(f"  Model provider: {MODEL_PROVIDER}")
    print("  Ask anything about the New Vision tracker.")
    print("  Commands:  'refresh' = refetch sheet now,  'exit' = quit")
    print("=" * 64)

    # Warm up: fetch the sheet once now so the first question is fast and so any
    # credential/sheet error shows up immediately, not mid-conversation.
    print("\nLoading tracker data...")
    df = get_new_vision_df()
    print(f"Loaded {len(df)} rows, {len(df.columns)} columns.\n")

    # Create the agent ONCE — this is what gives us conversation memory.
    agent = create_agent()

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break

        if user_input.lower() in ("exit", "quit", "bye"):
            print("Goodbye!")
            break
        if not user_input:
            continue
        if user_input.lower() == "refresh":
            get_new_vision_df(force_refresh=True)
            print("Tracker data refreshed.\n")
            continue

        print("\nThinking...\n")
        try:
            result = agent(user_input)
            print(f"Assistant: {answer_text(result)}\n")
        except Exception as e:
            print(f"Assistant: Sorry, something went wrong: {e}\n")


if __name__ == "__main__":
    run_chat()
