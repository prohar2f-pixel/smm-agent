"""Reproducible Markdown report for a content-factory pilot."""

from __future__ import annotations

import hashlib
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
        active = sum(row["active_sec"] for row in times)
        labor_known = all(row["rate_minor_per_hour"] is not None for row in times)
        labor_cost = sum(round(row["active_sec"] * row["rate_minor_per_hour"] / 3600)
                         for row in times if row["rate_minor_per_hour"] is not None)
        seen_windows = {row["window"] for row in snapshots if row["is_measured"]}
        missing = [window for window in self.WINDOWS if window not in seen_windows]
        unknown = any(row["status"] == "unknown_outcome" for row in jobs)
        reduction = None
        if baseline and baseline["duration_sec"]:
            reduction = 1 - active / baseline["duration_sec"]
        sufficient = bool(jobs) and not missing and not unknown and baseline is not None
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
            f"- Трудозатраты: {labor_cost} minor units ({'полные' if labor_known else 'частичные'}).",
            "", "## Метрики", "", *metric_lines, "",
            f"- Отсутствующие окна: {', '.join(missing) if missing else 'нет'}.",
            f"- Неопределённые внешние операции: {'да' if unknown else 'нет'}.",
            "", "## Решение", "", f"**{decision}**", "",
        ]
        markdown = "\n".join(lines)
        return PilotReport(markdown, decision, hashlib.sha256(markdown.encode()).hexdigest())
