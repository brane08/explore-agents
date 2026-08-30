"""Server-side state: sessions, invocations, trace events, error records.

`Store` is the drop-in seam (UI-PLANE §6 targets PostgreSQL); `SQLiteStore`
is the POC implementation. SQL is kept engine-portable: TEXT/INTEGER columns,
no engine-specific syntax beyond `INSERT OR`-free upsert avoidance.

The trace IS the stream (UI-PLANE §4): every SSE event persists 1:1 here with
a monotonically increasing per-invocation seq, and reconnects replay from it.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    session_id  TEXT PRIMARY KEY,
    user_sub    TEXT NOT NULL,
    catalog_ref TEXT NOT NULL,
    thread_id   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS invocation (
    invocation_id TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    agent_id      TEXT,
    status        TEXT NOT NULL DEFAULT 'running',
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS trace_event (
    invocation_id TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    event_type    TEXT NOT NULL,
    data          TEXT NOT NULL,
    PRIMARY KEY (invocation_id, seq)
);
CREATE TABLE IF NOT EXISTS error_record (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    code     TEXT NOT NULL,
    residual TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS stale_queue (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL,
    reason   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS supervised_run (
    run_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id       TEXT NOT NULL,
    entry_version  TEXT NOT NULL,
    model_profile  TEXT NOT NULL,
    invocation_id  TEXT NOT NULL,
    mode           TEXT NOT NULL,
    verdict        TEXT NOT NULL DEFAULT 'pending',
    reason         TEXT NOT NULL DEFAULT '',
    counterpart_id TEXT,
    adjudicated_by TEXT,
    adjudicated_at TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""


@dataclass
class Session:
    session_id: str
    user_sub: str
    catalog_ref: str
    thread_id: str


@dataclass
class Invocation:
    invocation_id: str
    session_id: str
    agent_id: str | None
    status: str


@dataclass
class TraceEvent:
    seq: int
    event_type: str
    data: dict


@dataclass
class SupervisedRun:
    run_id: int
    entry_id: str
    entry_version: str
    model_profile: str
    invocation_id: str
    mode: str
    verdict: str
    reason: str
    counterpart_id: str | None
    adjudicated_by: str | None


class Store(Protocol):
    def create_session(self, user_sub: str, catalog_ref: str) -> Session: ...
    def get_session(self, session_id: str) -> Session | None: ...
    def create_invocation(self, session_id: str, agent_id: str | None) -> Invocation: ...
    def get_invocation(self, invocation_id: str) -> Invocation | None: ...
    def set_invocation(self, invocation_id: str, *, status: str | None = None,
                       agent_id: str | None = None) -> None: ...
    def append_event(self, invocation_id: str, event_type: str, data: dict) -> int: ...
    def events_after(self, invocation_id: str, after_seq: int) -> list[TraceEvent]: ...
    def record_error(self, code: str, residual: str) -> None: ...
    def error_records(self) -> list[tuple[str, str]]: ...
    def queue_stale(self, entry_id: str, reason: str) -> None: ...
    def stale_queue(self) -> list[tuple[str, str]]: ...
    def open_supervised_run(self, *, entry_id: str, entry_version: str,
                            model_profile: str, invocation_id: str, mode: str,
                            counterpart_id: str | None = None) -> int: ...
    def close_supervised_run(self, run_id: int, *, verdict: str, reason: str = "",
                             adjudicated_by: str | None = None) -> None: ...
    def supervised_streak(self, entry_id: str, entry_version: str,
                          model_profile: str) -> int: ...
    def supervised_runs(self, entry_id: str) -> list[SupervisedRun]: ...
    def pending_supervised_runs(self) -> list[SupervisedRun]: ...


class SQLiteStore:
    def __init__(self, db_path: Path | str) -> None:
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _write(self, sql: str, args: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, args)
            self._conn.commit()

    def _query(self, sql: str, args: tuple = ()) -> list:
        # rows are fetched under the lock: the connection is shared across
        # threads, and another thread's execute/commit between our execute
        # and fetch would reset the pending cursor
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    # -- sessions -----------------------------------------------------------

    def create_session(self, user_sub: str, catalog_ref: str) -> Session:
        sid, tid = uuid.uuid4().hex, uuid.uuid4().hex
        self._write(
            "INSERT INTO session (session_id, user_sub, catalog_ref, thread_id) VALUES (?,?,?,?)",
            (sid, user_sub, catalog_ref, tid),
        )
        return Session(sid, user_sub, catalog_ref, tid)

    def get_session(self, session_id: str) -> Session | None:
        rows = self._query(
            "SELECT session_id, user_sub, catalog_ref, thread_id FROM session WHERE session_id=?",
            (session_id,),
        )
        return Session(*rows[0]) if rows else None

    # -- invocations ----------------------------------------------------------

    def create_invocation(self, session_id: str, agent_id: str | None) -> Invocation:
        iid = uuid.uuid4().hex
        self._write(
            "INSERT INTO invocation (invocation_id, session_id, agent_id) VALUES (?,?,?)",
            (iid, session_id, agent_id),
        )
        return Invocation(iid, session_id, agent_id, "running")

    def get_invocation(self, invocation_id: str) -> Invocation | None:
        rows = self._query(
            "SELECT invocation_id, session_id, agent_id, status FROM invocation "
            "WHERE invocation_id=?",
            (invocation_id,),
        )
        return Invocation(*rows[0]) if rows else None

    def set_invocation(self, invocation_id: str, *, status: str | None = None,
                       agent_id: str | None = None) -> None:
        if status is not None:
            self._write("UPDATE invocation SET status=? WHERE invocation_id=?",
                       (status, invocation_id))
        if agent_id is not None:
            self._write("UPDATE invocation SET agent_id=? WHERE invocation_id=?",
                       (agent_id, invocation_id))

    # -- trace (the stream persists here 1:1) ---------------------------------

    def append_event(self, invocation_id: str, event_type: str, data: dict) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM trace_event WHERE invocation_id=?",
                (invocation_id,),
            )
            seq = cur.fetchone()[0]
            self._conn.execute(
                "INSERT INTO trace_event (invocation_id, seq, event_type, data) VALUES (?,?,?,?)",
                (invocation_id, seq, event_type, json.dumps(data, sort_keys=True)),
            )
            self._conn.commit()
        return seq

    def events_after(self, invocation_id: str, after_seq: int) -> list[TraceEvent]:
        rows = self._query(
            "SELECT seq, event_type, data FROM trace_event "
            "WHERE invocation_id=? AND seq>? ORDER BY seq",
            (invocation_id, after_seq),
        )
        return [TraceEvent(seq, et, json.loads(data)) for seq, et, data in rows]

    # -- error corpus + stale queue -------------------------------------------

    def record_error(self, code: str, residual: str) -> None:
        self._write("INSERT INTO error_record (code, residual) VALUES (?,?)", (code, residual))

    def error_records(self) -> list[tuple[str, str]]:
        return self._query("SELECT code, residual FROM error_record ORDER BY id")

    def queue_stale(self, entry_id: str, reason: str) -> None:
        self._write("INSERT INTO stale_queue (entry_id, reason) VALUES (?,?)", (entry_id, reason))

    def stale_queue(self) -> list[tuple[str, str]]:
        return self._query("SELECT entry_id, reason FROM stale_queue ORDER BY id")

    # -- supervised runs -------------------------------------------------------

    _RUN_COLS = ("run_id, entry_id, entry_version, model_profile, invocation_id, "
                 "mode, verdict, reason, counterpart_id, adjudicated_by")

    def open_supervised_run(self, *, entry_id: str, entry_version: str,
                            model_profile: str, invocation_id: str, mode: str,
                            counterpart_id: str | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO supervised_run (entry_id, entry_version, model_profile,"
                " invocation_id, mode, counterpart_id) VALUES (?,?,?,?,?,?)",
                (entry_id, entry_version, model_profile, invocation_id, mode,
                 counterpart_id),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def close_supervised_run(self, run_id: int, *, verdict: str, reason: str = "",
                             adjudicated_by: str | None = None) -> None:
        if verdict not in {"clean", "incident", "void"}:
            raise ValueError(f"cannot close a run as {verdict!r}")
        rows = self._query(
            "SELECT verdict FROM supervised_run WHERE run_id=?", (run_id,))
        if not rows:
            raise ValueError(f"no supervised run {run_id!r}")
        if rows[0][0] != "pending":
            raise ValueError(
                f"supervised run {run_id!r} is already {rows[0][0]!r}, not pending")
        self._write(
            "UPDATE supervised_run SET verdict=?, reason=?, adjudicated_by=?,"
            " adjudicated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE run_id=?",
            (verdict, reason, adjudicated_by, run_id),
        )

    def supervised_streak(self, entry_id: str, entry_version: str,
                          model_profile: str) -> int:
        # Voided and still-pending runs did not measure the agent: they neither
        # extend the streak nor break it, so they are filtered out before the
        # consecutive-clean count rather than treated as a gap.
        rows = self._query(
            "SELECT verdict FROM supervised_run WHERE entry_id=? AND entry_version=?"
            " AND model_profile=? AND verdict IN ('clean','incident')"
            " ORDER BY run_id DESC",
            (entry_id, entry_version, model_profile),
        )
        streak = 0
        for (verdict,) in rows:
            if verdict != "clean":
                break
            streak += 1
        return streak

    def supervised_runs(self, entry_id: str) -> list[SupervisedRun]:
        rows = self._query(
            f"SELECT {self._RUN_COLS} FROM supervised_run WHERE entry_id=?"
            " ORDER BY run_id", (entry_id,))
        return [SupervisedRun(*r) for r in rows]

    def pending_supervised_runs(self) -> list[SupervisedRun]:
        rows = self._query(
            f"SELECT {self._RUN_COLS} FROM supervised_run WHERE verdict='pending'"
            " ORDER BY run_id")
        return [SupervisedRun(*r) for r in rows]
