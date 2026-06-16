"""
app.py — Chainlit web UI for the Vision Assistant.

The one critical detail (this is why we discussed cl.user_session):
the agent is created ONCE in on_chat_start and stored in the session, then
REUSED on every message. If we created a new Agent() inside on_message, it would
forget everything between turns. Storing the instance keeps Strands' built-in
conversation memory alive for the whole chat.

We run the (synchronous) agent inside asyncio.to_thread so the agent's LLM/SQL
work doesn't block Chainlit's async event loop (which would freeze the UI).
"""

import asyncio
import chainlit as cl

from agents import create_agent, answer_text, MODEL_PROVIDER
from loader import get_new_vision_df


def _sql_calls_since(agent, start_idx: int):
    """
    Collect the SQL strings the agent ran during the latest turn, so we can show
    the user "here's exactly what I queried" (transparency builds CEO trust).

    Strands keeps the full conversation in agent.messages. A tool call shows up
    as a content block with a "toolUse" entry. We scan only the messages added
    after start_idx (i.e. this turn).
    """
    sqls = []
    for msg in agent.messages[start_idx:]:
        for block in msg.get("content", []) or []:
            if isinstance(block, dict) and "toolUse" in block:
                tool_use = block["toolUse"]
                if tool_use.get("name") == "query_tracker":
                    sql = tool_use.get("input", {}).get("sql")
                    if sql:
                        sqls.append(sql)
    return sqls


@cl.on_chat_start
async def on_chat_start():
    """Runs once when a user opens the chat. Build + cache the agent here."""
    # Warm the data cache up front so the first question is fast, and so a bad
    # credential/sheet shows an error immediately rather than mid-conversation.
    try:
        df = await asyncio.to_thread(get_new_vision_df)
        rows = len(df)
    except Exception as e:
        await cl.Message(
            content=f"⚠️ Could not load the tracker: {e}"
        ).send()
        return

    # Create the agent ONCE and stash it in the per-user session.
    agent = await asyncio.to_thread(create_agent)
    cl.user_session.set("agent", agent)

    await cl.Message(
        content=(
            f"Hi! I'm **Vision Assistant** — I answer questions about the "
            f"**New Vision** tracker ({rows} initiatives). Model: `{MODEL_PROVIDER}`.\n\n"
            "Ask me things like:\n"
            "- *How many projects are delayed, and why?*\n"
            "- *Which initiatives go live this month?*\n"
            "- *AU Bank ka status kya hai?*\n\n"
            "_Type `refresh` to pull the latest data from the sheet._"
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    agent = cl.user_session.get("agent")
    if agent is None:
        await cl.Message(
            content="Session not ready — please refresh the page to start a new chat."
        ).send()
        return

    # Manual refresh command.
    if message.content.strip().lower() == "refresh":
        await asyncio.to_thread(get_new_vision_df, True)  # force_refresh=True
        await cl.Message(content="✅ Pulled the latest data from the sheet.").send()
        return

    # Remember where the conversation was, so we can find THIS turn's SQL after.
    start_idx = len(agent.messages)

    # Stream the answer token-by-token. We create an empty message and push tokens
    # into it as the model writes, so the user reads the answer as it forms instead
    # of waiting for the whole thing.
    #
    # agent.stream_async() runs the FULL agent loop (it may call query_tracker /
    # find_entity along the way) and yields events. Two keys matter to us:
    #   - "data"   → a chunk of the assistant's visible answer text (stream it)
    #   - "result" → the final AgentResult (used only as a fallback)
    # Tool-call steps happen between text, during which no "data" arrives — that's
    # the brief "thinking" gap before the answer starts flowing.
    msg = cl.Message(content="")
    await msg.send()

    final_result = None
    try:
        async for event in agent.stream_async(message.content):
            if "data" in event:
                await msg.stream_token(event["data"])
            elif "result" in event:
                final_result = event["result"]
    except Exception as e:
        await msg.stream_token(f"\n\nSorry, something went wrong: {e}")

    # Fallback: if nothing streamed as "data" (rare), fill from the final result.
    if not msg.content and final_result is not None:
        msg.content = answer_text(final_result)

    await msg.update()

    # Show the exact SQL the agent ran, in a collapsible side element, so the
    # user can audit how the answer was produced.
    sqls = _sql_calls_since(agent, start_idx)
    if sqls:
        joined = "\n\n".join(f"```sql\n{s}\n```" for s in sqls)
        await cl.Message(
            content="",
            elements=[cl.Text(name="SQL I ran", content=joined, display="side")],
        ).send()
