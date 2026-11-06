"""SQLite persistence. Owner checks apply to every user-visible lookup."""
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL, due REAL NOT NULL, text TEXT NOT NULL,
                    delivered INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS drafts (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                    recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'draft');
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_reminder(self, user_id, chat_id, seconds, text):
        seconds = float(seconds)
        if not 1 <= seconds <= 366 * 86400 or not text.strip() or len(text) > 3000:
            raise ValueError("Reminder: 1 second..366 days and 1..3000 characters")
        with self.connect() as db:
            cur = db.execute("INSERT INTO reminders(user_id,chat_id,due,text) VALUES(?,?,?,?)",
                             (user_id, chat_id, time.time() + seconds, text))
            return {"id": cur.lastrowid, "in_seconds": seconds, "text": text}

    def reminders(self, user_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,due,text FROM reminders WHERE user_id=? AND delivered=0 ORDER BY due", (user_id,))]

    def cancel(self, user_id, reminder_id):
        with self.connect() as db:
            cur = db.execute("DELETE FROM reminders WHERE id=? AND user_id=? AND delivered=0", (int(reminder_id), user_id))
            return {"cancelled": cur.rowcount == 1}

    def due(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM reminders WHERE delivered=0 AND due<=? ORDER BY due LIMIT 20", (time.time(),))]

    def delivered(self, reminder_id):
        with self.connect() as db:
            db.execute("UPDATE reminders SET delivered=1 WHERE id=?", (reminder_id,))

    def draft(self, user_id, recipient, subject, body):
        if not recipient or "@" not in recipient or any(c in recipient + subject for c in "\r\n"):
            raise ValueError("Provide a valid recipient and a single-line subject")
        if not subject.strip() or not body.strip() or len(body) > 20000:
            raise ValueError("Email needs a subject and body (max 20000 characters)")
        with self.connect() as db:
            cur = db.execute("INSERT INTO drafts(user_id,recipient,subject,body) VALUES(?,?,?,?)", (user_id, recipient, subject, body))
            return {"draft_id": cur.lastrowid, "to": recipient, "subject": subject, "body": body,
                    "next_step": f"Review and send with /send {cur.lastrowid}"}

    def get_draft(self, user_id, draft_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM drafts WHERE id=? AND user_id=?", (int(draft_id), user_id)).fetchone()
            return dict(row) if row else None

    def drafts(self, user_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,recipient,subject,state FROM drafts WHERE user_id=? ORDER BY id DESC LIMIT 20", (user_id,))]

    def claim_draft(self, user_id, draft_id):
        with self.connect() as db:
            return db.execute("UPDATE drafts SET state='sending' WHERE id=? AND user_id=? AND state='draft'", (int(draft_id), user_id)).rowcount == 1

    def draft_state(self, user_id, draft_id, state):
        with self.connect() as db:
            db.execute("UPDATE drafts SET state=? WHERE id=? AND user_id=?", (state, int(draft_id), user_id))
