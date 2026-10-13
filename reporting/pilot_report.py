"""Reproducible Markdown report for a content-factory pilot."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from db.content_repository import ContentRepository
from domain.errors import NotFoundError


@dataclass(frozen=True)
class PilotReport:
    markdown: str
    decision: str
    sha256: str


class PilotReportBuilder:
    WINDOWS = ("24h", "72h", "7d")

    def __init__(self, repository: ContentRepository):
        self.repository = repository

    def build(self, pilot_id: str) -> PilotReport:
        pilot = self.repository.fetchone("SELECT * FROM pilot_passports WHERE id=?", (pilot_id,))
        if pilot is None:
            raise NotFoundError("pilot not found")
        times = self.repository.fetchall(
            "SELECT * FROM time_entries WHERE pilot_id=? ORDER BY started_at,id", (pilot_id,),
        )
        baseline = self.repository.fetchone(
            "SELECT * FROM baselines WHERE pilot_id=? ORDER BY created_at,id LIMIT 1", (pilot_id,),
        )
        jobs = self.repository.fetchall(
            """SELECT pj.* FROM publish_jobs pj JOIN publish_packages pp ON pp.id=pj.package_id
            JOIN content_projects cp ON cp.id=pp.project_id WHERE cp.pilot_id=? ORDER BY pj.id""",
            (pilot_id,),
        )
        snapshots = self.repository.fetchall(
            """SELECT ms.* FROM metric_snapshots ms JOIN publish_jobs pj ON pj.id=ms.publish_job_id
            JOIN publish_packages pp ON pp.id=pj.package_id JOIN content_projects cp ON cp.id=pp.project_id
            WHERE cp.pilot_id=? ORDER BY ms.publish_job_id,ms.window,ms.metric_name""",
            (pilot_id,),
        )
        try:
            thresholds = json.loads(pilot["thresholds_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            thresholds = {}
        required_metrics = thresholds.get("required_metrics", [])
        if (not isinstance(required_metrics, list) or not required_metrics
                or any(not isinstance(name, str) or not name for name in required_metrics)):
            required_metrics = []
        active = sum(row["active_sec"] for row in times)
        labor_known = bool(times) and all(
            row["rate_minor_per_hour"] is not None and row["currency"] is not None
            for row in times
        )
        labor_costs = {}
        for entry in times:
            if entry["rate_minor_per_hour"] is not None and entry["currency"] is not None:
                amount = round(entry["active_sec"] * entry["rate_minor_per_hour"] / 3600)
                labor_costs[entry["currency"]] = labor_costs.get(entry["currency"], 0) + amount
        published = [row for row in jobs if row["status"] == "published"]
        measured = {(row["publish_job_id"], row["window"], row["metric_name"])
                    for row in snapshots if row["is_measured"]}
        missing = [f"{job['id']}:{window}:{metric}" for job in published
                   for window in self.WINDOWS for metric in required_metrics
                   if (job["id"], window, metric) not in measured]
        unresolved_operations = self.repository.fetchall(
            """SELECT o.id FROM external_operations o WHERE o.pilot_id=?
            AND (o.status IN ('submitting','unknown_outcome')
            OR o.billing_status IN ('pending','unknown','partially_charged'))""", (pilot_id,),
        )
        unresolved_reservations = self.repository.fetchall(
            """SELECT id FROM budget_reservations WHERE pilot_id=?
            AND status IN ('held','partially_settled','unknown','overspent')""", (pilot_id,),
        )
        unknown = (any(row["status"] == "unknown_outcome" for row in jobs)
                   or bool(unresolved_operations) or bool(unresolved_reservations))
        cost_rows = self.repository.fetchall(
            """SELECT ce.kind,ce.amount_minor,ce.currency FROM cost_entries ce
            JOIN external_operations o ON o.id=ce.operation_id WHERE o.pilot_id=?
            ORDER BY ce.occurred_at,ce.id""", (pilot_id,),
        )
        provider_costs = {}
        for cost in cost_rows:
            signed = -cost["amount_minor"] if cost["kind"] == "refund" else cost["amount_minor"]
            provider_costs[cost["currency"]] = provider_costs.get(cost["currency"], 0) + signed
        reduction = None
        if baseline and baseline["duration_sec"]:
            reduction = 1 - active / baseline["duration_sec"]
        sufficient = (bool(times) and active > 0 and labor_known and bool(required_metrics)
                      and len(published) >= pilot["target_count"] and not missing
                      and not unknown and baseline is not None)
        success = sufficient and reduction is not None and reduction >= 0.20
        decision = "insufficient_data" if not sufficient else ("success" if success else "failure")
        metric_lines = [
            f"- {row['publish_job_id']} {row['window']} {row['metric_name']}: "
            + (str(row["value_numeric"]) if row["is_measured"] else "нет данных")
            for row in snapshots
        ] or ["- Метрики не собраны"]
        lines = [
            f"# Отчёт пилота {pilot_id}", "", "## Производство", "",
            f"- Активное время: {active} сек.",
            f"- Базовое время: {baseline['duration_sec'] if baseline else 'нет данных'} сек.",
            f"- Снижение времени: {round(reduction * 100, 2) if reduction is not None else 'нет данных'}%.",
            "- Трудозатраты: " + (
                ", ".join(f"{amount} {currency} minor units"
                          for currency, amount in sorted(labor_costs.items()))
                if labor_costs else "нет данных"
            ) + f" ({'полные' if labor_known else 'частичные'}).",
            "- Внешние затраты: " + (
                ", ".join(f"{amount} {currency} minor units"
                          for currency, amount in sorted(provider_costs.items()))
                if provider_costs else "нет списаний"
            ) + ".",
            "", "## Метрики", "", *metric_lines, "",
            f"- Отсутствующие окна: {', '.join(missing) if missing else 'нет'}.",
            f"- Опубликовано: {len(published)} из {pilot['target_count']}.",
            f"- Неопределённые внешние операции: {'да' if unknown else 'нет'}.",
            "", "## Решение", "", f"**{decision}**", "",
        ]
        markdown = "\n".join(lines)
        return PilotReport(markdown, decision, hashlib.sha256(markdown.encode()).hexdigest())
