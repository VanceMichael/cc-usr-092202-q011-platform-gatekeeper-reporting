"""守门人整改报告服务的规则测试。"""
from __future__ import annotations

import unittest
from pathlib import Path

from src.reporting import (
    GATEKEEPER_DOMAINS,
    LEVELS,
    TASK_CODES,
    ReportingError,
    ReportingService,
    load_case,
)

FIXTURE = Path("fixtures/case_sample.json")
LEAD = "合规负责人-测试"


def _minimal_event(svc: ReportingService, *, level: str = "major", teams: int = 2,
                   filed_at: str = "2026-09-10T15:00") -> str:
    """构造一条已归并 N 个团队上报的事件，不含封存与任务。"""
    team_names = ["甲团队", "乙团队", "丙团队", "丁团队"]
    report_ids = []
    for i in range(teams):
        rid = svc.create_report(
            team=team_names[i],
            title=f"{team_names[i]}上报同一规则异常",
            observed_at="2026-09-10T08:00",
            detail="回滚窗口内指标异常",
            by=f"值班-{i}",
            tags=["rule-x"],
        )
        report_ids.append(rid)
    return svc.file_event(
        title="规则回滚并发影响事件",
        level=level,
        gatekeeper_domains=["算法", "规则"],
        first_observed_at="2026-09-10T08:00",
        report_ids=report_ids,
        by=LEAD,
        at=filed_at,
    )


def _freeze(svc: ReportingService, event_id: str, at: str = "2026-09-10T18:00") -> None:
    svc.freeze_snapshot(
        event_id,
        window_start="2026-09-10T02:00",
        window_end="2026-09-10T09:30",
        rule_versions=[{"code": "rule-x", "version": "v1"}],
        affected_subjects=[{"type": "merchant_segment", "segment": "中小商家", "count": 100}],
        interim_measures=[{"measure": "冻结配置并回退", "at": "2026-09-10T09:10"}],
        by=LEAD,
        at=at,
    )


def _approved_evidence(svc: ReportingService, event_id: str, evidence_id: str = "EV-001",
                       uploader: str = "提交人-甲", controlled: bool = False) -> str:
    svc.add_evidence(
        event_id, evidence_id=evidence_id, title="证据材料", kind="report",
        ref="controlled://vault/x" if controlled else "docs://x",
        sha256="ab" * 32, uploaded_by=uploader, controlled=controlled,
        at="2026-09-11T10:00",
    )
    svc.review_evidence(event_id, evidence_id, decision="approved", by="审签-A", at="2026-09-11T11:00")
    svc.review_evidence(event_id, evidence_id, decision="approved", by="审签-B", at="2026-09-11T12:00")
    return evidence_id


class SignalBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = ReportingService()

    def test_signal_cannot_carry_level_or_file_event(self):
        self.svc.ingest_signal(
            monitor="自动监测", rule_code="rule-x", title="指标异常",
            detail="仅线索", observed_at="2026-09-10T08:00", teams=["甲团队"])
        signal = next(iter(self.svc.signals.values()))
        self.assertNotIn("level", signal)  # 自动监测不得自行定级
        with self.assertRaisesRegex(ReportingError, "监测线索不能直接立案"):
            self.svc.file_event(
                title="越权立案", level="major", gatekeeper_domains=["算法"],
                first_observed_at="2026-09-10T08:00", report_ids=[], by=LEAD,
                at="2026-09-10T09:00")

    def test_signal_requires_human_confirmation_to_report(self):
        sid = self.svc.ingest_signal(
            monitor="自动监测", rule_code="rule-x", title="指标异常",
            detail="仅线索", observed_at="2026-09-10T08:00", teams=["甲团队", "乙团队"])
        rid = self.svc.create_report(
            team="甲团队", title="人工确认后的上报", observed_at="2026-09-10T09:00",
            detail="属实", by="值班-甲", signal_ids=[sid])
        self.assertEqual(self.svc.signals[sid]["status"], "converted")
        self.assertEqual(self.svc.signals[sid]["linked_report_ids"], [rid])
        # 另一团队对同一线索的人工确认同样成立，并成为跨团队归并依据
        rid2 = self.svc.create_report(
            team="乙团队", title="另一团队确认同一线索", observed_at="2026-09-10T09:30",
            detail="x", by="值班-乙", signal_ids=[sid])
        self.assertEqual(self.svc.signals[sid]["linked_report_ids"], [rid, rid2])
        # 已人工排除的线索不得再转报
        sid2 = self.svc.ingest_signal(
            monitor="自动监测", rule_code="rule-x", title="疑似误报",
            detail="噪声", observed_at="2026-09-10T08:00", teams=["甲团队"],
            confidence="low")
        self.svc.dismiss_signal(sid2, by="值班-甲", reason="时钟漂移")
        with self.assertRaisesRegex(ReportingError, "已人工排除"):
            self.svc.create_report(
                team="甲团队", title="试图转报已排除线索", observed_at="2026-09-10T09:40",
                detail="x", by="值班-甲", signal_ids=[sid2])

    def test_dismissed_signal_is_human_disposition(self):
        sid = self.svc.ingest_signal(
            monitor="自动监测", rule_code="rule-x", title="疑似误报",
            detail="噪声", observed_at="2026-09-10T08:00", teams=["甲团队"],
            confidence="low")
        with self.assertRaises(ReportingError):
            self.svc.dismiss_signal(sid, by="", reason="")
        self.svc.dismiss_signal(sid, by="值班-甲", reason="时钟漂移导致的误报")
        self.assertEqual(self.svc.signals[sid]["status"], "dismissed")


class GradedFilingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = ReportingService()

    def test_filing_deadlines_by_level(self):
        from datetime import datetime, timedelta
        expected = {"major": 1, "large": 3, "ordinary": 7}
        for level, days in expected.items():
            svc = ReportingService()
            observed = "2026-09-10T08:00"
            filed_at = (datetime.fromisoformat(observed) + timedelta(days=days)).isoformat()
            event_id = _minimal_event(svc, level=level, teams=2, filed_at=filed_at)
            event = svc.events[event_id]
            self.assertEqual(event["filing_timeliness"], "on_time")
            self.assertEqual(
                event["filing_deadline"],
                (datetime.fromisoformat(observed) + timedelta(days=days)).isoformat())
            self.assertEqual(LEVELS[level]["filing_days"], days)

    def test_late_filing_is_flagged(self):
        # 重大级：9 月 10 日 08:00 发现，9 月 12 日报送 -> 迟报
        event_id = _minimal_event(self.svc, level="major", filed_at="2026-09-12T09:00")
        self.assertEqual(self.svc.events[event_id]["filing_timeliness"], "late")

    def test_on_time_filing(self):
        event_id = _minimal_event(self.svc, level="major", filed_at="2026-09-11T07:59")
        self.assertEqual(self.svc.events[event_id]["filing_timeliness"], "on_time")

    def test_invalid_level_and_domains_rejected(self):
        with self.assertRaisesRegex(ReportingError, "分级非法"):
            _minimal_event(self.svc, level="super")
        rid = self.svc.create_report(
            team="甲团队", title="x", observed_at="2026-09-10T08:00",
            detail="x", by="v")
        with self.assertRaisesRegex(ReportingError, "守门人领域"):
            self.svc.file_event(
                title="t", level="major", gatekeeper_domains=["税务"],
                first_observed_at="2026-09-10T08:00", report_ids=[rid],
                by=LEAD, at="2026-09-10T09:00")

    def test_domains_cover_four_gatekeeper_areas(self):
        self.assertEqual(GATEKEEPER_DOMAINS, {"数据", "算法", "流量", "规则"})
        self.assertEqual(set(TASK_CODES), {"root_cause", "user_remedy", "policy_revision", "retest"})


class MergeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = ReportingService()

    def test_candidates_are_suggestions_only(self):
        for team, by in [("甲团队", "a"), ("乙团队", "b")]:
            self.svc.create_report(
                team=team, title=f"{team}指标异常", observed_at="2026-09-10T08:00",
                detail="x", by=by, tags=["rule-x"])
        candidates = self.svc.find_candidate_reports()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(candidates[0]["report_ids"]), 2)
        # 仅建议：所有上报仍为 new，没有事件产生
        self.assertTrue(all(r["status"] == "new" for r in self.svc.reports.values()))
        self.assertEqual(self.svc.events, {})

    def test_same_team_reports_not_cross_team_candidate(self):
        for i in range(2):
            self.svc.create_report(
                team="甲团队", title=f"重复上报{i}", observed_at="2026-09-10T08:00",
                detail="x", by="a", tags=["rule-x"])
        self.assertEqual(self.svc.find_candidate_reports(), [])

    def test_shared_signal_links_candidates_even_without_tags(self):
        sid = self.svc.ingest_signal(
            monitor="监测", rule_code="r", title="t", detail="d",
            observed_at="2026-09-10T08:00", teams=["甲团队", "乙团队"])
        # 两个团队引用同一线索，但标签不重合、且超出 1 天时间窗
        self.svc.create_report(team="甲团队", title="t1", observed_at="2026-09-10T08:30",
                               detail="x", by="a", tags=["r1"], signal_ids=[sid])
        self.svc.create_report(team="乙团队", title="t2", observed_at="2026-09-12T08:30",
                               detail="x", by="b", tags=["other"], signal_ids=[sid])
        candidates = self.svc.find_candidate_reports(window_days=1)
        self.assertEqual(len(candidates), 1)  # 共同线索构成归并依据
        self.assertEqual(candidates[0]["teams"], ["乙团队", "甲团队"])

    def test_no_link_when_signals_and_tags_both_absent(self):
        self.svc.create_report(team="甲团队", title="t1", observed_at="2026-09-10T08:30",
                               detail="x", by="a", tags=["r1"])
        self.svc.create_report(team="乙团队", title="t2", observed_at="2026-09-12T08:30",
                               detail="x", by="b", tags=["other"])
        self.assertEqual(self.svc.find_candidate_reports(window_days=1), [])

    def test_merge_lifecycle_and_duplicate_reject(self):
        event_id = _minimal_event(self.svc)
        rid = self.svc.create_report(
            team="丙团队", title="后续排查到的关联上报", observed_at="2026-09-10T13:00",
            detail="x", by="c", tags=["rule-x"])
        self.svc.merge_reports(event_id, [rid], by=LEAD, at="2026-09-11T09:00")
        self.assertIn(rid, self.svc.events[event_id]["reports"])
        self.assertIn("丙团队", self.svc.events[event_id]["teams"])
        with self.assertRaisesRegex(ReportingError, "不能重复归并"):
            self.svc.merge_reports(event_id, [rid], by=LEAD, at="2026-09-11T10:00")


class SnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = ReportingService()
        self.event_id = _minimal_event(self.svc)

    def test_freeze_once_and_correction_keeps_before(self):
        _freeze(self.svc, self.event_id)
        with self.assertRaisesRegex(ReportingError, "不能重复封存"):
            _freeze(self.svc, self.event_id)

        before = self.svc.events[self.event_id]["snapshot"]["affected_subjects"]
        self.svc.correct_snapshot(
            self.event_id, field="affected_subjects",
            before=before, after=[{"type": "merchant_segment", "segment": "中小商家", "count": 120}],
            reason="补录边缘商家", by="数据-丁", at="2026-09-11T14:00")
        correction = self.svc.events[self.event_id]["snapshot_corrections"][-1]
        self.assertEqual(correction["before"], before)  # 原值留痕
        self.assertEqual(correction["after"][0]["count"], 120)

    def test_correction_rejects_unknown_field(self):
        _freeze(self.svc, self.event_id)
        with self.assertRaisesRegex(ReportingError, "不允许修正封存字段"):
            self.svc.correct_snapshot(
                self.event_id, field="level", before="major", after="ordinary",
                reason="试图改分级", by=LEAD, at="2026-09-11T10:00")

    def test_window_order_validated(self):
        with self.assertRaisesRegex(ReportingError, "起点不能晚于终点"):
            self.svc.freeze_snapshot(
                self.event_id, window_start="2026-09-10T10:00", window_end="2026-09-10T02:00",
                rule_versions=[{"code": "r", "version": "v1"}],
                affected_subjects=[{"x": 1}], interim_measures=[{"m": 1}],
                by=LEAD, at="2026-09-10T18:00")

    def test_no_change_after_closure(self):
        _freeze(self.svc, self.event_id)
        for code in TASK_CODES:
            self.svc.assign_task(
                self.event_id, code=code, title=f"{code}", owner_team="组",
                due="2026-09-20T18:00", by=LEAD, at="2026-09-10T19:00")
        # 未完成不能关闭；手工把任务走完需要证据，这里直接验证关闭后封锁
        self.svc.events[self.event_id]["status"] = "closed"
        with self.assertRaisesRegex(ReportingError, "事件已关闭，禁止变更"):
            self.svc.assign_task(
                self.event_id, code="root_cause", title="x", owner_team="组",
                due="2026-09-20T18:00", by=LEAD, at="2026-09-10T19:00")


class TaskAndEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = ReportingService()
        self.event_id = _minimal_event(self.svc)
        _freeze(self.svc, self.event_id)

    def test_duplicate_task_code_rejected(self):
        self.svc.assign_task(
            self.event_id, code="root_cause", title="t", owner_team="组",
            due="2026-09-14T18:00", by=LEAD, at="2026-09-10T19:00")
        with self.assertRaisesRegex(ReportingError, "不能重复分派"):
            self.svc.assign_task(
                self.event_id, code="root_cause", title="t2", owner_team="组2",
                due="2026-09-14T18:00", by=LEAD, at="2026-09-10T19:30")

    def test_submit_requires_approved_evidence(self):
        tid = self.svc.assign_task(
            self.event_id, code="root_cause", title="t", owner_team="组",
            due="2026-09-14T18:00", by=LEAD, at="2026-09-10T19:00")
        self.svc.add_evidence(
            self.event_id, evidence_id="EV-x", title="未审签材料", kind="report",
            ref="docs://x", sha256="cd" * 32, uploaded_by="提交人-甲",
            at="2026-09-11T10:00")
        with self.assertRaisesRegex(ReportingError, "证据未通过审签"):
            self.svc.submit_task(self.event_id, tid, evidence_ids=["EV-x"],
                                 note="x", by="提交人-甲", at="2026-09-11T15:00")

    def test_two_person_review_segregation_of_duty(self):
        self.svc.add_evidence(
            self.event_id, evidence_id="EV-y", title="材料", kind="report",
            ref="docs://y", sha256="ef" * 32, uploaded_by="提交人-甲",
            at="2026-09-11T10:00")
        # 上传人不能审批
        with self.assertRaisesRegex(ReportingError, "职责分离"):
            self.svc.review_evidence(self.event_id, "EV-y", decision="approved",
                                     by="提交人-甲", at="2026-09-11T11:00")
        self.svc.review_evidence(self.event_id, "EV-y", decision="approved",
                                 by="审签-A", at="2026-09-11T11:00")
        # 一人不能重复签署
        with self.assertRaisesRegex(ReportingError, "不能重复签署"):
            self.svc.review_evidence(self.event_id, "EV-y", decision="approved",
                                     by="审签-A", at="2026-09-11T12:00")
        self.assertEqual(
            next(e for e in self.svc.events[self.event_id]["evidence"] if e["id"] == "EV-y")["state"],
            "submitted")  # 一签不足
        self.svc.review_evidence(self.event_id, "EV-y", decision="approved",
                                 by="审签-B", at="2026-09-11T13:00")
        self.assertEqual(
            next(e for e in self.svc.events[self.event_id]["evidence"] if e["id"] == "EV-y")["state"],
            "approved")

    def test_rejected_evidence_cannot_be_used(self):
        self.svc.add_evidence(
            self.event_id, evidence_id="EV-z", title="材料", kind="report",
            ref="docs://z", sha256="12" * 32, uploaded_by="提交人-甲",
            at="2026-09-11T10:00")
        self.svc.review_evidence(self.event_id, "EV-z", decision="rejected",
                                 by="审签-A", note="数据无法复现", at="2026-09-11T11:00")
        tid = self.svc.assign_task(
            self.event_id, code="root_cause", title="t", owner_team="组",
            due="2026-09-14T18:00", by=LEAD, at="2026-09-10T19:00")
        with self.assertRaisesRegex(ReportingError, "证据未通过审签"):
            self.svc.submit_task(self.event_id, tid, evidence_ids=["EV-z"],
                                 note="x", by="提交人-甲", at="2026-09-12T10:00")

    def test_retest_requires_explicit_result_and_failure_blocks_risks(self):
        tid = self.svc.assign_task(
            self.event_id, code="retest", title="复测", owner_team="测试组",
            due="2026-09-20T18:00", by=LEAD, at="2026-09-10T19:00")
        eid = _approved_evidence(self.svc, self.event_id, "EV-r")
        self.svc.submit_task(self.event_id, tid, evidence_ids=[eid], note="复测完成",
                             by="测试-丁", at="2026-09-19T10:00")
        with self.assertRaisesRegex(ReportingError, "必须明确是否通过"):
            self.svc.complete_task(self.event_id, tid, by=LEAD, at="2026-09-19T11:00")
        self.svc.complete_task(self.event_id, tid, by=LEAD, at="2026-09-19T11:00",
                               retest_passed=False)
        codes = {r["code"] for r in self.svc.unresolved_risks(self.event_id, on="2026-09-20T09:00")}
        self.assertIn("RETEST_FAILED", codes)
        # 复测失败后重新打开任务
        self.svc.reopen_task(self.event_id, tid, reason="复测未通过需修复后重测",
                             by=LEAD, at="2026-09-20T10:00")
        self.assertEqual(
            next(t for t in self.svc.events[self.event_id]["tasks"] if t["id"] == tid)["status"],
            "open")


class CommitmentAndReminderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = ReportingService()
        self.event_id = _minimal_event(self.svc)

    def test_verify_requires_evidence_and_failed_stays_risk(self):
        cid = self.svc.add_commitment(
            self.event_id, statement="十日内完成制度修订", owner_team="制度组",
            due="2026-09-20T18:00", by=LEAD, at="2026-09-10T19:00")
        with self.assertRaisesRegex(ReportingError, "必须附通过审签的证据"):
            self.svc.verify_commitment(
                self.event_id, cid, decision="verified", note="无证据",
                by=LEAD, at="2026-09-19T10:00")
        self.svc.verify_commitment(
            self.event_id, cid, decision="failed", note="修订未获通过",
            by=LEAD, at="2026-09-19T10:00")
        risk_text = " ".join(
            r["text"] for r in self.svc.unresolved_risks(self.event_id, on="2026-09-19T12:00"))
        self.assertIn("验证未通过", risk_text)
        # 承诺原文仍保留，未被改写
        self.assertEqual(
            next(c for c in self.svc.events[self.event_id]["commitments"] if c["id"] == cid)["statement"],
            "十日内完成制度修订")

    def test_reminders_split_overdue_and_due_soon(self):
        self.svc.assign_task(
            self.event_id, code="root_cause", title="根因", owner_team="算法组",
            due="2026-09-15T18:00", by=LEAD, at="2026-09-10T19:00")
        self.svc.add_commitment(
            self.event_id, statement="临期承诺", owner_team="制度组",
            due="2026-09-22T18:00", by=LEAD, at="2026-09-10T19:00")
        reminders = self.svc.due_reminders(on="2026-09-20T09:00", within_days=3)
        overdue_items = {(r["item"], r["kind"]) for r in reminders["overdue"]}
        soon_items = {(r["item"], r["kind"]) for r in reminders["due_soon"]}
        self.assertIn(("TASK-0001", "根因调查"), overdue_items)
        self.assertIn(("CMT-001", "整改承诺"), soon_items)


