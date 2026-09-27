"""生成“算法回滚”跨团队重大事项的公开样例。

运行：python fixtures/build_case_sample.py

产物：
- fixtures/case_sample.json：服务完整状态（事件 / 上报 / 线索 / 封存 / 任务 /
  证据 / 承诺 / 复发关联 / 留痕），可由 src.reporting.load_case 重新载入；
- fixtures/regulatory_summary.sample.json：监管人员视角的整改报告摘要，
  含承诺兑现对照与未淡化的未解决风险。

样例中的平台、人员、商家与数据均为虚构汇总信息，不含账号、密钥、凭据或
可识别个人；商业秘密材料仅以受控附件形式登记编号与哈希，不收录正文。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.reporting import ReportingService, ReportingError

HERE = Path(__file__).resolve().parent
COMPLIANCE_LEAD = "合规负责人-周岚"


def build_historical_event(svc: ReportingService) -> str:
    """2026 年 3 月已关闭的历史事件：promo-rank 大促流量失真。"""
    svc.ingest_signal(
        signal_id="SGN-H01",
        monitor="大促流量看板（自动监测）",
        rule_code="promo-rank",
        title="大促开门红时段部分商家曝光偏离基线",
        detail="曝光基尼系数超阈值，自动监测仅输出线索",
        observed_at="2026-03-12T07:10",
        teams=["流量分发团队", "商家治理团队"],
        confidence="high",
    )
    svc.create_report(
        report_id="RPT-H01",
        team="流量分发团队",
        title="promo-rank v7 上线后中小商家曝光异常下降",
        observed_at="2026-03-12T07:40",
        detail="3 月 12 日开门红后两个流量层级偏离基线 30% 以上",
        by="流量值班-孟初",
        tags=["promo-rank", "traffic-fairness", "major-sale"],
        signal_ids=["SGN-H01"],
    )
    svc.create_report(
        report_id="RPT-H02",
        team="商家治理团队",
        title="大促商家申诉量环比上升，集中反馈曝光骤降",
        observed_at="2026-03-12T08:30",
        detail="申诉工单标签集中在“搜索曝光”与“活动流量”",
        by="商家值班-邵敏",
        tags=["promo-rank", "merchant-appeal", "major-sale"],
    )

    event_id = svc.file_event(
        event_id="EVT-2026-03",
        title="大促期间排序规则 promo-rank v7 流量分发失真事件",
        level="large",
        gatekeeper_domains=["算法", "流量"],
        first_observed_at="2026-03-12T07:40",
        report_ids=["RPT-H01", "RPT-H02"],
        by=COMPLIANCE_LEAD,
        at="2026-03-13T10:00",
    )
    svc.freeze_snapshot(
        event_id,
        window_start="2026-03-12T00:30",
        window_end="2026-03-12T09:00",
        rule_versions=[
            {"code": "promo-rank", "version": "v7", "note": "大促排序权重版本"},
            {"code": "traffic-shield", "version": "v2", "note": "流量异常兜底策略"},
        ],
        affected_subjects=[
            {"type": "merchant_segment", "segment": "中小商家", "count": 9800},
            {"type": "consumer_orders", "count_estimate": 512000},
        ],
        interim_measures=[
            {"measure": "暂停 promo-rank v7 新权重，回退至 v6 稳定版本", "at": "2026-03-12T09:10"},
            {"measure": "开通大促申诉绿色通道并优先核处", "at": "2026-03-12T10:00"},
        ],
        by=COMPLIANCE_LEAD,
        at="2026-03-13T11:00",
    )

    owners = {
        "root_cause": ("算法平台组", "2026-03-20T18:00"),
        "user_remedy": ("客户服务团队", "2026-03-22T18:00"),
        "policy_revision": ("合规与制度组", "2026-03-26T18:00"),
        "retest": ("质量保障组", "2026-03-28T18:00"),
    }
    titles = {
        "root_cause": "定位 promo-rank v7 权重计算缺陷",
        "user_remedy": "对受影响商家补发流量券并逐单回访",
        "policy_revision": "修订排序规则发布与回滚管理办法",
        "retest": "灰度环境复测流量分发公平性指标",
    }
    task_ids = {}
    for code, (team, due) in owners.items():
        task_ids[code] = svc.assign_task(
            event_id, code=code, title=titles[code], owner_team=team, due=due,
            by=COMPLIANCE_LEAD, at="2026-03-13T14:00",
        )

    svc.add_evidence(
        event_id, evidence_id="EV-H01", title="promo-rank v7 权重缺陷复盘报告",
        kind="root_cause_report", ref="docs://gatekeeper/2026/03/EV-H01",
        sha256="h1-" + "a" * 64, uploaded_by="算法-高衡",
        controlled=False, at="2026-03-19T16:00",
    )
    svc.review_evidence(event_id, "EV-H01", decision="approved", by="审签-林绍",
                        note="根因链条与数据复现完整", at="2026-03-19T17:00")
    svc.review_evidence(event_id, "EV-H01", decision="approved", by="审签-韩静",
                        note="同意结论与改进方向", at="2026-03-20T09:00")
    svc.submit_task(event_id, task_ids["root_cause"], evidence_ids=["EV-H01"],
                    note="权重归一化缺陷导致补贴敏感商家被系统性降权",
                    by="算法-高衡", at="2026-03-20T10:00")
    svc.complete_task(event_id, task_ids["root_cause"], by=COMPLIANCE_LEAD, at="2026-03-20T15:00")

    svc.add_evidence(
        event_id, evidence_id="EV-H02", title="受影响商家流量补偿执行清册",
        kind="remedy_ledger", ref="controlled://vault/2026/03/EV-H02",
        sha256="h2-" + "b" * 64, uploaded_by="客服-叶宁",
        controlled=True, at="2026-03-22T15:00",
    )
    svc.review_evidence(event_id, "EV-H02", decision="approved", by="审签-林绍",
                        note="受控清册与补偿口径一致", at="2026-03-22T16:00")
    svc.review_evidence(event_id, "EV-H02", decision="approved", by="审签-韩静",
                        note="抽核 30 户无误", at="2026-03-22T17:00")
    svc.submit_task(event_id, task_ids["user_remedy"], evidence_ids=["EV-H02"],
                    note="9800 户商家流量券补发完成，回访满意 96%",
                    by="客服-叶宁", at="2026-03-22T17:30")
    svc.complete_task(event_id, task_ids["user_remedy"], by=COMPLIANCE_LEAD, at="2026-03-23T09:00")

    svc.submit_task(event_id, task_ids["policy_revision"], evidence_ids=["EV-H01"],
                    note="管理办法增补发布前公平性评估条款",
                    by="制度-方澄", at="2026-03-25T10:00")
    svc.complete_task(event_id, task_ids["policy_revision"], by=COMPLIANCE_LEAD, at="2026-03-25T15:00")

    svc.submit_task(event_id, task_ids["retest"], evidence_ids=["EV-H01"],
                    note="灰度 72 小时曝光基尼系数回到基线区间",
                    by="测试-闻迪", at="2026-03-27T16:00")
    svc.complete_task(event_id, task_ids["retest"], by=COMPLIANCE_LEAD,
                      at="2026-03-27T17:00", retest_passed=True)

    svc.add_commitment(
        event_id, commitment_id="CMT-H01",
        statement="2026 年二季度前将排序规则变更纳入双人复核与灰度观测",
        owner_team="算法平台组", due="2026-06-30T18:00",
        by=COMPLIANCE_LEAD, at="2026-03-23T10:00",
    )
    svc.verify_commitment(
        event_id, "CMT-H01", decision="verified", evidence_ids=["EV-H01"],
        note="复核流程与灰度看板已上线运行", by=COMPLIANCE_LEAD, at="2026-06-25T11:00",
    )
    svc.request_closure(event_id, by=COMPLIANCE_LEAD, at="2026-06-26T10:00")
    return event_id


def build_current_event(svc: ReportingService) -> str:
    """2026 年 9 月：一次算法回滚同时牵动商家申诉、价格补贴与流量歧视。"""
    # 自动监测只能提出线索，并可附上疑似历史事件供人工参考
    svc.ingest_signal(
        signal_id="SGN-0001",
        monitor="流量公平监测（自动监测）",
        rule_code="promo-rank",
        title="凌晨回滚后中小商家曝光骤降，疑似回滚到旧权重",
        detail="02:00 后曝光基尼系数 18 分钟内突破红线；线索不含定级，不得自动立案",
        observed_at="2026-09-10T08:20",
        teams=["流量分发团队"],
        confidence="high",
        suspected_event_id="EVT-2026-03",
    )
    svc.ingest_signal(
        signal_id="SGN-0002",
        monitor="商家申诉舆情监测（自动监测）",
        rule_code="promo-rank",
        title="商家申诉工单激增并出现“补贴没到账+流量掉了”并诉",
        detail="08:00 至 09:00 关联工单环比上升 240%",
        observed_at="2026-09-10T09:10",
        teams=["商家治理团队"],
        confidence="medium",
    )
    svc.ingest_signal(
        signal_id="SGN-0003",
        monitor="价格爬虫巡检（自动监测）",
        rule_code="price-crawler",
        title="部分商品页面价格抖动（疑似误报）",
        detail="跨域时间戳不一致导致的巡检噪声",
        observed_at="2026-09-10T11:00",
        teams=["价格补贴团队"],
        confidence="low",
    )
    # 误报线索由人工核实排除，自动监测无权自行处置
    svc.dismiss_signal("SGN-0003", by="价格值班-程野",
                       reason="爬虫时钟漂移，与本次回滚无关，不予转报")

    # 三个团队分别人工上报；监测线索必须经责任人确认才能转报
    svc.create_report(
        report_id="RPT-0001",
        team="商家治理团队",
        title="商家申诉激增：回退后补贴与曝光问题并发",
        observed_at="2026-09-10T09:40",
        detail="申诉同时指向活动补贴未核销与商品曝光下降，需与其他团队并案核对",
        by="商家值班-邵敏",
        tags=["algo-rollback", "promo-rank", "merchant-appeal", "subsidy"],
        signal_ids=["SGN-0002"],
        at="2026-09-10T11:20",
    )
    svc.create_report(
        report_id="RPT-0002",
        team="价格补贴团队",
        title="回退版本 subsidy-settle 计价口径与当前补贴活动不兼容",
        observed_at="2026-09-10T10:15",
        detail="满减叠加券按旧口径结算，部分订单少补或多扣，批次已冻结",
        by="价格值班-程野",
        tags=["algo-rollback", "promo-rank", "subsidy", "settlement"],
        at="2026-09-10T12:05",
    )
    svc.create_report(
        report_id="RPT-0003",
        team="流量分发团队",
        title="promo-rank 回滚至 v8 后中小商家流量分布异常",
        observed_at="2026-09-10T08:20",
        detail="回滚窗口内两个商家层级曝光占比偏离基线，触发公平性红线",
        by="流量值班-孟初",
        tags=["algo-rollback", "promo-rank", "traffic-fairness"],
        signal_ids=["SGN-0001"],
        at="2026-09-10T10:30",
    )

    # 系统仅给出归并候选；是否同一事件由合规负责人确认
    candidates = svc.find_candidate_reports(window_days=3)
    assert len(candidates) == 1, "样例预期产生一组跨团队归并候选"
    assert candidates[0]["teams"] == ["价格补贴团队", "商家治理团队", "流量分发团队"]

    event_id = svc.file_event(
        event_id="EVT-2026-09",
        title="排序规则回滚引发的商家申诉、价格补贴与流量歧视并发事件",
        level="major",
        gatekeeper_domains=["数据", "算法", "流量", "规则"],
        first_observed_at="2026-09-10T08:20",
        report_ids=["RPT-0001", "RPT-0002", "RPT-0003"],
        by=COMPLIANCE_LEAD,
        at="2026-09-10T15:40",  # 重大级 1 日内报送，按时
    )

    # 报送后第四个团队才排查到关联工单，追加归并
    svc.create_report(
        report_id="RPT-0004",
        team="客户服务团队",
        title="热线侧同窗口补贴与流量类来电集中出现",
        observed_at="2026-09-10T13:00",
        detail="来电工单时间窗与标签与已立事件重合",
        by="客服值班-叶宁",
        tags=["algo-rollback", "promo-rank", "merchant-appeal"],
        at="2026-09-11T09:00",
    )
    svc.merge_reports(event_id, ["RPT-0004"], by=COMPLIANCE_LEAD, at="2026-09-11T09:40")

    # 事件发生后冻结影响时间窗、规则版本、受影响主体与临时措施
    svc.freeze_snapshot(
        event_id,
        window_start="2026-09-10T02:00",
        window_end="2026-09-10T09:30",
        rule_versions=[
            {"code": "promo-rank", "version": "v8", "note": "回滚目标版本（旧权重）"},
            {"code": "promo-rank", "version": "v9", "note": "回滚前版本"},
            {"code": "subsidy-settle", "version": "v3", "note": "与 v8 配套的补贴计价版本"},
        ],
        affected_subjects=[
            {"type": "merchant_segment", "segment": "中小商家", "count": 12000},
            {"type": "consumer_orders", "count_estimate": 865000},
            {"type": "appeal_tickets", "count_estimate": 4300},
        ],
        interim_measures=[
            {"measure": "09:10 停止 v8 兜底流量分发，切回 v9 并冻结配置变更", "at": "2026-09-10T09:10"},
            {"measure": "冻结 09-10 凌晨补贴结算批次，暂停自动打款", "at": "2026-09-10T09:20"},
            {"measure": "开通商家申诉与消费者退款双通道，优先 24 小时核处", "at": "2026-09-10T10:00"},
        ],
        by=COMPLIANCE_LEAD,
        at="2026-09-10T18:00",
    )
    # 封存后修正必须留痕：原统计值保留在 before 中
    svc.correct_snapshot(
        event_id, field="affected_subjects",
        before=[
            {"type": "merchant_segment", "segment": "中小商家", "count": 12000},
            {"type": "consumer_orders", "count_estimate": 865000},
            {"type": "appeal_tickets", "count_estimate": 4300},
        ],
        after=[
            {"type": "merchant_segment", "segment": "中小商家", "count": 12430},
            {"type": "consumer_orders", "count_estimate": 865000},
            {"type": "appeal_tickets", "count_estimate": 4318},
        ],
        reason="结算批次解冻后补录 430 户边缘商家与 18 件延迟工单",
        by="数据-童岑",
        at="2026-09-11T14:00",
    )

    # 分派四类整改任务
    task_rc = svc.assign_task(
        event_id, code="root_cause", title="定位回滚流程为何绕过权重兼容校验",
        owner_team="算法平台组", due="2026-09-14T18:00",
        by=COMPLIANCE_LEAD, at="2026-09-10T19:00")
    task_remedy = svc.assign_task(
        event_id, code="user_remedy", title="补贴差价补发、申诉逐户核处与流量补偿",
        owner_team="客户服务团队", due="2026-09-20T18:00",
        by=COMPLIANCE_LEAD, at="2026-09-10T19:00")
    task_policy = svc.assign_task(
        event_id, code="policy_revision", title="修订算法回滚管理办法：双人审批+兼容校验+灰度门禁",
        owner_team="合规与制度组", due="2026-09-25T18:00",
        by=COMPLIANCE_LEAD, at="2026-09-10T19:00")
    svc.assign_task(
        event_id, code="retest", title="对修复版本与补贴口径开展全链路复测",
        owner_team="质量保障组", due="2026-09-27T18:00",
        by=COMPLIANCE_LEAD, at="2026-09-10T19:00")

    # 根因调查：证据双人双签后随结果提交
    svc.add_evidence(
        event_id, evidence_id="EV-001", title="回滚事件根因技术分析报告",
        kind="root_cause_report", ref="docs://gatekeeper/2026/09/EV-001",
        sha256="cur1-" + "c" * 60, uploaded_by="算法-高衡",
        controlled=False, at="2026-09-13T15:00")
    svc.review_evidence(event_id, "EV-001", decision="approved", by="审签-林绍",
                        note="时间线与回滚操作记录一致", at="2026-09-13T16:00")
    svc.review_evidence(event_id, "EV-001", decision="approved", by="审签-韩静",
                        note="兼容校验被旁路的根因成立", at="2026-09-14T09:00")
    svc.submit_task(event_id, task_rc, evidence_ids=["EV-001"],
                    note="凌晨应急回滚走了人工快速通道，未执行权重兼容校验，"
                         "promo-rank v8 与 subsidy-settle v3 计价口径不兼容",
                    by="算法-高衡", at="2026-09-14T10:00")
    svc.complete_task(event_id, task_rc, by=COMPLIANCE_LEAD, at="2026-09-14T15:00")

    # 用户补救：商业秘密（商家清册）只登记受控附件
    svc.add_evidence(
        event_id, evidence_id="EV-002", title="受影响商家补贴与流量补偿清册（受控）",
        kind="remedy_ledger", ref="controlled://vault/2026/09/EV-002",
        sha256="cur2-" + "d" * 60, uploaded_by="客服-叶宁",
        controlled=True, at="2026-09-15T17:00")
    svc.add_evidence(
        event_id, evidence_id="EV-004", title="补贴差价补发结算回单汇总",
        kind="settlement_receipt", ref="docs://gatekeeper/2026/09/EV-004",
        sha256="cur4-" + "e" * 60, uploaded_by="结算-韦姗",
        controlled=False, at="2026-09-16T11:00")
    svc.review_evidence(event_id, "EV-002", decision="approved", by="审签-韩静",
                        note="受控通道内逐户核对，汇总数与封存口径一致", at="2026-09-15T18:00")
    svc.review_evidence(event_id, "EV-002", decision="approved", by="审签-林绍",
                        note="抽核 50 户补偿到账无误", at="2026-09-16T09:30")
    svc.review_evidence(event_id, "EV-004", decision="approved", by="审签-林绍",
                        note="回单金额与清册勾稽一致", at="2026-09-16T13:00")
    svc.review_evidence(event_id, "EV-004", decision="approved", by="审签-韩静",
                        note="同意作为补救完成佐证", at="2026-09-16T14:00")
    svc.submit_task(event_id, task_remedy, evidence_ids=["EV-002", "EV-004"],
                    note="12430 户商家补贴差价与流量补偿全部发放，4318 件申诉办结 99.2%",
                    by="客服-叶宁", at="2026-09-16T15:00")
    svc.complete_task(event_id, task_remedy, by=COMPLIANCE_LEAD, at="2026-09-16T17:00")

    # 制度修订进行中：修订稿仅完成第一签，证据审签未完结
    svc.add_evidence(
        event_id, evidence_id="EV-003", title="算法回滚管理办法（修订稿）",
        kind="policy_draft", ref="docs://gatekeeper/2026/09/EV-003",
        sha256="cur3-" + "f" * 60, uploaded_by="制度-方澄",
        controlled=False, at="2026-09-22T10:00")
    svc.review_evidence(event_id, "EV-003", decision="approved", by="审签-林绍",
                        note="双人审批与灰度门禁条款补齐，待法务第二签", at="2026-09-22T14:00")

    # 整改承诺：一条已按期验证，一条已逾期尚未验证（摘要必须如实列示）
    svc.add_commitment(
        event_id, commitment_id="CMT-001",
        statement="2026-09-17 前完成全部受影响订单的补贴差价补发",
        owner_team="客户服务团队", due="2026-09-17T18:00",
        by=COMPLIANCE_LEAD, at="2026-09-11T10:00")
    svc.verify_commitment(
        event_id, "CMT-001", decision="verified", evidence_ids=["EV-002", "EV-004"],
        note="结算回单与受控清册勾稽一致，9 月 16 日补发完成",
        by=COMPLIANCE_LEAD, at="2026-09-16T18:00")

    svc.add_commitment(
        event_id, commitment_id="CMT-002",
        statement="2026-09-22 前发布回滚管理办法，强制双人审批、兼容校验与灰度门禁",
        owner_team="合规与制度组", due="2026-09-22T18:00",
        by=COMPLIANCE_LEAD, at="2026-09-11T10:00")
    # CMT-002 截至样例时点仍 open：制度文本待第二签，承诺已逾期

    # 复发关联：人工确认与 3 月已关闭事件为同一规则模块再度失效
    svc.link_recurrence(
        event_id, "EVT-2026-03",
        reason="promo-rank 模块在发布管控失效后半年内再度引发流量分发失真，"
               "3 月事件承诺的双人复核未覆盖应急回滚快速通道",
        by=COMPLIANCE_LEAD, at="2026-09-11T16:00")

    return event_id


def main() -> None:
    svc = ReportingService()
    build_historical_event(svc)
    event_id = build_current_event(svc)

    # 关闭闸门在风险未清零时必须拒绝（演示，不改变样例状态）
    try:
        svc.request_closure(event_id, by=COMPLIANCE_LEAD, at="2026-09-23T09:00")
    except ReportingError as exc:
        assert "关闭闸门未通过" in str(exc)
    else:  # pragma: no cover - 样例自检
        raise AssertionError("存在未解决风险时不应允许关闭事件")

    svc.save_case(HERE / "case_sample.json", as_of="2026-09-23")

    summary = svc.regulatory_summary(event_id, on="2026-09-23T09:00", within_days=5)
    (HERE / "regulatory_summary.sample.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    rows = svc.compare_commitments(event_id, on="2026-09-23T09:00")
    verdicts = {row["commitment_id"]: row["对账结论"] for row in rows}
    assert verdicts == {"CMT-001": "已兑现", "CMT-002": "逾期未兑现"}, verdicts

    print("样例已生成：")
    print("- fixtures/case_sample.json")
    print("- fixtures/regulatory_summary.sample.json")
    print("摘要结论：", summary["摘要结论"])


if __name__ == "__main__":
    main()
