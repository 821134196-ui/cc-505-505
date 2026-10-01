"""领域服务：座椅台账、报损归并、工单、封控、复验、材料消耗。

所有写操作在函数内部开启 IMMEDIATE 事务，保证并发安全。
"""
import base64
import uuid
from datetime import datetime

from .db import get_conn, PHOTO_DIR

# ---------------- 常量与现场措施 ----------------

DAMAGE_TYPES = {
    "loose_bolt": {
        "label": "螺栓松动",
        "measure": "立即围蔽停用，禁止落座；重新紧固或更换膨胀螺栓，逐个检查相邻固件。",
    },
    "broken_plank": {
        "label": "木板断裂",
        "label_icon": "🪵",
        "measure": "围蔽并清除断裂木条，更换防腐木板，打磨毛刺后补刷木蜡油。",
    },
    "wet_paint": {
        "label": "油漆未干",
        "measure": "保留警示带与“油漆未干”告示，严禁解封与落座；晾干确认后再安排复验。",
    },
    "moved": {
        "label": "座椅被搬动",
        "measure": "核对原始安装点位，复位后重新锚固，核对编号与台账位置一致。",
    },
    "removed": {
        "label": "已拆除待重装",
        "measure": "登记暂存物料与数量，按原混凝土基础重装，补拍基础与成品照片。",
    },
    "other": {"label": "其他损坏", "measure": "现场围蔽并由养护班组评估处置。"},
}

MATERIALS = {
    "wood": "木质", "metal": "金属", "stone": "石材", "plastic": "塑木复合",
}
ARMREST = {
    "ok": "扶手完好", "damaged": "扶手损坏", "none": "无扶手",
}
FIXTURE = {
    "ok": "固定牢固", "loose": "螺栓松动", "moved": "被搬动", "removed": "已拆除待重装",
}

UNSAFE_DAMAGE = {"loose_bolt", "broken_plank", "moved", "removed", "wet_paint", "other"}


class BusinessError(Exception):
    """业务规则冲突，HTTP 层映射为 400。"""


def _log(conn, actor, action, detail=""):
    conn.execute(
        "INSERT INTO audit_log(actor, action, detail) VALUES (?,?,?)",
        (actor, action, detail),
    )


# ---------------- 座椅台账 ----------------

def list_seats(park=None, include_closed=True):
    conn = get_conn()
    try:
        sql = "SELECT * FROM seats"
        args = []
        if park:
            sql += " WHERE park=?"
            args.append(park)
        sql += " ORDER BY code"
        rows = conn.execute(sql, args).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["open_incident_id"] = conn.execute(
                "SELECT id FROM incidents WHERE seat_id=? AND status='open' LIMIT 1",
                (r["id"],),
            ).fetchone()
            result.append(d)
        for d in result:
            d["open_incident_id"] = d["open_incident_id"][0] if d["open_incident_id"] else None
        return result
    finally:
        conn.close()


def get_seat(seat_id):
    conn = get_conn()
    try:
        r = conn.execute("SELECT * FROM seats WHERE id=?", (seat_id,)).fetchone()
        if not r:
            raise BusinessError("座椅不存在")
        d = dict(r)
        inc = conn.execute(
            "SELECT * FROM incidents WHERE seat_id=? AND status='open' ORDER BY id DESC LIMIT 1",
            (seat_id,),
        ).fetchone()
        d["open_incident"] = dict(inc) if inc else None
        return d
    finally:
        conn.close()


