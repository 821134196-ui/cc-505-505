"""端到端业务规则测试。

运行：python3 -m unittest -v   或   python3 tests/test_workflow.py

覆盖：
  1) 并发认领：两名（多名）班组同时认领一单，仅一组获得任务
  2) 错误归并：不同座椅不能归并、已结事件不能归并、重复报损自动合并
  3) 复验权限：维修人员不能复验自己的工单；非巡查员不能复验
  4) 未干油漆阻断：油漆未干 / 警示带未撤 / 缺照片 / 未勾选核对 → 均不能恢复使用
另含完整维修-复验闭环与材料消耗校验。
"""
import base64
import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import zlib
import struct
from http.server import ThreadingHTTPServer

# 必须在导入 app 之前指定独立数据目录
TMP = tempfile.mkdtemp(prefix="seat-test-")
os.environ["SEAT_DATA_DIR"] = TMP

from app import services  # noqa: E402
from app.db import init_db, get_conn  # noqa: E402
from app.server import Handler  # noqa: E402


def reset_db():
    conn = get_conn()
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(
        "DELETE FROM inspections; DELETE FROM materials; DELETE FROM photos; "
        "DELETE FROM closures; DELETE FROM work_orders; DELETE FROM reports; "
        "DELETE FROM incidents; DELETE FROM seats; DELETE FROM audit_log;")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.close()


def make_seat(code="T-001", **kw):
    defaults = dict(name="测试椅", park="测试公园", location="测试点位",
                    lat=31.23, lng=121.47, material="wood",
                    armrest="ok", fixture="ok")
    defaults.update(kw)
    return services.create_seat("测试员", code, **defaults)["id"]


def tiny_png_b64():
    """生成 1x1 红点 PNG，base64。"""
    raw = b"\x00" + b"\xff\x00\x00" * 1
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    return base64.b64encode(png).decode()


