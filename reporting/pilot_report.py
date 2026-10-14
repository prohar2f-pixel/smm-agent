"""Reproducible Markdown report for a content-factory pilot."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from statistics import median

from db.content_repository import ContentRepository
from domain.errors import NotFoundError
from domain.package_hash import canonical_package_hash
from domain.time_utils import parse_instant


@dataclass(frozen=True)
class PilotReport:
    markdown: str
    decision: str
    sha256: str


class PilotReportBuilder:
    WINDOWS = ("24h", "72h", "7d")
    WINDOW_DELTA = {"24h": timedelta(hours=24), "72h": timedelta(hours=72),
                    "7d": timedelta(days=7)}
    REQUIRED_QA = {
        "source_traceable", "rights_allowed", "privacy_clear",
        "claims_supported_or_labeled", "script_approved", "duration_in_profile",
        "media_readable", "profile_matches", "subtitles_match_voice",
        "text_legible_safe_zones", "audio_clear", "cta_single_and_working",
        "no_critical_artifacts", "package_hash_verified",
    }

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
        published_by_id = {row["id"]: row for row in published}
        measured = set()
        for snapshot in snapshots:
            job = published_by_id.get(snapshot["publish_job_id"])
            if not job or not snapshot["is_measured"] or snapshot["window"] not in self.WINDOW_DELTA:
                continue
            captured = parse_instant(snapshot["captured_at"], "captured_at")
            published_at = parse_instant(job["updated_at"], "published_at")
            if captured >= published_at + self.WINDOW_DELTA[snapshot["window"]]:
                measured.add((snapshot["publish_job_id"], snapshot["window"],
                              snapshot["metric_name"]))
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
        accepted_packages = []
        for package_id in sorted({row["package_id"] for row in published}):
            package = self.repository.fetchone(
                """SELECT id,project_id,revision_id,package_sha256,caption_text,assets_json
                FROM publish_packages WHERE id=?""",
                (package_id,),
            )
            if package["package_sha256"] != canonical_package_hash(
                    package["caption_text"], package["assets_json"]):
                continue
            try:
                asset_ids = json.loads(package["assets_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(asset_ids, list) or not asset_ids:
                continue
            placeholders = ",".join("?" for _ in asset_ids)
            masters = self.repository.fetchall(
                f"""SELECT id,sha256,duration_ms FROM assets WHERE id IN ({placeholders})
                AND project_id=? AND revision_id=? AND role='master'
                AND duration_ms BETWEEN 30000 AND 45000""",
                (*asset_ids, package["project_id"], package["revision_id"]),
            )
            final = self.repository.fetchone(
                """SELECT status,subject_sha256 FROM approval_decisions
                WHERE project_id=? AND revision_id=? AND stage='final'
                ORDER BY created_at DESC,id DESC LIMIT 1""",
                (package["project_id"], package["revision_id"]),
            )
            approved_master = next(
                (asset for asset in masters if final and final["status"] == "approved"
                 and asset["sha256"] == final["subject_sha256"]), None,
            )
            if approved_master is None:
                continue
            qa = self.repository.fetchall(
                """SELECT check_code,status FROM qa_results WHERE project_id=?
                AND revision_id=? AND asset_id=?""",
                (package["project_id"], package["revision_id"], approved_master["id"]),
            )
            passed_codes = {row["check_code"] for row in qa if row["status"] == "pass"}
            failed_codes = {row["check_code"] for row in qa if row["status"] == "fail"}
            if (self.REQUIRED_QA.issubset(passed_codes)
                    and not self.REQUIRED_QA.intersection(failed_codes)):
                accepted_packages.append(package)
        source_ids = set()
        for package in accepted_packages:
            rows = self.repository.fetchall(
                "SELECT source_id FROM content_project_sources WHERE project_id=?",
                (package["project_id"],),
            )
            source_ids.update(row["source_id"] for row in rows)
        project_times = {}
        for entry in times:
            if entry["project_id"]:
                project_times[entry["project_id"]] = (
                    project_times.get(entry["project_id"], 0) + entry["active_sec"]
                )
        pilot_samples = [project_times[package["project_id"]]
                         for package in accepted_packages
                         if package["project_id"] in project_times]
        baselines = self.repository.fetchall(
            """SELECT duration_sec FROM baselines WHERE pilot_id=?
            AND comparable_format='screen_demo' AND duration_sec IS NOT NULL
            AND duration_sec>0 ORDER BY created_at,id""", (pilot_id,),
        )
        median_pilot = median(pilot_samples) if pilot_samples else None
        median_baseline = median([row["duration_sec"] for row in baselines]) if baselines else None
        reduction = None
        if median_baseline and median_pilot is not None:
            reduction = 1 - median_pilot / median_baseline
        qa_complete = len(accepted_packages) >= pilot["target_count"]
        distinct_sources = len(source_ids) >= pilot["target_count"]
        sufficient = (bool(times) and active > 0 and labor_known and bool(required_metrics)
                      and qa_complete and distinct_sources and not missing
                      and not unknown and median_baseline is not None
                      and len(pilot_samples) >= pilot["target_count"])
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
            f"- Медиана времени пилота: {median_pilot if median_pilot is not None else 'нет данных'} сек.",
            f"- Медиана baseline: {median_baseline if median_baseline is not None else 'нет данных'} сек.",
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
            f"- Опубликовано platform jobs: {len(published)}.",
            f"- Принято пакетов: {len(accepted_packages)} из {pilot['target_count']}.",
            f"- Разных исходников: {len(source_ids)} из {pilot['target_count']}.",
            f"- Неопределённые внешние операции: {'да' if unknown else 'нет'}.",
            "", "## Решение", "", f"**{decision}**", "",
        ]
        markdown = "\n".join(lines)
        return PilotReport(markdown, decision, hashlib.sha256(markdown.encode()).hexdigest())
