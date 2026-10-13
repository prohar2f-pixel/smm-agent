"""Immutable cost evidence and reservation settlement."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from db.content_repository import ContentRepository
from domain.errors import ConflictError, NotFoundError, ValidationError


class ReconciliationRequiredError(ValidationError):
    pass


@dataclass(frozen=True)
class SettlementResult:
    reservation_id: str
    committed_minor: int
    held_minor: int
    status: str


class CostService:
    def __init__(self, repository: ContentRepository):
        self.repository = repository

    @staticmethod
    def round_minor(value) -> int:
        return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def record_charge(self, entry_id, operation_id, reservation_id, provider,
                      evidence_id, amount_minor, currency, occurred_at,
                      *, final=True) -> SettlementResult:
        return self._record_cost(
            entry_id, operation_id, reservation_id, provider, evidence_id,
            "charge", amount_minor, currency, occurred_at, final,
        )

    def record_refund(self, entry_id, operation_id, reservation_id, provider,
                      evidence_id, amount_minor, currency, occurred_at) -> SettlementResult:
        if not isinstance(amount_minor, int) or amount_minor <= 0:
            raise ValidationError("refund must be positive minor units")
        with self.repository.transaction() as connection:
            duplicate = self._duplicate(connection, provider, evidence_id)
            if duplicate:
                self._assert_same(duplicate, operation_id, reservation_id, "refund", amount_minor, currency)
                return self._result(connection, reservation_id)
            reservation = self._reservation(connection, reservation_id)
            self._assert_tuple(connection, reservation, operation_id, provider, currency)
            committed = self._net_committed(connection, reservation_id)
            if amount_minor > committed:
                raise ValidationError("refund exceeds confirmed charges")
            self._insert_cost(connection, entry_id, operation_id, reservation_id,
                              provider, evidence_id, "refund", amount_minor,
                              currency, occurred_at)
            net = committed - amount_minor
            connection.execute(
                """UPDATE budget_reservations SET settled_minor=?,updated_at=?,
                row_version=row_version+1 WHERE id=?""", (net, occurred_at, reservation_id),
            )
            connection.execute(
                """UPDATE external_operations SET billing_status=?,updated_at=?,row_version=row_version+1
                WHERE id=?""", ("refunded" if net == 0 else "partially_refunded", occurred_at, operation_id),
            )
            return self._result(connection, reservation_id)

    def _record_cost(self, entry_id, operation_id, reservation_id, provider,
                     evidence_id, kind, amount_minor, currency, occurred_at,
                     final) -> SettlementResult:
        if not isinstance(amount_minor, int) or amount_minor <= 0:
            raise ValidationError("charge must be positive minor units")
        with self.repository.transaction() as connection:
            duplicate = self._duplicate(connection, provider, evidence_id)
            if duplicate:
                self._assert_same(duplicate, operation_id, reservation_id, kind, amount_minor, currency)
                return self._result(connection, reservation_id)
            reservation = self._reservation(connection, reservation_id)
            self._assert_tuple(connection, reservation, operation_id, provider, currency)
            self._insert_cost(connection, entry_id, operation_id, reservation_id,
                              provider, evidence_id, kind, amount_minor, currency,
                              occurred_at)
            committed = self._net_committed(connection, reservation_id)
            contradictory = reservation["status"] in {"settled", "released", "overspent"}
            overspent = committed > reservation["upper_bound_minor"] or contradictory
            if overspent:
                held, status = 0, "overspent"
                connection.execute(
                    """INSERT OR IGNORE INTO budget_incidents(
                    id,pilot_id,project_id,operation_id,kind,status,details_json,created_at)
                    VALUES(?,?,?,?,?,'open',?,?)""",
                    (f"incident:{operation_id}:{evidence_id}", reservation["pilot_id"],
                     reservation["project_id"], operation_id,
                     "contradictory_evidence" if contradictory else "overspend",
                     json.dumps({"upper": reservation["upper_bound_minor"], "charged": committed},
                                sort_keys=True, separators=(",", ":")), occurred_at),
                )
            elif final:
                held, status = 0, "settled"
            else:
                held = max(reservation["upper_bound_minor"] - committed, 0)
                status = "partially_settled"
            connection.execute(
                """UPDATE budget_reservations SET held_minor=?,settled_minor=?,status=?,
                updated_at=?,row_version=row_version+1 WHERE id=?""",
                (held, committed, status, occurred_at, reservation_id),
            )
            connection.execute(
                """UPDATE external_operations SET status=?,billing_status=?,updated_at=?,
                row_version=row_version+1 WHERE id=?""",
                ("succeeded" if final else "submitting",
                 "charged" if final else "partially_charged", occurred_at, operation_id),
            )
            return self._result(connection, reservation_id)

    def mark_unknown(self, operation_id, at) -> SettlementResult:
        with self.repository.transaction() as connection:
            reservation = self._by_operation(connection, operation_id)
            connection.execute(
                """UPDATE external_operations SET status='unknown_outcome',billing_status='unknown',
                updated_at=?,row_version=row_version+1 WHERE id=?""", (at, operation_id),
            )
            connection.execute(
                """UPDATE budget_reservations SET status='unknown',updated_at=?,
                row_version=row_version+1 WHERE id=?""", (at, reservation["id"]),
            )
            return self._result(connection, reservation["id"])

    def finalize_charges(self, operation_id, reservation_id, at) -> SettlementResult:
        with self.repository.transaction() as connection:
            reservation = self._reservation(connection, reservation_id)
            if reservation["operation_id"] != operation_id:
                raise ValidationError("operation and reservation mismatch")
            committed = self._net_committed(connection, reservation_id)
            if committed <= 0:
                raise ValidationError("no confirmed charge to finalize")
            connection.execute(
                """UPDATE budget_reservations SET status='settled',held_minor=0,
                   settled_minor=?,updated_at=?,row_version=row_version+1 WHERE id=?""",
                (committed, at, reservation_id),
            )
            connection.execute(
                """UPDATE external_operations SET status='succeeded',billing_status='charged',
                   updated_at=?,row_version=row_version+1 WHERE id=?""", (at, operation_id),
            )
            return self._result(connection, reservation_id)

    def mark_no_charge(self, operation_id, evidence_ref, at) -> SettlementResult:
        with self.repository.transaction() as connection:
            operation = connection.execute("SELECT * FROM external_operations WHERE id=?", (operation_id,)).fetchone()
            if operation is None:
                raise NotFoundError(f"operation not found: {operation_id}")
            if operation["status"] == "unknown_outcome":
                raise ReconciliationRequiredError("unknown operation requires reconciliation")
            return self._release(connection, operation_id, evidence_ref, at)

    def reconcile(self, operation_id, outcome, evidence_ref, at) -> SettlementResult:
        if outcome != "no_charge":
            raise ReconciliationRequiredError("unsupported reconciliation outcome")
        contradiction = False
        with self.repository.transaction() as connection:
            reservation = self._by_operation(connection, operation_id)
            if reservation["status"] == "released":
                if reservation["release_evidence_ref"] != evidence_ref:
                    raise ConflictError("reconciliation evidence conflict")
                return self._result(connection, reservation["id"])
            if self._net_committed(connection, reservation["id"]):
                contradiction = True
                connection.execute(
                    """INSERT OR IGNORE INTO budget_incidents(
                    id,pilot_id,project_id,operation_id,kind,status,details_json,created_at)
                    VALUES(?,?,?,?,?,'open',?,?)""",
                    (f"incident:no-charge:{operation_id}", reservation["pilot_id"],
                     reservation["project_id"], operation_id, "contradictory_evidence",
                     json.dumps({"evidence_ref": evidence_ref}, sort_keys=True), at),
                )
            else:
                return self._release(connection, operation_id, evidence_ref, at)
        if contradiction:
            raise ReconciliationRequiredError("no-charge evidence contradicts confirmed cost")

    def _release(self, connection, operation_id, evidence_ref, at):
        if not evidence_ref or not evidence_ref.strip():
            raise ValidationError("release evidence is required")
        reservation = self._by_operation(connection, operation_id)
        if self._net_committed(connection, reservation["id"]):
            raise ReconciliationRequiredError("cannot release reservation with confirmed cost")
        connection.execute(
            """UPDATE external_operations SET status='reconciled',billing_status='no_charge',
            updated_at=?,row_version=row_version+1 WHERE id=?""", (at, operation_id),
        )
        connection.execute(
            """UPDATE budget_reservations SET status='released',held_minor=0,
            release_evidence_ref=?,updated_at=?,row_version=row_version+1 WHERE id=?""",
            (evidence_ref, at, reservation["id"]),
        )
        return self._result(connection, reservation["id"])

    @staticmethod
    def _duplicate(connection, provider, evidence_id):
        return connection.execute(
            "SELECT * FROM cost_entries WHERE provider=? AND evidence_id=?",
            (provider, evidence_id),
        ).fetchone()

    @staticmethod
    def _assert_same(row, operation_id, reservation_id, kind, amount, currency):
        if (row["operation_id"], row["reservation_id"], row["kind"],
                row["amount_minor"], row["currency"]) != (
                operation_id, reservation_id, kind, amount, currency):
            raise ConflictError("provider evidence contradicts existing entry")

    @staticmethod
    def _insert_cost(connection, entry_id, operation_id, reservation_id,
                     provider, evidence_id, kind, amount, currency, at):
        try:
            connection.execute(
                """INSERT INTO cost_entries(id,operation_id,reservation_id,provider,
                evidence_id,kind,amount_minor,currency,occurred_at,evidence_ref,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (entry_id, operation_id, reservation_id, provider, evidence_id,
                 kind, amount, currency, at, evidence_id, at),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("cost entry identity conflict") from exc

    @staticmethod
    def _reservation(connection, reservation_id):
        row = connection.execute("SELECT * FROM budget_reservations WHERE id=?", (reservation_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"reservation not found: {reservation_id}")
        return row

    @staticmethod
    def _assert_tuple(connection, reservation, operation_id, provider, currency):
        operation = connection.execute(
            "SELECT provider_key FROM external_operations WHERE id=?", (operation_id,)
        ).fetchone()
        if (reservation["operation_id"] != operation_id
                or reservation["currency"] != currency
                or operation is None or operation["provider_key"] != provider):
            raise ValidationError("operation, reservation, provider or currency mismatch")

    @staticmethod
    def _by_operation(connection, operation_id):
        row = connection.execute("SELECT * FROM budget_reservations WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"reservation for operation not found: {operation_id}")
        return row

    @staticmethod
    def _net_committed(connection, reservation_id):
        return connection.execute(
            """SELECT COALESCE(SUM(CASE WHEN kind='refund' THEN -amount_minor ELSE amount_minor END),0)
            FROM cost_entries WHERE reservation_id=?""", (reservation_id,),
        ).fetchone()[0]

    @classmethod
    def _result(cls, connection, reservation_id):
        row = cls._reservation(connection, reservation_id)
        return SettlementResult(row["id"], row["settled_minor"], row["held_minor"], row["status"])