class RecurrenceTest(unittest.TestCase):
    def _closed_prior(self, code: str = "promo-rank") -> str:
        svc = self.svc
        rid1 = svc.create_report(
            team="甲团队", title="历史上报1", observed_at="2026-03-12T07:40",
            detail="x", by="a", tags=[code])
        rid2 = svc.create_report(
            team="乙团队", title="历史上报2", observed_at="2026-03-12T08:30",
            detail="x", by="b", tags=[code])
        prior = svc.file_event(
            title="历史事件", level="large", gatekeeper_domains=["算法"],
            first_observed_at="2026-03-12T07:40", report_ids=[rid1, rid2],
            by=LEAD, at="2026-03-13T10:00")
        svc.freeze_snapshot(
            prior, window_start="2026-03-12T00:00", window_end="2026-03-12T09:00",
            rule_versions=[{"code": code, "version": "v7"}],
            affected_subjects=[{"type": "merchant_segment", "count": 1}],
            interim_measures=[{"measure": "回退", "at": "2026-03-12T09:10"}],
            by=LEAD, at="2026-03-13T11:00")
        # 关闭历史事件所需的最小闭环
        for task_code in TASK_CODES:
            tid = svc.assign_task(
                prior, code=task_code, title=task_code, owner_team="组",
                due="2026-03-28T18:00", by=LEAD, at="2026-03-13T14:00")
            eid = _approved_evidence(svc, prior, f"EV-{code}-{task_code}")
            svc.submit_task(prior, tid, evidence_ids=[eid], note="完成",
                            by="执行人", at="2026-03-27T10:00")
            svc.complete_task(prior, tid, by=LEAD, at="2026-03-27T11:00",
                              retest_passed=True if task_code == "retest" else None)
        cid = svc.add_commitment(
            prior, statement="纳入双人复核", owner_team="算法组",
            due="2026-06-30T18:00", by=LEAD, at="2026-03-20T10:00")
        svc.verify_commitment(prior, cid, decision="verified",
                              evidence_ids=[f"EV-{code}-root_cause"],
                              note="已上线", by=LEAD, at="2026-06-25T10:00")
        svc.request_closure(prior, by=LEAD, at="2026-06-26T10:00")
        return prior

    def setUp(self) -> None:
        self.svc = ReportingService()
        self.prior = self._closed_prior()
        self.current = _minimal_event(self.svc)
        _freeze(self.svc, self.current)

    def test_recurrence_requires_shared_rule_and_closed_prior(self):
        # 当前封存 rule-x，历史 promo-rank：规则模块无重合 -> 拒绝
        with self.assertRaisesRegex(ReportingError, "规则模块无重合"):
            self.svc.link_recurrence(
                self.current, self.prior, reason="时间相近", by=LEAD,
                at="2026-09-11T16:00")

        # 修正当前封存为共同模块后人工确认成功
        self.svc.correct_snapshot(
            self.current, field="rule_versions",
            before=[{"code": "rule-x", "version": "v1"}],
            after=[{"code": "promo-rank", "version": "v8"}],
            reason="核对后确认同一模块", by=LEAD, at="2026-09-11T15:00")
        self.svc.link_recurrence(
            self.current, self.prior,
            reason="同一模块发布管控再度失效", by=LEAD, at="2026-09-11T16:00")
        links = self.svc.events[self.current]["recurrence"]["prior"]
        self.assertEqual(links[0]["shared_rule_codes"], ["promo-rank"])
        # 反向索引：历史事件记录后续复发
        self.assertEqual(
            self.svc.events[self.prior]["recurrence"]["followups"][0]["event_id"],
            self.current)

    def test_cannot_link_to_open_event(self):
        with self.assertRaisesRegex(ReportingError, "已关闭的历史事件"):
            self.svc.link_recurrence(self.prior, self.current,
                                     reason="反向尝试", by=LEAD, at="2026-09-11T16:00")


class SummaryAndClosureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = ReportingService()
        self.event_id = _minimal_event(self.svc)

    def test_summary_does_not_whitewash_open_risks(self):
        summary = self.svc.regulatory_summary(self.event_id, on="2026-09-23T09:00")
        self.assertTrue(summary["未解决风险"])
        self.assertIn("不得作为整改完成", summary["摘要结论"])
        # 尚未封存时摘要如实显示
        self.assertEqual(summary["封存事实"], "尚未封存")

    def test_controlled_attachments_masked_in_summary(self):
        _freeze(self.svc, self.event_id)
        _approved_evidence(self.svc, self.event_id, "EV-secret", controlled=True)
        summary = self.svc.regulatory_summary(self.event_id, on="2026-09-23T09:00")
        item = next(e for e in summary["证据审签"] if e["evidence_id"] == "EV-secret")
        self.assertTrue(item["controlled"])
        self.assertNotIn("ref", item)
        self.assertNotIn("sha256", item)
        self.assertIn("受控通道", item["access"])

    def test_closure_gate_blocks_until_everything_done(self):
        with self.assertRaisesRegex(ReportingError, "关闭闸门未通过"):
            self.svc.request_closure(self.event_id, by=LEAD, at="2026-09-23T09:00")

        _freeze(self.svc, self.event_id)
        for code in TASK_CODES:
            tid = self.svc.assign_task(
                self.event_id, code=code, title=code, owner_team="组",
                due="2026-09-26T18:00", by=LEAD, at="2026-09-10T19:00")
            eid = _approved_evidence(self.svc, self.event_id, f"EV-{code}")
            self.svc.submit_task(self.event_id, tid, evidence_ids=[eid], note="完成",
                                 by="执行人", at="2026-09-25T10:00")
            self.svc.complete_task(self.event_id, tid, by=LEAD, at="2026-09-25T11:00",
                                   retest_passed=True if code == "retest" else None)
        cid = self.svc.add_commitment(
            self.event_id, statement="完成整改", owner_team="组",
            due="2026-09-26T18:00", by=LEAD, at="2026-09-10T19:00")
        # 承诺未验证前仍不能关闭
        with self.assertRaisesRegex(ReportingError, "关闭闸门未通过"):
            self.svc.request_closure(self.event_id, by=LEAD, at="2026-09-26T09:00")
        self.svc.verify_commitment(
            self.event_id, cid, decision="verified", evidence_ids=["EV-root_cause"],
            note="已完成", by=LEAD, at="2026-09-26T10:00")
        self.svc.request_closure(self.event_id, by=LEAD, at="2026-09-26T15:00")
        self.assertEqual(self.svc.events[self.event_id]["status"], "closed")
        summary = self.svc.regulatory_summary(self.event_id, on="2026-09-26T16:00")
        self.assertEqual(summary["未解决风险"], [])
        self.assertIn("可提交关闭审议", summary["摘要结论"])

    def test_commitment_comparison_verdicts(self):
        c1 = self.svc.add_commitment(
            self.event_id, statement="按期完成", owner_team="组",
            due="2026-09-20T18:00", by=LEAD, at="2026-09-10T19:00")
        c2 = self.svc.add_commitment(
            self.event_id, statement="逾期完成", owner_team="组",
            due="2026-09-20T18:00", by=LEAD, at="2026-09-10T19:00")
        c3 = self.svc.add_commitment(
            self.event_id, statement="验证未过", owner_team="组",
            due="2026-09-20T18:00", by=LEAD, at="2026-09-10T19:00")
        c4 = self.svc.add_commitment(
            self.event_id, statement="仍在期限内", owner_team="组",
            due="2026-09-30T18:00", by=LEAD, at="2026-09-10T19:00")
        eid = _approved_evidence(self.svc, self.event_id, "EV-c")
        self.svc.verify_commitment(self.event_id, c1, decision="verified",
                                   evidence_ids=[eid], note="ok", by=LEAD,
                                   at="2026-09-19T10:00")
        self.svc.verify_commitment(self.event_id, c2, decision="verified",
                                   evidence_ids=[eid], note="late", by=LEAD,
                                   at="2026-09-22T10:00")
        self.svc.verify_commitment(self.event_id, c3, decision="failed",
                                   note="未落实", by=LEAD, at="2026-09-19T10:00")
        verdicts = {r["commitment_id"]: r["对账结论"]
                    for r in self.svc.compare_commitments(self.event_id, on="2026-09-25T09:00")}
        self.assertEqual(verdicts[c1], "已兑现")
        self.assertEqual(verdicts[c2], "逾期兑现")
        self.assertEqual(verdicts[c3], "验证未通过")
        self.assertEqual(verdicts[c4], "待验证（整改期内）")
        # 不传 on 时，open 承诺无法判定逾期，保持待验证
        self.assertEqual(
            next(r for r in self.svc.compare_commitments(self.event_id)
                 if r["commitment_id"] == c2)["对账结论"],
            "逾期兑现")  # 逾期兑现不依赖 on


