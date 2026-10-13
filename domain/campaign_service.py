"""Capacity-aware 14-day campaign planning."""

from __future__ import annotations

from datetime import date

from db.content_repository import ContentRepository
from domain.errors import ValidationError


class CampaignService:
    def __init__(self, repository: ContentRepository):
        self.repository = repository

    def create(self, *, campaign_id, pilot_id, name, goal, start_day, end_day,
               capacity, formats_json, created_at):
        span = (date.fromisoformat(end_day) - date.fromisoformat(start_day)).days + 1
        if not 1 <= span <= 14:
            raise ValidationError("campaign window must be 1-14 days")
        with self.repository.transaction() as connection:
            connection.execute(
                """INSERT INTO campaigns(id,pilot_id,name,goal,start_day,end_day,capacity,
                formats_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'draft',?,?)""",
                (campaign_id, pilot_id, name, goal, start_day, end_day, capacity,
                 formats_json, created_at, created_at),
            )

    def add_slot(self, *, slot_id, campaign_id, publish_day, slot_no,
                 idea_id, platform, created_at):
        with self.repository.transaction() as connection:
            campaign = connection.execute(
                "SELECT * FROM campaigns WHERE id=?", (campaign_id,),
            ).fetchone()
            if campaign is None or not campaign["start_day"] <= publish_day <= campaign["end_day"]:
                raise ValidationError("slot is outside campaign")
            count = connection.execute(
                "SELECT COUNT(*) FROM campaign_slots WHERE campaign_id=?", (campaign_id,),
            ).fetchone()[0]
            if count >= campaign["capacity"]:
                raise ValidationError("campaign capacity exceeded")
            connection.execute(
                """INSERT INTO campaign_slots(id,campaign_id,publish_day,slot_no,idea_id,
                platform,created_at) VALUES(?,?,?,?,?,?,?)""",
                (slot_id, campaign_id, publish_day, slot_no, idea_id, platform, created_at),
            )
