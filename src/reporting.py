"""平台守门人：统一重大事项与整改报告服务。

在现有领域资料（domain/sample 标识、参与者、记录结构）之上提供：

- 分级报送：重大 / 较大 / 一般三级，自动记录报送时限与迟报标记；
- 线索边界：自动监测只能产生线索（signal），立案、归并、定级必须人工确认；
- 跨团队归并：同一事件的多团队上报归并到同一事件，系统只给候选建议；
- 事实封存：影响时间窗、规则版本、受影响主体、临时措施冻结后只能追加修正留痕；
- 整改分派：根因调查、用户补救、制度修订、复测验证四类任务闭环；
- 整改承诺与期限提醒：承诺不可改写，逾期 / 临期自动提示；
- 证据审签：双人双签、上传与审签职责分离，商业秘密仅登记受控附件；
- 复发关联：与已关闭的历史事件人工确认关联；
- 监管摘要：未解决风险必须如实列示，不得淡化，受控附件不出摘要；
- 关闭闸门与承诺对账：任务、证据、承诺全部完成方可关闭，监管可比对承诺与实际。

本模块只依赖标准库，时间由调用方传入，便于复测与审计。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "ReportingError",
    "LEVELS",
    "TASK_CODES",
    "GATEKEEPER_DOMAINS",
    "ReportingService",
    "load_case",
]


class ReportingError(ValueError):
    """服务规则被违反（参数错误或流程越权）。"""


# 分级 -> 中文标签与报送时限（自首次发现起的自然日）
LEVELS: dict[str, dict[str, Any]] = {
    "major": {"label": "重大", "filing_days": 1},
    "large": {"label": "较大", "filing_days": 3},
    "ordinary": {"label": "一般", "filing_days": 7},
}

# 整改任务四类标准动作
TASK_CODES: dict[str, str] = {
    "root_cause": "根因调查",
    "user_remedy": "用户补救",
    "policy_revision": "制度修订",
    "retest": "复测验证",
}

# 守门人责任四领域
GATEKEEPER_DOMAINS = {"数据", "算法", "流量", "规则"}

# 事实封存允许修正的字段
_SNAPSHOT_FIELDS = {
    "window_start",
    "window_end",
    "rule_versions",
    "affected_subjects",
    "interim_measures",
}


def _dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:  # pragma: no cover - 防御性提示
        raise ReportingError(f"时间格式无法解析：{value!r}") from exc


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReportingError(message)


def _rule_code(version: dict[str, str]) -> str:
    return f"{version['code']}@{version['version']}"


class ReportingService:
    """重大事项与整改报告的内存服务，可导出 / 载入 JSON 样例。"""

    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.reports: dict[str, dict[str, Any]] = {}
        self.signals: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ 线索

    def ingest_signal(
        self,
        *,
        monitor: str,
        rule_code: str,
        title: str,
        detail: str,
        observed_at: str | datetime,
        teams: Iterable[str],
        confidence: str = "medium",
        signal_id: str | None = None,
        suspected_event_id: str | None = None,
    ) -> str:
        """登记一条自动监测线索。

        自动监测的边界：只能产生线索，不允许携带分级，不允许直接立案、
        归并或关联历史事件。``suspected_event_id`` 仅作为供人工参考的猜测。
        """
        _require(monitor and title and rule_code, "监测线索缺少来源、规则编号或标题")
        _require(confidence in {"low", "medium", "high"}, "线索置信度取值非法")
        signal_id = signal_id or f"SGN-{len(self.signals) + 1:04d}"
        _require(signal_id not in self.signals, f"线索标识重复：{signal_id}")
        self.signals[signal_id] = {
            "id": signal_id,
            "source_kind": "monitor_signal",
            "monitor": monitor,
            "rule_code": rule_code,
            "title": title,
            "detail": detail,
            "observed_at": _dt(observed_at).isoformat(),
            "teams": list(teams),
            "confidence": confidence,
            "status": "new",  # new / converted / dismissed，仅人工可改
            "linked_report_ids": [],
            "suspected_event_id": suspected_event_id,
        }
        return signal_id

    def dismiss_signal(self, signal_id: str, *, by: str, reason: str) -> None:
        """人工核实后排除线索（自动监测无权处置）。"""
        signal = self._get_signal(signal_id)
        _require(by and reason, "排除线索需要责任人和理由")
        signal["status"] = "dismissed"
        signal["disposition"] = {"by": by, "reason": reason}

    def create_report(
        self,
        *,
        team: str,
        title: str,
        observed_at: str | datetime,
        detail: str,
        by: str,
        tags: Iterable[str] = (),
        signal_ids: Iterable[str] | None = None,
        report_id: str | None = None,
        at: str | datetime | None = None,
    ) -> str:
        """团队人工上报。即使由监测线索触发，也必须有责任人确认。"""
        _require(team and title and by, "上报缺少团队、标题或责任人")
        signal_ids = list(signal_ids or [])
        for sid in signal_ids:
            signal = self._get_signal(sid)
            _require(signal["status"] != "dismissed", f"线索已人工排除，不能转报：{sid}")
        report_id = report_id or f"RPT-{len(self.reports) + 1:04d}"
        _require(report_id not in self.reports, f"上报标识重复：{report_id}")
        report = {
            "id": report_id,
            "team": team,
            "title": title,
            "detail": detail,
            "tags": list(tags),
            "observed_at": _dt(observed_at).isoformat(),
            "created_at": (_dt(at) if at else _dt(observed_at)).isoformat(),
            "by": by,
            "source": "lead_confirmed" if signal_ids else "manual",
            "signal_ids": signal_ids,
            "status": "new",  # new / merged
            "merged_event_id": None,
        }
        self.reports[report_id] = report
        for sid in signal_ids:
            signal = self.signals[sid]
            signal["status"] = "converted"
            signal["linked_report_ids"].append(report_id)
        return report_id

    def find_candidate_reports(self, *, window_days: int = 3) -> list[dict[str, Any]]:
        """给出跨团队归并候选（仅建议，不改变任何状态）。

        判定线索：同一监测线索、或时间窗内标签重合。最终归并必须由
        合规负责人通过 file_event / merge_reports 确认。
        """
        open_reports = [r for r in self.reports.values() if r["status"] == "new"]
        parent = {r["id"]: r["id"] for r in open_reports}

        def find(x: str) -> str:
            parent[x] = x if parent[x] == x else find(parent[x])
            return parent[x]

        def union(a: str, b: str) -> None:
            parent[find(a)] = find(b)

        pairs: list[dict[str, str]] = []
        for i, left in enumerate(open_reports):
            for right in open_reports[i + 1 :]:
                shared_signals = sorted(set(left["signal_ids"]) & set(right["signal_ids"]))
                shared_tags = sorted(set(left["tags"]) & set(right["tags"]))
                within = abs(_dt(left["observed_at"]) - _dt(right["observed_at"])) <= timedelta(days=window_days)
                if shared_signals or (shared_tags and within):
                    union(left["id"], right["id"])
                    pairs.append(
                        {
                            "left": left["id"],
                            "right": right["id"],
                            "ids": {left["id"], right["id"]},
                            "shared_signals": ",".join(shared_signals),
                            "shared_tags": ",".join(shared_tags),
                        }
                    )

        groups: dict[str, list[str]] = {}
        for report in open_reports:
            groups.setdefault(find(report["id"]), []).append(report["id"])
        suggestions = []
        for ids in groups.values():
            if len(ids) < 2:
                continue
            teams = sorted({self.reports[i]["team"] for i in ids})
            if len(teams) < 2:
                continue  # 同团队重复上报不属于跨团队归并
            id_set = set(ids)
            reasons = [
                f"{p['left']}↔{p['right']}"
                + (f"（共同线索 {p['shared_signals']}）" if p["shared_signals"] else "")
                + (f"（共同标签 {p['shared_tags']}）" if p["shared_tags"] else "")
                for p in pairs
                if p["ids"] <= id_set
            ]
            suggestions.append(
                {
                    "report_ids": sorted(ids),
                    "teams": teams,
                    "basis": reasons,
                    "note": "系统仅提出归并建议，需合规负责人确认",
                }
            )
        return suggestions

    # ------------------------------------------------------------------ 报送

    def file_event(
        self,
        *,
        title: str,
        level: str,
        gatekeeper_domains: Iterable[str],
        first_observed_at: str | datetime,
        report_ids: Iterable[str],
        by: str,
        at: str | datetime,
        event_id: str | None = None,
    ) -> str:
        """分级报送并归并首批跨团队上报。报送后分级不可更改。"""
        _require(title and by, "事件缺少标题或报送责任人")
        _require(level in LEVELS, f"分级非法：{level!r}，可选 {sorted(LEVELS)}")
        domains = list(dict.fromkeys(gatekeeper_domains))
        _require(domains and set(domains) <= GATEKEEPER_DOMAINS, "守门人领域取值非法")
        report_ids = list(dict.fromkeys(report_ids))
        _require(report_ids, "立案至少归并一条团队上报（监测线索不能直接立案）")
        reports = [self._get_report(rid) for rid in report_ids]
        for report in reports:
            _require(report["status"] == "new", f"上报已归并到其他事件：{report['id']}")
        teams = sorted({r["team"] for r in reports})
        _require(len(teams) >= 2 or len(reports) == 1, "多条同团队上报请先在团队内核对，不应跨团队归并")

        event_id = event_id or f"EVT-{len(self.events) + 1:04d}"
        _require(event_id not in self.events, f"事件标识重复：{event_id}")
        observed = _dt(first_observed_at)
        filed = _dt(at)
        deadline = observed + timedelta(days=LEVELS[level]["filing_days"])

        event = {
            "id": event_id,
            "title": title,
            "level": level,
            "gatekeeper_domains": domains,
            "status": "reported",
            "first_observed_at": observed.isoformat(),
            "filed_at": filed.isoformat(),
            "filing_deadline": deadline.isoformat(),
            "filing_timeliness": "on_time" if filed <= deadline else "late",
            "filed_by": by,
            "teams": teams,
            "reports": [],
            "snapshot": None,
            "snapshot_corrections": [],
            "tasks": [],
            "evidence": [],
            "commitments": [],
            "recurrence": {"prior": [], "followups": []},
            "closed_at": None,
            "history": [],
        }
        self.events[event_id] = event
        for report in reports:
            report["status"] = "merged"
            report["merged_event_id"] = event_id
            event["reports"].append(report["id"])
        self._log(event, filed, by, "graded_filing", f"按{LEVELS[level]['label']}级报送并归并 {len(reports)} 条上报")
        return event_id

    def merge_reports(
        self, event_id: str, report_ids: Iterable[str], *, by: str, at: str | datetime
    ) -> None:
        """事件报送后继续归并新发现的跨团队上报。"""
        event = self._get_event(event_id)
        self._ensure_open(event)
        for rid in dict.fromkeys(report_ids):
            report = self._get_report(rid)
            _require(report["status"] == "new", f"上报已归并，不能重复归并：{rid}")
            report["status"] = "merged"
            report["merged_event_id"] = event_id
            event["reports"].append(report["id"])
            if report["team"] not in event["teams"]:
                event["teams"].append(report["team"])
                event["teams"].sort()
            self._log(event, at, by, "merge_report", f"追加上报归并：{rid}（{report['team']}）")

    # ------------------------------------------------------------------ 封存

    def freeze_snapshot(
        self,
        event_id: str,
        *,
        window_start: str | datetime,
        window_end: str | datetime,
        rule_versions: Iterable[dict[str, str]],
        affected_subjects: Iterable[dict[str, Any]],
        interim_measures: Iterable[dict[str, Any]],
        by: str,
        at: str | datetime,
    ) -> None:
        """冻结影响时间窗、规则版本、受影响主体与临时措施。"""
        event = self._get_event(event_id)
        self._ensure_open(event)
        _require(event["snapshot"] is None, "事实封存已完成，不能重复封存；如需变更请追加修正")
        start, end = _dt(window_start), _dt(window_end)
        _require(start <= end, "影响时间窗起点不能晚于终点")
        rule_versions = list(rule_versions)
        affected = list(affected_subjects)
        measures = list(interim_measures)
        _require(rule_versions, "必须冻结规则版本")
        _require(affected, "必须冻结受影响主体")
        _require(measures, "必须冻结已采取的临时措施")
        for version in rule_versions:
            _require({"code", "version"} <= set(version), "规则版本需包含 code 与 version")
        event["snapshot"] = {
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "rule_versions": [dict(v) for v in rule_versions],
            "affected_subjects": [dict(a) for a in affected],
            "interim_measures": [dict(m) for m in measures],
            "frozen_by": by,
            "frozen_at": _dt(at).isoformat(),
        }
        self._log(event, at, by, "freeze_snapshot", "冻结影响时间窗、规则版本、受影响主体与临时措施")

    def correct_snapshot(
        self,
        event_id: str,
        *,
        field: str,
        before: Any,
        after: Any,
        reason: str,
        by: str,
        at: str | datetime,
    ) -> None:
        """修正封存内容：原值保留在修正记录中，不得覆盖抹除。"""
        event = self._get_event(event_id)
        self._ensure_open(event)
        snapshot = event["snapshot"]
        _require(snapshot is not None, "尚未封存，不能修正")
        _require(field in _SNAPSHOT_FIELDS, f"不允许修正封存字段：{field}")
        _require(reason and by, "封存修正需要理由与责任人")
        seq = len(event["snapshot_corrections"]) + 1
        event["snapshot_corrections"].append(
            {
                "seq": seq,
                "field": field,
                "before": before,
                "after": after,
                "reason": reason,
                "by": by,
                "at": _dt(at).isoformat(),
            }
        )
        snapshot[field] = after
        self._log(event, at, by, "correct_snapshot", f"第 {seq} 次封存修正（{field}），原值已留痕")

    # ------------------------------------------------------------------ 分派

    def assign_task(
        self,
        event_id: str,
        *,
        code: str,
        title: str,
        owner_team: str,
        due: str | datetime,
        by: str,
        at: str | datetime,
        task_id: str | None = None,
    ) -> str:
        event = self._get_event(event_id)
        self._ensure_open(event)
        _require(code in TASK_CODES, f"任务类型非法：{code}")
        _require(not any(t["code"] == code for t in event["tasks"]), f"该事件已分派 {TASK_CODES[code]}，不能重复分派")
        task_id = task_id or f"TASK-{len(event['tasks']) + 1:04d}"
        _require(not any(t["id"] == task_id for t in event["tasks"]), f"任务标识重复：{task_id}")
        event["tasks"].append(
            {
                "id": task_id,
                "code": code,
                "title": title,
                "owner_team": owner_team,
                "due": _dt(due).isoformat(),
                "status": "open",
                "evidence_ids": [],
                "result_note": None,
                "submitted_by": None,
                "submitted_at": None,
                "completed_at": None,
                "retest_results": [],
            }
        )
        if event["status"] == "reported":
            event["status"] = "investigating"
        self._log(event, at, by, "assign_task", f"分派{TASK_CODES[code]}：{title}（{owner_team}，期限 {due}）")
        return task_id

    def submit_task(
        self,
        event_id: str,
        task_id: str,
        *,
        evidence_ids: Iterable[str],
        note: str,
        by: str,
        at: str | datetime,
    ) -> None:
        """提交整改结果。关联证据必须已经通过审签，复测任务须给出结论。"""
        event = self._get_event(event_id)
        self._ensure_open(event)
        task = self._get_task(event, task_id)
        _require(task["status"] == "open", "仅进行中的任务可以提交结果")
        evidence_ids = list(dict.fromkeys(evidence_ids))
        _require(evidence_ids, "提交整改结果必须附证据")
        for eid in evidence_ids:
            evidence = self._get_evidence(event, eid)
            _require(evidence["state"] == "approved", f"证据未通过审签，不能随结果提交：{eid}")
        task["evidence_ids"] = evidence_ids
        task["result_note"] = note
        task["submitted_by"] = by
        task["submitted_at"] = _dt(at).isoformat()
        task["status"] = "in_review"
        self._log(event, at, by, "submit_task", f"{TASK_CODES[task['code']]}结果提交审签")

    def complete_task(
        self,
        event_id: str,
        task_id: str,
        *,
        by: str,
        at: str | datetime,
        retest_passed: bool | None = None,
    ) -> None:
        """合规复核通过后关闭任务；复测任务须明确是否通过，失败结论将阻止事件关闭。"""
        event = self._get_event(event_id)
        self._ensure_open(event)
        task = self._get_task(event, task_id)
        _require(task["status"] == "in_review", "仅审签中的任务可以办结")
        if task["code"] == "retest":
            _require(retest_passed is not None, "复测任务必须明确是否通过")
            task["retest_results"].append({"passed": bool(retest_passed), "by": by, "at": _dt(at).isoformat()})
        else:
            _require(retest_passed is None, "非复测任务不得填报复测结论")
        task["status"] = "done"
        task["completed_at"] = _dt(at).isoformat()
        suffix = "（复测通过）" if retest_passed else ("（复测未通过）" if retest_passed is False else "")
        self._log(event, at, by, "complete_task", f"{TASK_CODES[task['code']]}办结{suffix}")

    def reopen_task(self, event_id: str, task_id: str, *, reason: str, by: str, at: str | datetime) -> None:
        """复测失败或审签退回时重新打开任务，留痕保留。"""
        event = self._get_event(event_id)
        self._ensure_open(event)
        task = self._get_task(event, task_id)
        _require(task["status"] == "done", "仅已办结任务可以重新打开")
        _require(reason and by, "重新打开任务需要理由与责任人")
        task["status"] = "open"
        task["completed_at"] = None
        self._log(event, at, by, "reopen_task", f"{TASK_CODES[task['code']]}重新打开：{reason}")

    # ------------------------------------------------------------------ 证据

    def add_evidence(
        self,
        event_id: str,
        *,
        title: str,
        kind: str,
        ref: str,
        sha256: str,
        uploaded_by: str,
        controlled: bool = False,
        at: str | datetime | None = None,
        evidence_id: str | None = None,
        supersedes: str | None = None,
    ) -> str:
        """登记证据。受控附件（商业秘密）只登记编号、位置与哈希，不收存正文。"""
        event = self._get_event(event_id)
        self._ensure_open(event)
        _require(title and kind and ref and sha256 and uploaded_by, "证据要素不完整")
        evidence_id = evidence_id or f"EV-{len(event['evidence']) + 1:03d}"
        _require(not any(e["id"] == evidence_id for e in event["evidence"]), f"证据标识重复：{evidence_id}")
        if supersedes:
            self._get_evidence(event, supersedes)
        event["evidence"].append(
            {
                "id": evidence_id,
                "title": title,
                "kind": kind,
                "ref": ref,
                "sha256": sha256,
                "controlled": controlled,
                "uploaded_by": uploaded_by,
                "uploaded_at": _dt(at).isoformat() if at else None,
                "state": "submitted",
                "reviews": [],
                "supersedes": supersedes,
            }
        )
        label = "受控附件（商业秘密）" if controlled else "证据"
        self._log(event, at or event["filed_at"], uploaded_by, "add_evidence", f"登记{label}：{evidence_id} {title}")
        return evidence_id

    def review_evidence(
        self,
        event_id: str,
        evidence_id: str,
        *,
        decision: str,
        by: str,
        note: str = "",
        at: str | datetime,
    ) -> None:
        """双人双签：两名不同审批人批准方可通过；上传人不得审批本人材料。"""
        event = self._get_event(event_id)
        self._ensure_open(event)
        evidence = self._get_evidence(event, evidence_id)
        _require(decision in {"approved", "rejected"}, "审签结论只能是 approved / rejected")
        _require(evidence["state"] != "rejected", "证据已被否决，应重新登记替代证据")
        reviewers = {r["by"] for r in evidence["reviews"] if r["decision"] == "approved"}
        _require(by != evidence["uploaded_by"], "上传与审签职责分离，不能审批本人提交的证据")
        _require(by not in reviewers, "同一审批人不能重复签署")

        review = {"by": by, "decision": decision, "note": note, "at": _dt(at).isoformat()}
        evidence["reviews"].append(review)
        if decision == "rejected":
            evidence["state"] = "rejected"
        elif len(reviewers | {by}) >= 2:
            evidence["state"] = "approved"
        suffix = "（双人双签完成）" if evidence["state"] == "approved" else "（待另一审批人签署）"
        self._log(event, at, by, "review_evidence", f"证据 {evidence_id} 审签：{decision}{suffix}")

    # ------------------------------------------------------------------ 承诺

    def add_commitment(
        self,
        event_id: str,
        *,
        statement: str,
        owner_team: str,
        due: str | datetime,
        by: str,
        at: str | datetime,
        commitment_id: str | None = None,
    ) -> str:
        event = self._get_event(event_id)
        self._ensure_open(event)
        _require(statement and owner_team and by, "承诺事项、责任团队与责任人不能为空")
        commitment_id = commitment_id or f"CMT-{len(event['commitments']) + 1:03d}"
        _require(not any(c["id"] == commitment_id for c in event["commitments"]), f"承诺标识重复：{commitment_id}")
        event["commitments"].append(
            {
                "id": commitment_id,
                "statement": statement,
                "owner_team": owner_team,
                "due": _dt(due).isoformat(),
                "created_at": _dt(at).isoformat(),
                "status": "open",  # open / verified / failed
                "verified_at": None,
                "verify_note": None,
                "verified_by": None,
                "evidence_ids": [],
            }
        )
        if event["status"] == "investigating":
            event["status"] = "remediating"
        self._log(event, at, by, "add_commitment", f"作出整改承诺：{commitment_id}（期限 {due}）")
        return commitment_id

    def verify_commitment(
        self,
        event_id: str,
        commitment_id: str,
        *,
        decision: str,
        evidence_ids: Iterable[str] = (),
        note: str,
        by: str,
        at: str | datetime,
    ) -> None:
        """验证承诺兑现情况；承诺表述本身不可改写，未通过保留为未解决风险。"""
        event = self._get_event(event_id)
        self._ensure_open(event)
        commitment = self._get_commitment(event, commitment_id)
        _require(decision in {"verified", "failed"}, "承诺验证结论只能是 verified / failed")
        evidence_ids = list(evidence_ids)
        for eid in evidence_ids:
            evidence = self._get_evidence(event, eid)
            _require(evidence["state"] == "approved", f"用于承诺验证的证据未通过审签：{eid}")
        if decision == "verified":
            _require(evidence_ids, "承诺已兑现必须附通过审签的证据")
        commitment["status"] = decision
        commitment["verified_at"] = _dt(at).isoformat()
        commitment["verified_by"] = by
        commitment["verify_note"] = note
        commitment["evidence_ids"] = evidence_ids
        self._log(event, at, by, "verify_commitment", f"承诺 {commitment_id} 验证结论：{decision}")

    # ------------------------------------------------------------------ 提醒 / 风险

    def due_reminders(self, *, on: str | datetime, within_days: int = 3) -> dict[str, list[dict[str, str]]]:
        """逾期与临期（默认未来 3 天）提醒，覆盖任务与承诺。"""
        on_dt = _dt(on)
        horizon = on_dt + timedelta(days=within_days)
        overdue: list[dict[str, str]] = []
        due_soon: list[dict[str, str]] = []

        def collect(row: dict[str, str], due: datetime, done: bool) -> None:
            if done:
                return
            if due < on_dt:
                overdue.append(row)
            elif due <= horizon:
                due_soon.append(row)

        for event in self.events.values():
            if event["status"] == "closed":
                continue
            for task in event["tasks"]:
                collect(
                    {
                        "event_id": event["id"],
                        "item": task["id"],
                        "kind": TASK_CODES[task["code"]],
                        "owner_team": task["owner_team"],
                        "due": task["due"],
                    },
                    _dt(task["due"]),
                    task["status"] == "done",
                )
            for commitment in event["commitments"]:
                collect(
                    {
                        "event_id": event["id"],
                        "item": commitment["id"],
                        "kind": "整改承诺",
                        "owner_team": commitment["owner_team"],
                        "due": commitment["due"],
                    },
                    _dt(commitment["due"]),
                    commitment["status"] == "verified",
                )
        return {"overdue": overdue, "due_soon": due_soon}

    def unresolved_risks(self, event_id: str, *, on: str | datetime) -> list[dict[str, str]]:
        """如实计算未解决风险。监管摘要与关闭闸门共用同一口径，不得淡化。"""
        event = self._get_event(event_id)
        on_dt = _dt(on)
        risks: list[dict[str, str]] = []

        if event["snapshot"] is None:
            risks.append({"code": "SNAPSHOT_MISSING", "severity": "high",
                          "text": "影响时间窗、规则版本、受影响主体与临时措施尚未封存"})

        for code, label in TASK_CODES.items():
            task = next((t for t in event["tasks"] if t["code"] == code), None)
            if task is None:
                risks.append({"code": f"TASK_MISSING_{code}", "severity": "high", "text": f"{label}尚未分派"})
                continue
            if task["status"] != "done":
                overdue = _dt(task["due"]) < on_dt
                tail = "，已逾期" if overdue else ""
                risks.append(
                    {
                        "code": f"TASK_OPEN_{code}",
                        "severity": "high" if overdue else "medium",
                        "text": f"{label}未办结（责任团队 {task['owner_team']}，期限 {task['due']}）{tail}",
                    }
                )
            elif code == "retest" and task["retest_results"] and not task["retest_results"][-1]["passed"]:
                risks.append({"code": "RETEST_FAILED", "severity": "high",
                              "text": "最近一次复测结论为未通过，整改有效性未证实"})

        for commitment in event["commitments"]:
            if commitment["status"] == "verified":
                continue
            overdue = _dt(commitment["due"]) < on_dt
            if commitment["status"] == "failed":
                risks.append(
                    {
                        "code": f"COMMITMENT_{commitment['id']}",
                        "severity": "high",
                        "text": f"承诺 {commitment['id']} 验证未通过：{commitment['statement']}",
                    }
                )
            else:
                tail = "，已逾期" if overdue else ""
                risks.append(
                    {
                        "code": f"COMMITMENT_{commitment['id']}",
                        "severity": "high" if overdue else "medium",
                        "text": f"承诺 {commitment['id']} 尚未验证兑现：{commitment['statement']}"
                        f"（期限 {commitment['due']}）{tail}",
                    }
                )

        for evidence in event["evidence"]:
            if evidence["state"] == "submitted":
                approvals = sum(1 for r in evidence["reviews"] if r["decision"] == "approved")
                risks.append(
                    {
                        "code": f"EVIDENCE_PENDING_{evidence['id']}",
                        "severity": "medium",
                        "text": f"证据 {evidence['id']} 审签未完结（已 {approvals}/2 签）：{evidence['title']}",
                    }
                )
            elif evidence["state"] == "rejected" and any(
                evidence["id"] in t["evidence_ids"] for t in event["tasks"]
            ):
                risks.append(
                    {
                        "code": f"EVIDENCE_REJECTED_USED_{evidence['id']}",
                        "severity": "high",
                        "text": f"被否决证据 {evidence['id']} 仍被整改结果引用：{evidence['title']}",
                    }
                )
        return risks

    # ------------------------------------------------------------------ 复发

    def link_recurrence(
        self,
        event_id: str,
        prior_event_id: str,
        *,
        reason: str,
        by: str,
        at: str | datetime,
    ) -> None:
        """人工确认当前事件与已关闭历史事件构成复发（同规则模块再度失效）。

        自动监测只能在线索里给出疑似事件编号，不得自动建立复发关联。
        """
        event = self._get_event(event_id)
        prior = self._get_event(prior_event_id)
        _require(event_id != prior_event_id, "不能与自身建立复发关联")
        _require(prior["status"] == "closed", "只能与已关闭的历史事件确认复发关联")
        _require(event["snapshot"] is not None and prior["snapshot"] is not None, "双方事件均须完成事实封存")
        _require(reason and by, "复发关联需要人工理由与责任人")
        current_codes = {v["code"] for v in event["snapshot"]["rule_versions"]}
        prior_codes = {v["code"] for v in prior["snapshot"]["rule_versions"]}
        shared = sorted(current_codes & prior_codes)
        _require(shared, "两事件规则模块无重合，不能仅凭时间相近认定复发")
        _require(not any(link["event_id"] == prior_event_id for link in event["recurrence"]["prior"]), "复发关联已存在")
        link = {
            "event_id": prior_event_id,
            "prior_title": prior["title"],
            "shared_rule_codes": shared,
            "current_versions": sorted(
                {_rule_code(v) for v in event["snapshot"]["rule_versions"] if v["code"] in shared}
            ),
            "prior_versions": sorted(
                {_rule_code(v) for v in prior["snapshot"]["rule_versions"] if v["code"] in shared}
            ),
            "reason": reason,
            "by": by,
            "at": _dt(at).isoformat(),
        }
        event["recurrence"]["prior"].append(link)
        prior["recurrence"]["followups"].append(
            {
                "event_id": event_id,
                "title": event["title"],
                "shared_rule_codes": shared,
                "reason": reason,
                "by": by,
                "at": _dt(at).isoformat(),
            }
        )
        self._log(event, at, by, "link_recurrence", f"确认与历史事件 {prior_event_id} 构成复发（共同规则模块 {'、'.join(shared)}）")

    # ------------------------------------------------------------------ 摘要 / 对账

    def compare_commitments(self, event_id: str, *, on: str | datetime | None = None) -> list[dict[str, Any]]:
        """监管视角：逐条比对平台承诺与实际完成（验证结论 + 批准证据 + 期限）。"""
        event = self._get_event(event_id)
        on_dt = _dt(on) if on else None
        rows = []
        for commitment in event["commitments"]:
            evidence_rows = []
            for eid in commitment["evidence_ids"]:
                evidence = self._get_evidence(event, eid)
                evidence_rows.append(
                    {
                        "evidence_id": evidence["id"],
                        "title": evidence["title"],
                        "state": evidence["state"],
                        "controlled": evidence["controlled"],
                    }
                )
            if commitment["status"] == "verified":
                verdict = "逾期兑现" if _dt(commitment["verified_at"]) > _dt(commitment["due"]) else "已兑现"
            elif commitment["status"] == "failed":
                verdict = "验证未通过"
            elif on_dt and _dt(commitment["due"]) < on_dt:
                verdict = "逾期未兑现"
            else:
                verdict = "待验证（整改期内）"
            rows.append(
                {
                    "commitment_id": commitment["id"],
                    "承诺事项": commitment["statement"],
                    "责任团队": commitment["owner_team"],
                    "承诺期限": commitment["due"],
                    "验证状态": commitment["status"],
                    "验证时间": commitment["verified_at"],
                    "验证说明": commitment["verify_note"],
                    "佐证证据": evidence_rows,
                    "对账结论": verdict,
                }
            )
        return rows

    def regulatory_summary(
        self,
        event_id: str,
        *,
        on: str | datetime,
        within_days: int = 3,
    ) -> dict[str, Any]:
        """生成监管摘要。

        约束：未解决风险必须原样列示，结论不得与之矛盾；商业秘密受控附件
        只出现编号与标题，不携带位置、哈希或正文。
        """
        event = self._get_event(event_id)
        risks = self.unresolved_risks(event_id, on=on)
        snapshot = event["snapshot"]
        evidence_view = []
        for evidence in event["evidence"]:
            if evidence["controlled"]:
                evidence_view.append(
                    {
                        "evidence_id": evidence["id"],
                        "title": evidence["title"],
                        "state": evidence["state"],
                        "controlled": True,
                        "access": "受控附件，凭受控通道单独调阅，不随本摘要分发",
                    }
                )
            else:
                evidence_view.append(
                    {
                        "evidence_id": evidence["id"],
                        "title": evidence["title"],
                        "kind": evidence["kind"],
                        "state": evidence["state"],
                        "controlled": False,
                        "approvals": sum(1 for r in evidence["reviews"] if r["decision"] == "approved"),
                    }
                )

        summary: dict[str, Any] = {
            "生成时间": _dt(on).isoformat(),
            "事件基本情况": {
                "event_id": event["id"],
                "title": event["title"],
                "level": LEVELS[event["level"]]["label"],
                "status": event["status"],
                "守门人领域": event["gatekeeper_domains"],
                "首次发现时间": event["first_observed_at"],
                "报送时间": event["filed_at"],
                "报送时限": event["filing_deadline"],
                "报送及时性": "按时" if event["filing_timeliness"] == "on_time" else "迟报",
                "涉及团队": event["teams"],
            },
            "封存事实": (
                {
                    "影响时间窗": [snapshot["window_start"], snapshot["window_end"]],
                    "规则版本": [_rule_code(v) for v in snapshot["rule_versions"]],
                    "受影响主体": snapshot["affected_subjects"],
                    "临时措施": snapshot["interim_measures"],
                    "封存修正次数": len(event["snapshot_corrections"]),
                }
                if snapshot
                else "尚未封存"
            ),
            "归并上报": [
                {
                    "report_id": rid,
                    "team": self.reports[rid]["team"],
                    "title": self.reports[rid]["title"],
                    "source": self.reports[rid]["source"],
                }
                for rid in event["reports"]
            ],
            "整改任务": [
                {
                    "task_id": t["id"],
                    "类型": TASK_CODES[t["code"]],
                    "title": t["title"],
                    "责任团队": t["owner_team"],
                    "status": t["status"],
                    "期限": t["due"],
                    "复测结论": (
                        ("通过" if t["retest_results"][-1]["passed"] else "未通过")
                        if t["retest_results"]
                        else None
                    ),
                }
                for t in event["tasks"]
            ],
            "承诺兑现对照": self.compare_commitments(event_id, on=on),
            "证据审签": evidence_view,
            "复发关联": [
                {
                    "prior_event_id": link["event_id"],
                    "prior_title": link["prior_title"],
                    "shared_rule_codes": link["shared_rule_codes"],
                    "版本变化": {"历史版本": link["prior_versions"], "本次版本": link["current_versions"]},
                    "确认理由": link["reason"],
                }
                for link in event["recurrence"]["prior"]
            ],
            "期限提醒": self.due_reminders(on=on, within_days=within_days),
            "未解决风险": risks,
        }

        high_count = sum(1 for risk in risks if risk["severity"] == "high")
        if risks:
            summary["摘要结论"] = (
                f"截至{_dt(on).date()}，本事件仍存在 {len(risks)} 项未解决风险"
                f"（其中高风险 {high_count} 项），整改尚未完成；"
                "本摘要不得作为整改完成或风险已消除的依据。"
            )
        else:
            summary["摘要结论"] = (
                f"截至{_dt(on).date()}，封存要素齐备，四类任务全部完成且复测通过，"
                "整改承诺均经证据验证，可提交关闭审议。"
            )
        return summary

    # ------------------------------------------------------------------ 关闭

    def request_closure(self, event_id: str, *, by: str, at: str | datetime) -> None:
        """关闭闸门：任务/证据/承诺/复测/风险全部清零，且须有人工责任人。"""
        event = self._get_event(event_id)
        self._ensure_open(event)
        _require(by, "关闭必须有责任人")
        problems: list[str] = []

        if event["snapshot"] is None:
            problems.append("事实尚未封存")
        existing = {t["code"]: t for t in event["tasks"]}
        for code, label in TASK_CODES.items():
            task = existing.get(code)
            if task is None:
                problems.append(f"{label}未分派")
            elif task["status"] != "done":
                problems.append(f"{label}未办结")
        retest = existing.get("retest")
        if retest and retest["status"] == "done" and retest["retest_results"] and not retest["retest_results"][-1]["passed"]:
            problems.append("复测未通过")
        for commitment in event["commitments"]:
            if commitment["status"] != "verified":
                problems.append(f"承诺 {commitment['id']} 未验证兑现")
        for evidence in event["evidence"]:
            if evidence["state"] == "submitted":
                problems.append(f"证据 {evidence['id']} 审签未完结")
            if evidence["state"] == "rejected" and any(evidence["id"] in t["evidence_ids"] for t in event["tasks"]):
                problems.append(f"被否决证据 {evidence['id']} 仍被引用")
        risks = self.unresolved_risks(event_id, on=at)
        if risks:
            problems.append(f"存在 {len(risks)} 项未解决风险")
        _require(not problems, "关闭闸门未通过：" + "；".join(problems))

        event["status"] = "closed"
        event["closed_at"] = _dt(at).isoformat()
        self._log(event, at, by, "close_event", "通过关闭闸门：任务、证据、承诺与复测全部完成")

    # ------------------------------------------------------------------ 存取

    def to_dict(self, *, as_of: str | None = None) -> dict[str, Any]:
        return {
            "domain": "platform-gatekeeper-reporting",
            "version": 2,
            "as_of": as_of or datetime.now().date().isoformat(),
            "events": [self.events[k] for k in sorted(self.events)],
            "reports": [self.reports[k] for k in sorted(self.reports)],
            "signals": [self.signals[k] for k in sorted(self.signals)],
        }

    def save_case(self, path: str | Path, *, as_of: str | None = None) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(as_of=as_of), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReportingService":
        for key in ("events", "reports", "signals"):
            _require(isinstance(data.get(key), list), f"样例缺少列表字段：{key}")
        service = cls()
        for event in data["events"]:
            service.events[event["id"]] = event
        for report in data["reports"]:
            service.reports[report["id"]] = report
        for signal in data["signals"]:
            service.signals[signal["id"]] = signal
        return service

    # ------------------------------------------------------------------ 内部

    def _get_event(self, event_id: str) -> dict[str, Any]:
        event = self.events.get(event_id)
        _require(event is not None, f"事件不存在：{event_id}")
        return event  # type: ignore[return-value]

    def _get_report(self, report_id: str) -> dict[str, Any]:
        report = self.reports.get(report_id)
        _require(report is not None, f"上报不存在：{report_id}")
        return report  # type: ignore[return-value]

    def _get_signal(self, signal_id: str) -> dict[str, Any]:
        signal = self.signals.get(signal_id)
        _require(signal is not None, f"监测线索不存在：{signal_id}")
        return signal  # type: ignore[return-value]

    @staticmethod
    def _get_task(event: dict[str, Any], task_id: str) -> dict[str, Any]:
        task = next((t for t in event["tasks"] if t["id"] == task_id), None)
        _require(task is not None, f"任务不存在：{task_id}")
        return task  # type: ignore[return-value]

    @staticmethod
    def _get_evidence(event: dict[str, Any], evidence_id: str) -> dict[str, Any]:
        evidence = next((e for e in event["evidence"] if e["id"] == evidence_id), None)
        _require(evidence is not None, f"证据不存在：{evidence_id}")
        return evidence  # type: ignore[return-value]

    @staticmethod
    def _get_commitment(event: dict[str, Any], commitment_id: str) -> dict[str, Any]:
        commitment = next((c for c in event["commitments"] if c["id"] == commitment_id), None)
        _require(commitment is not None, f"承诺不存在：{commitment_id}")
        return commitment  # type: ignore[return-value]

    @staticmethod
    def _ensure_open(event: dict[str, Any]) -> None:
        _require(event["status"] != "closed", f"事件已关闭，禁止变更：{event['id']}")

    @staticmethod
    def _log(event: dict[str, Any], at: Any, by: str, action: str, detail: str) -> None:
        when = at.isoformat() if isinstance(at, datetime) else str(at)
        event["history"].append({"at": when, "by": by, "action": action, "detail": detail})


def load_case(path: str | Path) -> ReportingService:
    """读取 version >= 2 的整改报告服务样例。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    _require(data.get("domain") == "platform-gatekeeper-reporting", "样例领域标识不匹配")
    _require(data.get("version", 0) >= 2, "样例版本过旧，请使用整改报告服务样例（version >= 2）")
    return ReportingService.from_dict(data)
