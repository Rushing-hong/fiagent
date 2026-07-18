"""本地研究库：分钟/日线缓存、一致预期快照、可交易池点位快照。

路径默认 data/research.db（gitignore）。多次拉取后可累积近端分钟与共识历史。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paths import DATA_DIR

DB_PATH = DATA_DIR / "research.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResearchStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._local = threading.local()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure()

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode = WAL")
            self._local.conn = c
        return c

    def _ensure(self) -> None:
        conn = self._conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS bars (
                code TEXT NOT NULL,
                interval TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL, volume REAL,
                PRIMARY KEY (code, interval, trade_date)
            );
            CREATE INDEX IF NOT EXISTS idx_bars_range
                ON bars(code, interval, trade_date);

            CREATE TABLE IF NOT EXISTS consensus_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                asof TEXT NOT NULL,
                source TEXT NOT NULL,
                fiscal_year TEXT,
                eps REAL,
                meta_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_cons_code
                ON consensus_snapshots(code, asof);

            CREATE TABLE IF NOT EXISTS universe_snapshots (
                asof TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT 'default',
                codes_json TEXT NOT NULL,
                meta_json TEXT,
                PRIMARY KEY (asof, name)
            );

            CREATE TABLE IF NOT EXISTS trade_calendar (
                trade_date TEXT PRIMARY KEY,
                is_open INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS macro_series (
                indicator TEXT NOT NULL,
                asof TEXT NOT NULL,
                value REAL,
                unit TEXT,
                frequency TEXT,
                source TEXT,
                fetch_time TEXT,
                PRIMARY KEY (indicator, asof, source)
            );
            CREATE INDEX IF NOT EXISTS idx_macro_ind
                ON macro_series(indicator, asof);

            CREATE TABLE IF NOT EXISTS factor_values (
                asof TEXT NOT NULL,
                code TEXT NOT NULL,
                factor_id TEXT NOT NULL,
                value REAL,
                purpose TEXT NOT NULL,
                PRIMARY KEY (asof, code, factor_id, purpose)
            );
            CREATE INDEX IF NOT EXISTS idx_fv_factor_asof
                ON factor_values(factor_id, asof);
            CREATE INDEX IF NOT EXISTS idx_fv_code_asof
                ON factor_values(code, asof);
            CREATE INDEX IF NOT EXISTS idx_fv_purpose
                ON factor_values(purpose, asof, factor_id);

            CREATE TABLE IF NOT EXISTS micro_signals (
                asof TEXT NOT NULL,
                code TEXT NOT NULL,
                signal_id TEXT NOT NULL,
                value REAL,
                unit TEXT,
                meta_json TEXT,
                PRIMARY KEY (asof, code, signal_id)
            );

            CREATE TABLE IF NOT EXISTS run_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT,
                created_at TEXT,
                payload_json TEXT
            );
            """
        )
        conn.commit()

    # --- trade calendar ---
    def replace_trade_calendar(self, days: list[str]) -> int:
        conn = self._conn()
        conn.execute("DELETE FROM trade_calendar")
        conn.executemany(
            "INSERT INTO trade_calendar(trade_date, is_open) VALUES(?,1)",
            [(d,) for d in days],
        )
        conn.commit()
        return len(days)

    def count_trade_days(self) -> int:
        cur = self._conn().execute("SELECT COUNT(*) FROM trade_calendar")
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def load_trade_days(self, start: str | None = None, end: str | None = None) -> list[str]:
        q = "SELECT trade_date FROM trade_calendar WHERE is_open=1"
        args: list[Any] = []
        if start:
            q += " AND trade_date >= ?"
            args.append(start[:10])
        if end:
            q += " AND trade_date <= ?"
            args.append(end[:10])
        q += " ORDER BY trade_date"
        return [r[0] for r in self._conn().execute(q, args).fetchall()]

    # --- macro ---
    def upsert_macro_points(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        conn = self._conn()
        payload = []
        for r in rows:
            payload.append((
                str(r["indicator"]),
                str(r["asof"])[:10],
                float(r["value"]) if r.get("value") is not None else None,
                r.get("unit"),
                r.get("frequency"),
                r.get("source") or "akshare",
                r.get("fetch_time") or _now(),
            ))
        conn.executemany(
            """
            INSERT INTO macro_series(indicator, asof, value, unit, frequency, source, fetch_time)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(indicator, asof, source) DO UPDATE SET
              value=excluded.value, unit=excluded.unit, frequency=excluded.frequency,
              fetch_time=excluded.fetch_time
            """,
            payload,
        )
        conn.commit()
        return len(payload)

    def load_macro(
        self,
        indicator: str,
        *,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        q = "SELECT indicator, asof, value, unit, frequency, source, fetch_time FROM macro_series WHERE indicator=?"
        args: list[Any] = [indicator]
        if start:
            q += " AND asof >= ?"
            args.append(start[:10])
        if end:
            q += " AND asof <= ?"
            args.append(end[:10])
        q += " ORDER BY asof"
        return [dict(r) for r in self._conn().execute(q, args).fetchall()]

    # --- factors (bulk) ---
    def upsert_factor_values(self, rows: list[tuple[str, str, str, float, str]]) -> int:
        """rows: (asof, code, factor_id, value, purpose)."""
        if not rows:
            return 0
        conn = self._conn()
        conn.executemany(
            """
            INSERT INTO factor_values(asof, code, factor_id, value, purpose)
            VALUES(?,?,?,?,?)
            ON CONFLICT(asof, code, factor_id, purpose) DO UPDATE SET value=excluded.value
            """,
            rows,
        )
        conn.commit()
        return len(rows)

    def prune_factor_values(self, keep_from: str) -> int:
        cur = self._conn().execute(
            "DELETE FROM factor_values WHERE asof < ?", (keep_from[:10],)
        )
        self._conn().commit()
        return int(cur.rowcount or 0)

    def upsert_micro_signals(self, rows: list[dict[str, Any]]) -> int:
        """rows: asof, code, signal_id, value, unit?, meta_json?"""
        if not rows:
            return 0
        conn = self._conn()
        payload = []
        for r in rows:
            meta = r.get("meta_json")
            if isinstance(meta, dict):
                meta = json.dumps(meta, ensure_ascii=False)
            payload.append((
                str(r["asof"])[:10],
                str(r["code"]),
                str(r["signal_id"]),
                float(r["value"]) if r.get("value") is not None else None,
                r.get("unit"),
                meta,
            ))
        conn.executemany(
            """
            INSERT INTO micro_signals(asof, code, signal_id, value, unit, meta_json)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(asof, code, signal_id) DO UPDATE SET
              value=excluded.value, unit=excluded.unit, meta_json=excluded.meta_json
            """,
            payload,
        )
        conn.commit()
        return len(payload)

    def upsert_bars(
        self,
        code: str,
        interval: str,
        rows: list[dict[str, Any]],
    ) -> int:
        if not rows:
            return 0
        conn = self._conn()
        n = 0
        for r in rows:
            td = str(r.get("trade_date") or "")
            if not td:
                continue
            conn.execute(
                """
                INSERT INTO bars(code, interval, trade_date, open, high, low, close, volume)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(code, interval, trade_date) DO UPDATE SET
                  open=excluded.open, high=excluded.high, low=excluded.low,
                  close=excluded.close, volume=excluded.volume
                """,
                (
                    code,
                    interval,
                    td,
                    float(r.get("open") or 0),
                    float(r.get("high") or 0),
                    float(r.get("low") or 0),
                    float(r.get("close") or 0),
                    float(r.get("volume") or 0),
                ),
            )
            n += 1
        conn.commit()
        return n

    def load_bars(
        self,
        code: str,
        interval: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        conn = self._conn()
        q = "SELECT trade_date, open, high, low, close, volume FROM bars WHERE code=? AND interval=?"
        args: list[Any] = [code, interval]
        if start_date:
            q += " AND trade_date >= ?"
            args.append(start_date)
        if end_date:
            # include full day for date-only end
            end_key = end_date if " " in end_date else end_date + " 23:59:59"
            q += " AND trade_date <= ?"
            args.append(end_key)
        q += " ORDER BY trade_date"
        cur = conn.execute(q, args)
        return [
            {
                "trade_date": row["trade_date"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
            }
            for row in cur.fetchall()
        ]

    def save_consensus(
        self,
        code: str,
        *,
        source: str,
        points: list[dict[str, Any]],
        asof: str | None = None,
    ) -> int:
        asof = asof or _now()[:10]
        conn = self._conn()
        n = 0
        for p in points:
            year = str(p.get("year") or p.get("fiscal_year") or "")
            eps = p.get("eps")
            if eps is None:
                continue
            meta = json.dumps(
                {k: v for k, v in p.items() if k not in ("year", "fiscal_year", "eps")},
                ensure_ascii=False,
            )
            conn.execute(
                """
                DELETE FROM consensus_snapshots
                WHERE code=? AND asof=? AND source=? AND fiscal_year=?
                """,
                (code, asof, source, year),
            )
            conn.execute(
                """
                INSERT INTO consensus_snapshots(code, asof, source, fiscal_year, eps, meta_json)
                VALUES(?,?,?,?,?,?)
                """,
                (code, asof, source, year, float(eps), meta),
            )
            n += 1
        conn.commit()
        return n

    def load_consensus_history(self, code: str, days: int = 365) -> list[dict[str, Any]]:
        conn = self._conn()
        cur = conn.execute(
            """
            SELECT asof, source, fiscal_year, eps, meta_json
            FROM consensus_snapshots
            WHERE code=?
            ORDER BY asof ASC
            """,
            (code,),
        )
        out = []
        for row in cur.fetchall():
            out.append({
                "asof": row["asof"],
                "source": row["source"],
                "year": row["fiscal_year"],
                "eps": row["eps"],
            })
        if days > 0 and out:
            from datetime import timezone

            cutoff = (
                datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days)
            ).strftime("%Y-%m-%d")
            out = [x for x in out if str(x["asof"])[:10] >= cutoff]
        return out

    def save_universe(
        self,
        codes: list[str],
        *,
        asof: str | None = None,
        name: str = "default",
        meta: dict[str, Any] | None = None,
    ) -> str:
        asof = asof or datetime.now().strftime("%Y-%m-%d")
        conn = self._conn()
        conn.execute(
            """
            INSERT INTO universe_snapshots(asof, name, codes_json, meta_json)
            VALUES(?,?,?,?)
            ON CONFLICT(asof, name) DO UPDATE SET
              codes_json=excluded.codes_json, meta_json=excluded.meta_json
            """,
            (asof, name, json.dumps(codes, ensure_ascii=False), json.dumps(meta or {}, ensure_ascii=False)),
        )
        conn.commit()
        return asof

    def load_universe_pit(
        self,
        asof: str,
        *,
        name: str = "default",
    ) -> dict[str, Any] | None:
        """Nearest snapshot on or before asof."""
        conn = self._conn()
        cur = conn.execute(
            """
            SELECT asof, name, codes_json, meta_json FROM universe_snapshots
            WHERE name=? AND asof <= ?
            ORDER BY asof DESC LIMIT 1
            """,
            (name, asof),
        )
        row = cur.fetchone()
        # 禁止向前取未来快照（避免存活偏差）；无 asof<= 则返回 None
        if row is None:
            return None
        return {
            "asof": row["asof"],
            "name": row["name"],
            "codes": json.loads(row["codes_json"]),
            "meta": json.loads(row["meta_json"] or "{}"),
            "requested_asof": asof,
        }

    def list_universes(self, name: str = "default", limit: int = 50) -> list[dict[str, Any]]:
        conn = self._conn()
        cur = conn.execute(
            """
            SELECT asof, name, codes_json FROM universe_snapshots
            WHERE name=? ORDER BY asof DESC LIMIT ?
            """,
            (name, limit),
        )
        return [
            {"asof": r["asof"], "name": r["name"], "count": len(json.loads(r["codes_json"]))}
            for r in cur.fetchall()
        ]


_STORE: ResearchStore | None = None


def get_store() -> ResearchStore:
    global _STORE
    if _STORE is None:
        _STORE = ResearchStore()
    return _STORE
