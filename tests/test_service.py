"""领域规则测试：并发认领、错误归并、复验权限、未干油漆阻断、封控/照片门槛。"""
from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from benchcare.service import (
    BenchService,
    ConflictError,
    ServiceError,
)


class MutableClock:
    """可手动推进的时钟，替代 datetime.now。"""

    def __init__(self, t: datetime | None = None) -> None:
        self.t = t or datetime(2026, 10, 1, 9, 0, 0)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, minutes: float) -> None:
        self.t += timedelta(minutes=minutes)


class BenchTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = MutableClock()
        self.svc = BenchService(
            Path(self.tmp.name) / "test.db",
            photo_dir=Path(self.tmp.name) / "photos",
            clock=self.clock,
        )
        self.svc.add_bench("B-001", "wood", 30.25, 120.15, "公园一号椅")
        self.svc.add_bench("B-002", "metal", 30.26, 120.16, "广场二号椅")
        # 占位照片文件
        self.photo = self.svc.save_photo(b"\xff\xd8\xff\xd9", "jpg")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _full_flow_to_review(self, issue="loose_bolt", crew="维保一班",
                             painted=False, dry_minutes=60):
        """报损 → 认领 → 封控 → 完工待复验，返回 event_id。"""
        eid = self.svc.report_damage("B-001", issue, reporter="市民")["event_id"]
        self.svc.claim(eid, crew)
        self.svc.cordon(eid, crew, "警示带围挡")
        self.svc.complete_repair(
            eid, crew, repair_note="已修复", after_photo=self.photo,
            painted=painted, paint_dry_minutes=dry_minutes)
        return eid


class TestReportMerge(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = MutableClock()
        self.svc = BenchService(
            Path(self.tmp.name) / "t.db",
            photo_dir=Path(self.tmp.name) / "p", clock=self.clock)
        self.svc.add_bench("B-001", "wood", 30.25, 120.15, "一号椅")
        self.svc.add_bench("B-002", "wood", 30.25, 120.16, "二号椅")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_repeated_reports_merge_to_one_event(self):
        r1 = self.svc.report_damage("B-001", "wood_split", "王女士", "板裂了")
        self.assertFalse(r1["merged"])
        r2 = self.svc.report_damage("B-001", "wood_split", "李先生", "同一张")
        r3 = self.svc.report_damage("B-001", "wood_split", "匿名市民", "还没人修？")
        self.assertTrue(r2["merged"])
        self.assertTrue(r3["merged"])
        self.assertEqual(r1["event_id"], r2["event_id"], r3["event_id"])
        ev = self.svc.event_detail(r1["event_id"])
        self.assertEqual(ev["report_count"], 3)
        self.assertEqual(len(ev["merged_reports"]), 3)
        # 只有一张工单
        wos = self.svc.list_work_orders()
        self.assertEqual(len(wos), 1)
        self.assertTrue(ev["bench"]["usable"] is False or ev["bench"]["usable"] == 0)

    def test_different_issue_type_is_not_merged(self):
        # 错误归并防护：同座椅不同损坏类型 → 拒绝并提示
        self.svc.report_damage("B-001", "wood_split", "王女士")
        with self.assertRaises(ConflictError) as cm:
            self.svc.report_damage("B-001", "loose_bolt", "李先生")
        self.assertIn("不能归并", str(cm.exception))
        # 事件仍只有 1 个，报损数仍是 1
        evs = self.svc.list_events(open_only=True)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["report_count"], 1)

    def test_different_benches_not_merged(self):
        a = self.svc.report_damage("B-001", "wood_split", "甲")
        b = self.svc.report_damage("B-002", "wood_split", "乙")
        self.assertNotEqual(a["event_id"], b["event_id"])

    def test_new_report_after_close_creates_new_event(self):
        r1 = self.svc.report_damage("B-001", "loose_bolt", "甲")
        eid = r1["event_id"]
        self.svc.claim(eid, "维保一班")
        self.svc.cordon(eid, "维保一班")
        photo = self.svc.save_photo(b"x", "jpg")
        self.svc.complete_repair(eid, "维保一班", after_photo=photo)
        self.svc.review(eid, "复验员老周", passed=True, tape_removed=True)
        # 归档后座椅恢复可用
        bench = self.svc.bench_detail("B-001")
        self.assertEqual(bench["usable"], 1)
        self.assertIsNone(bench["open_event_id"])
        # 再报损 → 新事件（不与已归档事件归并），座椅重新停用
        r2 = self.svc.report_damage("B-001", "loose_bolt", "乙")
        self.assertFalse(r2["merged"])
        self.assertNotEqual(eid, r2["event_id"])
        self.assertEqual(self.svc.bench_detail("B-001")["usable"], 0)


