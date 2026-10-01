"""初始化演示数据：座椅台账 + 多个处于不同流程阶段的事件/工单。

重点案例：GY-003 被两位市民先后报损（螺栓松动 + 木板断裂），
系统自动归并到同一未结事件，只有一张待认领工单。
"""
from .db import init_db, get_conn
from . import services

SEATS = [
    # code, name, park, location, lat, lng, material, armrest, fixture
    ("GY-001", "湖滨长椅1号", "中央公园", "滨湖步道K0+200，柳树旁", 31.2305, 121.4702, "wood", "ok", "ok"),
    ("GY-002", "湖滨长椅2号", "中央公园", "滨湖步道K0+350，观景台西侧", 31.2312, 121.4710, "wood", "damaged", "ok"),
    ("GY-003", "樱花大道长椅", "中央公园", "樱花大道中段，3号灯杆下", 31.2320, 121.4706, "wood", "ok", "loose"),
    ("GY-004", "儿童乐园座椅", "中央公园", "儿童乐园入口北侧", 31.2298, 121.4715, "plastic", "none", "ok"),
    ("GY-005", "花岗岩休息凳", "中央公园", "山顶平台东侧", 31.2331, 121.4722, "stone", "none", "ok"),
    ("JD-101", "文一路公交站长椅", "文一路", "文一路公交站（东向）", 31.2284, 121.4650, "metal", "ok", "moved"),
    ("JD-102", "文一路街心座椅", "文一路", "街心花园西北角", 31.2289, 121.4661, "metal", "none", "ok"),
    ("GY-006", "南门条凳", "中央公园", "南门广场东侧花池边", 31.2290, 121.4690, "wood", "ok", "removed"),
]


def seed(reset=False):
    init_db()
    conn = get_conn()
    try:
        if reset:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.executescript(
                "DELETE FROM inspections; DELETE FROM materials; DELETE FROM photos; "
                "DELETE FROM closures; DELETE FROM work_orders; DELETE FROM reports; "
                "DELETE FROM incidents; DELETE FROM seats; DELETE FROM audit_log;")
            conn.execute("PRAGMA foreign_keys=ON")
        elif conn.execute("SELECT COUNT(*) FROM seats").fetchone()[0] > 0:
            print("数据已存在，跳过初始化（如需重置：python run.py reset）")
            return

        for s in SEATS:
            services.create_seat("系统", *s)
        ids = {r["code"]: r["id"] for r in
               conn.execute("SELECT id, code FROM seats").fetchall()}

        # —— 重点：GY-003 重复报损，归并同一未结事件 ——
        r1 = services.submit_report("市民张女士", ids["GY-003"], "loose_bolt",
                                    "坐下时椅子晃动，地脚螺栓好像松了")
        r2 = services.submit_report("市民李先生", ids["GY-003"], "broken_plank",
                                    "座面有块木板裂了，木茬扎手")
        assert r1["merged"] is False and r2["merged"] is True, "重复报损归并演示数据异常"

        # —— GY-002：已接单、已封控、维修中（扶手损坏）——
        services.submit_report("晨练市民", ids["GY-002"], "broken_plank",
                               "靠背木条缺失，扶手也晃")
        wo2 = conn.execute(
            "SELECT w.id FROM work_orders w JOIN incidents i ON i.id=w.incident_id "
            "WHERE i.seat_id=? ORDER BY w.id LIMIT 1", (ids["GY-002"],)).fetchone()[0]
        services.claim_work_order("绿境养护一班", wo2)
        services.record_closure("绿境养护一班", wo2, "tape_on",
                                "作业区围蔽，两端放置停用牌")

        # —— JD-101：座椅被搬动，已完工但油漆未干，阻断重开 ——
        services.submit_report("巡路员", ids["JD-101"], "moved",
                               "长椅被推离原位约2米，占了盲道")
        wo3 = conn.execute(
            "SELECT w.id FROM work_orders w JOIN incidents i ON i.id=w.incident_id "
            "WHERE i.seat_id=? ORDER BY w.id LIMIT 1", (ids["JD-101"],)).fetchone()[0]
        services.claim_work_order("市政维修二班", wo3)
        services.record_closure("市政维修二班", wo3, "tape_on", "复位锚固作业")
        services.finish_work_order("市政维修二班", wo3,
                                   "已复位并重新锚固，补刷防锈漆", paint_wet=True)

        # —— GY-006：已拆除待重装，待认领 ——
        services.submit_report("市民王大伯", ids["GY-006"], "removed",
                               "旧座椅拆走了，基础还在，一直没装回来")

        print("演示数据初始化完成：")
        print("  · GY-003 重复报损 2 次 → 已归并到同一未结事件，1 张待认领工单")
        print("  · GY-002 养护一班已接单并封控，维修中")
        print("  · JD-101 已完工但油漆未干，复验将被阻断")
        print("  · GY-006 已拆除待重装，等待班组接单")
    finally:
        conn.close()


if __name__ == "__main__":
    seed(reset="--reset" in __import__("sys").argv)
