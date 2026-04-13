import os
import json
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, asdict

from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./scheduler.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})


def init_db():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                last_login TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS accounts (
                account_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                api_key TEXT NOT NULL,
                api_secret TEXT NOT NULL,
                access_token TEXT NOT NULL,
                access_token_secret TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                post_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                text TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                image_paths TEXT DEFAULT '[]',
                status TEXT DEFAULT 'pending',
                tweet_id TEXT,
                error TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """))
        conn.commit()


@dataclass
class ScheduledPost:
    post_id: str
    account_id: str
    text: str
    scheduled_at: datetime
    image_paths: str = "[]"
    status: str = "pending"
    tweet_id: Optional[str] = None
    error: Optional[str] = None


class Database:
    def __init__(self):
        init_db()

    # ── Users ─────────────────────────────────────────────────────────────────

    def create_user(self, username: str, display_name: str, password_hash: str, role: str = "member"):
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO users (username, display_name, password_hash, role)
                VALUES (:username, :display_name, :password_hash, :role)
            """), {"username": username, "display_name": display_name, "password_hash": password_hash, "role": role})
            conn.commit()

    def get_user(self, username: str) -> Optional[dict]:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM users WHERE username = :u AND is_active = 1"),
                {"u": username}
            ).mappings().first()
            return dict(row) if row else None

    def get_all_users(self) -> list[dict]:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT username, display_name, role, is_active, created_at, last_login FROM users ORDER BY created_at DESC")).mappings().all()
            return [dict(r) for r in rows]

    def update_user_password(self, username: str, password_hash: str):
        with engine.connect() as conn:
            conn.execute(text("UPDATE users SET password_hash = :h WHERE username = :u"), {"h": password_hash, "u": username})
            conn.commit()

    def update_user_role(self, username: str, role: str):
        with engine.connect() as conn:
            conn.execute(text("UPDATE users SET role = :r WHERE username = :u"), {"r": role, "u": username})
            conn.commit()

    def set_user_active(self, username: str, is_active: bool):
        with engine.connect() as conn:
            conn.execute(text("UPDATE users SET is_active = :a WHERE username = :u"), {"a": int(is_active), "u": username})
            conn.commit()

    def touch_last_login(self, username: str):
        with engine.connect() as conn:
            conn.execute(text("UPDATE users SET last_login = datetime('now') WHERE username = :u"), {"u": username})
            conn.commit()

    def user_exists(self, username: str) -> bool:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT 1 FROM users WHERE username = :u"), {"u": username}).first()
            return row is not None

    # ── Accounts ──────────────────────────────────────────────────────────────

    def upsert_account(self, data: dict):
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO accounts (account_id, label, api_key, api_secret, access_token, access_token_secret)
                VALUES (:account_id, :label, :api_key, :api_secret, :access_token, :access_token_secret)
                ON CONFLICT(account_id) DO UPDATE SET
                    label=excluded.label,
                    api_key=excluded.api_key,
                    api_secret=excluded.api_secret,
                    access_token=excluded.access_token,
                    access_token_secret=excluded.access_token_secret
            """), data)
            conn.commit()

    def get_accounts(self) -> list[dict]:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT * FROM accounts ORDER BY created_at DESC")).mappings().all()
            return [dict(r) for r in rows]

    def get_account(self, account_id: str) -> Optional[dict]:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM accounts WHERE account_id = :id"),
                {"id": account_id}
            ).mappings().first()
            return dict(row) if row else None

    def delete_account(self, account_id: str):
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM accounts WHERE account_id = :id"), {"id": account_id})
            conn.commit()

    # ── Posts ─────────────────────────────────────────────────────────────────

    def save_post(self, post: ScheduledPost):
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO scheduled_posts (post_id, account_id, text, scheduled_at, image_paths, status)
                VALUES (:post_id, :account_id, :text, :scheduled_at, :image_paths, :status)
            """), {
                "post_id": post.post_id,
                "account_id": post.account_id,
                "text": post.text,
                "scheduled_at": post.scheduled_at.isoformat(),
                "image_paths": post.image_paths,
                "status": post.status,
            })
            conn.commit()

    def get_post(self, post_id: str) -> Optional[dict]:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM scheduled_posts WHERE post_id = :id"),
                {"id": post_id}
            ).mappings().first()
            return dict(row) if row else None

    def get_posts(self, status: Optional[str] = None) -> list[dict]:
        with engine.connect() as conn:
            if status:
                rows = conn.execute(
                    text("SELECT * FROM scheduled_posts WHERE status = :s ORDER BY scheduled_at ASC"),
                    {"s": status}
                ).mappings().all()
            else:
                rows = conn.execute(
                    text("SELECT * FROM scheduled_posts ORDER BY scheduled_at DESC")
                ).mappings().all()
            return [dict(r) for r in rows]

    def update_post_status(self, post_id: str, status: str, tweet_id: str = None, error: str = None):
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE scheduled_posts
                SET status = :status,
                    tweet_id = :tweet_id,
                    error = :error,
                    updated_at = datetime('now')
                WHERE post_id = :post_id
            """), {"post_id": post_id, "status": status, "tweet_id": tweet_id, "error": error})
            conn.commit()


db = Database()