def create_seat(actor, code, name, park, location, lat, lng, material,
                armrest="none", fixture="ok"):
    if material not in MATERIALS:
        raise BusinessError("材质不合法")
    if armrest not in ARMREST or fixture not in FIXTURE:
        raise BusinessError("扶手/固定件状态不合法")
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """INSERT INTO seats(code,name,park,location,lat,lng,material,armrest,fixture)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (code, name, park, location, lat, lng, material, armrest, fixture),
            )
            _log(conn, actor, "seat_create", code)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return get_seat(conn.execute("SELECT id FROM seats WHERE code=?", (code,)).fetchone()[0])
    finally:
        conn.close()


def update_seat(actor, seat_id, **fields):
    allowed = {"name", "park", "location", "lat", "lng", "material",
               "armrest", "fixture", "usable", "status_note"}
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            exists = conn.execute("SELECT 1 FROM seats WHERE id=?", (seat_id,)).fetchone()
            if not exists:
                raise BusinessError("座椅不存在")
            sets, args = [], []
            for k, v in fields.items():
                if k not in allowed:
                    raise BusinessError(f"非法字段 {k}")
                sets.append(f"{k}=?")
                args.append(v)
            if sets:
                args.append(seat_id)
                conn.execute(f"UPDATE seats SET {','.join(sets)} WHERE id=?", args)
            _log(conn, actor, "seat_update", f"seat={seat_id} fields={','.join(fields)}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return get_seat(seat_id)
    finally:
        conn.close()


# ---------------- 报损与归并 ----------------

def submit_report(actor, seat_id, damage_type, description="", phone=""):
    """市民报损。同一座椅已有未结事件则归并（merged=True），否则新建事件与工单。"""
    if damage_type not in DAMAGE_TYPES:
        raise BusinessError("损坏类型不合法")
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            seat = conn.execute("SELECT * FROM seats WHERE id=?", (seat_id,)).fetchone()
            if not seat:
                raise BusinessError("座椅不存在，无法报损")

            open_inc = conn.execute(
                "SELECT * FROM incidents WHERE seat_id=? AND status='open' ORDER BY id DESC LIMIT 1",
                (seat_id,),
            ).fetchone()

            if open_inc:
                # 重复报损：归并到同一未结事件
                cur = conn.execute(
                    """INSERT INTO reports(seat_id,damage_type,description,reporter,phone,incident_id)
                       VALUES (?,?,?,?,?,?)""",
                    (seat_id, damage_type, description, actor, phone, open_inc["id"]),
                )
                report_id = cur.lastrowid
                conn.execute(
                    "UPDATE incidents SET title=? WHERE id=?",
                    (f"{seat['code']} 综合损坏（含{DAMAGE_TYPES[damage_type]['label']}）",
                     open_inc["id"]),
                )
                _log(conn, actor, "report_merge",
                     f"report={report_id} -> incident={open_inc['id']}")
                merged = True
                incident_id = open_inc["id"]
            else:
                # 首次报损：建事件 + 待认领工单
                title = f"{seat['code']} {DAMAGE_TYPES[damage_type]['label']}"
                cur = conn.execute(
                    "INSERT INTO incidents(seat_id,title) VALUES (?,?)",
                    (seat_id, title),
                )
                incident_id = cur.lastrowid
                wo_code = _next_wo_code(conn)
                wocur = conn.execute(
                    """INSERT INTO work_orders(code,incident_id,seat_id,summary)
                       VALUES (?,?,?,?)""",
                    (wo_code, incident_id, seat_id,
                     f"{DAMAGE_TYPES[damage_type]['label']}：{description or '按现场措施处置'}"),
                )
                wo_id = wocur.lastrowid
                conn.execute("UPDATE incidents SET work_order_id=? WHERE id=?",
                             (wo_id, incident_id))
                cur = conn.execute(
                    """INSERT INTO reports(seat_id,damage_type,description,reporter,phone,incident_id)
                       VALUES (?,?,?,?,?,?)""",
                    (seat_id, damage_type, description, actor, phone, incident_id),
                )
                report_id = cur.lastrowid
                _log(conn, actor, "report_new",
                     f"report={report_id} incident={incident_id} wo={wo_id}")
                merged = False

            # 现场安全：损坏类型一律先停用，等待封控/维修
            if damage_type in UNSAFE_DAMAGE:
                fixture_map = {"loose_bolt": "loose", "moved": "moved", "removed": "removed"}
                if damage_type in fixture_map:
                    conn.execute("UPDATE seats SET usable=0, fixture=? WHERE id=?",
                                 (fixture_map[damage_type], seat_id))
                else:
                    conn.execute(
                        "UPDATE seats SET usable=0, status_note=? WHERE id=?",
                        (DAMAGE_TYPES[damage_type]["label"], seat_id),
                    )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return {"report_id": report_id, "incident_id": incident_id, "merged": merged}
    finally:
        conn.close()


def link_report_to_incident(actor, report_id, incident_id):
    """显式归并（调度员用）。校验：同一座椅、事件未结，否则拒绝——防止错误归并。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            report = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
            incident = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
            if not report:
                raise BusinessError("报损记录不存在")
            if not incident:
                raise BusinessError("事件不存在，不能归并")
            if incident["status"] != "open":
                raise BusinessError("事件已结案，不能归并到历史事件")
            if report["seat_id"] != incident["seat_id"]:
                raise BusinessError("不能归并：报损座椅与事件座椅不一致")
            if report["incident_id"] == incident_id:
                raise BusinessError("该报损已在此事件下")
            conn.execute("UPDATE reports SET incident_id=? WHERE id=?",
                         (incident_id, report_id))
            _log(conn, actor, "report_link",
                 f"report={report_id} -> incident={incident_id}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return {"report_id": report_id, "incident_id": incident_id}
    finally:
        conn.close()


def list_incidents(status=None):
    conn = get_conn()
    try:
        sql = ("SELECT i.*, s.code seat_code, s.park, s.usable, s.location, "
               "w.code wo_code, w.status wo_status, w.crew, "
               "(SELECT COUNT(*) FROM reports r WHERE r.incident_id=i.id) report_count "
               "FROM incidents i JOIN seats s ON s.id=i.seat_id "
               "LEFT JOIN work_orders w ON w.id=i.work_order_id")
        args = []
        if status:
            sql += " WHERE i.status=?"
            args.append(status)
        sql += " ORDER BY i.id DESC"
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


# ---------------- 工单 ----------------

def _next_wo_code(conn):
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"WO-{today}-"
    row = conn.execute(
        "SELECT COUNT(*) FROM work_orders WHERE code LIKE ?", (prefix + "%",)
    ).fetchone()[0]
    return f"{prefix}{row + 1:03d}"


def list_work_orders(status=None):
    conn = get_conn()
    try:
        sql = ("SELECT w.*, s.code seat_code, s.park, s.location, s.usable seat_usable, "
               "i.status incident_status "
               "FROM work_orders w JOIN seats s ON s.id=w.seat_id "
               "JOIN incidents i ON i.id=w.incident_id")
        args = []
        if status:
            sql += " WHERE w.status=?"
            args.append(status)
        sql += " ORDER BY w.id DESC"
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def get_work_order(wo_id):
    conn = get_conn()
    try:
        r = conn.execute(
            """SELECT w.*, s.code seat_code, s.name seat_name, s.park, s.location,
                      s.lat, s.lng, s.material, s.usable seat_usable
               FROM work_orders w JOIN seats s ON s.id=w.seat_id WHERE w.id=?""",
            (wo_id,),
        ).fetchone()
        if not r:
            raise BusinessError("工单不存在")
        d = dict(r)
        d["reports"] = [dict(x) for x in conn.execute(
            "SELECT * FROM reports WHERE incident_id=? ORDER BY id",
            (d["incident_id"],)).fetchall()]
        d["closures"] = [dict(x) for x in conn.execute(
            "SELECT * FROM closures WHERE work_order_id=? ORDER BY id", (wo_id,)
        ).fetchall()]
        d["photos"] = [dict(x) for x in conn.execute(
            "SELECT * FROM photos WHERE work_order_id=? ORDER BY id", (wo_id,)
        ).fetchall()]
        d["materials"] = [dict(x) for x in conn.execute(
            "SELECT * FROM materials WHERE work_order_id=? ORDER BY id", (wo_id,)
        ).fetchall()]
        insp = conn.execute(
            "SELECT * FROM inspections WHERE work_order_id=? ORDER BY id DESC", (wo_id,)
        ).fetchall()
        d["inspections"] = [dict(x) for x in insp]
        d["tape_state"] = tape_state(conn, d["seat_id"])
        return d
    finally:
        conn.close()


def claim_work_order(actor, wo_id):
    """班组接单。原子认领：UPDATE ... WHERE status='open'，并发时仅一组成功。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            wo = conn.execute("SELECT * FROM work_orders WHERE id=?", (wo_id,)).fetchone()
            if not wo:
                raise BusinessError("工单不存在")
            cur = conn.execute(
                """UPDATE work_orders
                   SET status='claimed', crew=?, claimed_at=datetime('now','localtime')
                   WHERE id=? AND status='open'""",
                (actor, wo_id),
            )
            if cur.rowcount == 0:
                holder = conn.execute(
                    "SELECT crew FROM work_orders WHERE id=?", (wo_id,)
                ).fetchone()[0]
                _log(conn, actor, "claim_lost", f"wo={wo_id} holder={holder}")
                raise BusinessError(f"手慢一步：工单已被班组「{holder}」认领")
            _log(conn, actor, "claim_win", f"wo={wo_id}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return get_work_order(wo_id)
    finally:
        conn.close()


def record_closure(actor, wo_id, action, note=""):
    """封控记录：tape_on 设置警示带 / tape_off 撤除警示带。"""
    if action not in ("tape_on", "tape_off"):
        raise BusinessError("封控动作不合法")
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            wo = conn.execute("SELECT * FROM work_orders WHERE id=?", (wo_id,)).fetchone()
            if not wo:
                raise BusinessError("工单不存在")
            if wo["status"] not in ("claimed", "done"):
                raise BusinessError("当前工单状态不能登记封控")
            latest = conn.execute(
                "SELECT action FROM closures WHERE seat_id=? ORDER BY id DESC LIMIT 1",
                (wo["seat_id"],),
            ).fetchone()
            if latest and latest["action"] == action:
                raise BusinessError(
                    "警示带已设置" if action == "tape_on" else "警示带已撤除，请勿重复登记")
            cur = conn.execute(
                "INSERT INTO closures(work_order_id,seat_id,action,operator,note) VALUES (?,?,?,?,?)",
                (wo_id, wo["seat_id"], action, actor, note),
            )
            if action == "tape_on":
                conn.execute("UPDATE seats SET usable=0 WHERE id=?", (wo["seat_id"],))
            _log(conn, actor, "closure", f"wo={wo_id} {action}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return {"closure_id": cur.lastrowid, "action": action}
    finally:
        conn.close()


def tape_state(conn, seat_id):
    """根据封控记录推断当前警示带状态。"""
    row = conn.execute(
        "SELECT action FROM closures WHERE seat_id=? ORDER BY id DESC LIMIT 1",
        (seat_id,),
    ).fetchone()
    if not row:
        return "none"        # 从未围蔽
    return "on" if row["action"] == "tape_on" else "off"


def finish_work_order(actor, wo_id, repair_note="", paint_wet=False):
    """班组完成维修，进入待复验。paint_wet=True 时保留阻断，不得解封。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            wo = conn.execute("SELECT * FROM work_orders WHERE id=?", (wo_id,)).fetchone()
            if not wo:
                raise BusinessError("工单不存在")
            if wo["status"] != "claimed":
                raise BusinessError("只有已接单的工单可以填报完工")
            if wo["crew"] != actor:
                raise BusinessError("只有认领该单的班组可以填报完工")
            # 完工前必须已设置警示带（先封控再施工）
            if tape_state(conn, wo["seat_id"]) != "on":
                raise BusinessError("请先登记设置警示带（封控）后再完工")
            conn.execute(
                """UPDATE work_orders
                   SET status='done', finished_at=datetime('now','localtime'),
                       repair_note=?, paint_wet=? WHERE id=?""",
                (repair_note, 1 if paint_wet else 0, wo_id),
            )
            _log(conn, actor, "finish",
                 f"wo={wo_id} paint_wet={int(paint_wet)}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return get_work_order(wo_id)
    finally:
        conn.close()


def mark_paint_dry(actor, wo_id):
    """油漆晾干后由施工班组解除未干标记。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            wo = conn.execute("SELECT * FROM work_orders WHERE id=?", (wo_id,)).fetchone()
            if not wo:
                raise BusinessError("工单不存在")
            if wo["crew"] != actor:
                raise BusinessError("只有施工班组可以更新油漆状态")
            if not wo["paint_wet"]:
                raise BusinessError("该工单没有未干油漆标记")
            conn.execute("UPDATE work_orders SET paint_wet=0 WHERE id=?", (wo_id,))
            _log(conn, actor, "paint_dry", f"wo={wo_id}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return get_work_order(wo_id)
    finally:
        conn.close()


# ---------------- 复验与重开 ----------------

def inspect_work_order(actor, role, wo_id, passed, note="", tape_confirmed=False):
    """巡查员复验。维修人员不能复验自己的工单；重开前核对警示带撤除与照片。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            wo = conn.execute("SELECT * FROM work_orders WHERE id=?", (wo_id,)).fetchone()
            if not wo:
                raise BusinessError("工单不存在")
            if role != "inspector":
                raise BusinessError("复验仅限巡查员执行")
            if wo["crew"] and actor == wo["crew"]:
                raise BusinessError("维修人员不能复验自己完成的工单，请移交其他巡查员")
            if wo["status"] != "done":
                raise BusinessError("只有待复验的工单可以复验")

            if passed:
                if wo["paint_wet"]:
                    raise BusinessError("阻断：油漆未干，禁止恢复使用。请待晾干后由班组解除标记")
                if tape_state(conn, wo["seat_id"]) != "off":
                    raise BusinessError("阻断：警示带尚未撤除，不能恢复使用")
                if not tape_confirmed:
                    raise BusinessError("请现场核对警示带已撤除并勾选确认")
                reopen_photo = conn.execute(
                    "SELECT id FROM photos WHERE work_order_id=? AND phase='reopen' ORDER BY id DESC LIMIT 1",
                    (wo_id,),
                ).fetchone()
                if not reopen_photo:
                    raise BusinessError("阻断：缺少重新开放前的现场核对照片")
                photo_id = reopen_photo[0]
                conn.execute(
                    "INSERT INTO inspections(work_order_id,inspector,passed,tape_removed,"
                    "reopen_photo_id,note) VALUES (?,?,1,1,?,?)",
                    (wo_id, actor, photo_id, note),
                )
                conn.execute(
                    "UPDATE work_orders SET status='verified' WHERE id=?", (wo_id,))
                conn.execute(
                    """UPDATE seats SET usable=1, fixture='ok', armrest='ok',
                              status_note='' WHERE id=?""",
                    (wo["seat_id"],),
                )
                conn.execute(
                    "UPDATE incidents SET status='closed', closed_at=datetime('now','localtime') WHERE id=?",
                    (wo["incident_id"],),
                )
                _log(conn, actor, "inspect_pass", f"wo={wo_id}")
            else:
                conn.execute(
                    """INSERT INTO inspections(work_order_id,inspector,passed,tape_removed,note)
                       VALUES (?,?,0,?,?)""",
                    (wo_id, actor, 1 if tape_confirmed else 0, note or "复验驳回，返工"),
                )
                # 驳回返工：工单退回已接单，座椅继续封控
                conn.execute("UPDATE work_orders SET status='claimed' WHERE id=?", (wo_id,))
                _log(conn, actor, "inspect_reject", f"wo={wo_id}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return get_work_order(wo_id)
    finally:
        conn.close()


# ---------------- 照片与材料 ----------------

def save_photo(actor, seat_id, phase, data_b64, work_order_id=None, ext="jpg"):
    """照片本地落盘 data/photos/，元数据入库。"""
    allowed_phase = {"report", "before", "after", "reopen"}
    if phase not in allowed_phase:
        raise BusinessError("照片阶段不合法")
    if ext.lower().lstrip(".") not in ("jpg", "jpeg", "png", "webp", "gif"):
        raise BusinessError("照片格式不支持")
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            seat = conn.execute("SELECT 1 FROM seats WHERE id=?", (seat_id,)).fetchone()
            if not seat:
                raise BusinessError("座椅不存在")
            if work_order_id:
                wo = conn.execute(
                    "SELECT 1 FROM work_orders WHERE id=? AND seat_id=?",
                    (work_order_id, seat_id),
                ).fetchone()
                if not wo:
                    raise BusinessError("工单与座椅不匹配")
            name = f"{uuid.uuid4().hex}.{ext.lower().lstrip('.')}"
            PHOTO_DIR.mkdir(parents=True, exist_ok=True)
            raw = base64.b64decode(data_b64)
            (PHOTO_DIR / name).write_bytes(raw)
            cur = conn.execute(
                """INSERT INTO photos(seat_id,work_order_id,phase,file_name,uploader)
                   VALUES (?,?,?,?,?)""",
                (seat_id, work_order_id, phase, name, actor),
            )
            _log(conn, actor, "photo_upload", f"seat={seat_id} phase={phase} file={name}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return {"photo_id": cur.lastrowid, "file_name": name}
    finally:
        conn.close()


def photo_path(file_name):
    # 防目录穿越
    if "/" in file_name or ".." in file_name:
        raise BusinessError("非法文件名")
    p = PHOTO_DIR / file_name
    if not p.exists():
        raise BusinessError("照片不存在")
    return p


def add_material(actor, wo_id, name, quantity, unit="件"):
    if quantity <= 0:
        raise BusinessError("材料数量必须大于 0")
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            wo = conn.execute("SELECT status, crew FROM work_orders WHERE id=?", (wo_id,)).fetchone()
            if not wo:
                raise BusinessError("工单不存在")
            cur = conn.execute(
                "INSERT INTO materials(work_order_id,name,unit,quantity) VALUES (?,?,?,?)",
                (wo_id, name, unit, quantity),
            )
            _log(conn, actor, "material_use", f"wo={wo_id} {name}x{quantity}{unit}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return {"material_id": cur.lastrowid}
    finally:
        conn.close()


def list_reports(incident_id=None):
    conn = get_conn()
    try:
        sql = ("SELECT r.*, s.code seat_code FROM reports r JOIN seats s ON s.id=r.seat_id")
        args = []
        if incident_id:
            sql += " WHERE r.incident_id=?"
            args.append(incident_id)
        sql += " ORDER BY r.id DESC"
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()