class PersistenceTest(unittest.TestCase):
    def test_roundtrip_and_fixture(self):
        self.assertTrue(FIXTURE.exists(), "请先运行 python fixtures/build_case_sample.py")
        svc = load_case(FIXTURE)
        event = svc.events["EVT-2026-09"]
        self.assertEqual(event["level"], "major")
        self.assertEqual(len(event["reports"]), 4)  # 三团队 + 客服追并
        self.assertEqual(event["gatekeeper_domains"], ["数据", "算法", "流量", "规则"])
        self.assertTrue(event["snapshot_corrections"])  # 封存修正留痕
        self.assertEqual(event["recurrence"]["prior"][0]["event_id"], "EVT-2026-03")
        # 历史事件已关闭，当前事件仍开放
        self.assertEqual(svc.events["EVT-2026-03"]["status"], "closed")
        self.assertNotEqual(event["status"], "closed")
        # 误报线索保留人工排除记录
        self.assertEqual(svc.signals["SGN-0003"]["status"], "dismissed")

        # 重新导出再载入，状态等价
        svc2 = ReportingService.from_dict(svc.to_dict())
        self.assertEqual(set(svc2.events), set(svc.events))
        self.assertEqual(
            svc2.regulatory_summary("EVT-2026-09", on="2026-09-23T09:00")["未解决风险"],
            svc.regulatory_summary("EVT-2026-09", on="2026-09-23T09:00")["未解决风险"],
        )

    def test_load_rejects_wrong_domain_or_old_version(self):
        import tempfile
        cases = [
            ({"domain": "other", "version": 2, "events": [], "reports": [], "signals": []},
             "领域标识不匹配"),
            ({"domain": "platform-gatekeeper-reporting", "version": 1,
              "events": [], "reports": [], "signals": []},
             "版本过旧"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for i, (data, message) in enumerate(cases):
                path = Path(tmp) / f"bad{i}.json"
                path.write_text(__import__("json").dumps(data), encoding="utf-8")
                with self.assertRaisesRegex(ReportingError, message):
                    load_case(path)
        # 现有公开样例为 v1 资料，按整改报告服务载入时应提示版本过旧
        with self.assertRaisesRegex(ReportingError, "版本过旧"):
            load_case(Path("fixtures/context.json"))


if __name__ == "__main__":
    unittest.main()
