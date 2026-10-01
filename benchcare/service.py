"""领域服务：报损归并、工单认领、封控、维修、复验、材料消耗、照片存储。

关键规则
--------
* 重复报损归并到同一「未结事件」（同座椅 + 同损坏类型）；类型不同或事件已结
  不会错误归并。
* 认领工单用单条条件 UPDATE 原子抢占，并发下仅一个班组成功。
* 维修人员不能复验自己的工单；复验须由复验员完成。
* 复验通过前必须：警示带已撤除 + 维修后照片已上传；油漆未干一律阻断。
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from . import db as dbmod
from .models import DEFAULT_PAINT_DRY_MINUTES, ISSUE_TYPES

OPEN_STATUSES = ("reported", "claimed", "repairing", "awaiting_review")


class ServiceError(Exception):
    """业务校验失败。"""


class NotFound(ServiceError):
    pass


class ConflictError(ServiceError):
    """并发冲突（如工单已被他人认领）。"""


def now() -> datetime:
    return datetime.now()


class LocationAdapter:
    """定位服务接口（真实实现可接高德/系统 GPS）。"""

    def current_position(self) -> dict[str, float]:
        raise NotImplementedError


class MockLocationAdapter(LocationAdapter):
    """模拟定位适配器：在公园范围内随机游走的固定巡更点。"""

    # 公园中心附近的模拟巡更点（与种子数据同一坐标系）
    _POINTS = [
        (30.2512, 120.1555, "河滨公园东门"),
        (30.2498, 120.1530, "河滨公园湖心亭"),
        (30.2485, 120.1568, "滨河步道中段"),
        (30.2505, 120.1512, "市民广场北侧"),
    ]

    def __init__(self) -> None:
        self._i = 0
        self._lock = threading.Lock()

    def current_position(self) -> dict[str, Any]:
        with self._lock:
            lat, lng, name = self._POINTS[self._i % len(self._POINTS)]
            self._i += 1
        # 模拟微小漂移
        frac = (self._i % 7) * 0.00008
        return {"lat": round(lat + frac, 6), "lng": round(lng - frac, 6), "desc": name}


class BenchService:
    def __init__(
        self,
        db_path: str | Path,
        photo_dir: str | Path | None = None,
        location: LocationAdapter | None = None,
        clock: Callable[[], datetime] = now,
    ) -> None:
        self.db_path = str(db_path)
        dbmod.init_db(self.db_path)
        self.photo_dir = Path(photo_dir) if photo_dir else Path(self.db_path).parent / "photos"
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        self.location = location or MockLocationAdapter()
        self.clock = clock

    # ---- 基础工具 ----------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        return dbmod.get_conn(self.db_path)

    def _log(self, conn: sqlite3.Connection, event_id: int, action: str,
             actor: str = "系统", detail: str = "") -> None:
        conn.execute(
            "INSERT INTO event_log(event_id, actor, action, detail) VALUES(?,?,?,?)",
            (event_id, actor, action, detail),
        )

    def _get_bench(self, conn: sqlite3.Connection, bench_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM benches WHERE id=?", (bench_id,)).fetchone()
        if not row:
            raise NotFound(f"座椅 {bench_id} 不存在")
        return row

    def _get_event(self, conn: sqlite3.Connection, event_id: int) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise NotFound(f"事件 #{event_id} 不存在")
        return row

    # ---- 座椅台账 ----------------------------------------------------------

    def list_benches(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM benches ORDER BY removed, id"
        ).fetchall()
        return [dict(r) for r in rows]

    def bench_detail(self, bench_id: str) -> dict[str, Any]:
        conn = self.conn
        bench = dict(self._get_bench(conn, bench_id))
        events = conn.execute(
            "SELECT * FROM events WHERE bench_id=? ORDER BY id DESC", (bench_id,)
        ).fetchall()
        bench["events"] = [self._event_brief(dict(e)) for e in events]
        return bench

    def add_bench(self, bench_id: str, material: str, lat: float, lng: float,
                  location_name: str, armrest: str = "ok", fixture: str = "ok") -> dict:
        if not re.fullmatch(r"B-\d{3,}", bench_id):
            raise ServiceError("座椅编号格式应为 B-001 形式")
        if material not in ("wood", "metal", "stone", "plastic"):
            raise ServiceError("材质不合法")
        conn = self.conn
        try:
            conn.execute(
                "INSERT INTO benches(id,material,lat,lng,location_name,armrest,fixture)"
                " VALUES(?,?,?,?,?,?,?)",
                (bench_id, material, lat, lng, location_name, armrest, fixture),
            )
        except sqlite3.IntegrityError as e:
            raise ConflictError(f"座椅 {bench_id} 已存在") from e
        return self.bench_detail(bench_id)

    def update_bench_parts(self, bench_id: str, armrest: str | None = None,
                           fixture: str | None = None) -> None:
        conn = self.conn
        self._get_bench(conn, bench_id)
        if armrest:
            conn.execute("UPDATE benches SET armrest=? WHERE id=?", (armrest, bench_id))
        if fixture:
            conn.execute("UPDATE benches SET fixture=? WHERE id=?", (fixture, bench_id))

    # ---- 报损与归并 --------------------------------------------------------

    def report_damage(self, bench_id: str, issue_type: str, reporter: str = "匿名市民",
                      note: str = "") -> dict[str, Any]:
        if issue_type not in ISSUE_TYPES:
            raise ServiceError("损坏类型不合法")
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            bench = self._get_bench(conn, bench_id)
            open_event = None
            if bench["open_event_id"]:
                open_event = conn.execute(
                    "SELECT * FROM events WHERE id=? AND status IN (?,?,?,?)",
                    (bench["open_event_id"], *OPEN_STATUSES),
                ).fetchone()

            if open_event is not None:
                if open_event["issue_type"] != issue_type:
                    # 防止错误归并：不同损坏类型不能并入同一事件
                    raise ConflictError(
                        f"该座椅已有未结事件 #{open_event['id']}"
                        f"（{ISSUE_TYPES[open_event['issue_type']]['label']}），"
                        "不同损坏类型不能归并，请在原事件上跟进"
                    )
                # —— 重复报损归并 ——
                merged = json.loads(open_event["merged_reports"] or "[]")
                merged.append({"reporter": reporter, "note": note,
                               "at": self.clock().strftime("%Y-%m-%d %H:%M:%S")})
                conn.execute(
                    "UPDATE events SET report_count=report_count+1,"
                    " merged_reports=? WHERE id=?",
                    (json.dumps(merged, ensure_ascii=False), open_event["id"]),
                )
                conn.execute(
                    "INSERT INTO reports(event_id,bench_id,reporter,note) VALUES(?,?,?,?)",
                    (open_event["id"], bench_id, reporter, note),
                )
                self._log(conn, open_event["id"], "重复报损已归并", reporter, note)
                event_id = open_event["id"]
                merged_flag = True
            else:
                # —— 新事件 ——
                cur = conn.execute(
                    "INSERT INTO events(bench_id,issue_type,first_reporter,merged_reports)"
                    " VALUES(?,?,?,?)",
                    (bench_id, issue_type, reporter,
                     json.dumps([{"reporter": reporter, "note": note,
                                  "at": self.clock().strftime("%Y-%m-%d %H:%M:%S")}],
                                ensure_ascii=False)),
                )
                event_id = cur.lastrowid
                conn.execute(
                    "INSERT INTO reports(event_id,bench_id,reporter,note) VALUES(?,?,?,?)",
                    (event_id, bench_id, reporter, note),
                )
                wo_no = self._next_wo_no(conn)
                conn.execute(
                    "INSERT INTO work_orders(id,event_id) VALUES(?,?)",
                    (wo_no, event_id),
                )
                # 报损即停用：松动螺栓等一律先打上「不可使用」
                conn.execute(
                    "UPDATE benches SET usable=0, open_event_id=?,"
                    " removed=? WHERE id=?",
                    (event_id, 1 if issue_type == "removed" else 0, bench_id),
                )
                self._log(conn, event_id, "市民报损", reporter,
                          ISSUE_TYPES[issue_type]["label"])
                merged_flag = False
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return {"event_id": event_id, "merged": merged_flag,
                "event": self.event_detail(event_id)}

    def _next_wo_no(self, conn: sqlite3.Connection) -> str:
        day = self.clock().strftime("%Y%m%d")
        prefix = f"WO-{day}-"
        row = conn.execute(
            "SELECT COUNT(*) c FROM work_orders WHERE id LIKE ?", (prefix + "%",)
        ).fetchone()
        return f"{prefix}{row['c'] + 1:04d}"

    # ---- 工单 --------------------------------------------------------------

    def list_work_orders(self, status: str | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT wo.*, e.bench_id, e.issue_type, e.status AS event_status,"
               " e.report_count FROM work_orders wo JOIN events e ON e.id=wo.event_id")
        args: tuple = ()
        if status:
            sql += " WHERE wo.status=?"
            args = (status,)
        sql += " ORDER BY wo.id DESC"
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def claim(self, event_id: int, crew: str) -> dict[str, Any]:
        """班组接单。条件 UPDATE 保证两个班组并发抢单只有一个成功。"""
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            ev = self._get_event(conn, event_id)
            if ev["status"] not in ("reported",):
                if ev["claimed_by"] and ev["claimed_by"] != crew:
                    raise ConflictError(
                        f"工单已被班组「{ev['claimed_by']}」认领"
                    )
                raise ConflictError(f"事件当前状态不可认领：{ev['status']}")
            ts = self.clock().strftime("%Y-%m-%d %H:%M:%S")
            cur = conn.execute(
                "UPDATE events SET status='claimed', claimed_by=?, claimed_at=? "
                "WHERE id=? AND status='reported'",
                (crew, ts, event_id),
            )
            if cur.rowcount != 1:
                # 并发下被别的班组抢先
                raise ConflictError("工单刚被其他班组认领，请刷新重试")
            conn.execute(
                "UPDATE work_orders SET status='claimed', crew=? WHERE event_id=?",
                (crew, event_id),
            )
            self._log(conn, event_id, "班组认领", crew)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return self.event_detail(event_id)

    # ---- 封控 --------------------------------------------------------------

    def cordon(self, event_id: int, crew: str, note: str = "") -> dict[str, Any]:
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            ev = self._get_event(conn, event_id)
            self._require_crew(ev, crew)
            if ev["status"] not in ("claimed", "repairing", "awaiting_review"):
                raise ServiceError("认领后才能设置封控")
            ts = self.clock().strftime("%Y-%m-%d %H:%M:%S")
            kind = ISSUE_TYPES[ev["issue_type"]]["cordon"]
            conn.execute(
                "UPDATE events SET cordon=1, cordon_by=?, cordon_at=?,"
                " cordon_note=?, status=CASE WHEN status='claimed' THEN"
                " 'repairing' ELSE status END WHERE id=?",
                (crew, ts, note or f"{kind}（按{ISSUE_TYPES[ev['issue_type']]['label']}"
                 "现场措施布设）", event_id),
            )
            self._log(conn, event_id, "布设封控", crew,
                      f"{kind}：{ISSUE_TYPES[ev['issue_type']]['measure']}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return self.event_detail(event_id)

    def reopen_cordon_note(self, event_id: int, crew: str, note: str) -> None:
        """封控调整记录（如更换围挡位置）。"""
        conn = self.conn
        ev = self._get_event(conn, event_id)
        self._require_crew(ev, crew)
        conn.execute("UPDATE events SET cordon_note=? WHERE id=?", (note, event_id))
        self._log(conn, event_id, "封控调整", crew, note)

    # ---- 维修 --------------------------------------------------------------

    def save_photo(self, data: bytes, suffix: str = "jpg") -> str:
        """本地存储照片，返回可访问的相对路径 /photos/xxx.jpg。"""
        name = f"{self.clock().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}.{suffix}"
        (self.photo_dir / name).write_bytes(data)
        return f"/photos/{name}"

    def upload_photo(self, photo_b64: str, suffix: str = "jpg") -> str:
        import base64
        if "," in photo_b64 and photo_b64.startswith("data:"):
            photo_b64 = photo_b64.split(",", 1)[1]
        data = base64.b64decode(photo_b64)
        return self.save_photo(data, suffix)

    def complete_repair(self, event_id: int, crew: str, repair_note: str = "",
                        after_photo: str | None = None, painted: bool = False,
                        paint_dry_minutes: int = DEFAULT_PAINT_DRY_MINUTES) -> dict[str, Any]:
        """完成维修，提交复验。after_photo 为本地照片路径（/photos/...）。"""
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            ev = self._get_event(conn, event_id)
            self._require_crew(ev, crew)
            if ev["status"] not in ("claimed", "repairing"):
                raise ServiceError("只有维修中的工单可以提交复验")
            if not ev["cordon"]:
                raise ServiceError("提交复验前须先完成现场封控记录")
            photo = after_photo or ev["after_photo"]
            if not photo:
                raise ServiceError("须上传维修后照片才能提交复验")

            sets = ["status='awaiting_review'", "repair_note=?", "repaired_by=?",
                    "repaired_at=?", "after_photo=?"]
            args: list[Any] = [repair_note or ev["repair_note"], crew,
                               self.clock().strftime("%Y-%m-%d %H:%M:%S"), photo]
            if painted:
                sets.append("paint_applied_at=?")
                args.append(self.clock().strftime("%Y-%m-%d %H:%M:%S"))
                sets.append("paint_dry_minutes=?")
                args.append(paint_dry_minutes)
            if ev["issue_type"] == "removed":
                sets.append("reinstalled_at=?")
                args.append(self.clock().strftime("%Y-%m-%d %H:%M:%S"))
                conn.execute("UPDATE benches SET removed=0 WHERE id=?", (ev["bench_id"],))
            args.append(event_id)
            conn.execute(f"UPDATE events SET {', '.join(sets)} WHERE id=?", args)
            conn.execute("UPDATE work_orders SET status='done' WHERE event_id=?",
                         (event_id,))
            self._log(conn, event_id, "维修完成，申请复验", crew, repair_note)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return self.event_detail(event_id)

    def attach_before_photo(self, event_id: int, crew: str, photo: str) -> None:
        conn = self.conn
        ev = self._get_event(conn, event_id)
        self._require_crew(ev, crew)
        conn.execute("UPDATE events SET before_photo=? WHERE id=?", (photo, event_id))
        self._log(conn, event_id, "上传维修前照片", crew, photo)

    def _require_crew(self, ev: sqlite3.Row, crew: str) -> None:
        if not ev["claimed_by"]:
            raise ServiceError("该工单尚未被认领")
        if ev["claimed_by"] != crew:
            raise ServiceError(f"工单由班组「{ev['claimed_by']}」负责，"
                               f"「{crew}」无权操作")

    # ---- 材料消耗 ----------------------------------------------------------

    def use_materials(self, event_id: int, crew: str,
                      items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            ev = self._get_event(conn, event_id)
            self._require_crew(ev, crew)
            out = []
            for it in items:
                qty = float(it.get("qty", 0))
                if qty <= 0:
                    raise ServiceError("材料数量须大于 0")
                cur = conn.execute(
                    "INSERT INTO materials(event_id,item,qty,unit,used_by)"
                    " VALUES(?,?,?,?,?)",
                    (event_id, it["item"].strip(), qty, it.get("unit", "个"), crew),
                )
                out.append({"id": cur.lastrowid, "item": it["item"].strip(),
                            "qty": qty, "unit": it.get("unit", "个")})
            self._log(conn, event_id, "登记材料消耗", crew,
                      "；".join(f"{m['item']}x{m['qty']}{m['unit']}" for m in out))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return out

    def list_materials(self, event_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM materials WHERE event_id=? ORDER BY id", (event_id,))]

    # ---- 复验 --------------------------------------------------------------

    def paint_status(self, ev: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        if not ev["paint_applied_at"]:
            return {"painted": False, "dry": True, "remain_minutes": 0}
        applied = datetime.strptime(ev["paint_applied_at"], "%Y-%m-%d %H:%M:%S")
        ready_at = applied + timedelta(minutes=ev["paint_dry_minutes"])
        remain = (ready_at - self.clock()).total_seconds() / 60
        return {"painted": True, "dry": remain <= 0,
                "remain_minutes": max(0, round(remain, 1)),
                "ready_at": ready_at.strftime("%Y-%m-%d %H:%M:%S")}

    def review(self, event_id: int, inspector: str, passed: bool,
               tape_removed: bool, note: str = "") -> dict[str, Any]:
        """复验员复验。维修人本人不得复验；未撤警示带/缺照片/油漆未干均阻断。"""
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            ev = self._get_event(conn, event_id)
            # 权限：维修人员不能复验自己的工单
            if ev["claimed_by"] and inspector == ev["claimed_by"]:
                raise ServiceError("维修班组不能复验自己负责的工单，请由复验员复验")
            if ev["repaired_by"] and inspector == ev["repaired_by"]:
                raise ServiceError("维修人员不能复验自己完成的维修")
            if ev["status"] != "awaiting_review":
                raise ServiceError("只有待复验的工单可以复验")

            ts = self.clock().strftime("%Y-%m-%d %H:%M:%S")
            if passed:
                if not tape_removed:
                    raise ServiceError("重新开放前须确认警示带已撤除")
                if not ev["after_photo"]:
                    raise ServiceError("缺少维修后照片，不能通过复验")
                paint = self.paint_status(ev)
                if paint["painted"] and not paint["dry"]:
                    raise ServiceError(
                        f"油漆未干，预计还需 {paint['remain_minutes']} 分钟"
                        f"（{paint['ready_at']} 后），暂不能开放"
                    )
                conn.execute(
                    "UPDATE events SET status='verified', reviewed_by=?, reviewed_at=?,"
                    " review_note=?, review_passed=1, tape_removed=1 WHERE id=?",
                    (inspector, ts, note, event_id),
                )
                conn.execute(
                    "UPDATE work_orders SET status='reviewed' WHERE event_id=?",
                    (event_id,),
                )
                conn.execute(
                    "UPDATE benches SET usable=1, open_event_id=NULL, armrest='ok',"
                    " fixture='ok' WHERE id=?",
                    (ev["bench_id"],),
                )
                self._log(conn, event_id, "复验通过，恢复使用", inspector, note)
                self._close(conn, event_id, inspector)
            else:
                conn.execute(
                    "UPDATE events SET status='repairing', reviewed_by=?, reviewed_at=?,"
                    " review_note=?, review_passed=0, tape_removed=? WHERE id=?",
                    (inspector, ts, note, 1 if tape_removed else 0, event_id),
                )
                conn.execute(
                    "UPDATE work_orders SET status='claimed' WHERE event_id=?",
                    (event_id,),
                )
                self._log(conn, event_id, "复验不通过，返工", inspector, note)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return self.event_detail(event_id)

    def _close(self, conn: sqlite3.Connection, event_id: int, actor: str) -> None:
        ts = self.clock().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE events SET status='closed', closed_at=? WHERE id=?",
                     (ts, event_id))
        self._log(conn, event_id, "事件归档", actor)

    # ---- 查询 --------------------------------------------------------------

    def _event_brief(self, e: dict[str, Any]) -> dict[str, Any]:
        e["issue_label"] = ISSUE_TYPES[e["issue_type"]]["label"]
        e["measure"] = ISSUE_TYPES[e["issue_type"]]["measure"]
        e["cordon_kind"] = ISSUE_TYPES[e["issue_type"]]["cordon"]
        return e

    def event_detail(self, event_id: int) -> dict[str, Any]:
        conn = self.conn
        ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if not ev:
            raise NotFound(f"事件 #{event_id} 不存在")
        d = dict(ev)
        d["merged_reports"] = json.loads(d["merged_reports"] or "[]")
        wo = conn.execute("SELECT * FROM work_orders WHERE event_id=?",
                          (event_id,)).fetchone()
        d["work_order"] = dict(wo) if wo else None
        bench = conn.execute("SELECT * FROM benches WHERE id=?",
                             (d["bench_id"],)).fetchone()
        d["bench"] = dict(bench)
        d["materials"] = self.list_materials(event_id)
        d["logs"] = [dict(r) for r in conn.execute(
            "SELECT * FROM event_log WHERE event_id=? ORDER BY id", (event_id,))]
        d["paint"] = self.paint_status(d)
        return self._event_brief(d)

    def list_events(self, open_only: bool = False) -> list[dict[str, Any]]:
        sql = ("SELECT e.*, wo.id AS wo_id, wo.crew AS wo_crew FROM events e"
               " LEFT JOIN work_orders wo ON wo.event_id=e.id")
        if open_only:
            sql += " WHERE e.status IN ('reported','claimed','repairing','awaiting_review')"
        sql += " ORDER BY e.id DESC"
        out = []
        for r in self.conn.execute(sql).fetchall():
            d = dict(r)
            d["merged_reports"] = json.loads(d["merged_reports"] or "[]")
            out.append(self._event_brief(d))
        return out

    def log_list(self, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM event_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