class TestConcurrentClaim(BenchTestBase):
    def test_two_crews_only_one_wins(self):
        eid = self.svc.report_damage("B-001", "loose_bolt", "市民")["event_id"]
        results: dict[str, object] = {}
        errors: list[str] = []
        barrier = threading.Barrier(2)

        def worker(crew: str) -> None:
            try:
                barrier.wait()
                results[crew] = self.svc.claim(eid, crew)
            except ConflictError as e:
                errors.append(str(e))
            except Exception as e:  # noqa: BLE001
                errors.append(f"OTHER:{e}")

        t1 = threading.Thread(target=worker, args=("维保一班",))
        t2 = threading.Thread(target=worker, args=("维保二班",))
        t1.start(); t2.start(); t1.join(); t2.join()

        self.assertEqual(len(results), 1, "恰好一个班组认领成功")
        self.assertEqual(len(errors), 1, "另一个班组必须收到冲突错误")
        self.assertIn("已被", errors[0] + "认领")
        ev = self.svc.event_detail(eid)
        self.assertEqual(ev["claimed_by"], next(iter(results)))
        self.assertEqual(ev["status"], "claimed")

    def test_double_claim_same_crew_idempotent_conflict(self):
        eid = self.svc.report_damage("B-001", "moved", "市民")["event_id"]
        self.svc.claim(eid, "维保一班")
        with self.assertRaises(ConflictError):
            self.svc.claim(eid, "维保二班")


class TestCordonRepairGuards(BenchTestBase):
    def test_other_crew_cannot_touch_order(self):
        eid = self.svc.report_damage("B-001", "moved", "市民")["event_id"]
        self.svc.claim(eid, "维保一班")
        with self.assertRaises(ServiceError):
            self.svc.cordon(eid, "维保二班", "抢单操作")
        with self.assertRaises(ServiceError):
            self.svc.use_materials(eid, "维保二班",
                                   [{"item": "螺栓", "qty": 2, "unit": "套"}])

    def test_complete_requires_cordon_then_photo(self):
        eid = self.svc.report_damage("B-001", "wood_split", "市民")["event_id"]
        self.svc.claim(eid, "维保一班")
        # 没有封控记录不能提交复验
        with self.assertRaises(ServiceError) as cm:
            self.svc.complete_repair(eid, "维保一班", after_photo=self.photo)
        self.assertIn("封控", str(cm.exception))
        self.svc.cordon(eid, "维保一班", "整椅围挡")
        # 没有维修后照片不能提交
        with self.assertRaises(ServiceError) as cm:
            self.svc.complete_repair(eid, "维保一班")
        self.assertIn("照片", str(cm.exception))

    def test_cordon_record_content_per_issue_type(self):
        cases = {
            "loose_bolt": "局部警示带",
            "wood_split": "整椅围挡",
            "wet_paint": "隔离绳+告示牌",
            "moved": "复位区域警示带",
            "removed": "基础坑位盖板+警示带",
        }
        for issue, expect in cases.items():
            bid = "B-001"
            r = self.svc.report_damage(bid, issue, "市民")
            # 第二个座椅用于其余案例：复用同椅需先归档，改为每类后重置标记
            eid = r["event_id"]
            self.svc.claim(eid, "维保一班")
            ev = self.svc.cordon(eid, "维保一班", "")
            self.assertIn(expect, ev["cordon_note"])
            # 清理：直接让事件可继续，不影响下一类——重置座椅未结事件
            self.svc.conn.execute("UPDATE benches SET open_event_id=NULL, usable=1")
            self.svc.conn.execute("UPDATE events SET status='closed'")

    def test_materials_consumption(self):
        eid = self._full_flow_to_review()
        used = self.svc.use_materials(eid, "维保一班", [
            {"item": "膨胀螺栓", "qty": 4, "unit": "套"},
            {"item": "垫片", "qty": 4, "unit": "个"},
        ])
        self.assertEqual(len(used), 2)
        with self.assertRaises(ServiceError):
            self.svc.use_materials(eid, "维保一班",
                                   [{"item": "螺栓", "qty": 0, "unit": "套"}])

    def test_removed_bench_marked_and_reinstalled(self):
        eid = self.svc.report_damage("B-001", "removed", "巡查员")["event_id"]
        self.assertEqual(self.svc.bench_detail("B-001")["removed"], 1)
        self.svc.claim(eid, "维保一班")
        self.svc.cordon(eid, "维保一班")
        self.svc.complete_repair(eid, "维保一班", after_photo=self.photo)
        ev = self.svc.event_detail(eid)
        self.assertIsNotNone(ev["reinstalled_at"])
        self.assertEqual(ev["bench"]["removed"], 0)


