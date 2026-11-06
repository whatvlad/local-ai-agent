import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from .config import load_config
from .llm import Agent
from .store import Store
from .tools import Tools, send_email

HELP = """Local AI agent: send a request in English or your preferred language.

/search query — search the web without the model
/read https://... — read a public page
/remind 60 text — notify you in 60 seconds
/reminders — list reminders (UTC times)
/cancel ID — cancel a reminder
/email recipient | subject | body — create a draft
/drafts — recent drafts and email states
/draft ID — show a draft
/send ID — send a reviewed draft
/inbox — latest 5 emails (requires IMAP)
/reset — clear model history
/status — check Ollama
/help — show this help

Examples: "Find recent Python news with sources", "Remind me in 5 minutes to take a break".
Keep Python running for notifications. Reminders and drafts survive restarts."""
log = logging.getLogger(__name__)


async def chunks(message, text):
    text = str(text)
    for offset in range(0, len(text), 3500):
        await message.reply_text(text[offset:offset + 3500])


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs request URLs which would contain the Telegram token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        config = load_config()
    except ValueError as exc:
        raise SystemExit(str(exc))
    store = Store(config.database)
    agent = Agent(config, store)

    async def allowed(update):
        user, chat = update.effective_user, update.effective_chat
        if not user or not chat or not update.effective_message:
            return False
        if chat.type != "private" or user.id not in config.allowed:
            await update.effective_message.reply_text(f"Access denied. Your Telegram ID: {user.id}. The owner must add it to ALLOWED_USER_IDS and restart the bot. Use a private chat.")
            return False
        return True

    async def command(update, context):
        if not await allowed(update):
            return
        message = update.effective_message
        user_id, chat_id = update.effective_user.id, update.effective_chat.id
        name = message.text.split()[0].split("@")[0][1:]
        argument = message.text.partition(" ")[2].strip()
        tools = Tools(store, user_id, chat_id)
        try:
            if name in ("start", "help"):
                await chunks(message, HELP)
                return
            if name == "reset":
                agent.reset(user_id)
                result = "History cleared. Reminders and drafts are preserved."
            elif name == "status":
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.get(config.ollama_host + "/api/tags")
                    response.raise_for_status()
                models = [m["name"] for m in response.json().get("models", [])]
                result = f"Ollama is available. Selected: {config.model}\nInstalled: {', '.join(models) or 'no models'}"
            elif name in ("search", "read", "inbox", "remind", "reminders", "cancel", "email"):
                if name == "search":
                    tool, args = "web_search", {"query": argument}
                elif name == "read":
                    tool, args = "read_webpage", {"url": argument}
                elif name == "inbox":
                    tool, args = "read_inbox", {}
                elif name == "remind":
                    seconds, text = argument.split(maxsplit=1)
                    tool, args = "set_reminder", {"seconds": float(seconds), "text": text}
                elif name == "reminders":
                    rows = store.reminders(user_id)
                    result = "\n".join(f"#{r['id']} | {datetime.fromtimestamp(r['due'], timezone.utc):%Y-%m-%d %H:%M:%S} UTC | {r['text']}" for r in rows) or "No pending reminders."
                    await chunks(message, result)
                    return
                elif name == "cancel":
                    tool, args = "cancel_reminder", {"reminder_id": int(argument)}
                else:
                    recipient, subject, body = (s.strip() for s in argument.split("|", 2))
                    tool, args = "draft_email", dict(recipient=recipient, subject=subject, body=body)
                result = await asyncio.to_thread(tools.execute, tool, args)
            elif name == "drafts":
                result = store.drafts(user_id) or "No drafts."
            elif name in ("draft", "send"):
                draft = store.get_draft(user_id, int(argument))
                if not draft:
                    raise ValueError("Draft not found")
                if name == "draft":
                    result = draft
                else:
                    if not store.claim_draft(user_id, draft["id"]):
                        raise ValueError(f"Email was already sent or sending has started (state={draft['state']}). Inspect with /draft ID; create a replacement with /email if needed.")
                    try:
                        await asyncio.to_thread(send_email, draft["recipient"], draft["subject"], draft["body"])
                    except Exception:
                        # SMTP may accept a message then disconnect: do not blindly retry.
                        store.draft_state(user_id, draft["id"], "uncertain")
                        raise
                    store.draft_state(user_id, draft["id"], "sent")
                    result = "Email accepted by the SMTP server."
            else:
                result = "Unknown command. Use /help."
            await chunks(message, json.dumps(result, ensure_ascii=False, indent=2) if not isinstance(result, str) else result)
        except Exception as exc:
            log.warning("Command %s failed (%s)", name, type(exc).__name__)
            await chunks(message, f"Error: {type(exc).__name__}: {exc}\nCheck the command format (/help) and .env settings.")

    async def chat(update, context):
        if not await allowed(update):
            return
        message = update.effective_message
        if len(message.text) > 6000:
            await message.reply_text("Please shorten your request to 6000 characters.")
            return
        await context.bot.send_chat_action(update.effective_chat.id, "typing")
        try:
            result = await agent.reply(update.effective_user.id, update.effective_chat.id, message.text)
            await chunks(message, result)
        except Exception as exc:
            log.warning("Agent request failed (%s)", type(exc).__name__)
            # A preceding tool may have completed before a later model request failed.
            await message.reply_text(f"The model did not complete its response ({type(exc).__name__}). Check Ollama, /status and the model name. Actions may have completed: check /reminders and /drafts before retrying. Direct commands: /help.")

    async def deliver(context):
        for row in store.due():
            if row["user_id"] not in config.allowed:
                continue
            try:
                await context.bot.send_message(row["chat_id"], f"⏰ Reminder #{row['id']}: {row['text']}")
                store.delivered(row["id"])
            except Exception as exc:
                log.warning("Reminder %s not delivered (%s); retrying next poll", row["id"], type(exc).__name__)

    async def on_error(update, context):
        log.error("Telegram update failed (%s)", type(context.error).__name__)

    application = Application.builder().token(config.token).concurrent_updates(False).build()
    application.add_handler(CommandHandler(["start", "help", "reset", "status", "search", "read", "remind", "reminders", "cancel", "email", "drafts", "draft", "send", "inbox"], command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    application.add_error_handler(on_error)
    application.job_queue.run_repeating(deliver, interval=5, first=1)
    if not config.allowed:
        log.warning("ALLOWED_USER_IDS is empty: bot will only display IDs and deny access")
    application.run_polling(allowed_updates=[Update.MESSAGE], drop_pending_updates=True)


if __name__ == "__main__":
    main()
