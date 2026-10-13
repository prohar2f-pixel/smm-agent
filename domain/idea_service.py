"""Idea capture and explainable scoring."""

from __future__ import annotations

import json

from db.content_repository import ContentRepository
from domain.errors import NotFoundError, ValidationError


class IdeaService:
    DIMENSIONS = ("relevance", "proof", "novelty", "fit", "effort")

    def __init__(self, repository: ContentRepository):
        self.repository = repository

    def capture(self, *, idea_id, pilot_id, source_id, angle, role,
                proof_summary, format, created_at):
        with self.repository.transaction() as connection:
            source = connection.execute(
                "SELECT * FROM sources WHERE id=? AND pilot_id=?", (source_id, pilot_id),
            ).fetchone()
            if source is None:
                raise NotFoundError("idea source not found")
            if source["rights_status"] != "allowed" or source["privacy_status"] != "clear":
                raise ValidationError("idea source is not usable")
            connection.execute(
                """INSERT INTO idea_cards(id,pilot_id,source_id,angle,role,proof_summary,
                format,decision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'captured',?,?)""",
                (idea_id, pilot_id, source_id, angle, role, proof_summary, format,
                 created_at, created_at),
            )

    def score(self, idea_id: str, scores: dict, *, rubric_version: str,
              evidence: dict, at: str):
        if set(scores) != set(self.DIMENSIONS) or any(
            not isinstance(value, int) or not 0 <= value <= 3 for value in scores.values()
        ):
            raise ValidationError("idea score must contain five 0-3 dimensions")
        provenance = {"scores": scores, "evidence": evidence}
        with self.repository.transaction() as connection:
            cursor = connection.execute(
                """UPDATE idea_cards SET relevance_score=?,proof_score=?,novelty_score=?,
                fit_score=?,effort_score=?,total_score=?,decision='scored',scoring_json=?,
                scoring_version=?,updated_at=?,row_version=row_version+1 WHERE id=?""",
                (scores["relevance"], scores["proof"], scores["novelty"], scores["fit"],
                 scores["effort"], sum(scores.values()), json.dumps(provenance, sort_keys=True),
                 rubric_version, at, idea_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("idea not found")
            return connection.execute("SELECT * FROM idea_cards WHERE id=?", (idea_id,)).fetchone()
