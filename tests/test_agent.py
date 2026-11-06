import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent.llm import Agent
from agent.store import Store
from agent.tools import Tools, public_url


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "test.sqlite3")

    def test_reminder_survives_restart_and_is_owner_scoped(self):
        reminder = self.store.add_reminder(1, 100, 60, "break")
        restored = Store(self.store.path)
        self.assertEqual(restored.reminders(1)[0]["id"], reminder["id"])
        self.assertEqual(restored.reminders(2), [])
        self.assertFalse(restored.cancel(2, reminder["id"])["cancelled"])
        with restored.connect() as db:
            db.execute("UPDATE reminders SET due=?", (time.time() - 1,))
        self.assertEqual(len(restored.due()), 1)
        restored.delivered(reminder["id"])
        self.assertEqual(restored.due(), [])

    def test_draft_ownership_and_duplicate_send_claim(self):
        draft = self.store.draft(1, "user@example.com", "Subject", "Body")
        self.assertIsNone(self.store.get_draft(2, draft["draft_id"]))
        self.assertEqual(self.store.drafts(2), [])
        self.assertEqual(self.store.drafts(1)[0]["id"], draft["draft_id"])
        self.assertFalse(self.store.claim_draft(2, draft["draft_id"]))
        self.assertTrue(self.store.claim_draft(1, draft["draft_id"]))
        self.assertFalse(self.store.claim_draft(1, draft["draft_id"]))

    def test_failed_tools_return_errors_without_executing_code(self):
        tools = Tools(self.store, 1, 100)
        self.assertIn("error", tools.execute("__import__", {}))
        self.assertIn("error", tools.execute("set_reminder", {"seconds": -1, "text": "bad"}))
        self.assertIn("error", tools.execute("set_reminder", {"wrong_argument": 1}))
        self.assertEqual(self.store.reminders(1), [])

    def test_private_webpage_blocked(self):
        for url in ("file:///secret", "http://127.0.0.1/", "http://localhost:11434/api/chat"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_url(url)


class ToolLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_receives_failed_tool_then_corrects_and_creates_reminder(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = SimpleNamespace(timeout=10, ollama_host="http://localhost:11434", model="test")
            store = Store(Path(tmp) / "db.sqlite3")
            agent = Agent(config, store)
            responses = [
                {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "set_reminder", "arguments": {"seconds": -1, "text": "break"}}}]},
                {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "set_reminder", "arguments": {"seconds": 60, "text": "break"}}}]},
                {"role": "assistant", "content": "Reminder created."},
            ]
            requests = []

            async def post(url, json):
                # Copy now: request histories are mutable while the loop continues.
                import copy
                requests.append(copy.deepcopy(json))
                message = responses.pop(0)
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"message": message})

            client = AsyncMock()
            client.post.side_effect = post
            client.__aenter__.return_value = client
            with patch("agent.llm.httpx.AsyncClient", return_value=client):
                answer = await agent.reply(1, 100, "Remind me in a minute")
            self.assertIn("Reminder created", answer)
            self.assertEqual(len(store.reminders(1)), 1)
            failed_result = requests[1]["messages"][-1]
            self.assertEqual(failed_result["role"], "tool")
            self.assertIn("error", json.loads(failed_result["content"]))
            self.assertEqual(json.loads(requests[2]["messages"][-1]["content"])["id"], 1)


if __name__ == "__main__":
    unittest.main()
