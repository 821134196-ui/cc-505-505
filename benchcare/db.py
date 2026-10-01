"""SQLite 持久化层：建表与连接管理。"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS benches (
    id              TEXT PRIMARY KEY,              -- 座椅编号，如 B-001
    material        TEXT NOT NULL,                 -- wood / metal / stone / plastic
    lat             REAL NOT NULL,
    lng             REAL NOT NULL,
    location_name   TEXT NOT NULL,                 -- 位置描述，如「河滨公园·东门步道」
    armrest         TEXT NOT NULL DEFAULT 'ok',    -- ok / loose / missing
    fixture         TEXT NOT NULL DEFAULT 'ok',    -- ok / loose / broken
    usable          INTEGER NOT NULL DEFAULT 1,    -- 可使用标记
    open_event_id   INTEGER,                       -- 当前未结事件（归并重复报损）
    removed         INTEGER NOT NULL DEFAULT 0,    -- 已拆除待重装
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bench_id        TEXT NOT NULL REFERENCES benches(id),
    issue_type      TEXT NOT NULL,                 -- loose_bolt/wood_split/wet_paint/moved/removed
    status          TEXT NOT NULL DEFAULT 'reported', -- reported/claimed/repairing/awaiting_review/verified/closed
    report_count    INTEGER NOT NULL DEFAULT 1,
    first_reporter  TEXT NOT NULL DEFAULT '匿名市民',
    merged_reports  TEXT NOT NULL DEFAULT '[]',    -- JSON: [{reporter,note,at}]
    claimed_by      TEXT,
    claimed_at      TEXT,
    -- 封控记录
    cordon          INTEGER NOT NULL DEFAULT 0,
    cordon_by       TEXT,
    cordon_at       TEXT,
    cordon_note     TEXT,
    -- 维修
    repair_note     TEXT,
    repaired_by     TEXT,
    repaired_at     TEXT,
    before_photo    TEXT,
    after_photo     TEXT,
    -- 复验
    reviewed_by     TEXT,
    reviewed_at     TEXT,
    review_note      TEXT,
    review_passed   INTEGER,
    tape_removed    INTEGER NOT NULL DEFAULT 0,    -- 复验前须确认警示带已撤除
    -- 油漆
    paint_applied_at TEXT,                         -- 涂漆时间；未干前禁止复验通过
    paint_dry_minutes INTEGER NOT NULL DEFAULT 60,
    -- 重装
    reinstalled_at  TEXT,
    closed_at       TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_events_bench ON events(bench_id);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);

CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL REFERENCES events(id),
    bench_id    TEXT NOT NULL,
    reporter    TEXT NOT NULL DEFAULT '匿名市民',
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS work_orders (
    id          TEXT PRIMARY KEY,                  -- 工单编号 WO-20261001-0001
    event_id    INTEGER NOT NULL UNIQUE REFERENCES events(id),
    crew        TEXT,
    status      TEXT NOT NULL DEFAULT 'open',      -- open/claimed/done/reviewed
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS materials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL REFERENCES events(id),
    item        TEXT NOT NULL,
    qty         REAL NOT NULL,
    unit        TEXT NOT NULL,
    used_by     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_materials_event ON materials(event_id);

CREATE TABLE IF NOT EXISTS event_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL,
    actor       TEXT NOT NULL DEFAULT '系统',
    action      TEXT NOT NULL,
    detail      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_log_event ON event_log(event_id);
"""

_tls = threading.local()


def get_conn(db_path: str | Path) -> sqlite3.Connection:
    """每个线程一条连接（SQLite 连接不能跨线程共享）。"""
    key = str(db_path)
    conns = getattr(_tls, "conns", None)
    if conns is None:
        conns = _tls.conns = {}
    conn = conns.get(key)
    if conn is None:
        conn = sqlite3.connect(key, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        conns[key] = conn
    return conn


def init_db(db_path: str | Path) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn(db_path)
    conn.executescript(SCHEMA)
