"""平台守门人重大事项与整改报告服务。

围绕一次重大事项（事件）提供统一记录：

- 分级报送：按等级设定初报时限，跨团队上报自动归并为同一事件；
- 事件冻结：影响时间窗、规则/算法版本、受影响主体、临时措施，
  冻结后修订必须留痕，原始内容不可被覆盖；
- 分派整改：根因调查、用户补救、制度修订、复测四条工作线，
  复测必须等待根因与制度修订完成后方可完成；
- 整改承诺与期限提醒：承诺截止时间，支持逾期/临近提醒；
- 证据审签：提交人与审签人职责分离，驳回后行动项重开；
- 复发关联：事件之间建立关联并互相可见；
- 自动监测只能提出线索，线索经人工核实/采纳后才能形成事件；
- 商业秘密保留在受控附件，对外摘要中以引用号替代；
- 监管摘要不得淡化未解决风险，且监管可对照承诺与实际完成。

本模块只使用标准库，所有数据均为显式登记，不做任何隐式自动立案。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable

# ---------------------------------------------------------------------------
# 基础定义
# ---------------------------------------------------------------------------


class IncidentLevel(str, Enum):
    """事项分级：等级越高，初报时限越短。"""

    MAJOR = "重大"
    SERIOUS = "较大"
    ORDINARY = "一般"


# 各等级初报时限（自首次发现起）
REPORT_DEADLINES: dict[IncidentLevel, timedelta] = {
    IncidentLevel.MAJOR: timedelta(hours=2),
    IncidentLevel.SERIOUS: timedelta(hours=24),
    IncidentLevel.ORDINARY: timedelta(days=3),
}

# 期限提醒窗口
DUE_SOON_WINDOW = timedelta(days=3)


class ActionKind(str, Enum):
    ROOT_CAUSE = "根因调查"
    REMEDY = "用户补救"
    POLICY = "制度修订"
    RETEST = "复测"


class ActionStatus(str, Enum):
    ASSIGNED = "已分派"
    IN_PROGRESS = "进行中"
    DONE = "待审"          # 证据已提交，等待审签
    APPROVED = "已通过"    # 审签通过
    REJECTED = "已驳回"    # 审签驳回，重新整改
    CLOSED = "已关闭"


class IncidentStatus(str, Enum):
    DRAFT = "草拟"
    REPORTED = "已报送"
    INVESTIGATING = "整改中"
    VERIFYING = "复测中"
    CLOSED = "已关闭"


class LeadStatus(str, Enum):
    NEW = "待核实"
    VERIFIED = "已采纳"
    DISMISSED = "未采纳"


class AttachmentVisibility(str, Enum):
    CONTROLLED = "受控"       # 含商业秘密，仅授权审签可见原文
    REGULATOR = "监管可见"
    INTERNAL = "内部"


# 复测完成前必须先完成（审签通过）的工作线
RETEST_PREREQUISITES = (ActionKind.ROOT_CAUSE, ActionKind.POLICY)

# 事件关闭前必须全部审签通过的工作线
CLOSURE_REQUIRED_KINDS = tuple(ActionKind)

FROZEN_FIELDS = ("affected_window", "rule_versions", "affected_subjects", "interim_measures")
FIELD_LABELS = {
    "affected_window": "影响时间窗",
    "rule_versions": "规则/算法版本",
    "affected_subjects": "受影响主体",
    "interim_measures": "临时措施",
}


class ReportingError(ValueError):
    """报告服务业务校验错误。"""


def _parse(ts: str | datetime) -> datetime:
    if isinstance(ts, datetime):
        return ts
    return datetime.fromisoformat(ts)


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return clock() if clock else datetime.now()


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class FreezeRevision:
    """冻结字段的一次修订记录；原始冻结内容永不覆盖。"""

    field: str
    before: Any
    after: Any
    reason: str
    by: str
    at: str

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "field_label": FIELD_LABELS.get(self.field, self.field),
            "before": self.before,
            "after": self.after,
            "reason": self.reason,
            "by": self.by,
            "at": self.at,
        }


@dataclass
class Attachment:
    """证据附件；受控附件保存脱敏视图，原文保留在受控位置。"""

    id: str
    name: str
    contains_trade_secret: bool
    visibility: AttachmentVisibility
    redacted_view: str
    controlled_ref: str
    uploaded_by: str
    uploaded_at: str
    authorized_reviewers: list[str] = field(default_factory=list)


@dataclass
class ReviewRecord:
    """证据审签记录。"""

    reviewer: str
    approved: bool
    at: str
    note: str = ""


@dataclass
class Commitment:
    """整改承诺。"""

    action_kind: ActionKind
    content: str
    due_at: str
    owner_team: str

    def to_dict(self) -> dict:
        return {
            "action_kind": self.action_kind.value,
            "content": self.content,
            "due_at": self.due_at,
            "owner_team": self.owner_team,
        }


@dataclass
class CorrectiveAction:
    kind: ActionKind
    owner_team: str
    status: ActionStatus = ActionStatus.ASSIGNED
    commitment: Commitment | None = None
    completed_at: str | None = None
    evidence: Attachment | None = None
    reviews: list[ReviewRecord] = field(default_factory=list)
    submitted_by: str | None = None
    submitted_at: str | None = None

    @property
    def latest_review(self) -> ReviewRecord | None:
        return self.reviews[-1] if self.reviews else None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "owner_team": self.owner_team,
            "status": self.status.value,
            "commitment": self.commitment.to_dict() if self.commitment else None,
            "completed_at": self.completed_at,
            "submitted_by": self.submitted_by,
            "submitted_at": self.submitted_at,
            "evidence": _attachment_dict(self.evidence),
            "reviews": [vars(r) for r in self.reviews],
        }


@dataclass
class Incident:
    id: str
    title: str
    level: IncidentLevel
    domains: list[str]                      # 数据/算法/流量/规则
    first_detected_at: str
    reporter: str
    report_deadline: str
    reported_at: str
    merged_report_ids: list[str] = field(default_factory=list)
    source_team_reports: list[dict] = field(default_factory=list)
    # 冻结信息（事件发生后锁定）
    frozen: dict[str, Any] = field(default_factory=dict)
    frozen_at: str | None = None
    frozen_by: str | None = None
    freeze_revisions: list[FreezeRevision] = field(default_factory=list)
    actions: dict[str, CorrectiveAction] = field(default_factory=dict)
    status: IncidentStatus = IncidentStatus.DRAFT
    regulator_summary: str | None = None
    recurrence_links: list[str] = field(default_factory=list)
    created_from_lead_id: str | None = None
    closed_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "level": self.level.value,
            "domains": list(self.domains),
            "status": self.status.value,
            "reporter": self.reporter,
            "first_detected_at": self.first_detected_at,
            "report_deadline": self.report_deadline,
            "reported_at": self.reported_at,
            "merged_report_ids": list(self.merged_report_ids),
            "source_team_reports": list(self.source_team_reports),
            "frozen_at": self.frozen_at,
            "frozen_by": self.frozen_by,
            "frozen": dict(self.frozen),
            "freeze_revisions": [r.to_dict() for r in self.freeze_revisions],
            "actions": [a.to_dict() for a in self.actions.values()],
            "regulator_summary": self.regulator_summary,
            "recurrence_links": list(self.recurrence_links),
            "created_from_lead_id": self.created_from_lead_id,
            "closed_at": self.closed_at,
        }


@dataclass
class MonitoringLead:
    """自动监测线索：只能提出，不能直接形成事件。"""

    id: str
    source: str                             # 监测系统名称
    signal: str
    observed_at: str
    status: LeadStatus = LeadStatus.NEW
    verification_note: str | None = None
    verified_by: str | None = None
    verified_at: str | None = None
    incident_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "signal": self.signal,
            "observed_at": self.observed_at,
            "status": self.status.value,
            "verification_note": self.verification_note,
            "verified_by": self.verified_by,
            "verified_at": self.verified_at,
            "incident_id": self.incident_id,
        }


def _attachment_dict(att: Attachment | None) -> dict | None:
    if att is None:
        return None
    return {
        "id": att.id,
        "name": att.name,
        "contains_trade_secret": att.contains_trade_secret,
        "visibility": att.visibility.value,
        "redacted_view": att.redacted_view,
        "controlled_ref": att.controlled_ref,
        "uploaded_by": att.uploaded_by,
        "uploaded_at": att.uploaded_at,
        "authorized_reviewers": list(att.authorized_reviewers),
    }


# ---------------------------------------------------------------------------
# 归并匹配
# ---------------------------------------------------------------------------


def _rule_overlap(a: Iterable[str], b: Iterable[str]) -> bool:
    """规则/算法版本必须标识精确一致；不同版本不得视为同一规则。"""

    left = {v.strip().lower() for v in a if v.strip()}
    right = {v.strip().lower() for v in b if v.strip()}
    return bool(left & right)


# 归并时用于判断“同一类线索”的信号词：必须在已建事件与新上报中同时命中
SIGNAL_WORDS = (
    "申诉", "回滚", "补贴", "流量", "价格", "算法", "数据", "歧视", "权限",
)


def _shared_signals(incident_text: str, report_text: str) -> list[str]:
    return [w for w in SIGNAL_WORDS if w in incident_text and w in report_text]


def _match_score(incident: Incident, report: dict) -> int:
    """计算跨团队上报与已建事件的归并得分：时间窗 + 规则版本 + 共有信号词。"""

    score = 0
    win = incident.frozen.get("affected_window") or {}
    start = win.get("start")
    end = win.get("end")
    when = report.get("occurred_at")
    if start and end and when and start <= when <= end:
        score += 2
    rules = incident.frozen.get("rule_versions") or []
    if rules and _rule_overlap(rules, report.get("rule_versions", [])):
        score += 2
    incident_text = " ".join([incident.title, *incident.domains])
    report_text = " ".join(
        [*report.get("keywords", []), report.get("summary", "")]
    )
    score += min(2, len(_shared_signals(incident_text, report_text)))
    return score


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------


class ReportingService:
    """重大事项与整改报告的统一登记与状态流转服务。"""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self.clock = clock
        self.incidents: dict[str, Incident] = {}
        self.leads: dict[str, MonitoringLead] = {}
        self._attachment_seq = 0

    # ----- 内部工具 -----

    def _now_iso(self) -> str:
        return _now(self.clock).isoformat()

    def _get_incident(self, incident_id: str) -> Incident:
        try:
            return self.incidents[incident_id]
        except KeyError:
            raise ReportingError(f"事件不存在：{incident_id}") from None

    def _get_action(self, incident_id: str, kind: ActionKind) -> CorrectiveAction:
        incident = self._get_incident(incident_id)
        try:
            return incident.actions[kind.value]
        except KeyError:
            raise ReportingError(f"事件 {incident_id} 未分派工作线：{kind.value}") from None

    @staticmethod
    def _require_fields(payload: dict, fields: Iterable[str]) -> None:
        missing = [f for f in fields if not payload.get(f)]
        if missing:
            raise ReportingError(f"缺少必填内容：{'、'.join(missing)}")

    # ----- 分级报送与跨团队归并 -----

    def report_incident(
        self,
        *,
        report_id: str,
        title: str,
        level: IncidentLevel,
        domains: list[str],
        team: str,
        reporter: str,
        occurred_at: str,
        first_detected_at: str | None = None,
        rule_versions: list[str] | None = None,
        keywords: list[str] | None = None,
        summary: str = "",
        merge_threshold: int = 3,
    ) -> Incident:
        """跨团队统一报送。

        若上报内容与已建事件在时间窗、规则版本、线索词上足够重合，
        则归并到既有事件，而不是另立事项。返回（已归并的）事件。
        """

        self._require_fields(
            {"title": title, "team": team, "reporter": reporter, "occurred_at": occurred_at},
            ["title", "team", "reporter", "occurred_at"],
        )
        if report_id in {rid for inc in self.incidents.values() for rid in inc.merged_report_ids}:
            raise ReportingError(f"上报编号重复：{report_id}")

        detected = first_detected_at or occurred_at
        candidate: Incident | None = None
        candidate_score = 0
        report_payload = {
            "occurred_at": occurred_at,
            "rule_versions": rule_versions or [],
            "keywords": keywords or [],
        }
        for incident in self.incidents.values():
            score = _match_score(incident, report_payload)
            if score > candidate_score:
                candidate, candidate_score = incident, score

        if candidate is not None and candidate_score >= merge_threshold:
            candidate.merged_report_ids.append(report_id)
            candidate.source_team_reports.append(
                {
                    "report_id": report_id,
                    "team": team,
                    "reporter": reporter,
                    "occurred_at": occurred_at,
                    "rule_versions": list(rule_versions or []),
                    "keywords": list(keywords or []),
                    "summary": summary,
                    "merged_at": self._now_iso(),
                }
            )
            # 归并上报可补充受影响主体与规则版本；事件冻结后补充必须修订留痕
            if rule_versions and candidate.frozen_at is not None:
                merged = sorted(
                    set(candidate.frozen.get("rule_versions", [])) | set(rule_versions)
                )
                if merged != candidate.frozen.get("rule_versions"):
                    self._revise_freeze(
                        candidate,
                        "rule_versions",
                        merged,
                        f"归并团队 {team} 上报，补充规则/算法版本",
                        reporter,
                    )
            return candidate

        incident = Incident(
            id=f"INC-{len(self.incidents) + 1:04d}",
            title=title,
            level=level,
            domains=list(domains),
            first_detected_at=detected,
            reporter=reporter,
            report_deadline=(
                _parse(detected) + REPORT_DEADLINES[level]
            ).isoformat(),
            reported_at=self._now_iso(),
            merged_report_ids=[report_id],
            source_team_reports=[
                {
                    "report_id": report_id,
                    "team": team,
                    "reporter": reporter,
                    "occurred_at": occurred_at,
                    "rule_versions": list(rule_versions or []),
                    "keywords": list(keywords or []),
                    "summary": summary,
                    "merged_at": self._now_iso(),
                }
            ],
            status=IncidentStatus.REPORTED,
        )
        self.incidents[incident.id] = incident
        return incident

    # ----- 事件冻结 -----

    def freeze_incident(
        self,
        incident_id: str,
        *,
        affected_window: dict,
        rule_versions: list[str],
        affected_subjects: dict,
        interim_measures: list[str],
        by: str,
    ) -> Incident:
        """事件发生后冻结影响时间窗、规则版本、受影响主体与临时措施。"""

        incident = self._get_incident(incident_id)
        self._require_fields(
            {
                "affected_window.start": affected_window.get("start"),
                "affected_window.end": affected_window.get("end"),
                "rule_versions": rule_versions,
                "affected_subjects": affected_subjects,
                "interim_measures": interim_measures,
                "by": by,
            },
            [
                "affected_window.start",
                "affected_window.end",
                "rule_versions",
                "affected_subjects",
                "interim_measures",
                "by",
            ],
        )
        if affected_window["start"] > affected_window["end"]:
            raise ReportingError("影响时间窗起始不得晚于结束")
        if incident.frozen_at is not None:
            raise ReportingError("事件已冻结，补充或更正必须通过修订留痕")
        incident.frozen = {
            "affected_window": dict(affected_window),
            "rule_versions": list(rule_versions),
            "affected_subjects": dict(affected_subjects),
            "interim_measures": list(interim_measures),
        }
        incident.frozen_at = self._now_iso()
        incident.frozen_by = by
        return incident

    def revise_frozen(
        self, incident_id: str, field: str, value: Any, reason: str, by: str
    ) -> FreezeRevision:
        """修订已冻结字段；原值进入修订记录，不允许覆盖。"""

        incident = self._get_incident(incident_id)
        return self._revise_freeze(incident, field, value, reason, by)

    def _revise_freeze(
        self, incident: Incident, field: str, value: Any, reason: str, by: str
    ) -> FreezeRevision:
        if incident.frozen_at is None:
            raise ReportingError("事件尚未冻结，无需修订")
        if field not in FROZEN_FIELDS:
            raise ReportingError(f"不允许修订该字段：{field}")
        if not reason or not by:
            raise ReportingError("冻结修订必须说明原因与操作人")
        before = incident.frozen.get(field)
        if before == value:
            raise ReportingError("修订内容与现值一致，无实质变更")
        revision = FreezeRevision(
            field=field,
            before=before,
            after=value,
            reason=reason,
            by=by,
            at=self._now_iso(),
        )
        incident.frozen[field] = value
        incident.freeze_revisions.append(revision)
        return revision

    # ----- 分派整改 -----

    def assign_action(
        self, incident_id: str, kind: ActionKind, owner_team: str
    ) -> CorrectiveAction:
        incident = self._get_incident(incident_id)
        if kind.value in incident.actions:
            raise ReportingError(f"工作线已分派：{kind.value}")
        action = CorrectiveAction(kind=kind, owner_team=owner_team)
        incident.actions[kind.value] = action
        if incident.status == IncidentStatus.REPORTED:
            incident.status = IncidentStatus.INVESTIGATING
        return action

    def make_commitment(
        self,
        incident_id: str,
        kind: ActionKind,
        *,
        content: str,
        due_at: str,
        owner_team: str | None = None,
    ) -> Commitment:
        """登记整改承诺与完成期限。"""

        action = self._get_action(incident_id, kind)
        if not content:
            raise ReportingError("承诺内容不能为空")
        due = _parse(due_at)
        if due < _now(self.clock):
            raise ReportingError("承诺截止时间不得早于当前时间")
        action.owner_team = owner_team or action.owner_team
        action.commitment = Commitment(
            action_kind=kind,
            content=content,
            due_at=due.isoformat(),
            owner_team=action.owner_team,
        )
        action.status = ActionStatus.IN_PROGRESS
        return action.commitment

    # ----- 证据与审签 -----

    def submit_evidence(
        self,
        incident_id: str,
        kind: ActionKind,
        *,
        submitted_by: str,
        attachment_name: str,
        redacted_view: str,
        contains_trade_secret: bool = False,
        controlled_ref: str = "",
        visibility: AttachmentVisibility | None = None,
        authorized_reviewers: list[str] | None = None,
        completed_at: str | None = None,
    ) -> Attachment:
        """提交整改完成证据。

        复测证据在根因调查与制度修订审签通过前不被接受。
        商业秘密必须放入受控附件，并提供脱敏视图。
        """

        incident = self._get_incident(incident_id)
        action = self._get_action(incident_id, kind)
        if not submitted_by:
            raise ReportingError("必须登记证据提交人")
        if kind == ActionKind.RETEST:
            for prereq in RETEST_PREREQUISITES:
                pre = incident.actions.get(prereq.value)
                if pre is None or pre.status != ActionStatus.APPROVED:
                    raise ReportingError(
                        f"复测前置工作未完成审签：{prereq.value}"
                    )
            incident.status = IncidentStatus.VERIFYING
        if contains_trade_secret:
            if not controlled_ref:
                raise ReportingError("含商业秘密的证据必须登记受控存放位置")
            vis = visibility or AttachmentVisibility.CONTROLLED
        else:
            vis = visibility or AttachmentVisibility.INTERNAL
        self._attachment_seq += 1
        attachment = Attachment(
            id=f"ATT-{incident.id}-{self._attachment_seq:03d}",
            name=attachment_name,
            contains_trade_secret=contains_trade_secret,
            visibility=vis,
            redacted_view=redacted_view,
            controlled_ref=controlled_ref,
            uploaded_by=submitted_by,
            uploaded_at=self._now_iso(),
            authorized_reviewers=list(authorized_reviewers or []),
        )
        action.evidence = attachment
        action.submitted_by = submitted_by
        action.submitted_at = self._now_iso()
        action.completed_at = completed_at or self._now_iso()
        action.status = ActionStatus.DONE
        return attachment

    def review_evidence(
        self,
        incident_id: str,
        kind: ActionKind,
        *,
        reviewer: str,
        approved: bool,
        note: str = "",
    ) -> ReviewRecord:
        """审签证据。提交人与审签人不得为同一人（职责分离）。"""

        incident = self._get_incident(incident_id)
        action = self._get_action(incident_id, kind)
        if action.status != ActionStatus.DONE:
            raise ReportingError(f"当前状态无可审签证据：{action.status.value}")
        if not reviewer:
            raise ReportingError("必须登记审签人")
        if reviewer == action.submitted_by:
            raise ReportingError("证据提交人不得审签本人提交的证据")
        att = action.evidence
        if (
            att is not None
            and att.contains_trade_secret
            and att.authorized_reviewers
            and reviewer not in att.authorized_reviewers
        ):
            raise ReportingError("审签人不在受控附件授权名单内")
        record = ReviewRecord(
            reviewer=reviewer, approved=approved, at=self._now_iso(), note=note
        )
        action.reviews.append(record)
        if approved:
            action.status = ActionStatus.APPROVED
        else:
            action.status = ActionStatus.REJECTED
            action.completed_at = None
        return record

    # ----- 复发关联 -----

    def link_recurrence(self, incident_id: str, prior_incident_id: str) -> None:
        """将本次事件关联到既往同类事件，双向建立关联。"""

        current = self._get_incident(incident_id)
        prior = self._get_incident(prior_incident_id)
        if incident_id == prior_incident_id:
            raise ReportingError("事件不能与自身关联")
        if prior_incident_id not in current.recurrence_links:
            current.recurrence_links.append(prior_incident_id)
        if incident_id not in prior.recurrence_links:
            prior.recurrence_links.append(incident_id)

    # ----- 自动监测线索（只提线索） -----

    def raise_lead(
        self, *, source: str, signal: str, observed_at: str
    ) -> MonitoringLead:
        """监测系统只能登记线索，状态始终为待核实，不会自动立案。"""

        if not source or not signal or not observed_at:
            raise ReportingError("线索来源、信号内容与观测时间均不能为空")
        lead = MonitoringLead(
            id=f"LEAD-{len(self.leads) + 1:04d}",
            source=source,
            signal=signal,
            observed_at=observed_at,
        )
        self.leads[lead.id] = lead
        return lead

    def verify_lead(
        self,
        lead_id: str,
        *,
        reviewer: str,
        adopt: bool,
        note: str,
        incident_payload: dict | None = None,
    ) -> MonitoringLead:
        """人工核实线索；采纳后才允许据其建立事件。"""

        try:
            lead = self.leads[lead_id]
        except KeyError:
            raise ReportingError(f"线索不存在：{lead_id}") from None
        if lead.status != LeadStatus.NEW:
            raise ReportingError("线索已完成核实，不能重复处置")
        if not reviewer or not note:
            raise ReportingError("核实必须记录核实人与核实意见")
        lead.verified_by = reviewer
        lead.verified_at = self._now_iso()
        lead.verification_note = note
        if not adopt:
            lead.status = LeadStatus.DISMISSED
            return lead
        if incident_payload is None:
            raise ReportingError("采纳线索时必须提供建事件信息")
        incident = self.report_incident(**incident_payload)
        lead.status = LeadStatus.VERIFIED
        lead.incident_id = incident.id
        incident.created_from_lead_id = lead.id
        return lead

    # ----- 监管摘要（不得淡化未解决风险） -----

    def publish_regulator_summary(self, incident_id: str, summary: str) -> str:
        incident = self._get_incident(incident_id)
        if not summary or not summary.strip():
            raise ReportingError("监管摘要不能为空")
        open_risks = self.unresolved_risks(incident_id)
        risk_words = ["未完成", "未解决", "风险", "整改中", "待复测", "逾期", "驳回", "待审"]
        if open_risks and not any(w in summary for w in risk_words):
            raise ReportingError(
                "存在未解决风险时，监管摘要不得淡化，必须明示未解决风险"
            )
        incident.regulator_summary = summary
        return summary

    def unresolved_risks(self, incident_id: str) -> list[dict]:
        """列出事件尚未关闭的风险：未通过/临期/逾期的工作线。"""

        incident = self._get_incident(incident_id)
        now = _now(self.clock)
        risks: list[dict] = []
        for kind in ActionKind:
            action = incident.actions.get(kind.value)
            if action is None:
                risks.append(
                    {"kind": kind.value, "issue": "工作线尚未分派", "open": True}
                )
                continue
            if action.status == ActionStatus.REJECTED:
                risks.append(
                    {"kind": kind.value, "issue": "证据审签被驳回，需重新整改", "open": True}
                )
            elif action.status != ActionStatus.APPROVED:
                risks.append(
                    {
                        "kind": kind.value,
                        "issue": f"工作线尚未审签通过（{action.status.value}）",
                        "open": True,
                    }
                )
            if action.commitment is not None and action.status != ActionStatus.APPROVED:
                due = _parse(action.commitment.due_at)
                if due < now:
                    risks.append(
                        {"kind": kind.value, "issue": "整改承诺已逾期", "open": True}
                    )
                elif due <= now + DUE_SOON_WINDOW:
                    risks.append(
                        {"kind": kind.value, "issue": "整改承诺临近到期", "open": True}
                    )
        return risks

    # ----- 期限提醒 -----

    def due_reminders(self, *, include_closed: bool = False) -> list[dict]:
        """生成承诺期限提醒：逾期与临近到期（默认仅未关闭事件）。"""

        now = _now(self.clock)
        reminders: list[dict] = []
        for incident in self.incidents.values():
            if not include_closed and incident.status == IncidentStatus.CLOSED:
                continue
            for action in incident.actions.values():
                if action.status == ActionStatus.APPROVED:
                    continue
                commitment = action.commitment
                if commitment is None:
                    continue
                due = _parse(commitment.due_at)
                if due < now:
                    state = "逾期"
                elif due <= now + DUE_SOON_WINDOW:
                    state = "临近到期"
                else:
                    continue
                reminders.append(
                    {
                        "incident_id": incident.id,
                        "incident_title": incident.title,
                        "level": incident.level.value,
                        "action_kind": action.kind.value,
                        "owner_team": action.owner_team,
                        "due_at": commitment.due_at,
                        "state": state,
                        "days_overdue": (now - due).days if due < now else 0,
                    }
                )
        reminders.sort(key=lambda r: (r["state"] != "逾期", r["due_at"]))
        return reminders

    def report_timeliness(self) -> list[dict]:
        """对照分级时限，检查各事件初报是否在规定时限内。"""

        rows = []
        for incident in self.incidents.values():
            deadline = _parse(incident.report_deadline)
            rows.append(
                {
                    "incident_id": incident.id,
                    "level": incident.level.value,
                    "first_detected_at": incident.first_detected_at,
                    "report_deadline": incident.report_deadline,
                    "report_count": len(incident.merged_report_ids),
                    "reported_at": incident.reported_at,
                    "on_time": _parse(incident.reported_at) <= deadline,
                }
            )
        return rows

    # ----- 关闭与对照 -----

    def can_close(self, incident_id: str) -> tuple[bool, list[str]]:
        incident = self._get_incident(incident_id)
        blockers: list[str] = []
        for kind in CLOSURE_REQUIRED_KINDS:
            action = incident.actions.get(kind.value)
            if action is None:
                blockers.append(f"{kind.value}尚未分派")
            elif action.status != ActionStatus.APPROVED:
                blockers.append(f"{kind.value}未审签通过（{action.status.value}）")
            elif action.commitment is None:
                blockers.append(f"{kind.value}缺少整改承诺")
            elif _parse(action.completed_at) > _parse(action.commitment.due_at):
                blockers.append(f"{kind.value}实际完成晚于承诺期限")
        if not incident.regulator_summary:
            blockers.append("尚未形成监管摘要")
        return not blockers, blockers

    def close_incident(self, incident_id: str) -> Incident:
        ok, blockers = self.can_close(incident_id)
        if not ok:
            raise ReportingError("事件尚不能关闭：" + "；".join(blockers))
        incident = self._get_incident(incident_id)
        for action in incident.actions.values():
            action.status = ActionStatus.CLOSED
        incident.status = IncidentStatus.CLOSED
        incident.closed_at = self._now_iso()
        return incident

    def commitment_compliance(self, incident_id: str) -> list[dict]:
        """逐条对照平台承诺与实际完成情况。"""

        incident = self._get_incident(incident_id)
        rows = []
        for kind in ActionKind:
            action = incident.actions.get(kind.value)
            commitment = action.commitment if action else None
            if commitment is None:
                rows.append(
                    {
                        "action_kind": kind.value,
                        "promised": False,
                        "commitment": None,
                        "actual_status": action.status.value if action else "未分派",
                        "fulfilled": False,
                        "on_time": False,
                    }
                )
                continue
            fulfilled = action.status == ActionStatus.APPROVED
            on_time = bool(
                fulfilled
                and action.completed_at is not None
                and _parse(action.completed_at) <= _parse(commitment.due_at)
            )
            rows.append(
                {
                    "action_kind": kind.value,
                    "promised": True,
                    "commitment": commitment.to_dict(),
                    "actual_status": action.status.value,
                    "actual_completed_at": action.completed_at,
                    "fulfilled": fulfilled,
                    "on_time": on_time,
                }
            )
        return rows

    # ----- 受控附件视图 -----

    @staticmethod
    def attachment_view(att: Attachment, *, viewer: str) -> dict:
        """按查看人身份返回附件视图；未授权者只能看到脱敏内容。"""

        if att.contains_trade_secret and viewer not in att.authorized_reviewers:
            return {
                "id": att.id,
                "name": att.name,
                "visibility": att.visibility.value,
                "view": att.redacted_view,
                "redacted": True,
                "controlled_ref": att.controlled_ref,
            }
        return {
            "id": att.id,
            "name": att.name,
            "visibility": att.visibility.value,
            "view": att.redacted_view,
            "redacted": False,
            "controlled_ref": att.controlled_ref,
        }

    # ----- 监管组合视图 -----

    def regulator_view(self, incident_id: str, *, viewer: str = "市场监管人员") -> dict:
        """监管人员最终视图：承诺与实际完成对照、风险与证据脱敏材料。"""

        incident = self._get_incident(incident_id)
        return {
            "incident": {
                "id": incident.id,
                "title": incident.title,
                "level": incident.level.value,
                "domains": incident.domains,
                "status": incident.status.value,
                "first_detected_at": incident.first_detected_at,
                "report_deadline": incident.report_deadline,
                "merged_report_count": len(incident.merged_report_ids),
                "source_teams": sorted(
                    {r["team"] for r in incident.source_team_reports}
                ),
                "frozen": incident.frozen,
                "freeze_revisions": [r.to_dict() for r in incident.freeze_revisions],
                "recurrence_links": incident.recurrence_links,
                "regulator_summary": incident.regulator_summary,
            },
            "unresolved_risks": self.unresolved_risks(incident_id),
            "commitment_compliance": self.commitment_compliance(incident_id),
            "evidence": [
                self.attachment_view(a.evidence, viewer=viewer)
                for a in incident.actions.values()
                if a.evidence is not None
            ],
        }

    # ----- 持久化 -----

    def to_dict(self) -> dict:
        return {
            "domain": "platform-gatekeeper-reporting",
            "service": "major-event-remediation-reporting",
            "version": 1,
            "generated_at": self._now_iso(),
            "incidents": [i.to_dict() for i in self.incidents.values()],
            "leads": [l.to_dict() for l in self.leads.values()],
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def load_service(path: str | Path, clock: Callable[[], datetime] | None = None) -> ReportingService:
    """从 JSON 束约文件只读回放服务快照（用于监管侧比对查看）。"""

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("domain") != "platform-gatekeeper-reporting":
        raise ReportingError("快照领域标识不匹配")
    svc = ReportingService(clock=clock)
    for lead_raw in raw.get("leads", []):
        lead = MonitoringLead(
            id=lead_raw["id"],
            source=lead_raw["source"],
            signal=lead_raw["signal"],
            observed_at=lead_raw["observed_at"],
            status=LeadStatus(lead_raw["status"]),
            verification_note=lead_raw.get("verification_note"),
            verified_by=lead_raw.get("verified_by"),
            verified_at=lead_raw.get("verified_at"),
            incident_id=lead_raw.get("incident_id"),
        )
        svc.leads[lead.id] = lead
    for inc_raw in raw.get("incidents", []):
        revisions = [
            FreezeRevision(
                field=r["field"],
                before=r["before"],
                after=r["after"],
                reason=r["reason"],
                by=r["by"],
                at=r["at"],
            )
            for r in inc_raw.get("freeze_revisions", [])
        ]
        actions: dict[str, CorrectiveAction] = {}
        for a_raw in inc_raw.get("actions", []):
            c_raw = a_raw.get("commitment")
            commitment = (
                Commitment(
                    action_kind=ActionKind(c_raw["action_kind"]),
                    content=c_raw["content"],
                    due_at=c_raw["due_at"],
                    owner_team=c_raw["owner_team"],
                )
                if c_raw
                else None
            )
            e_raw = a_raw.get("evidence")
            evidence = (
                Attachment(
                    id=e_raw["id"],
                    name=e_raw["name"],
                    contains_trade_secret=e_raw["contains_trade_secret"],
                    visibility=AttachmentVisibility(e_raw["visibility"]),
                    redacted_view=e_raw["redacted_view"],
                    controlled_ref=e_raw["controlled_ref"],
                    uploaded_by=e_raw["uploaded_by"],
                    uploaded_at=e_raw["uploaded_at"],
                    authorized_reviewers=list(e_raw.get("authorized_reviewers", [])),
                )
                if e_raw
                else None
            )
            reviews = [
                ReviewRecord(
                    reviewer=r["reviewer"],
                    approved=r["approved"],
                    at=r["at"],
                    note=r.get("note", ""),
                )
                for r in a_raw.get("reviews", [])
            ]
            action = CorrectiveAction(
                kind=ActionKind(a_raw["kind"]),
                owner_team=a_raw["owner_team"],
                status=ActionStatus(a_raw["status"]),
                commitment=commitment,
                completed_at=a_raw.get("completed_at"),
                evidence=evidence,
                reviews=reviews,
                submitted_by=a_raw.get("submitted_by"),
                submitted_at=a_raw.get("submitted_at"),
            )
            actions[action.kind.value] = action
        incident = Incident(
            id=inc_raw["id"],
            title=inc_raw["title"],
            level=IncidentLevel(inc_raw["level"]),
            domains=list(inc_raw.get("domains", [])),
            first_detected_at=inc_raw["first_detected_at"],
            reporter=inc_raw["reporter"],
            report_deadline=inc_raw["report_deadline"],
            reported_at=inc_raw.get("reported_at", inc_raw["first_detected_at"]),
            merged_report_ids=list(inc_raw.get("merged_report_ids", [])),
            source_team_reports=list(inc_raw.get("source_team_reports", [])),
            frozen=dict(inc_raw.get("frozen", {})),
            frozen_at=inc_raw.get("frozen_at"),
            frozen_by=inc_raw.get("frozen_by"),
            freeze_revisions=revisions,
            actions=actions,
            status=IncidentStatus(inc_raw["status"]),
            regulator_summary=inc_raw.get("regulator_summary"),
            recurrence_links=list(inc_raw.get("recurrence_links", [])),
            created_from_lead_id=inc_raw.get("created_from_lead_id"),
            closed_at=inc_raw.get("closed_at"),
        )
        svc.incidents[incident.id] = incident
    return svc
