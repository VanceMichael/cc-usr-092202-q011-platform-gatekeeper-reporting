"""通过报告服务 API 构建公开活动样例。

运行方式（自仓库根目录）：

    python fixtures/build_incident_fixture.py

样例演示一次算法回滚事件如何被三团队归并上报、冻结与修订、分派四条
整改线、审签证据（含驳回与受控商业秘密附件）、监测线索只提线索、复发
关联到历史事件，以及监管侧对照承诺与实际完成。全部为虚构数据。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.reporting import (  # noqa: E402
    ActionKind,
    AttachmentVisibility,
    IncidentLevel,
    ReportingService,
)


class MutableClock:
    def __init__(self, value: str) -> None:
        self.value = datetime.fromisoformat(value)

    def set(self, value: str) -> None:
        self.value = datetime.fromisoformat(value)

    def __call__(self) -> datetime:
        return self.value


def build() -> ReportingService:
    clock = MutableClock("2026-03-10T08:00:00")
    svc = ReportingService(clock=clock)

    # ------------------------------------------------------------------
    # 历史事件 INC-0001：上半年同类流量歧视，已整改关闭（用于复发关联）
    # ------------------------------------------------------------------
    prior = svc.report_incident(
        report_id="R-2026-0310-01",
        title="流量分配实验导致中小商家曝光歧视",
        level=IncidentLevel.MAJOR,
        domains=["算法", "流量"],
        team="流量与推荐团队",
        reporter="流量值班工程师（历史）",
        occurred_at="2026-03-10T07:20:00",
        first_detected_at="2026-03-10T07:40:00",
        rule_versions=["ranking-model@v291"],
        keywords=["流量", "算法", "歧视"],
        summary="实验组流量分配对中小商家形成系统性曝光歧视",
    )
    clock.set("2026-03-10T08:30:00")
    svc.freeze_incident(
        prior.id,
        affected_window={"start": "2026-03-10T06:00:00", "end": "2026-03-10T07:55:00"},
        rule_versions=["ranking-model@v291", "traffic-policy@v108"],
        affected_subjects={"merchants": 620, "merchant_type": "中小商家", "regions": ["华东", "西南"]},
        interim_measures=["停止实验分组", "恢复v290排序策略", "开放商家申诉专线"],
        by="合规值班经理（历史）",
    )
    svc.assign_action(prior.id, ActionKind.ROOT_CAUSE, "算法治理组")
    svc.assign_action(prior.id, ActionKind.REMEDY, "商家服务组")
    svc.assign_action(prior.id, ActionKind.POLICY, "平台规则组")
    svc.assign_action(prior.id, ActionKind.RETEST, "质量与风控组")
    svc.make_commitment(prior.id, ActionKind.ROOT_CAUSE, content="查明实验分流缺陷", due_at="2026-03-11T18:00:00")
    svc.make_commitment(prior.id, ActionKind.REMEDY, content="补偿受影响商家流量券", due_at="2026-03-12T18:00:00")
    svc.make_commitment(prior.id, ActionKind.POLICY, content="修订实验上线灰度规则", due_at="2026-03-13T18:00:00")
    svc.make_commitment(prior.id, ActionKind.RETEST, content="对流量分配公平性复测", due_at="2026-03-14T18:00:00")

    clock.set("2026-03-11T15:00:00")
    svc.submit_evidence(
        prior.id, ActionKind.ROOT_CAUSE,
        submitted_by="算法工程师（历史）",
        attachment_name="实验分流根因报告（脱敏）",
        redacted_view="根因为实验哈希桶在特定商家规模上分布失衡",
    )
    svc.review_evidence(prior.id, ActionKind.ROOT_CAUSE, reviewer="合规审签人甲", approved=True, note="根因清晰")

    clock.set("2026-03-12T16:00:00")
    svc.submit_evidence(
        prior.id, ActionKind.REMEDY,
        submitted_by="商家服务专员（历史）",
        attachment_name="流量券补发清单（汇总）",
        redacted_view="已向620家商家补发等值流量券",
    )
    svc.review_evidence(prior.id, ActionKind.REMEDY, reviewer="合规审签人甲", approved=True)

    clock.set("2026-03-13T10:00:00")
    svc.submit_evidence(
        prior.id, ActionKind.POLICY,
        submitted_by="规则专员（历史）",
        attachment_name="灰度发布规则修订文本",
        redacted_view="新增实验组商家覆盖比例上限与公平性观测卡口",
    )
    svc.review_evidence(prior.id, ActionKind.POLICY, reviewer="合规审签人甲", approved=True)

    clock.set("2026-03-14T11:00:00")
    svc.submit_evidence(
        prior.id, ActionKind.RETEST,
        submitted_by="复测工程师（历史）",
        attachment_name="流量公平性复测报告",
        redacted_view="连续24小时分组曝光差异回落至阈值内",
    )
    svc.review_evidence(prior.id, ActionKind.RETEST, reviewer="合规审签人甲", approved=True)
    svc.publish_regulator_summary(prior.id, "历史事件：四条整改线均已审签通过，复测无异常，事件关闭。")
    svc.close_incident(prior.id)

    # ------------------------------------------------------------------
    # 监测线索：自动监测只能提出线索，人工采纳后才据以建立事件
    # ------------------------------------------------------------------
    clock.set("2026-09-19T23:40:00")
    lead = svc.raise_lead(
        source="算法运行监测平台",
        signal="实验组商家曝光骤降，申诉队列积压，疑似与排序模型回滚有关",
        observed_at="2026-09-19T23:35:00",
    )
    # 另一条被人工否决的线索（监测噪声）
    noise = svc.raise_lead(
        source="价格异常波动监测",
        signal="部分类目成交价短时下滑",
        observed_at="2026-09-15T10:00:00",
    )
    clock.set("2026-09-16T11:00:00")
    svc.verify_lead(
        noise.id,
        reviewer="合规值班经理",
        adopt=False,
        note="经核实为平台大促正常降价，无违法风险，不予立案",
    )

    # ------------------------------------------------------------------
    # 主事件 INC-0002：人工采纳线索后，由算法团队首报
    # ------------------------------------------------------------------
    clock.set("2026-09-20T01:40:00")
    svc.verify_lead(
        lead.id,
        reviewer="合规值班经理",
        adopt=True,
        note="监测信号与值班告警一致，疑似模型回滚引发多链路影响，采纳立案",
        incident_payload=dict(
            report_id="R-2026-0920-01",
            title="排序模型回滚引发商家申诉失败、补贴错发与流量歧视",
            level=IncidentLevel.MAJOR,
            domains=["数据", "算法", "流量", "规则"],
            team="算法团队",
            reporter="算法值班工程师",
            occurred_at="2026-09-20T01:05:00",
            first_detected_at="2026-09-20T01:12:00",
            rule_versions=["ranking-model@v318", "subsidy-rule@v204"],
            keywords=["算法", "回滚", "流量"],
            summary="v318排序模型回滚后申诉链路与补贴规则同时异常",
        ),
    )
    incident_id = lead.incident_id

    # 事件发生后冻结影响时间窗、规则版本、受影响主体与临时措施
    clock.set("2026-09-20T02:30:00")
    svc.freeze_incident(
        incident_id,
        affected_window={"start": "2026-09-20T00:50:00", "end": "2026-09-20T02:10:00"},
        rule_versions=["ranking-model@v318", "appeal-flow@v57", "subsidy-rule@v204"],
        affected_subjects={
            "merchants": 1200,
            "consumers": 86000,
            "merchant_types": ["品牌商家", "中小商家"],
            "channels": ["商家申诉", "价格补贴", "搜索流量"],
        },
        interim_measures=[
            "排序回滚至 ranking-model@v317",
            "暂停补贴自动发放，改人工复核",
            "积压申诉转人工通道限时处理",
        ],
        by="合规值班经理",
    )

    # 商家治理团队第二份上报：落在同一时间窗、命中相同规则版本 → 归并
    clock.set("2026-09-20T03:10:00")
    svc.report_incident(
        report_id="R-2026-0920-02",
        title="商家申诉工单超时且补贴发放金额错误",
        level=IncidentLevel.MAJOR,
        domains=["数据", "规则"],
        team="商家治理团队",
        reporter="商家治理值班",
        occurred_at="2026-09-20T01:30:00",
        rule_versions=["appeal-flow@v57", "subsidy-rule@v204"],
        keywords=["申诉", "补贴", "价格"],
        summary="申诉状态机卡单，补贴按旧价规则错发",
    )

    # 流量团队第三份上报：引入新的规则版本，归并并形成冻结修订
    clock.set("2026-09-20T03:40:00")
    svc.report_incident(
        report_id="R-2026-0920-03",
        title="回滚后中小商家搜索曝光量异常下降",
        level=IncidentLevel.MAJOR,
        domains=["算法", "流量"],
        team="流量与推荐团队",
        reporter="流量值班工程师",
        occurred_at="2026-09-20T01:40:00",
        rule_versions=["ranking-model@v318", "traffic-policy@v112"],
        keywords=["流量", "歧视", "算法"],
        summary="v318回滚后流量分发对中小商家不友好",
    )

    # 冻结后的事实更正必须留痕
    clock.set("2026-09-21T10:00:00")
    svc.revise_frozen(
        incident_id,
        "interim_measures",
        [
            "排序回滚至 ranking-model@v317",
            "暂停补贴自动发放，改人工复核",
            "积压申诉转人工通道限时处理",
            "启用补贴垫付通道，对受影响商家先行赔付",
        ],
        reason="垫付通道上线，临时措施增加先行赔付",
        by="商家服务组负责人",
    )
    clock.set("2026-09-22T15:00:00")
    svc.revise_frozen(
        incident_id,
        "affected_subjects",
        {
            "merchants": 1340,
            "consumers": 91200,
            "merchant_types": ["品牌商家", "中小商家"],
            "channels": ["商家申诉", "价格补贴", "搜索流量"],
        },
        reason="全量日志核查扩样，主体数量上修",
        by="数据核查组",
    )

    # 四条整改线分派
    svc.assign_action(incident_id, ActionKind.ROOT_CAUSE, "算法治理组")
    svc.assign_action(incident_id, ActionKind.REMEDY, "商家服务组")
    svc.assign_action(incident_id, ActionKind.POLICY, "平台规则组")
    svc.assign_action(incident_id, ActionKind.RETEST, "质量与风控组")

    svc.make_commitment(
        incident_id, ActionKind.ROOT_CAUSE,
        content="定位回滚导致三链路异常的共同根因并出具报告",
        due_at="2026-09-24T18:00:00",
    )
    svc.make_commitment(
        incident_id, ActionKind.REMEDY,
        content="按冻结主体清单完成申诉补处理、补贴补差与流量补偿",
        due_at="2026-09-25T18:00:00",
    )
    svc.make_commitment(
        incident_id, ActionKind.POLICY,
        content="修订模型回滚预案与跨链路影响评审规则",
        due_at="2026-09-29T18:00:00",
    )
    svc.make_commitment(
        incident_id, ActionKind.RETEST,
        content="对申诉、补贴、流量三类影响统一复测并出具报告",
        due_at="2026-10-02T18:00:00",
    )

    # 根因调查：按期完成并审签通过
    clock.set("2026-09-23T17:00:00")
    svc.submit_evidence(
        incident_id, ActionKind.ROOT_CAUSE,
        submitted_by="算法治理组工程师",
        attachment_name="回滚共同根因分析报告（脱敏）",
        redacted_view="共同根因：回滚未同步申诉状态机与补贴、流量策略的版本兼容矩阵",
    )
    svc.review_evidence(
        incident_id, ActionKind.ROOT_CAUSE,
        reviewer="合规审签人甲", approved=True, note="根因链路完整，跨团队影响解释一致",
    )

    # 用户补救：证据含商业秘密，进入受控附件；审签被驳回 → 行动项重开、承诺逾期
    clock.set("2026-09-26T16:00:00")
    svc.submit_evidence(
        incident_id, ActionKind.REMEDY,
        submitted_by="商家服务组专员",
        attachment_name="商家补发与补贴补差核算表",
        redacted_view="已脱敏：商家名称、结算单价与账户信息已遮蔽，仅保留批次与金额区间",
        contains_trade_secret=True,
        controlled_ref="vault://evidence/INC-0002/remedy-payroll",
        visibility=AttachmentVisibility.CONTROLLED,
        authorized_reviewers=["合规审签人甲", "监管联络人乙"],
    )
    svc.review_evidence(
        incident_id, ActionKind.REMEDY,
        reviewer="合规审签人甲", approved=False,
        note="补发金额未覆盖冻结清单中全部1340家商家，需按修订后主体清单补差后重新提交",
    )

    # 制度修订仍在进行（承诺临近到期）；复测因前置未通过而不能提交证据

    # 与历史同类事件建立复发关联
    svc.link_recurrence(incident_id, prior.id)

    # 监管摘要：未解决风险必须明示，不得淡化
    svc.publish_regulator_summary(
        incident_id,
        "排序模型回滚事件根因已查明并通过审签；商家补发证据审签被驳回且承诺已逾期，"
        "制度修订仍在整改中（临近到期），复测尚未开始，未解决风险持续存在并将跟踪至关闭。",
    )

    # ------------------------------------------------------------------
    # 另一起较大事件 INC-0003：证据待审，展示另一分级与状态
    # ------------------------------------------------------------------
    clock.set("2026-09-25T10:00:00")
    third = svc.report_incident(
        report_id="R-2026-0925-01",
        title="商家数据接口字段权限配置错误",
        level=IncidentLevel.SERIOUS,
        domains=["数据", "规则"],
        team="数据安全团队",
        reporter="数据安全值班",
        occurred_at="2026-09-25T09:00:00",
        rule_versions=["merchant-api@v73"],
        keywords=["数据", "接口", "权限"],
        summary="接口越权返回非授权经营字段",
    )
    clock.set("2026-09-25T11:00:00")
    svc.freeze_incident(
        third.id,
        affected_window={"start": "2026-09-24T20:00:00", "end": "2026-09-25T09:30:00"},
        rule_versions=["merchant-api@v73"],
        affected_subjects={"api_callers": 46, "leaked_fields": ["经营报表-毛利字段"]},
        interim_measures=["下线越权字段", "回滚接口配置至v72", "通知调用方清除缓存"],
        by="数据安全负责人",
    )
    svc.assign_action(third.id, ActionKind.ROOT_CAUSE, "数据安全组")
    svc.assign_action(third.id, ActionKind.REMEDY, "商家服务组")
    svc.assign_action(third.id, ActionKind.POLICY, "平台规则组")
    svc.assign_action(third.id, ActionKind.RETEST, "质量与风控组")
    svc.make_commitment(third.id, ActionKind.ROOT_CAUSE, content="查明权限配置错误来源", due_at="2026-09-26T18:00:00")
    svc.make_commitment(third.id, ActionKind.REMEDY, content="排查调用方留存并通知受影响商家", due_at="2026-09-28T18:00:00")
    svc.make_commitment(third.id, ActionKind.POLICY, content="修订接口字段分级与发布校验规则", due_at="2026-10-02T18:00:00")
    svc.make_commitment(third.id, ActionKind.RETEST, content="对全量字段权限矩阵复测", due_at="2026-10-06T18:00:00")

    clock.set("2026-09-26T14:00:00")
    svc.submit_evidence(
        third.id, ActionKind.ROOT_CAUSE,
        submitted_by="数据安全工程师",
        attachment_name="权限配置根因说明",
        redacted_view="根因为字段分级表漏配毛利字段的授权角色",
    )
    svc.review_evidence(third.id, ActionKind.ROOT_CAUSE, reviewer="合规审签人甲", approved=True)

    clock.set("2026-09-26T18:30:00")
    svc.submit_evidence(
        third.id, ActionKind.REMEDY,
        submitted_by="商家服务组专员",
        attachment_name="调用方留存排查与商家通知记录",
        redacted_view="46个调用方已排查，商家通知已发出，留存清除证明待补",
    )
    # 用户补救证据处于待审状态

    svc.publish_regulator_summary(
        third.id,
        "数据接口越权事件根因已审签通过；用户补救证据正在审签，制度修订与复测未完成，未解决风险仍在整改中。",
    )

    # ------------------------------------------------------------------
    # 仍待人工核实的监测线索（不得自动立案）
    # ------------------------------------------------------------------
    clock.set("2026-09-27T09:00:00")
    svc.raise_lead(
        source="流量公平性监测",
        signal="某区域中小商家曝光差异扩大，需人工核实是否与回滚残留有关",
        observed_at="2026-09-27T08:20:00",
    )

    return svc


def main() -> None:
    svc = build()
    out = Path(__file__).resolve().parent / "incidents.json"
    svc.save(out)
    print(f"已写出 {len(svc.incidents)} 个事件、{len(svc.leads)} 条线索 → {out}")


if __name__ == "__main__":
    main()
