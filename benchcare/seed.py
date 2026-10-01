"""种子数据：公园座椅台账 + 一个包含重复报损的演示案例。"""
from __future__ import annotations

from pathlib import Path

from .service import BenchService

BENCHES = [
    # 编号, 材质, lat, lng, 位置, 扶手, 固定件
    ("B-001", "wood",    30.25120, 120.15550, "河滨公园·东门入口长椅", "ok", "ok"),
    ("B-002", "wood",    30.25010, 120.15420, "河滨公园·林荫步道 1 号", "ok", "ok"),
    ("B-003", "metal",   30.24980, 120.15300, "河滨公园·湖心亭观景椅", "ok", "ok"),
    ("B-004", "stone",   30.24890, 120.15610, "滨河步道·亲水平台石凳", "ok", "ok"),
    ("B-005", "wood",    30.24850, 120.15680, "滨河步道·中段休息椅", "loose", "loose"),
    ("B-006", "plastic", 30.25050, 120.15120, "市民广场·北侧塑木椅", "ok", "ok"),
    ("B-007", "metal",   30.25180, 120.15240, "市民广场·健身区旁金属椅", "ok", "ok"),
    ("B-008", "wood",    30.24930, 120.15760, "滨河步道·南段木栈道椅", "ok", "ok"),
    ("B-009", "stone",   30.25090, 120.15010, "市民广场·南门石凳", "missing", "ok"),
    ("B-010", "wood",    30.24810, 120.15500, "河滨公园·西门花圃长椅", "ok", "broken"),
]


def seed(svc: BenchService, reset: bool = True) -> dict:
    """写入演示数据。reset=True 时先清空数据库。"""
    if reset:
        conn = svc.conn
        for t in ("event_log", "materials", "work_orders", "reports", "events", "benches"):
            conn.execute(f"DELETE FROM {t}")

    for b in BENCHES:
        svc.add_bench(*b)

    results: dict = {"events": []}

    # ① 重复报损案例：B-002 木板断裂，被三位市民先后报损 → 归并为同一事件/工单
    r1 = svc.report_damage("B-002", "wood_split", reporter="王女士",
                           note="坐板裂了一条大口子，差点扎到孩子")
    r2 = svc.report_damage("B-002", "wood_split", reporter="李先生",
                           note="东门进来第二张椅子，木板断了")
    r3 = svc.report_damage("B-002", "wood_split", reporter="晨跑市民",
                           note="同一张，还没人来修吗")
    results["events"].append({"bench": "B-002", "title": "木板断裂·3 次重复报损归并",
                              "event_id": r1["event_id"], "merged": [r1, r2, r3]})

    # ② 松动螺栓：已报损待认领
    svc.report_damage("B-005", "loose_bolt", reporter="张师傅",
                      note="椅子一晃一晃的，地脚螺栓松了")

    # ③ 油漆未干：维修完成待复验（复验时会被干燥计时阻断）
    rp = svc.report_damage("B-008", "wet_paint", reporter="保洁员",
                           note="刚刷完漆，没有告示")
    eid = rp["event_id"]
    svc.claim(eid, "维保一班")
    svc.cordon(eid, "维保一班", "隔离绳围合，已立「油漆未干」告示牌")
    svc.attach_before_photo(eid, "维保一班", "/photos/seed-b008-before.jpg")
    svc.use_materials(eid, "维保一班", [
        {"item": "防腐面漆", "qty": 0.4, "unit": "升"},
        {"item": "毛刷", "qty": 1, "unit": "把"},
    ])
    svc.complete_repair(eid, "维保一班", repair_note="整椅打磨后刷面漆两道",
                        after_photo="/photos/seed-b008-after.jpg", painted=True,
                        paint_dry_minutes=60)
    results["events"].append({"bench": "B-008", "title": "油漆未干·待复验（阻断案例）",
                              "event_id": eid})

    # ④ 已拆除待重装：待认领
    svc.report_damage("B-010", "removed", reporter="巡查员老周",
                      note="基础锈蚀，座椅已拆除运回仓库，坑位盖了板")

    # ⑤ 已认领维修中（座椅被搬动）
    rm = svc.report_damage("B-007", "moved", reporter="市民热线",
                           note="健身区旁边的椅子被人挪到树底下了")
    svc.claim(rm["event_id"], "维保二班")
    svc.cordon(rm["event_id"], "维保二班", "复位点局部警示带")
    results["events"].append({"bench": "B-007", "title": "座椅被搬动·维修中",
                              "event_id": rm["event_id"]})

    _ensure_seed_photos(svc.photo_dir)
    return results


def _ensure_seed_photos(photo_dir: Path) -> None:
    """为演示案例生成两张本地占位照片（1x1 JPEG），避免照片缺失。"""
    # 最小合法 JPEG（灰点）
    jpeg = bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb0043000806060706050807"
        "07070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c"
        "231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101"
        "011100ffc4001f0000010501010101010100000000000000000102030405060708"
        "090a0bffc400b5100002010303020403050504040000017d010203000411051221"
        "31410613516107227114328191a1082342b1c11552d1f02433627282090a161718"
        "191a25262728292a3435363738393a434445464748494a535455565758595a6364"
        "65666768696a737475767778797a838485868788898a92939495969798999aa2a3"
        "a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9"
        "dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fbffd9"
    )
    for name in ("seed-b008-before.jpg", "seed-b008-after.jpg"):
        p = photo_dir / name
        if not p.exists():
            p.write_bytes(jpeg)
