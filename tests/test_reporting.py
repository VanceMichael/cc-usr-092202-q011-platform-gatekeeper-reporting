"""重大事项与整改报告服务测试。"""

import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from src.reporting import (
    ActionKind,
    ActionStatus,
    AttachmentVisibility,
    DUE_SOON_WINDOW,
    IncidentLevel,
    IncidentStatus,
    LeadStatus,
    REPORT_DEADLINES,
    ReportingError,
    ReportingService,
    load_service,
)

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    """可手动推进的时钟，保证期限类测试确定可复现。"""

    def __init__(self, value: str = "2026-09-20T10:00:00") -> None:
        self.value = datetime.fromisoformat(value)

    def set(self, value: str) -> None:
        self.value = datetime.fromisoformat(value)

    def advance(self, **delta) -> None:
        self.value += timedelta(**delta)

    def iso(self) -> str:
        return self.value.isoformat()

    def __call__(self) -> datetime:
        return self.value


def base_report_kwargs(**overrides):
    payload = dict(
        report_id="R-001",
        title="排序模型回滚异常",
        level=IncidentLevel.MAJOR,
        domains=["算法", "流量"],
        team="算法团队",
        reporter="值班工程师",
        occurred_at="2026-09-20T01:00:00",
        first_detected_at="2026-09-20T01:10:00",
        rule_versions=["ranking-model@v318"],
        keywords=["算法", "回滚"],
        summary="回滚后异常",
    )
    payload.update(overrides)
    return payload


def freeze(svc, incident_id, by="合规经理"):
    return svc.freeze_incident(
        incident_id,
        affected_window={"start": "2026-09-20T00:30:00", "end": "2026-09-20T02:30:00"},
        rule_versions=["ranking-model@v318", "subsidy-rule@v204"],
        affected_subjects={"merchants": 100},
        interim_measures=["回滚至上一稳定版本"],
        by=by,
    )


def assign_all(svc, incident_id):
    svc.assign_action(incident_id, ActionKind.ROOT_CAUSE, "算法治理组")
    svc.assign_action(incident_id, ActionKind.REMEDY, "商家服务组")
    svc.assign_action(incident_id, ActionKind.POLICY, "平台规则组")
    svc.assign_action(incident_id, ActionKind.RETEST, "质量与风控组")


class LeveledReportingTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.svc = ReportingService(clock=self.clock)

    def test_levels_have_report_deadlines(self):
        self.assertEqual(REPORT_DEADLINES[IncidentLevel.MAJOR], timedelta(hours=2))
        self.assertEqual(REPORT_DEADLINES[IncidentLevel.SERIOUS], timedelta(hours=24))
        self.assertEqual(REPORT_DEADLINES[IncidentLevel.ORDINARY], timedelta(days=3))

    def test_report_deadline_computed_from_level(self):
        inc = self.svc.report_incident(**base_report_kwargs())
        expected = (
            datetime.fromisoformat("2026-09-20T01:10:00")
            + REPORT_DEADLINES[IncidentLevel.MAJOR]
        )
        self.assertEqual(inc.report_deadline, expected.isoformat())
        self.assertEqual(inc.status, IncidentStatus.REPORTED)

    def test_duplicate_report_id_rejected(self):
        self.svc.report_incident(**base_report_kwargs())
        with self.assertRaises(ReportingError):
            self.svc.report_incident(**base_report_kwargs(
                title="另一起事件", occurred_at="2026-09-25T01:00:00",
                first_detected_at="2026-09-25T01:10:00",
            ))

    def test_timeliness_flags_late_initial_report(self):
        # 发现后 3 小时才报送，超过重大事项 2 小时时限
        self.clock.set("2026-09-20T04:10:00")
        self.svc.report_incident(**base_report_kwargs())
        rows = self.svc.report_timeliness()
        self.assertFalse(rows[0]["on_time"])

    def test_timeliness_accepts_report_within_window(self):
        self.clock.set("2026-09-20T02:00:00")
        self.svc.report_incident(**base_report_kwargs())
        self.assertTrue(self.svc.report_timeliness()[0]["on_time"])


class FreezeTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.svc = ReportingService(clock=self.clock)
        self.incident = self.svc.report_incident(**base_report_kwargs())

    def test_freeze_locks_core_fields(self):
        freeze(self.svc, self.incident.id)
        inc = self.svc.incidents[self.incident.id]
        self.assertEqual(inc.frozen["rule_versions"],
                         ["ranking-model@v318", "subsidy-rule@v204"])
        self.assertIsNotNone(inc.frozen_at)
        self.assertEqual(inc.frozen_by, "合规经理")

    def test_double_freeze_rejected(self):
        freeze(self.svc, self.incident.id)
        with self.assertRaises(ReportingError):
            freeze(self.svc, self.incident.id)

    def test_freeze_validates_window_order(self):
        with self.assertRaises(ReportingError):
            self.svc.freeze_incident(
                self.incident.id,
                affected_window={"start": "2026-09-20T03:00:00", "end": "2026-09-20T02:00:00"},
                rule_versions=["x@v1"], affected_subjects={"a": 1},
                interim_measures=["m"], by="合规经理",
            )

    def test_revision_keeps_history_and_never_overwrites_original(self):
        freeze(self.svc, self.incident.id)
        rev = self.svc.revise_frozen(
            self.incident.id, "affected_subjects", {"merchants": 180},
            reason="日志扩样", by="数据组",
        )
        inc = self.svc.incidents[self.incident.id]
        self.assertEqual(rev.before, {"merchants": 100})
        self.assertEqual(rev.after, {"merchants": 180})
        self.assertEqual(inc.frozen["affected_subjects"], {"merchants": 180})
        self.assertEqual(len(inc.freeze_revisions), 1)
        self.assertEqual(inc.freeze_revisions[0].reason, "日志扩样")

    def test_revision_requires_reason(self):
        freeze(self.svc, self.incident.id)
        with self.assertRaises(ReportingError):
            self.svc.revise_frozen(
                self.incident.id, "affected_subjects", {"merchants": 200},
                reason="", by="数据组",
            )

    def test_noop_revision_rejected(self):
        freeze(self.svc, self.incident.id)
        with self.assertRaises(ReportingError):
            self.svc.revise_frozen(
                self.incident.id, "rule_versions",
                ["ranking-model@v318", "subsidy-rule@v204"],
                reason="无变化", by="数据组",
            )

    def test_revision_before_freeze_rejected(self):
        with self.assertRaises(ReportingError):
            self.svc.revise_frozen(
                self.incident.id, "affected_subjects", {"merchants": 200},
                reason="提前修改", by="数据组",
            )


class CrossTeamMergeTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.svc = ReportingService(clock=self.clock)
        self.incident = self.svc.report_incident(**base_report_kwargs())
        freeze(self.svc, self.incident.id)

    def _second_report(self, **overrides):
        payload = dict(
            report_id="R-002",
            title="申诉失败与补贴错发",
            level=IncidentLevel.MAJOR,
            domains=["数据", "规则"],
            team="商家治理团队",
            reporter="商家治理值班",
            occurred_at="2026-09-20T01:20:00",
            rule_versions=["subsidy-rule@v204", "appeal-flow@v57"],
            keywords=["申诉", "补贴", "价格"],
            summary="同一回滚引发申诉和补贴问题",
        )
        payload.update(overrides)
        return self.svc.report_incident(**payload)

    def test_same_window_and_rules_merge_to_one_incident(self):
        merged = self._second_report()
        self.assertEqual(merged.id, self.incident.id)
        self.assertEqual(len(self.svc.incidents), 1)
        self.assertEqual(
            self.svc.incidents[self.incident.id].merged_report_ids, ["R-001", "R-002"]
        )

    def test_unrelated_report_creates_new_incident(self):
        # 时间窗外 + 规则版本不同 + 信号词不共现 → 不满足归并阈值
        new = self._second_report(
            report_id="R-099",
            title="数据接口越权",
            occurred_at="2026-05-01T01:20:00",
            rule_versions=["merchant-api@v73"],
            keywords=["数据", "接口", "权限"],
            summary="接口返回越权字段",
        )
        self.assertNotEqual(new.id, self.incident.id)
        self.assertEqual(len(self.svc.incidents), 2)

    def test_different_version_of_same_rule_does_not_merge(self):
        # v204 与 v205 是不同版本，时间窗内 + 共有“补贴”信号也只到阈值以下
        new = self._second_report(
            report_id="R-098",
            occurred_at="2026-09-20T01:25:00",
            rule_versions=["subsidy-rule@v205"],
            keywords=["补贴"],
            summary="另一版本补贴问题",
        )
        self.assertNotEqual(new.id, self.incident.id)

    def test_merge_adds_rule_version_via_freeze_revision(self):
        self._second_report()
        inc = self.svc.incidents[self.incident.id]
        self.assertIn("appeal-flow@v57", inc.frozen["rule_versions"])
        self.assertEqual(len(inc.freeze_revisions), 1)
        self.assertEqual(inc.freeze_revisions[0].field, "rule_versions")


class ActionWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.svc = ReportingService(clock=self.clock)
        self.incident = self.svc.report_incident(**base_report_kwargs())
        freeze(self.svc, self.incident.id)
        assign_all(self.svc, self.incident.id)

    def test_assign_starts_investigation(self):
        self.assertEqual(self.svc.incidents[self.incident.id].status,
                         IncidentStatus.INVESTIGATING)

    def test_duplicate_assignment_rejected(self):
        with self.assertRaises(ReportingError):
            self.svc.assign_action(self.incident.id, ActionKind.ROOT_CAUSE, "其他组")

    def test_commitment_due_in_past_rejected(self):
        self.clock.set("2026-09-21T00:00:00")
        with self.assertRaises(ReportingError):
            self.svc.make_commitment(
                self.incident.id, ActionKind.ROOT_CAUSE,
                content="查明根因", due_at="2026-09-20T00:00:00",
            )

    def test_retest_blocked_before_root_cause_and_policy_approved(self):
        with self.assertRaises(ReportingError):
            self.svc.submit_evidence(
                self.incident.id, ActionKind.RETEST,
                submitted_by="复测工程师",
                attachment_name="复测报告", redacted_view="复测通过",
            )

    def test_full_workflow_until_close(self):
        svc, iid = self.svc, self.incident.id
        self.clock.set("2026-09-20T12:00:00")
        for kind, who, team_member in [
            (ActionKind.ROOT_CAUSE, "根因报告", "算法工程师"),
            (ActionKind.POLICY, "规则修订文本", "规则专员"),
        ]:
            svc.make_commitment(iid, kind, content=f"完成{kind.value}",
                                due_at="2026-09-22T18:00:00")
            svc.submit_evidence(iid, kind, submitted_by=team_member,
                                attachment_name=f"{kind.value}证据",
                                redacted_view=who)
            svc.review_evidence(iid, kind, reviewer="合规审签人甲", approved=True)

        svc.make_commitment(iid, ActionKind.REMEDY, content="完成补救",
                            due_at="2026-09-22T18:00:00")
        svc.submit_evidence(iid, ActionKind.REMEDY, submitted_by="商家专员",
                            attachment_name="补救证据", redacted_view="已补发")
        svc.review_evidence(iid, ActionKind.REMEDY, reviewer="合规审签人甲", approved=True)

        # 根因与制度修订通过后，复测方可提交
        svc.make_commitment(iid, ActionKind.RETEST, content="完成复测",
                            due_at="2026-09-23T18:00:00")
        svc.submit_evidence(iid, ActionKind.RETEST, submitted_by="复测工程师",
                            attachment_name="复测证据", redacted_view="指标恢复")
        svc.review_evidence(iid, ActionKind.RETEST, reviewer="合规审签人甲", approved=True)

        with self.assertRaises(ReportingError):
            svc.close_incident(iid)  # 还没有监管摘要
        svc.publish_regulator_summary(iid, "全部整改已审签通过，复测合格，无未解决风险。")
        closed = svc.close_incident(iid)
        self.assertEqual(closed.status, IncidentStatus.CLOSED)
        self.assertIsNotNone(closed.closed_at)
        for action in closed.actions.values():
            self.assertEqual(action.status, ActionStatus.CLOSED)

    def test_rejection_reopens_action_and_allows_resubmission(self):
        svc, iid = self.svc, self.incident.id
        svc.make_commitment(iid, ActionKind.ROOT_CAUSE, content="根因",
                            due_at="2026-09-30T18:00:00")
        svc.submit_evidence(iid, ActionKind.ROOT_CAUSE, submitted_by="算法工程师",
                            attachment_name="根因报告", redacted_view="初版")
        svc.review_evidence(iid, ActionKind.ROOT_CAUSE,
                            reviewer="合规审签人甲", approved=False, note="证据不足")
        action = svc.incidents[iid].actions[ActionKind.ROOT_CAUSE.value]
        self.assertEqual(action.status, ActionStatus.REJECTED)
        self.assertIsNone(action.completed_at)
        # 重新整改后再次提交、审签通过
        svc.submit_evidence(iid, ActionKind.ROOT_CAUSE, submitted_by="算法工程师",
                            attachment_name="根因报告v2", redacted_view="补充日志")
        svc.review_evidence(iid, ActionKind.ROOT_CAUSE,
                            reviewer="合规审签人甲", approved=True, note="补证充分")
        self.assertEqual(action.status, ActionStatus.APPROVED)
        self.assertFalse(action.reviews[0].approved)
        self.assertTrue(action.reviews[1].approved)


class EvidenceReviewTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.svc = ReportingService(clock=self.clock)
        self.incident = self.svc.report_incident(**base_report_kwargs())
        freeze(self.svc, self.incident.id)
        self.svc.assign_action(self.incident.id, ActionKind.REMEDY, "商家服务组")
        self.svc.make_commitment(
            self.incident.id, ActionKind.REMEDY, content="补发补贴",
            due_at="2026-09-30T18:00:00",
        )

    def test_self_review_forbidden(self):
        self.svc.submit_evidence(
            self.incident.id, ActionKind.REMEDY,
            submitted_by="商家专员", attachment_name="补发清单",
            redacted_view="已补发",
        )
        with self.assertRaises(ReportingError):
            self.svc.review_evidence(
                self.incident.id, ActionKind.REMEDY,
                reviewer="商家专员", approved=True,
            )

    def test_controlled_trade_secret_requires_ref(self):
        with self.assertRaises(ReportingError):
            self.svc.submit_evidence(
                self.incident.id, ActionKind.REMEDY,
                submitted_by="商家专员", attachment_name="核算表",
                redacted_view="脱敏视图", contains_trade_secret=True,
            )

    def test_unauthorized_reviewer_cannot_open_controlled_attachment(self):
        self.svc.submit_evidence(
            self.incident.id, ActionKind.REMEDY,
            submitted_by="商家专员", attachment_name="商家核算表",
            redacted_view="仅金额区间", contains_trade_secret=True,
            controlled_ref="vault://evidence/x",
            visibility=AttachmentVisibility.CONTROLLED,
            authorized_reviewers=["合规审签人甲"],
        )
        with self.assertRaises(ReportingError):
            self.svc.review_evidence(
                self.incident.id, ActionKind.REMEDY,
                reviewer="未授权审签人", approved=True,
            )

    def test_attachment_view_redacts_for_outsider(self):
        att = self.svc.submit_evidence(
            self.incident.id, ActionKind.REMEDY,
            submitted_by="商家专员", attachment_name="商家核算表",
            redacted_view="脱敏：仅金额区间", contains_trade_secret=True,
            controlled_ref="vault://evidence/x",
            authorized_reviewers=["合规审签人甲"],
        )
        outside = ReportingService.attachment_view(att, viewer="市场监管人员")
        self.assertTrue(outside["redacted"])
        self.assertEqual(outside["view"], "脱敏：仅金额区间")
        inside = ReportingService.attachment_view(att, viewer="合规审签人甲")
        self.assertFalse(inside["redacted"])


class LeadTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock("2026-09-19T23:00:00")
        self.svc = ReportingService(clock=self.clock)

    def test_monitoring_only_raises_leads_never_incidents(self):
        lead = self.svc.raise_lead(
            source="监测平台", signal="曝光骤降", observed_at=self.clock.iso()
        )
        self.assertEqual(lead.status, LeadStatus.NEW)
        self.assertEqual(len(self.svc.incidents), 0)

    def test_lead_requires_content(self):
        with self.assertRaises(ReportingError):
            self.svc.raise_lead(source="", signal="x", observed_at="2026-09-19T23:00:00")

    def test_adopted_lead_creates_incident_with_human_record(self):
        lead = self.svc.raise_lead(
            source="监测平台", signal="曝光骤降", observed_at=self.clock.iso()
        )
        self.clock.advance(hours=2)
        result = self.svc.verify_lead(
            lead.id, reviewer="合规值班经理", adopt=True,
            note="人工核实属实", incident_payload=base_report_kwargs(),
        )
        self.assertEqual(result.status, LeadStatus.VERIFIED)
        self.assertIsNotNone(result.incident_id)
        incident = self.svc.incidents[result.incident_id]
        self.assertEqual(incident.created_from_lead_id, lead.id)

    def test_adoption_requires_payload(self):
        lead = self.svc.raise_lead(
            source="监测平台", signal="曝光骤降", observed_at=self.clock.iso()
        )
        with self.assertRaises(ReportingError):
            self.svc.verify_lead(
                lead.id, reviewer="合规值班经理", adopt=True, note="核实属实",
            )

    def test_dismissed_lead_creates_nothing(self):
        lead = self.svc.raise_lead(
            source="监测平台", signal="短时降价", observed_at=self.clock.iso()
        )
        self.svc.verify_lead(lead.id, reviewer="合规值班经理", adopt=False,
                             note="大促正常波动")
        self.assertEqual(lead.status, LeadStatus.DISMISSED)
        self.assertEqual(len(self.svc.incidents), 0)

    def test_lead_cannot_be_verified_twice(self):
        lead = self.svc.raise_lead(
            source="监测平台", signal="x", observed_at=self.clock.iso()
        )
        self.svc.verify_lead(lead.id, reviewer="合规值班经理", adopt=False, note="噪声")
        with self.assertRaises(ReportingError):
            self.svc.verify_lead(lead.id, reviewer="合规值班经理", adopt=False, note="再核")


class ReminderAndRiskTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock("2026-09-20T10:00:00")
        self.svc = ReportingService(clock=self.clock)
        self.incident = self.svc.report_incident(**base_report_kwargs())
        freeze(self.svc, self.incident.id)
        assign_all(self.svc, self.incident.id)
        self.svc.make_commitment(
            self.incident.id, ActionKind.REMEDY, content="补发",
            due_at="2026-09-22T18:00:00",
        )
        self.svc.make_commitment(
            self.incident.id, ActionKind.POLICY, content="修订制度",
            due_at="2026-10-20T18:00:00",
        )

    def test_overdue_reminder(self):
        self.clock.set("2026-09-23T10:00:00")
        reminders = self.svc.due_reminders()
        overdue = [r for r in reminders if r["state"] == "逾期"]
        self.assertEqual(len(overdue), 1)
        self.assertEqual(overdue[0]["action_kind"], "用户补救")
        self.assertGreaterEqual(overdue[0]["days_overdue"], 0)

    def test_due_soon_reminder(self):
        self.clock.set("2026-09-21T10:00:00")  # 距补救截止不到三天
        reminders = self.svc.due_reminders()
        self.assertEqual([r["action_kind"] for r in reminders], ["用户补救"])
        self.assertEqual(reminders[0]["state"], "临近到期")

    def test_no_reminder_when_far_out(self):
        self.clock.set("2026-09-19T10:00:00")  # 距补救截止超过三天
        self.assertEqual(self.svc.due_reminders(), [])

    def test_approved_action_never_reminds(self):
        self.svc.submit_evidence(
            self.incident.id, ActionKind.REMEDY, submitted_by="商家专员",
            attachment_name="补发证据", redacted_view="已补发",
        )
        self.svc.review_evidence(
            self.incident.id, ActionKind.REMEDY,
            reviewer="合规审签人甲", approved=True,
        )
        self.clock.set("2026-10-19T10:00:00")  # 制度修订临近到期，补救早已通过
        reminders = self.svc.due_reminders()
        self.assertEqual(
            [r["action_kind"] for r in reminders],
            ["制度修订"],
        )

    def test_window_constant_is_three_days(self):
        self.assertEqual(DUE_SOON_WINDOW, timedelta(days=3))


class RecurrenceTest(unittest.TestCase):
    def setUp(self):
        self.svc = ReportingService(clock=Clock())

    def _make(self, rid, title):
        return self.svc.report_incident(**base_report_kwargs(
            report_id=rid, title=title,
            occurred_at="2026-09-20T01:00:00",
            first_detected_at="2026-09-20T01:10:00",
        ))

    def test_recurrence_link_is_bidirectional(self):
        prior = self._make("R-A", "历史流量歧视")
        current = self._make("R-B", "再次流量歧视")
        self.svc.link_recurrence(current.id, prior.id)
        self.assertIn(prior.id, current.recurrence_links)
        self.assertIn(current.id,
                      self.svc.incidents[prior.id].recurrence_links)

    def test_self_link_rejected(self):
        inc = self._make("R-A", "事件")
        with self.assertRaises(ReportingError):
            self.svc.link_recurrence(inc.id, inc.id)

    def test_duplicate_link_idempotent(self):
        prior = self._make("R-A", "历史")
        current = self._make("R-B", "再次")
        self.svc.link_recurrence(current.id, prior.id)
        self.svc.link_recurrence(current.id, prior.id)
        self.assertEqual(current.recurrence_links, [prior.id])


class RegulatorSummaryTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock("2026-09-20T10:00:00")
        self.svc = ReportingService(clock=self.clock)
        self.incident = self.svc.report_incident(**base_report_kwargs())
        freeze(self.svc, self.incident.id)
        assign_all(self.svc, self.incident.id)

    def test_sugarcoated_summary_rejected_while_risks_open(self):
        # 四条工作线均未完成，存在未解决风险
        with self.assertRaises(ReportingError):
            self.svc.publish_regulator_summary(
                self.incident.id,
                "平台已全面妥善解决相关问题，一切正常，无需进一步关注。",
            )

    def test_empty_summary_rejected(self):
        with self.assertRaises(ReportingError):
            self.svc.publish_regulator_summary(self.incident.id, "   ")

    def test_summary_acknowledging_open_risk_accepted(self):
        text = "根因调查整改中，其余措施未完成，未解决风险将持续跟踪。"
        self.svc.publish_regulator_summary(self.incident.id, text)
        self.assertEqual(self.svc.incidents[self.incident.id].regulator_summary, text)


class ComplianceComparisonTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock("2026-09-20T10:00:00")
        self.svc = ReportingService(clock=self.clock)
        inc = self.svc.report_incident(**base_report_kwargs())
        freeze(self.svc, inc.id)
        assign_all(self.svc, inc.id)
        self.iid = inc.id
        for kind in ActionKind:
            self.svc.make_commitment(
                inc.id, kind, content=f"{kind.value}承诺",
                due_at="2026-09-22T18:00:00",
            )

    def _approve(self, kind, submitter):
        self.svc.submit_evidence(
            self.iid, kind, submitted_by=submitter,
            attachment_name=f"{kind.value}证据", redacted_view="完成",
        )
        self.svc.review_evidence(self.iid, kind, reviewer="合规审签人甲", approved=True)

    def test_compliance_rows_compare_promise_and_actual(self):
        # 根因与制度修订先行通过（复测前置）
        self._approve(ActionKind.ROOT_CAUSE, "算法工程师")
        self._approve(ActionKind.POLICY, "规则专员")
        self._approve(ActionKind.REMEDY, "商家专员")
        # 补救在承诺期后才完成
        self.clock.set("2026-09-21T10:00:00")
        self._approve(ActionKind.RETEST, "复测工程师")
        # 手工把补救完成时间推到晚于承诺，演示逾期完成的对照
        remedy = self.svc.incidents[self.iid].actions[ActionKind.REMEDY.value]
        remedy.completed_at = "2026-09-25T10:00:00"

        rows = {r["action_kind"]: r for r in self.svc.commitment_compliance(self.iid)}
        self.assertTrue(rows["根因调查"]["fulfilled"])
        self.assertTrue(rows["根因调查"]["on_time"])
        self.assertTrue(rows["用户补救"]["fulfilled"])
        self.assertFalse(rows["用户补救"]["on_time"])
        self.assertEqual(rows["复测"]["actual_status"], "已通过")

    def test_close_blocked_when_completion_later_than_promise(self):
        self._approve(ActionKind.ROOT_CAUSE, "算法工程师")
        self._approve(ActionKind.POLICY, "规则专员")
        self._approve(ActionKind.REMEDY, "商家专员")
        self.clock.set("2026-09-21T10:00:00")
        self._approve(ActionKind.RETEST, "复测工程师")
        self.svc.incidents[self.iid].actions[ActionKind.REMEDY.value].completed_at = (
            "2026-09-25T10:00:00"
        )
        ok, blockers = self.svc.can_close(self.iid)
        self.assertFalse(ok)
        self.assertTrue(any("晚于承诺期限" in b for b in blockers))

    def test_regulator_view_contains_comparison_and_redacted_evidence(self):
        self.svc.submit_evidence(
            self.iid, ActionKind.REMEDY, submitted_by="商家专员",
            attachment_name="核算表", redacted_view="金额区间（脱敏）",
            contains_trade_secret=True, controlled_ref="vault://e/1",
            authorized_reviewers=["合规审签人甲"],
        )
        view = self.svc.regulator_view(self.iid)
        self.assertIn("commitment_compliance", view)
        self.assertIn("unresolved_risks", view)
        self.assertEqual(view["incident"]["merged_report_count"], 1)
        self.assertEqual(view["evidence"][0]["view"], "金额区间（脱敏）")
        self.assertTrue(view["evidence"][0]["redacted"])