class ServiceTests(unittest.TestCase):
    def setUp(self):
        reset_db()

    # ---------- 归并 ----------
    def test_duplicate_reports_merge_to_one_open_incident(self):
        sid = make_seat("M-001")
        r1 = services.submit_report("市民甲", sid, "loose_bolt", "晃")
        r2 = services.submit_report("市民乙", sid, "broken_plank", "木板裂")
        r3 = services.submit_report("市民丙", sid, "loose_bolt", "还是晃")
        self.assertFalse(r1["merged"])
        self.assertTrue(r2["merged"])
        self.assertTrue(r3["merged"])
        # 同一事件，且只有一张工单
        conn = get_conn()
        n_inc = conn.execute("SELECT COUNT(*) FROM incidents WHERE seat_id=?", (sid,)).fetchone()[0]
        n_wo = conn.execute("SELECT COUNT(*) FROM work_orders WHERE seat_id=?", (sid,)).fetchone()[0]
        n_reports = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE incident_id=?", (r1["incident_id"],)).fetchone()[0]
        conn.close()
        self.assertEqual(n_inc, 1)
        self.assertEqual(n_wo, 1)
        self.assertEqual(n_reports, 3)

    def test_different_seats_do_not_merge(self):
        s1 = make_seat("M-101")
        s2 = make_seat("M-102")
        r1 = services.submit_report("市民甲", s1, "loose_bolt", "晃")
        r2 = services.submit_report("市民乙", s2, "loose_bolt", "另一把也晃")
        self.assertNotEqual(r1["incident_id"], r2["incident_id"])

        # 显式错误归并：跨座椅 → 拒绝
        with self.assertRaises(services.BusinessError) as cm:
            services.link_report_to_incident("调度员", r2["report_id"], r1["incident_id"])
        self.assertIn("不一致", str(cm.exception))

    def test_cannot_merge_into_closed_incident(self):
        s1 = make_seat("M-201")
        r1 = services.submit_report("市民甲", s1, "other", "x")
        conn = get_conn()
        conn.execute("UPDATE incidents SET status='closed' WHERE id=?", (r1["incident_id"],))
        conn.close()
        new_report = services.submit_report("市民乙", s1, "other", "再次损坏")
        # 已结案后再次报损应开新事件，而不是并入旧事件
        self.assertFalse(new_report["merged"])
        self.assertNotEqual(new_report["incident_id"], r1["incident_id"])
        with self.assertRaises(services.BusinessError):
            services.link_report_to_incident("调度员", new_report["report_id"], r1["incident_id"])

    # ---------- 并发认领 ----------
    def test_concurrent_claim_only_one_winner(self):
        sid = make_seat("C-001")
        services.submit_report("市民", sid, "loose_bolt", "晃")
        conn = get_conn()
        wo_id = conn.execute("SELECT id FROM work_orders WHERE seat_id=?", (sid,)).fetchone()[0]
        conn.close()

        results, errors = [], []
        lock = threading.Lock()
        crews = ["养护一班", "养护二班", "养护三班", "养护四班",
                 "养护五班", "养护六班", "养护七班", "养护八班"]

        def claim(crew):
            try:
                w = services.claim_work_order(crew, wo_id)
                with lock:
                    results.append((crew, w["status"]))
            except services.BusinessError as e:
                with lock:
                    errors.append((crew, str(e)))

        threads = [threading.Thread(target=claim, args=(c,)) for c in crews]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 1, "必须恰好一组认领成功")
        self.assertEqual(results[0][1], "claimed")
        self.assertEqual(len(errors), len(crews) - 1)
        # 失败方收到的提示指明获胜班组
        winner = results[0][0]
        for _, msg in errors:
            self.assertIn(winner, msg)
        conn = get_conn()
        row = conn.execute("SELECT status, crew FROM work_orders WHERE id=?", (wo_id,)).fetchone()
        conn.close()
        self.assertEqual(row["status"], "claimed")
        self.assertEqual(row["crew"], winner)

    def test_claim_then_second_attempt_fails(self):
        sid = make_seat("C-002")
        services.submit_report("市民", sid, "broken_plank", "裂")
        conn = get_conn()
        wo_id = conn.execute("SELECT id FROM work_orders WHERE seat_id=?", (sid,)).fetchone()[0]
        conn.close()
        services.claim_work_order("甲班", wo_id)
        with self.assertRaises(services.BusinessError) as cm:
            services.claim_work_order("乙班", wo_id)
        self.assertIn("已被班组", str(cm.exception))

    # ---------- 复验权限 ----------
    def test_repair_crew_cannot_inspect_own_work(self):
        sid = make_seat("I-001")
        services.submit_report("市民", sid, "loose_bolt", "晃")
        conn = get_conn()
        wo_id = conn.execute("SELECT id FROM work_orders WHERE seat_id=?", (sid,)).fetchone()[0]
        conn.close()
        crew = "绿境班"
        services.claim_work_order(crew, wo_id)
        services.record_closure(crew, wo_id, "tape_on")
        services.finish_work_order(crew, wo_id, "已紧固")
        services.record_closure(crew, wo_id, "tape_off")
        services.save_photo(crew, sid, "reopen", tiny_png_b64(), wo_id, "png")

        # 非巡查员角色不能复验
        with self.assertRaises(services.BusinessError):
            services.inspect_work_order(crew, "crew", wo_id, True, tape_confirmed=True)
        # 巡查员恰好就是施工本人 → 禁止自验
        with self.assertRaises(services.BusinessError) as cm:
            services.inspect_work_order(crew, "inspector", wo_id, True, tape_confirmed=True)
        self.assertIn("不能复验自己", str(cm.exception))
        # 另一名巡查员可以复验
        w = services.inspect_work_order("巡查员老周", "inspector", wo_id, True,
                                        tape_confirmed=True)
        self.assertEqual(w["status"], "verified")

    # ---------- 油漆阻断 + 完整闭环 ----------
    def test_wet_paint_blocks_reopen_until_all_checks_pass(self):
        sid = make_seat("P-001")
        services.submit_report("市民", sid, "broken_plank", "木板坏")
        conn = get_conn()
        wo_id = conn.execute("SELECT id FROM work_orders WHERE seat_id=?", (sid,)).fetchone()[0]
        inc_id = conn.execute("SELECT incident_id FROM work_orders WHERE id=?", (wo_id,)).fetchone()[0]
        conn.close()
        crew = "二班"
        inspector = "巡查员老周"
        self.assertNotEqual(crew, inspector)

        services.claim_work_order(crew, wo_id)
        # 未封控先完工 → 拒绝
        with self.assertRaises(services.BusinessError):
            services.finish_work_order(crew, wo_id, "换板", paint_wet=True)

        services.record_closure(crew, wo_id, "tape_on")
        services.save_photo(crew, sid, "before", tiny_png_b64(), wo_id, "png")
        services.add_material(crew, wo_id, "防腐木板", 2, "块")
        services.add_material(crew, wo_id, "木蜡油", 0.5, "升")
        services.finish_work_order(crew, wo_id, "更换两块木板并刷漆", paint_wet=True)
        services.save_photo(crew, sid, "after", tiny_png_b64(), wo_id, "png")

        # 阻断 1：油漆未干
        with self.assertRaises(services.BusinessError) as cm:
            services.inspect_work_order(inspector, "inspector", wo_id, True,
                                        tape_confirmed=True)
        self.assertIn("油漆未干", str(cm.exception))

        # 非施工班组不能解除油漆标记
        with self.assertRaises(services.BusinessError):
            services.mark_paint_dry("别的班", wo_id)
        services.mark_paint_dry(crew, wo_id)

        # 阻断 2：警示带未撤除
        with self.assertRaises(services.BusinessError) as cm:
            services.inspect_work_order(inspector, "inspector", wo_id, True,
                                        tape_confirmed=True)
        self.assertIn("警示带尚未撤除", str(cm.exception))
        services.record_closure(crew, wo_id, "tape_off")

        # 阻断 3：缺重开核对照片
        with self.assertRaises(services.BusinessError) as cm:
            services.inspect_work_order(inspector, "inspector", wo_id, True,
                                        tape_confirmed=True)
        self.assertIn("照片", str(cm.exception))
        services.save_photo(inspector, sid, "reopen", tiny_png_b64(), wo_id, "png")

        # 阻断 4：未勾选现场核对
        with self.assertRaises(services.BusinessError) as cm:
            services.inspect_work_order(inspector, "inspector", wo_id, True,
                                        tape_confirmed=False)
        self.assertIn("警示带已撤除", str(cm.exception))

        # 全部满足：复验通过，恢复可使用，事件结案
        w = services.inspect_work_order(inspector, "inspector", wo_id, True,
                                        note="现场合格", tape_confirmed=True)
        self.assertEqual(w["status"], "verified")
        seat = services.get_seat(sid)
        self.assertEqual(seat["usable"], 1)
        self.assertIsNone(seat["open_incident"])
        conn = get_conn()
        st = conn.execute("SELECT status FROM incidents WHERE id=?", (inc_id,)).fetchone()[0]
        nm = conn.execute(
            "SELECT name, quantity, unit FROM materials WHERE work_order_id=? ORDER BY id",
            (wo_id,)).fetchall()
        conn.close()
        self.assertEqual(st, "closed")
        self.assertEqual([(r["name"], r["quantity"], r["unit"]) for r in nm],
                         [("防腐木板", 2.0, "块"), ("木蜡油", 0.5, "升")])

    def test_rejected_inspection_sends_back_to_crew(self):
        sid = make_seat("P-002")
        services.submit_report("市民", sid, "loose_bolt", "晃")
        conn = get_conn()
        wo_id = conn.execute("SELECT id FROM work_orders WHERE seat_id=?", (sid,)).fetchone()[0]
        conn.close()
        crew, inspector = "三班", "巡查员老周"
        services.claim_work_order(crew, wo_id)
        services.record_closure(crew, wo_id, "tape_on")
        services.finish_work_order(crew, wo_id, "紧了一下")
        services.record_closure(crew, wo_id, "tape_off")
        # 驳回不需要照片/勾选
        w = services.inspect_work_order(inspector, "inspector", wo_id, False,
                                        note="仍然晃动")
        self.assertEqual(w["status"], "claimed")
        self.assertEqual(services.get_seat(sid)["usable"], 0)


