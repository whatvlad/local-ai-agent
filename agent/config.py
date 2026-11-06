import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    token: str
    allowed: frozenset[int]
    ollama_host: str
    model: str
    timeout: float
    database: Path


def load_config():
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("Set TELEGRAM_BOT_TOKEN in .env (copy .env.example first)")
    allowed = frozenset(int(x.strip()) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip())
    db = Path(os.getenv("DATABASE_PATH", "data/agent.sqlite3"))
    if not db.is_absolute():
        db = Path(__file__).resolve().parent.parent / db
    return Config(token, allowed, os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/"),
                  os.getenv("OLLAMA_MODEL", "qwen3:4b"), float(os.getenv("OLLAMA_TIMEOUT", "180")), db)
