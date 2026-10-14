"""Conservative worker runtime: recovery is safe, execution is explicit."""

from __future__ import annotations

from domain.job_service import JobService


class WorkerRunner:
    def __init__(self, job_service: JobService, handlers: dict, *, enabled: bool):
        self.job_service = job_service
        self.handlers = dict(handlers)
        self.enabled = enabled

    def recover(self, now: str) -> tuple[int, int]:
        return self.job_service.recover_expired(now)

    def run_once(self, worker_id: str, now: str, lease_until: str):
        if not self.enabled or not self.handlers:
            return None
        claim = self.job_service.claim_next(worker_id, now, lease_until,
                                            kinds=tuple(self.handlers))
        if claim is None:
            return None
        try:
            return self.handlers[claim.kind](claim)
        except Exception as exc:
            return self.job_service.fail_retryable(
                claim.id, worker_id, claim.fence_token,
                type(exc).__name__.upper(), now, now,
            )
