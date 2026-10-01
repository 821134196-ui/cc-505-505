"""数据库连接与建表。SQLite，WAL，外键开启。"""
import os
import sqlite3
from pathlib import Path

# 可用 SEAT_DATA_DIR 指向临时目录（测试用）
DATA_DIR = Path(os.environ.get("SEAT_DATA_DIR",
                              Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "seats.db"
PHOTO_DIR = DATA_DIR / "photos"

SCHEMA = """
-- 公共座椅台账
CREATE TABLE IF NOT EXISTS seats (
    id            INTEGER PRIMARY KEY,
    code          TEXT NOT NULL UNIQUE,          -- 编号，如 GY-001
    name          TEXT NOT NULL,                 -- 名称
    park          TEXT NOT NULL,                 -- 所属公园/街道
    location      TEXT NOT NULL,                 -- 文字位置描述
    lat           REAL NOT NULL,                 -- 模拟纬度
    lng           REAL NOT NULL,                 -- 模拟经度
    material      TEXT NOT NULL,                 -- wood 木质 / metal 金属 / stone 石材 / plastic 复合塑木
    armrest       TEXT NOT NULL DEFAULT 'none',  -- ok / damaged / none （扶手状态；none 表示无扶手）
    fixture       TEXT NOT NULL DEFAULT 'ok',    -- ok 固定牢固 / loose 螺栓松动 / moved 被搬动 / removed 已拆除待重装
    usable        INTEGER NOT NULL DEFAULT 1,    -- 1 可使用 0 封控中
    status_note   TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 市民报损（单条上报，可被归并到同一事件）
CREATE TABLE IF NOT EXISTS reports (
    id           INTEGER PRIMARY KEY,
    seat_id      INTEGER NOT NULL REFERENCES seats(id),
    damage_type  TEXT NOT NULL,   -- loose_bolt 松动螺栓 / broken_plank 木板断裂 /
                                  -- wet_paint 油漆未干 / moved 座椅被搬动 / removed 已拆除待重装 / other
    description  TEXT NOT NULL DEFAULT '',
    reporter     TEXT NOT NULL DEFAULT '匿名市民',
    phone        TEXT NOT NULL DEFAULT '',
    incident_id  INTEGER REFERENCES incidents(id),  -- 归并到的事件
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_reports_incident ON reports(incident_id);
CREATE INDEX IF NOT EXISTS idx_reports_seat ON reports(seat_id);

-- 未结事件：同一座椅的重复报损归并于此
CREATE TABLE IF NOT EXISTS incidents (
    id          INTEGER PRIMARY KEY,
    seat_id     INTEGER NOT NULL REFERENCES seats(id),
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',   -- open 未结 / closed 已结
    work_order_id INTEGER REFERENCES work_orders(id),
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    closed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_seat_status ON incidents(seat_id, status);

-- 维修工单
CREATE TABLE IF NOT EXISTS work_orders (
    id           INTEGER PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,          -- 工单号 WO-YYYYMMDD-NNN
    incident_id  INTEGER NOT NULL REFERENCES incidents(id),
    seat_id      INTEGER NOT NULL REFERENCES seats(id),
    summary      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',  -- open 待认领 / claimed 已接单 / done 待复验 / verified 复验通过
    crew         TEXT,                          -- 认领班组
    claimed_at   TEXT,
    finished_at  TEXT,
    repair_note  TEXT NOT NULL DEFAULT '',
    paint_wet    INTEGER NOT NULL DEFAULT 0,    -- 油漆未干标记：复验/解封前必须为 0
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 封控记录（警示带围蔽 / 撤除）
CREATE TABLE IF NOT EXISTS closures (
    id           INTEGER PRIMARY KEY,
    work_order_id INTEGER NOT NULL REFERENCES work_orders(id),
    seat_id      INTEGER NOT NULL REFERENCES seats(id),
    action       TEXT NOT NULL,                 -- tape_on 设置警示带 / tape_off 撤除警示带
    operator     TEXT NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 照片（本地存储，文件落在 data/photos/）
CREATE TABLE IF NOT EXISTS photos (
    id           INTEGER PRIMARY KEY,
    seat_id      INTEGER NOT NULL REFERENCES seats(id),
    work_order_id INTEGER REFERENCES work_orders(id),
    phase        TEXT NOT NULL,                 -- report 报损 / before 维修前 / after 维修后 / reopen 重开核对
    file_name    TEXT NOT NULL,
    uploader     TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 材料消耗
CREATE TABLE IF NOT EXISTS materials (
    id           INTEGER PRIMARY KEY,
    work_order_id INTEGER NOT NULL REFERENCES work_orders(id),
    name         TEXT NOT NULL,                -- 木板 / 螺栓 / 油漆 ...
    unit         TEXT NOT NULL DEFAULT '件',
    quantity     REAL NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 复验记录
CREATE TABLE IF NOT EXISTS inspections (
    id           INTEGER PRIMARY KEY,
    work_order_id INTEGER NOT NULL REFERENCES work_orders(id),
    inspector    TEXT NOT NULL,                 -- 复验巡查员
    passed       INTEGER NOT NULL,              -- 1 通过 0 驳回
    tape_removed INTEGER NOT NULL,              -- 核对：警示带已撤除
    reopen_photo_id INTEGER REFERENCES photos(id),
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 操作日志（并发认领等关键动作留痕）
CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY,
    actor   TEXT NOT NULL,
    action  TEXT NOT NULL,
    detail  TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db():
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()
