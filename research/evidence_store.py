"""Evidence ledger and research run persistence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paths import DATA_DIR

RESEARCH_DB = DATA_DIR / "research.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class ResearchRun:
    id: str
    query: str
    mode: str
    status: str
    created_at: str


@dataclass
class EvidenceRecord:
    evidence_id: str
    run_id: str
    symbol: str
    source: str
    as_of_time: str
    pit_safe: bool
    quality: str
    payload: dict[str, Any]


class EvidenceStore:
    def __init__(self, db_path: Path = RESEARCH_DB) -> None:
        self.db_path = db_path
        self._local = threading.local()
        self._connections: set[sqlite3.Connection] = set()
        self._connections_lock = threading.RLock()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=15.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 15000")
            self._local.conn = conn
            with self._connections_lock:
                self._connections.add(conn)
        return conn

    def _ensure_schema(self) -> None:
        self._conn().executescript("""
            CREATE TABLE IF NOT EXISTS research_runs (
                id          TEXT PRIMARY KEY,
                query       TEXT NOT NULL,
                mode        TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'running',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_reports (
                id          TEXT PRIMARY KEY,
                run_id      TEXT NOT NULL,
                agent_name  TEXT NOT NULL,
                task_id     TEXT,
                content     TEXT NOT NULL,
                structured  TEXT,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES research_runs(id)
            );

            CREATE TABLE IF NOT EXISTS evidence (
                evidence_id TEXT PRIMARY KEY,
                run_id      TEXT NOT NULL,
                symbol      TEXT,
                source      TEXT,
                as_of_time  TEXT,
                pit_safe    INTEGER NOT NULL DEFAULT 0,
                quality     TEXT NOT NULL DEFAULT 'unknown',
                payload     TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES research_runs(id)
            );

            CREATE TABLE IF NOT EXISTS agent_tasks (
                id          TEXT PRIMARY KEY,
                run_id      TEXT NOT NULL,
                agent_name  TEXT NOT NULL,
                status      TEXT NOT NULL,
                depends_on  TEXT,
                created_at  TEXT NOT NULL,
                finished_at TEXT,
                FOREIGN KEY (run_id) REFERENCES research_runs(id)
            );

            CREATE TABLE IF NOT EXISTS policy_decisions (
                id          TEXT PRIMARY KEY,
                run_id      TEXT NOT NULL,
                engine      TEXT NOT NULL,
                payload     TEXT NOT NULL,
                approved    INTEGER NOT NULL,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES research_runs(id)
            );

            CREATE TABLE IF NOT EXISTS trade_attribution (
                id          TEXT PRIMARY KEY,
                run_id      TEXT NOT NULL,
                journal_path TEXT,
                payload     TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES research_runs(id)
            );

            CREATE TABLE IF NOT EXISTS decision_lineage (
                id          TEXT PRIMARY KEY,
                run_id      TEXT NOT NULL,
                symbol      TEXT,
                step        TEXT NOT NULL,
                payload     TEXT NOT NULL,
                parent_id   TEXT,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES research_runs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_lineage_run ON decision_lineage(run_id);
            CREATE INDEX IF NOT EXISTS idx_lineage_symbol ON decision_lineage(symbol);

            CREATE TABLE IF NOT EXISTS claims (
                id          TEXT PRIMARY KEY,
                run_id      TEXT NOT NULL,
                agent_name  TEXT NOT NULL,
                claim_type  TEXT NOT NULL,
                payload     TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES research_runs(id)
            );

            CREATE TABLE IF NOT EXISTS tool_calls (
                id          TEXT PRIMARY KEY,
                run_id      TEXT NOT NULL,
                agent_name  TEXT NOT NULL,
                tool_name   TEXT NOT NULL,
                arguments   TEXT NOT NULL,
                result_snip TEXT NOT NULL,
                success     INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES research_runs(id)
            );

            CREATE TABLE IF NOT EXISTS validated_lessons (
                id          TEXT PRIMARY KEY,
                source_run_id TEXT,
                symbol      TEXT,
                lesson      TEXT NOT NULL,
                validated   INTEGER NOT NULL DEFAULT 1,
                payload     TEXT,
                created_at  TEXT NOT NULL
            );
        """)
        self._conn().commit()

    def start_run(self, query: str, mode: str) -> ResearchRun:
        run_id = _short_id()
        now = _now()
        self._conn().execute(
            "INSERT INTO research_runs (id, query, mode, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'running', ?, ?)",
            (run_id, query, mode, now, now),
        )
        self._conn().commit()
        return ResearchRun(id=run_id, query=query, mode=mode, status="running", created_at=now)

    def finish_run(self, run_id: str, status: str = "completed") -> None:
        self._conn().execute(
            "UPDATE research_runs SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), run_id),
        )
        self._conn().commit()

    def list_recent_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT id, query, mode, status, created_at, updated_at "
            "FROM research_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [{
            "run_id": r["id"],
            "query": r["query"],
            "mode": r["mode"],
            "status": r["status"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        } for r in rows]

    def get_run_detail(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn().execute(
            "SELECT id, query, mode, status, created_at, updated_at "
            "FROM research_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "run_id": row["id"],
            "query": row["query"],
            "mode": row["mode"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "tasks": self.list_tasks(run_id),
            "reports": self.list_reports(run_id),
            "evidence": [
                {
                    "evidence_id": e.evidence_id,
                    "symbol": e.symbol,
                    "pit_safe": e.pit_safe,
                    "quality": e.quality,
                    "source": e.source,
                }
                for e in self.list_evidence(run_id)
            ],
            "claims": self.list_claims(run_id),
            "policy": self.list_policy_decisions(run_id),
            "lineage": self.get_lineage_chain(run_id),
            "tool_calls": self.list_tool_calls(run_id),
        }

    def save_report(
        self,
        run_id: str,
        agent_name: str,
        content: str,
        *,
        task_id: str | None = None,
        structured: dict[str, Any] | None = None,
    ) -> str:
        report_id = _short_id()
        self._conn().execute(
            "INSERT INTO agent_reports (id, run_id, agent_name, task_id, content, structured, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                report_id,
                run_id,
                agent_name,
                task_id,
                content,
                json.dumps(structured, ensure_ascii=False) if structured else None,
                _now(),
            ),
        )
        self._conn().commit()
        return report_id

    def list_reports(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT agent_name, content, structured, created_at FROM agent_reports "
            "WHERE run_id = ? ORDER BY created_at",
            (run_id,),
        ).fetchall()
        out = []
        for r in rows:
            out.append({
                "agent_name": r["agent_name"],
                "content": r["content"],
                "structured": json.loads(r["structured"]) if r["structured"] else None,
                "created_at": r["created_at"],
            })
        return out

    def add_evidence(
        self,
        run_id: str,
        *,
        symbol: str = "",
        source: str = "",
        as_of_time: str = "",
        pit_safe: bool = False,
        quality: str = "unknown",
        fields: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> EvidenceRecord:
        payload = dict(extra or {})
        if fields:
            payload["fields"] = fields
        declared_id = payload.get("evidence_id")
        if (
            isinstance(declared_id, str)
            and declared_id.startswith(f"EV-{run_id}-")
        ):
            existing = self._conn().execute(
                "SELECT * FROM evidence WHERE evidence_id = ? AND run_id = ?",
                (declared_id, run_id),
            ).fetchone()
            if existing is not None:
                existing_payload = json.loads(existing["payload"])
                existing_payload.update(payload)
                merged_symbol = symbol or existing["symbol"] or ""
                merged_source = source or existing["source"] or ""
                merged_as_of = as_of_time or existing["as_of_time"] or ""
                merged_pit_safe = bool(pit_safe or existing["pit_safe"])
                merged_quality = (
                    quality
                    if quality and quality != "unknown"
                    else existing["quality"]
                )
                blob = json.dumps(existing_payload, ensure_ascii=False, sort_keys=True)
                self._conn().execute(
                    "UPDATE evidence SET symbol = ?, source = ?, as_of_time = ?, "
                    "pit_safe = ?, quality = ?, payload = ? WHERE evidence_id = ?",
                    (
                        merged_symbol,
                        merged_source,
                        merged_as_of,
                        int(merged_pit_safe),
                        merged_quality,
                        blob,
                        declared_id,
                    ),
                )
                self._conn().commit()
                return EvidenceRecord(
                    evidence_id=declared_id,
                    run_id=run_id,
                    symbol=merged_symbol,
                    source=merged_source,
                    as_of_time=merged_as_of,
                    pit_safe=merged_pit_safe,
                    quality=merged_quality,
                    payload=existing_payload,
                )
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(blob.encode()).hexdigest()[:12]
        ev_id = f"EV-{run_id}-{digest}"
        now = _now()
        self._conn().execute(
            "INSERT OR REPLACE INTO evidence "
            "(evidence_id, run_id, symbol, source, as_of_time, pit_safe, quality, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ev_id, run_id, symbol, source, as_of_time, int(pit_safe), quality, blob, now),
        )
        self._conn().commit()
        return EvidenceRecord(
            evidence_id=ev_id,
            run_id=run_id,
            symbol=symbol,
            source=source,
            as_of_time=as_of_time,
            pit_safe=pit_safe,
            quality=quality,
            payload=payload,
        )

    def list_evidence(self, run_id: str) -> list[EvidenceRecord]:
        rows = self._conn().execute(
            "SELECT * FROM evidence WHERE run_id = ? ORDER BY created_at",
            (run_id,),
        ).fetchall()
        out: list[EvidenceRecord] = []
        for r in rows:
            out.append(EvidenceRecord(
                evidence_id=r["evidence_id"],
                run_id=r["run_id"],
                symbol=r["symbol"] or "",
                source=r["source"] or "",
                as_of_time=r["as_of_time"] or "",
                pit_safe=bool(r["pit_safe"]),
                quality=r["quality"],
                payload=json.loads(r["payload"]),
            ))
        return out

    def start_task(self, run_id: str, agent_name: str, depends_on: list[str] | None = None) -> str:
        task_id = _short_id()
        self._conn().execute(
            "INSERT INTO agent_tasks (id, run_id, agent_name, status, depends_on, created_at) "
            "VALUES (?, ?, ?, 'running', ?, ?)",
            (task_id, run_id, agent_name, json.dumps(depends_on or []), _now()),
        )
        self._conn().commit()
        return task_id

    def finish_task(self, task_id: str, status: str = "completed") -> None:
        self._conn().execute(
            "UPDATE agent_tasks SET status = ?, finished_at = ? WHERE id = ?",
            (status, _now(), task_id),
        )
        self._conn().commit()

    def list_tasks(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT id, agent_name, status, depends_on, created_at, finished_at "
            "FROM agent_tasks WHERE run_id = ? ORDER BY created_at, id",
            (run_id,),
        ).fetchall()
        return [{
            "task_id": row["id"],
            "agent_name": row["agent_name"],
            "status": row["status"],
            "depends_on": json.loads(row["depends_on"] or "[]"),
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        } for row in rows]

    def close_thread(self) -> None:
        """Release the connection owned by the current worker thread."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            self._local.conn = None
            with self._connections_lock:
                self._connections.discard(conn)
            conn.close()

    def close(self) -> None:
        """Close every thread-local connection owned by this store."""
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        self._local.conn = None
        for conn in connections:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def save_policy_decision(
        self,
        run_id: str,
        engine: str,
        payload: dict,
        *,
        approved: bool,
    ) -> str:
        decision_id = _short_id()
        self._conn().execute(
            "INSERT INTO policy_decisions (id, run_id, engine, payload, approved, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                decision_id,
                run_id,
                engine,
                json.dumps(payload, ensure_ascii=False),
                int(approved),
                _now(),
            ),
        )
        self._conn().commit()
        return decision_id

    def list_policy_decisions(self, run_id: str) -> list[dict]:
        rows = self._conn().execute(
            "SELECT engine, payload, approved, created_at FROM policy_decisions "
            "WHERE run_id = ? ORDER BY created_at",
            (run_id,),
        ).fetchall()
        out = []
        for r in rows:
            out.append({
                "engine": r["engine"],
                "payload": json.loads(r["payload"]),
                "approved": bool(r["approved"]),
                "created_at": r["created_at"],
            })
        return out

    def save_trade_attribution(
        self,
        run_id: str,
        journal_path: str,
        payload: dict,
    ) -> str:
        row_id = _short_id()
        self._conn().execute(
            "INSERT INTO trade_attribution (id, run_id, journal_path, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (row_id, run_id, journal_path, json.dumps(payload, ensure_ascii=False), _now()),
        )
        self._conn().commit()
        return row_id

    def save_lineage_step(
        self,
        run_id: str,
        symbol: str,
        step: str,
        payload: dict,
        *,
        parent_id: str | None = None,
    ) -> str:
        row_id = _short_id()
        self._conn().execute(
            "INSERT INTO decision_lineage (id, run_id, symbol, step, payload, parent_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row_id,
                run_id,
                symbol.upper() if symbol else "",
                step,
                json.dumps(payload, ensure_ascii=False),
                parent_id,
                _now(),
            ),
        )
        self._conn().commit()
        return row_id

    def get_lineage_chain(self, run_id: str, symbol: str | None = None) -> list[dict]:
        if symbol:
            rows = self._conn().execute(
                "SELECT id, symbol, step, payload, parent_id, created_at FROM decision_lineage "
                "WHERE run_id = ? AND symbol = ? ORDER BY created_at",
                (run_id, symbol.upper()),
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT id, symbol, step, payload, parent_id, created_at FROM decision_lineage "
                "WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            out.append({
                "id": r["id"],
                "symbol": r["symbol"],
                "step": r["step"],
                "payload": json.loads(r["payload"]),
                "parent_id": r["parent_id"],
                "created_at": r["created_at"],
            })
        return out

    def find_lineage_for_symbol(self, symbol: str, limit: int = 5) -> list[dict]:
        sym = symbol.upper()
        code = sym.split(".")[0]
        rows = self._conn().execute(
            "SELECT d.id, d.run_id, d.symbol, d.step, d.payload, d.created_at, r.query, r.mode "
            "FROM decision_lineage d "
            "JOIN research_runs r ON r.id = d.run_id "
            "WHERE d.symbol = ? OR d.symbol = ? OR d.payload LIKE ? "
            "ORDER BY d.created_at DESC LIMIT ?",
            (sym, code, f"%{code}%", limit * 4),
        ).fetchall()
        seen: set[str] = set()
        out: list[dict] = []
        for r in rows:
            key = f"{r['run_id']}:{r['step']}:{r['symbol']}"
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "run_id": r["run_id"],
                "query": r["query"],
                "mode": r["mode"],
                "symbol": r["symbol"],
                "step": r["step"],
                "payload": json.loads(r["payload"]),
                "created_at": r["created_at"],
            })
            if len(out) >= limit:
                break
        return out

    def save_claim(
        self,
        run_id: str,
        agent_name: str,
        claim_type: str,
        payload: dict,
    ) -> str:
        row_id = _short_id()
        self._conn().execute(
            "INSERT INTO claims (id, run_id, agent_name, claim_type, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                row_id, run_id, agent_name, claim_type,
                json.dumps(payload, ensure_ascii=False), _now(),
            ),
        )
        self._conn().commit()
        return row_id

    def list_claims(self, run_id: str) -> list[dict]:
        rows = self._conn().execute(
            "SELECT agent_name, claim_type, payload, created_at FROM claims "
            "WHERE run_id = ? ORDER BY created_at",
            (run_id,),
        ).fetchall()
        return [{
            "agent_name": r["agent_name"],
            "claim_type": r["claim_type"],
            "payload": json.loads(r["payload"]),
            "created_at": r["created_at"],
        } for r in rows]

    def log_tool_call(
        self,
        run_id: str,
        agent_name: str,
        tool_name: str,
        arguments: str,
        result_snip: str,
        *,
        success: bool = True,
    ) -> str:
        row_id = _short_id()
        self._conn().execute(
            "INSERT INTO tool_calls "
            "(id, run_id, agent_name, tool_name, arguments, result_snip, success, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row_id, run_id, agent_name, tool_name, arguments,
                result_snip[:4000], int(success), _now(),
            ),
        )
        self._conn().commit()
        return row_id

    def list_tool_calls(self, run_id: str, agent_name: str | None = None) -> list[dict]:
        if agent_name:
            rows = self._conn().execute(
                "SELECT agent_name, tool_name, arguments, result_snip, success, created_at "
                "FROM tool_calls WHERE run_id = ? AND agent_name = ? ORDER BY created_at",
                (run_id, agent_name),
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT agent_name, tool_name, arguments, result_snip, success, created_at "
                "FROM tool_calls WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        return [{
            "agent_name": r["agent_name"],
            "tool_name": r["tool_name"],
            "arguments": r["arguments"],
            "result_snip": r["result_snip"],
            "success": bool(r["success"]),
            "created_at": r["created_at"],
        } for r in rows]

    def save_validated_lesson(
        self,
        lesson: str,
        *,
        source_run_id: str = "",
        symbol: str = "",
        payload: dict | None = None,
    ) -> str:
        row_id = _short_id()
        self._conn().execute(
            "INSERT INTO validated_lessons "
            "(id, source_run_id, symbol, lesson, validated, payload, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (
                row_id, source_run_id, symbol.upper() if symbol else "",
                lesson, json.dumps(payload or {}, ensure_ascii=False), _now(),
            ),
        )
        self._conn().commit()
        return row_id

    def list_validated_lessons(self, symbol: str | None = None, limit: int = 20) -> list[dict]:
        if symbol:
            rows = self._conn().execute(
                "SELECT source_run_id, symbol, lesson, payload, created_at "
                "FROM validated_lessons WHERE symbol = ? OR symbol = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (symbol.upper(), symbol.split(".")[0], limit),
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT source_run_id, symbol, lesson, payload, created_at "
                "FROM validated_lessons ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{
            "source_run_id": r["source_run_id"],
            "symbol": r["symbol"],
            "lesson": r["lesson"],
            "payload": json.loads(r["payload"]) if r["payload"] else {},
            "created_at": r["created_at"],
        } for r in rows]

    def find_recent_runs_by_symbol(self, symbol: str, limit: int = 5) -> list[dict]:
        sym = symbol.upper()
        code = sym.split(".")[0]
        rows = self._conn().execute(
            "SELECT r.id, r.query, r.mode, r.created_at, a.agent_name, a.content "
            "FROM research_runs r "
            "JOIN agent_reports a ON a.run_id = r.id "
            "WHERE a.content LIKE ? OR a.content LIKE ? "
            "ORDER BY r.created_at DESC LIMIT ?",
            (f"%{sym}%", f"%{code}%", limit * 3),
        ).fetchall()
        seen: set[str] = set()
        out: list[dict] = []
        for r in rows:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            out.append({
                "run_id": r["id"],
                "query": r["query"],
                "mode": r["mode"],
                "created_at": r["created_at"],
                "matched_agent": r["agent_name"],
                "snippet": (r["content"] or "")[:400],
            })
            if len(out) >= limit:
                break
        return out