class TestReview(BenchTestBase):
    def test_repair_crew_cannot_review_own_work(self):
        eid = self._full_flow_to_review(crew="维保一班")
        with self.assertRaises(ServiceError) as cm:
            self.svc.review(eid, "维保一班", passed=True, tape_removed=True)
        self.assertIn("不能复验", str(cm.exception))

    def test_reopen_requires_tape_removed(self):
        eid = self._full_flow_to_review()
        with self.assertRaises(ServiceError) as cm:
            self.svc.review(eid, "复验员老周", passed=True, tape_removed=False)
        self.assertIn("警示带", str(cm.exception))

    def test_wet_paint_blocks_review_until_dry(self):
        # 刷漆后 60 分钟干燥
        eid = self._full_flow_to_review(issue="wet_paint", painted=True,
                                        dry_minutes=60)
        with self.assertRaises(ServiceError) as cm:
            self.svc.review(eid, "复验员老周", passed=True, tape_removed=True)
        self.assertIn("油漆未干", str(cm.exception))
        ev = self.svc.event_detail(eid)
        self.assertFalse(ev["paint"]["dry"])
        self.assertGreater(ev["paint"]["remain_minutes"], 0)
        self.assertEqual(ev["status"], "awaiting_review")  # 阻断不改变状态

        # 推进 59 分钟仍阻断
        self.clock.advance(59)
        with self.assertRaises(ServiceError):
            self.svc.review(eid, "复验员老周", passed=True, tape_removed=True)
        # 61 分钟后干燥，复验通过
        self.clock.advance(2)
        ev = self.svc.review(eid, "复验员老周", passed=True, tape_removed=True)
        self.assertEqual(ev["status"], "closed")
        self.assertEqual(ev["bench"]["usable"], 1)

    def test_failed_review_sends_back_to_repair(self):
        eid = self._full_flow_to_review()
        ev = self.svc.review(eid, "复验员老周", passed=False, tape_removed=False,
                             note="螺栓仍可晃动")
        self.assertEqual(ev["status"], "repairing")
        wo = ev["work_order"]
        self.assertEqual(wo["status"], "claimed")
        # 返工后重新提交，复验员复验通过
        self.svc.complete_repair(eid, "维保一班", repair_note="换了螺栓",
                                 after_photo=self.photo)
        ev = self.svc.review(eid, "复验员老周", passed=True, tape_removed=True)
        self.assertEqual(ev["review_passed"], 1)
        self.assertEqual(ev["status"], "closed")

    def test_full_success_restores_usable_and_links(self):
        eid = self._full_flow_to_review()
        ev = self.svc.review(eid, "复验员老周", passed=True, tape_removed=True,
                             note="紧固到位，现场清理干净")
        self.assertEqual(ev["bench"]["usable"], 1)
        self.assertIsNone(ev["bench"]["open_event_id"])
        self.assertTrue(ev["tape_removed"], 1)
        self.assertEqual(ev["work_order"]["status"], "reviewed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