# ---------------- HTTP 层测试（真实多线程服务器） ----------------

class HttpTests(unittest.TestCase):
    server = None

    @classmethod
    def setUpClass(cls):
        init_db()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        reset_db()

    def call(self, method, path, body=None, role=None, user=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if role:
            req.add_header("X-Role", role)
        if user:
            req.add_header("X-User", user)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_http_concurrent_claim_single_winner(self):
        sid = make_seat("H-001")
        self.call("POST", "/api/reports",
                  {"seat_id": sid, "damage_type": "loose_bolt", "description": "晃"},
                  role="citizen", user="citizen-wang")
        wos = services.list_work_orders("open")
        wo_id = wos[0]["id"]

        outcomes = []
        out_lock = threading.Lock()

        def http_claim(crew):
            st, data = self.call("POST", f"/api/work-orders/{wo_id}/claim", {},
                                 role="crew", user=crew)
            with out_lock:
                outcomes.append((crew, st, data))

        ts = [threading.Thread(target=http_claim, args=(f"crew-{i}",))
              for i in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        wins = [o for o in outcomes if o[1] == 200]
        loses = [o for o in outcomes if o[1] == 400]
        self.assertEqual(len(wins), 1)
        self.assertEqual(len(loses), 5)
        self.assertTrue(all("已被班组" in o[2]["error"] for o in loses))

    def test_http_role_permissions(self):
        sid = make_seat("H-002")
        # 市民不能直接建台账
        st, _ = self.call("POST", "/api/seats",
                          {"code": "X-1", "name": "x", "park": "p", "location": "l",
                           "lat": 1, "lng": 2, "material": "wood"},
                          role="citizen")
        self.assertEqual(st, 400)
        # 班组不能报损
        st, _ = self.call("POST", "/api/reports",
                          {"seat_id": sid, "damage_type": "loose_bolt"},
                          role="crew", user="crew-mou")
        self.assertEqual(st, 400)
        # 巡查员不能认领
        st, _ = self.call("POST", "/api/reports",
                          {"seat_id": sid, "damage_type": "loose_bolt"},
                          role="citizen", user="citizen-wang")
        wo_id = services.list_work_orders("open")[0]["id"]
        st, _ = self.call("POST", f"/api/work-orders/{wo_id}/claim", {},
                          role="inspector", user="inspector-zhou")
        self.assertEqual(st, 400)

    def test_http_wet_paint_full_flow_and_self_inspection_block(self):
        sid = make_seat("H-003")
        self.call("POST", "/api/reports",
                  {"seat_id": sid, "damage_type": "moved", "description": "被搬走"},
                  role="citizen", user="citizen-wang")
        wo_id = services.list_work_orders("open")[0]["id"]
        crew = "http-crew-1"

        st, _ = self.call("POST", f"/api/work-orders/{wo_id}/claim", {},
                          role="crew", user=crew)
        self.assertEqual(st, 200)
        self.assertEqual(201, self.call("POST", f"/api/work-orders/{wo_id}/closures",
                                        {"action": "tape_on"}, role="crew", user=crew)[0])
        st, data = self.call("POST", f"/api/work-orders/{wo_id}/finish",
                             {"repair_note": "复位", "paint_wet": True},
                             role="crew", user=crew)
        self.assertEqual(st, 200)

        # 维修人换个角色标头也不行：同名 inspector 自验被拒
        st, data = self.call("POST", f"/api/work-orders/{wo_id}/inspect",
                             {"passed": True, "tape_confirmed": True},
                             role="inspector", user=crew)
        self.assertEqual(st, 400)
        self.assertIn("不能复验自己", data["error"])

        # 真正巡查员：油漆未干阻断
        st, data = self.call("POST", f"/api/work-orders/{wo_id}/inspect",
                             {"passed": True, "tape_confirmed": True},
                             role="inspector", user="inspector-zhou")
        self.assertEqual(st, 400)
        self.assertIn("油漆未干", data["error"])

        # 解除油漆 → 撤带 → 传重开照片 → 勾选核对 → 通过
        self.assertEqual(200, self.call("POST", f"/api/work-orders/{wo_id}/paint-dry",
                                        {}, role="crew", user=crew)[0])
        self.assertEqual(201, self.call("POST", f"/api/work-orders/{wo_id}/closures",
                                        {"action": "tape_off"}, role="crew", user=crew)[0])
        st, _ = self.call("POST", f"/api/seats/{sid}/photos",
                          {"phase": "reopen", "data": tiny_png_b64(), "ext": "png",
                           "work_order_id": wo_id},
                          role="inspector", user="inspector-zhou")
        self.assertEqual(st, 201)
        st, data = self.call("POST", f"/api/work-orders/{wo_id}/inspect",
                             {"passed": True, "tape_confirmed": True},
                             role="inspector", user="inspector-zhou")
        self.assertEqual(st, 200)
        self.assertEqual(data["status"], "verified")
        st, seat = self.call("GET", f"/api/seats/{sid}")
        self.assertEqual(seat["usable"], 1)

    def test_http_photo_role_phase_restriction(self):
        sid = make_seat("H-004")
        # 市民不能传维修后照片
        st, data = self.call("POST", f"/api/seats/{sid}/photos",
                             {"phase": "after", "data": tiny_png_b64(), "ext": "png"},
                             role="citizen", user="citizen-wang")
        self.assertEqual(st, 400)
        self.assertIn("报损照片", data["error"])
        # 巡查员不能传 before
        st, _ = self.call("POST", f"/api/seats/{sid}/photos",
                          {"phase": "before", "data": tiny_png_b64(), "ext": "png"},
                          role="inspector", user="inspector-zhou")
        self.assertEqual(st, 400)
        # 报损照片可以
        st, _ = self.call("POST", f"/api/seats/{sid}/photos",
                          {"phase": "report", "data": tiny_png_b64(), "ext": "png"},
                          role="citizen", user="citizen-wang")
        self.assertEqual(st, 201)


if __name__ == "__main__":
    unittest.main(verbosity=2)
