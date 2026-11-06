# Local AI Agent

A local Python + Ollama assistant that runs as a Telegram bot. It connects model-generated tool calls to Python functions for web research, Telegram reminders and email. Tool failures are returned to the model so it can correct arguments instead of pretending an action succeeded.

**Stack:** Python 3.12, Ollama HTTP API, python-telegram-bot, DDGS, HTTPX, BeautifulSoup, SQLite, SMTP/IMAP.

## Setup

Run from this repository's directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

1. Create a Telegram bot with [@BotFather](https://t.me/BotFather), then put its token in `.env` as `TELEGRAM_BOT_TOKEN`.
2. Install [Ollama](https://ollama.com/), start it, and download a tool-capable model:

   ```powershell
   ollama pull qwen3:4b
   ```

   If Ollama is not already running, start `ollama serve` in another terminal. Set `OLLAMA_MODEL` if using another model. A local 4B model can be slow on CPU; commands below still work independently of the model.
3. Run the bot, send `/start` in a **private** Telegram chat, copy the numeric ID from the access-denied response into `ALLOWED_USER_IDS`, then restart. This is a user ID, not a bot ID or username. Only listed users are allowed to use the bot; an empty list denies everyone.

   ```powershell
   .\.venv\Scripts\python.exe -m agent
   ```

4. Run `/status` to check the local server and installed models. Set optional SMTP/IMAP configuration only if you need mail. `.env`, local databases and logs are gitignored.

No Telegram token, mailbox credentials or local model is bundled. Telegram and mail require internet access; inference stays on your configured Ollama server. Search queries are sent to search providers; page and email excerpts used by tools enter the local model context. Run one bot process per token/database.

## Use in Telegram

Natural-language examples:

- `Find the latest Python news and include source links.`
- `Read https://example.com and summarize it briefly.`
- `Remind me in 5 minutes to take a break.`
- `Draft an email to user@example.com with subject Meeting and body Let's meet tomorrow.`
- `Show my latest three emails.`

Direct commands bypass model interpretation:

| Command | Action |
| --- | --- |
| `/search query` | Search and return source snippets/URLs |
| `/read https://...` | Read a public text/HTML page |
| `/remind 60 Take a break` | Telegram notification in 60 seconds |
| `/reminders` | Pending reminders with UTC due times |
| `/cancel 1` | Cancel your reminder by ID |
| `/email user@example.com \| Subject \| Body` | Create an email draft |
| `/draft 1` | Show your draft and its state |
| `/drafts` | List your latest drafts and delivery states |
| `/send 1` | Send a reviewed draft via SMTP |
| `/inbox` | Read the latest five emails via IMAP |
| `/reset` | Clear model conversation history |
| `/status` | Check Ollama connectivity and model names |
| `/help` | Show command help |

Email tools create drafts; only the explicit `/send ID` command sends mail. The draft response contains the recipient, subject and body for review. Recipient ownership is checked on every draft lookup and send. A draft is claimed before sending to prevent duplicate submissions. If SMTP fails after submission may have started, its state becomes `uncertain`; check Sent/server status before creating a replacement. Missing SMTP configuration also requires creating a fresh draft after fixing configuration.

For SMTP use a provider-supported app password. Port 587 uses STARTTLS; implicit TLS on 465 requires `SMTP_SSL=true` and `SMTP_PORT=465`. Inbox reading uses IMAP over SSL and does not mark messages read. Set `IMAP_HOST`, `IMAP_USER` and `IMAP_PASSWORD` separately from SMTP.

## How it works

`Telegram message → Ollama /api/chat → tool_calls → allowlisted Python function → tool result → Ollama → Telegram reply`

The loop allows up to six model rounds and eight tool calls per round. Unknown functions and bad arguments become structured tool errors. The last five complete turns are kept in memory; `/reset` or restart clears them. Reminder/draft changes are returned alongside model text so their IDs remain visible. Reminders and email drafts are stored in SQLite and survive restarts.

The scheduler checks reminders every five seconds. Overdue reminders are delivered when the bot starts again. Delivery is at least once: a crash between Telegram accepting a message and the SQLite update can cause a duplicate. The process must stay running to deliver on time. These are one-off reminders, not a recurring calendar service.

Research uses search results plus optional public-page extraction. JavaScript-only pages, paywalls, rate limits and bot protection may prevent access; the agent reports tool errors. Page fetching blocks local/private addresses and validates redirect destinations. It is a personal local prototype, not a public web-fetching proxy. No arbitrary shell or code execution tool is exposed.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests exercise persistent reminders, user isolation, duplicate-send claims, invalid tool requests, private URL rejection and an Ollama tool-call loop that corrects a failed action. Ollama responses in unit tests are mocked; live Telegram and mailbox integration requires your credentials.

API references: [Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling), [Telegram application builder](https://docs.python-telegram-bot.org/en/stable/telegram.ext.applicationbuilder.html), [JobQueue](https://docs.python-telegram-bot.org/en/stable/telegram.ext.jobqueue.html), [DDGS](https://pypi.org/project/ddgs/).
