import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from paths import DATA_DIR

DB_PATH = DATA_DIR / "agent.db"
RETENTION_DAYS = 30
PURGE_INTERVAL_DAYS = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class SessionInfo:
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0


class SessionStore:
    """Session 持久化。每线程独立 SQLite 连接，供 TUI 后台线程安全读写。"""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._local = threading.local()
        self._schema_lock = threading.Lock()
        self._schema_ready = False
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            self._local.conn = conn
        return conn

    def _ensure_schema(self) -> None:
        with self._schema_lock:
            if self._schema_ready:
                return
            self._init_schema(self._connection())
            self._schema_ready = True

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL DEFAULT '新对话',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id        TEXT NOT NULL,
                role              TEXT NOT NULL,
                content           TEXT,
                reasoning_content TEXT,
                tool_calls        TEXT,
                tool_call_id      TEXT,
                sort_order        INTEGER NOT NULL,
                created_at        TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, sort_order);

            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        conn.commit()

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def create(self, title: str = "新对话") -> SessionInfo:
        session_id = _short_id()
        now = _now()
        conn = self._connection()
        conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, title, now, now),
        )
        conn.commit()
        return SessionInfo(id=session_id, title=title, created_at=now, updated_at=now)

    def list_sessions(self, limit: int = 20) -> list[SessionInfo]:
        conn = self._connection()
        rows = conn.execute(
            """
            SELECT s.id, s.title, s.created_at, s.updated_at,
                   COUNT(CASE WHEN m.role != 'system' THEN 1 END) AS message_count
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            GROUP BY s.id
            ORDER BY s.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            SessionInfo(
                id=row["id"],
                title=row["title"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                message_count=row["message_count"],
            )
            for row in rows
        ]

    def get(self, session_id: str) -> SessionInfo | None:
        conn = self._connection()
        row = conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role != 'system'",
            (session_id,),
        ).fetchone()[0]
        return SessionInfo(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            message_count=count,
        )

    def find(self, partial_id: str) -> SessionInfo | None:
        """精确匹配优先，其次前缀匹配（避免 LIKE %id% 误命中）。"""
        pid = partial_id.strip()
        if not pid:
            return None
        exact = self.get(pid)
        if exact is not None:
            return exact
        conn = self._connection()
        row = conn.execute(
            """
            SELECT id FROM sessions
            WHERE id LIKE ?
            ORDER BY LENGTH(id) ASC, updated_at DESC
            LIMIT 1
            """,
            (f"{pid}%",),
        ).fetchone()
        if row is None:
            return None
        return self.get(row["id"])

    def latest(self) -> SessionInfo | None:
        conn = self._connection()
        row = conn.execute(
            "SELECT id FROM sessions ORDER BY updated_at DESC LIMIT 1",
        ).fetchone()
        if row is None:
            return None
        return self.get(row["id"])

    def rename(self, session_id: str, title: str) -> None:
        conn = self._connection()
        conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), session_id),
        )
        conn.commit()

    def delete(self, session_id: str) -> bool:
        conn = self._connection()
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        return cur.rowcount > 0

    def touch(self, session_id: str) -> None:
        conn = self._connection()
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (_now(), session_id),
        )
        conn.commit()

    def load_messages(self, session_id: str) -> list[dict]:
        conn = self._connection()
        rows = conn.execute(
            """
            SELECT role, content, reasoning_content, tool_calls, tool_call_id
            FROM messages
            WHERE session_id = ?
            ORDER BY sort_order
            """,
            (session_id,),
        ).fetchall()
        messages = []
        for row in rows:
            msg: dict = {"role": row["role"]}
            if row["content"] is not None:
                msg["content"] = row["content"]
            if row["reasoning_content"]:
                msg["reasoning_content"] = row["reasoning_content"]
            if row["tool_calls"]:
                try:
                    msg["tool_calls"] = json.loads(row["tool_calls"])
                except json.JSONDecodeError:
                    # 损坏则丢弃 tool_calls；后续 orphan tool 由 sanitize 清理
                    pass
            if row["tool_call_id"]:
                msg["tool_call_id"] = row["tool_call_id"]
            messages.append(msg)
        return messages

    def save_messages(self, session_id: str, messages: list[dict]) -> None:
        conn = self._connection()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            now = _now()
            for i, msg in enumerate(messages):
                tool_calls = msg.get("tool_calls")
                tool_calls_json = None
                if tool_calls is not None:
                    try:
                        tool_calls_json = json.dumps(tool_calls, ensure_ascii=False)
                    except (TypeError, ValueError):
                        tool_calls_json = None
                conn.execute(
                    """
                    INSERT INTO messages
                        (session_id, role, content, reasoning_content, tool_calls, tool_call_id, sort_order, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        msg["role"],
                        msg.get("content"),
                        msg.get("reasoning_content"),
                        tool_calls_json,
                        msg.get("tool_call_id"),
                        i,
                        now,
                    ),
                )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def auto_title(self, session_id: str, first_user_message: str) -> None:
        info = self.get(session_id)
        if info is None or info.title != "新对话":
            return
        title = first_user_message.strip().replace("\n", " ")[:40]
        if title:
            self.rename(session_id, title)

    def _get_meta(self, key: str) -> str | None:
        conn = self._connection()
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        conn = self._connection()
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()

    def purge_old_sessions(self, days: int = RETENTION_DAYS) -> list[str]:
        conn = self._connection()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT id FROM sessions WHERE updated_at < ?",
            (cutoff,),
        ).fetchall()
        ids = [row["id"] for row in rows]
        for session_id in ids:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        return ids

    def maybe_auto_purge(
        self,
        retention_days: int = RETENTION_DAYS,
        interval_days: int = PURGE_INTERVAL_DAYS,
    ) -> list[str]:
        last_purge = self._get_meta("last_purge")
        if last_purge:
            last_dt = datetime.fromisoformat(last_purge)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            elapsed = datetime.now(timezone.utc) - last_dt
            if elapsed < timedelta(days=interval_days):
                return []

        deleted = self.purge_old_sessions(retention_days)
        self._set_meta("last_purge", _now())
        return deleted
