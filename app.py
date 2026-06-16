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

    # We stream the agent's run into ONE message. While it's still working (tool
    # calls + reasoning) we show a ChatGPT-style status line in that message; the
    # moment the real answer starts arriving, we clear the status and stream the
    # answer in token-by-token.
    #
    # agent.stream_async() runs the FULL agent loop and yields events. The keys we
    # care about:
    #   "current_tool_use" → the model is calling a tool (name + toolUseId)
    #   "start"            → a new reasoning cycle began (e.g. after a tool result)
    #   "data"            → a chunk of the visible answer text
    #   "result"          → the final AgentResult (fallback only)
    msg = cl.Message(content="")
    await msg.send()

    # Status text per tool the agent calls (no emojis — these get the animated
    # shimmer sweep via the .thinking-shimmer CSS class in public/custom.css).
    TOOL_LABELS = {
        "find_entity": "Matching the name",
        "query_tracker": "Querying the tracker",
    }

    def shimmer(text: str) -> str:
        # Wrap the status in a span our custom CSS animates. Requires
        # unsafe_allow_html = true in .chainlit/config.toml.
        return f'<span class="thinking-shimmer">{text}</span>'

    current_status = None

    async def set_status(label: str):
        # Update the status line only when it actually changes (avoids needless
        # re-renders / flicker).
        nonlocal current_status
        if label != current_status:
            current_status = label
            msg.content = shimmer(label)
            await msg.update()

    await set_status("Thinking")

    final_result = None
    answer_started = False
    seen_tools = set()
    tools_ran = 0

    try:
        async for event in agent.stream_async(message.content):
            if "data" in event:
                # The visible answer has begun → clear the status once, then stream.
                if not answer_started:
                    answer_started = True
                    msg.content = ""
                    await msg.update()
                await msg.stream_token(event["data"])

            elif "current_tool_use" in event and not answer_started:
                tu = event["current_tool_use"]
                tid, name = tu.get("toolUseId"), tu.get("name")
                if tid and name and tid not in seen_tools:
                    seen_tools.add(tid)
                    tools_ran += 1
                    await set_status(TOOL_LABELS.get(name, "Working"))

            elif "start" in event and tools_ran and not answer_started:
                # Model resumed after a tool result → it's interpreting the data.
                await set_status("Almost there — analyzing the results")

            elif "result" in event:
                final_result = event["result"]
    except Exception as e:
        if not answer_started:
            msg.content = ""
        await msg.stream_token(f"\n\nSorry, something went wrong: {e}")

    # Fallback: if no answer text ever streamed (rare), replace the status line
    # with the final result so the user never sees a stuck "Thinking" shimmer.
    if not answer_started and final_result is not None:
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
