import asyncio
import chainlit as cl

from agents import create_agent, answer_text, MODEL_PROVIDER
from loader import get_new_vision_df

# Registers the POST /transcribe endpoint on Chainlit's FastAPI app (speech-to-text).
import voice_api  # noqa: F401


@cl.on_chat_start
async def on_chat_start():
    try:
        df = await asyncio.to_thread(get_new_vision_df)
        rows = len(df)
    except Exception as e:
        await cl.Message(content=f"⚠️ Could not load the tracker: {e}").send()
        return

    agent = await asyncio.to_thread(create_agent)
    cl.user_session.set("agent", agent)

    await cl.Message(
        content=(
            f"Hi! I'm **Personal Assistant** — I answer questions about the "
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

    if message.content.strip().lower() == "refresh":
        await asyncio.to_thread(get_new_vision_df, True)
        await cl.Message(content="✅ Pulled the latest data from the sheet.").send()
        return

    msg = cl.Message(content="")
    await msg.send()

    TOOL_LABELS = {
        "find_entity": "Matching the name",
        "query_tracker": "Querying the tracker",
    }

    def shimmer(text: str) -> str:
        return f'<span class="thinking-shimmer">{text}</span>'

    current_status = None

    async def set_status(label: str):
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
                await set_status("Almost there — analyzing the results")

            elif "result" in event:
                final_result = event["result"]
    except Exception as e:
        if not answer_started:
            msg.content = ""
        await msg.stream_token(f"\n\nSorry, something went wrong: {e}")

    if not answer_started and final_result is not None:
        msg.content = answer_text(final_result)

    await msg.update()
