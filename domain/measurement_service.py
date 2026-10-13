"""Append-only capture of labor, metrics and CTA leads."""

from __future__ import annotations

from db.content_repository import ContentRepository
from domain.errors import ValidationError


class MeasurementService:
    def __init__(self, repository: ContentRepository):
        self.repository = repository

    def add_time(self, *, entry_id, pilot_id, project_id, actor_id, role, stage,
                 started_at, stopped_at, active_sec, created_at,
                 rate_minor_per_hour=None, currency=None):
        if active_sec < 0 or (rate_minor_per_hour is not None and not currency):
            raise ValidationError("invalid labor entry")
        with self.repository.transaction() as connection:
            connection.execute(
                """INSERT INTO time_entries(id,pilot_id,project_id,actor_id,role,stage,
                started_at,stopped_at,active_sec,rate_minor_per_hour,currency,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (entry_id, pilot_id, project_id, actor_id, role, stage, started_at,
                 stopped_at, active_sec, rate_minor_per_hour, currency, created_at),
            )

    def add_metric(self, *, snapshot_id, publish_job_id, window, metric_name,
                   value, measured, definition, source, captured_at, created_at,
                   evidence_ref=None):
        if measured and value is None:
            raise ValidationError("measured metric requires a value")
        if not measured and value is not None:
            raise ValidationError("missing metric cannot carry a value")
        with self.repository.transaction() as connection:
            connection.execute(
                """INSERT INTO metric_snapshots(id,publish_job_id,window,metric_name,
                value_numeric,is_measured,definition,source,evidence_ref,captured_at,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (snapshot_id, publish_job_id, window, metric_name, value,
                 int(measured), definition, source, evidence_ref, captured_at, created_at),
            )

    def add_lead(self, *, lead_id, pilot_id, publish_job_id, cta_token,
                 contact_hash, occurred_at, created_at, qualification="unknown"):
        with self.repository.transaction() as connection:
            connection.execute(
                """INSERT INTO leads(id,pilot_id,publish_job_id,cta_token,contact_hash,
                qualification,occurred_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (lead_id, pilot_id, publish_job_id, cta_token, contact_hash,
                 qualification, occurred_at, created_at, created_at),
            )
