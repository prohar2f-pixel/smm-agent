"""Comparable-only analytics that separates facts from hypotheses."""

from __future__ import annotations

import json
from dataclasses import dataclass

from db.content_repository import ContentRepository
from domain.errors import ValidationError


@dataclass(frozen=True)
class Comparison:
    winner_job_id: str | None
    kind: str
    recommendation: str
    values: dict[str, float]


class AnalyticsService:
    def __init__(self, repository: ContentRepository):
        self.repository = repository

    def compare(self, *, learning_id: str, pilot_id: str, candidates: list[dict],
                created_by: str, created_at: str, minimum_uplift: float = 0.1) -> Comparison:
        if len(candidates) < 2:
            raise ValidationError("comparison requires at least two candidates")
        keys = {(item["platform"], item["format"], item["window"], item["metric_name"])
                for item in candidates}
        if len(keys) != 1:
            raise ValidationError("analytics candidates are not comparable")
        values = {}
        for item in candidates:
            row = self.repository.fetchone(
                """SELECT ms.* FROM metric_snapshots ms JOIN publish_jobs pj
                ON pj.id=ms.publish_job_id JOIN publish_packages pp ON pp.id=pj.package_id
                JOIN content_projects cp ON cp.id=pp.project_id
                WHERE ms.publish_job_id=? AND ms.window=? AND ms.metric_name=?
                AND cp.pilot_id=? AND pj.platform=?""",
                (item["publish_job_id"], item["window"], item["metric_name"],
                 pilot_id, item["platform"]),
            )
            if row is None or not row["is_measured"]:
                comparison = Comparison(None, "hypothesis", "test_next", values)
                return self._record(learning_id, pilot_id, comparison, created_by, created_at)
            values[item["publish_job_id"]] = float(row["value_numeric"])
        ordered = sorted(values.items(), key=lambda pair: (-pair[1], pair[0]))
        winner, best = ordered[0]
        runner_up = ordered[1][1]
        uplift = (best - runner_up) / max(abs(runner_up), 1.0)
        if uplift < minimum_uplift:
            comparison = Comparison(None, "fact", "test_next", values)
        else:
            comparison = Comparison(winner, "fact", "repeat", values)
        return self._record(learning_id, pilot_id, comparison, created_by, created_at)

    def _record(self, learning_id, pilot_id, comparison, created_by, created_at):
        statement = (f"winner={comparison.winner_job_id}" if comparison.winner_job_id
                     else "no reliable winner")
        with self.repository.transaction() as connection:
            connection.execute(
                """INSERT INTO learnings(id,pilot_id,kind,statement,action,supporting_json,
                created_by,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                (learning_id, pilot_id, comparison.kind, statement,
                 comparison.recommendation, json.dumps(comparison.values, sort_keys=True),
                 created_by, created_at),
            )
        return comparison