class PersistenceTest(unittest.TestCase):
    def test_fixture_roundtrip_and_contract(self):
        snapshot = json.loads((ROOT / "fixtures" / "incidents.json").read_text("utf-8"))
        schema = json.loads(
            (ROOT / "contracts" / "reporting.schema.json").read_text("utf-8")
        )
        validate_against_schema(snapshot, schema)

        svc = load_service(ROOT / "fixtures" / "incidents.json")
        self.assertEqual(len(svc.incidents), 3)
        self.assertEqual(len(svc.leads), 3)
        main = svc.incidents["INC-0002"]
        self.assertEqual(len(main.merged_report_ids), 3)
        self.assertGreaterEqual(len(main.freeze_revisions), 3)
        self.assertEqual(main.actions["用户补救"].status, ActionStatus.REJECTED)
        self.assertIn("INC-0001", main.recurrence_links)
        # 回放后监管仍能比较承诺与实际
        rows = svc.commitment_compliance("INC-0002")
        self.assertEqual(len(rows), 4)
        # 待核实线索保持待核实，系统从未自动立案
        pending = [l for l in svc.leads.values() if l.status == LeadStatus.NEW]
        self.assertEqual(len(pending), 1)
        self.assertIsNone(pending[0].incident_id)


# ---------------------------------------------------------------------------
# 仅依赖标准库的轻量 JSON Schema 校验（覆盖束约中用到的关键字）
# ---------------------------------------------------------------------------

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


class _SchemaValidator:
    def __init__(self, schema):
        self.schema = schema
        self.defs = schema.get("$defs", {})

    def validate(self, instance, schema, path="$"):
        if "$ref" in schema:
            ref = schema["$ref"].split("/")[-1]
            self.validate(instance, self.defs[ref], path)
            return
        if "oneOf" in schema:
            for branch in schema["oneOf"]:
                try:
                    self.validate(instance, branch, path)
                    break
                except AssertionError:
                    continue
            else:
                raise AssertionError(f"{path}: 不满足任一 oneOf 分支")
            return
        if "const" in schema:
            if instance != schema["const"]:
                raise AssertionError(f"{path}: 期望常量 {schema['const']}，实际 {instance!r}")
            return
        t = schema.get("type")
        types = t if isinstance(t, list) else ([t] if t else [])
        if types and not any(_TYPE_CHECKS[name](instance) for name in types):
            raise AssertionError(f"{path}: 类型不匹配 {types}")
        if "enum" in schema and instance not in schema["enum"]:
            raise AssertionError(f"{path}: {instance!r} 不在枚举 {schema['enum']} 中")
        if t == "object" or "object" in types:
            for key in schema.get("required", []):
                if key not in instance:
                    raise AssertionError(f"{path}: 缺少必填字段 {key}")
            if schema.get("minProperties") and len(instance) < schema["minProperties"]:
                raise AssertionError(f"{path}: 属性数量不足")
            props = schema.get("properties", {})
            if schema.get("additionalProperties") is False:
                extra = set(instance) - set(props)
                if extra:
                    raise AssertionError(f"{path}: 存在未声明字段 {extra}")
            for key, value in instance.items():
                if key in props:
                    self.validate(value, props[key], f"{path}.{key}")
        elif t == "array":
            if "minItems" in schema and len(instance) < schema["minItems"]:
                raise AssertionError(f"{path}: 数组项数不足 {len(instance)}")
            if schema.get("uniqueItems"):
                seen = [json.dumps(x, sort_keys=True, ensure_ascii=False) for x in instance]
                if len(set(seen)) != len(seen):
                    raise AssertionError(f"{path}: 数组元素不唯一")
            item_schema = schema.get("items")
            if item_schema:
                for idx, item in enumerate(instance):
                    self.validate(item, item_schema, f"{path}[{idx}]")
        elif t == "string":
            if "minLength" in schema and len(instance) < schema["minLength"]:
                raise AssertionError(f"{path}: 字符串过短")


def validate_against_schema(instance, schema, path="$"):
    _SchemaValidator(schema).validate(instance, schema, path)


if __name__ == "__main__":
    unittest.main()
