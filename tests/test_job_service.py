import tempfile
import threading
import unittest
from pathlib import Path

from db.content_repository import ContentRepository
from db.migrate import run_migrations
from domain.errors import ConflictError
from domain.job_service import JobService, JobStateError


NOW = "2026-10-13T12:00:00+00:00"
LATER = "2026-10-13T12:05:00+00:00"


class JobServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"
        run_migrations(self.path)
        self.service = JobService(ContentRepository(self.path))

    def tearDown(self):
        self.temp.cleanup()

    def enqueue(self, job_id="job-1", command_id="cmd-1", available=NOW, payload=None):
        return self.service.enqueue(
            job_id, command_id, "llm", None, None, None,
            payload or {"purpose": "draft"}, available, NOW,
        )

    def test_enqueue_is_idempotent_but_payload_conflict_fails(self):
        first = self.enqueue()
        again = self.enqueue(job_id="different")
        self.assertEqual((first.id, again.id), ("job-1", "job-1"))
        with self.assertRaises(ConflictError):
            self.enqueue(job_id="other", payload={"purpose": "other"})

    def test_claim_is_oldest_and_increments_fence_and_attempt(self):
        self.enqueue("job-b", "cmd-b", "2026-10-13T11:59:00+00:00")
        self.enqueue("job-a", "cmd-a", "2026-10-13T11:59:00+00:00")
        claim = self.service.claim_next("worker", NOW, LATER)
        self.assertEqual((claim.id, claim.fence_token, claim.attempt_count), ("job-a", 1, 1))
        self.assertEqual(self.service.claim_next("worker", NOW, LATER).id, "job-b")

    def test_two_concurrent_workers_claim_one_job_once(self):
        self.enqueue()
        barrier = threading.Barrier(2)
        results = []

        def claim(worker):
            barrier.wait()
            results.append(self.service.claim_next(worker, NOW, LATER))

        threads = [threading.Thread(target=claim, args=(f"w-{n}",)) for n in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(item is not None for item in results), 1)

    def test_stale_fence_and_owner_cannot_complete(self):
        self.enqueue()
        claim = self.service.claim_next("worker", NOW, LATER)
        with self.assertRaises(ConflictError):
            self.service.succeed("job-1", "other", claim.fence_token, {"ok": True}, NOW)
        with self.assertRaises(ConflictError):
            self.service.succeed("job-1", "worker", 0, {"ok": True}, NOW)

    def test_expired_owner_cannot_complete_or_cancel_running_job(self):
        self.enqueue()
        claim = self.service.claim_next("worker", NOW, "2026-10-13T12:01:00+00:00")
        late = "2026-10-13T12:02:00+00:00"
        with self.assertRaises(ConflictError):
            self.service.succeed("job-1", "worker", claim.fence_token, {"ok": True}, late)
        with self.assertRaises(ConflictError):
            self.service.cancel("job-1", NOW)

    def test_retryable_failure_requeues_without_external_intent(self):
        self.enqueue()
        claim = self.service.claim_next("worker", NOW, LATER)
        row = self.service.fail_retryable(
            "job-1", "worker", claim.fence_token, "TEMP", LATER, NOW
        )
        self.assertEqual((row.status, row.available_at), ("queued", LATER))

    def test_cancel_allowed_only_before_external_intent(self):
        self.enqueue()
        self.assertEqual(self.service.cancel("job-1", NOW).status, "cancelled")

        self.enqueue("job-2", "cmd-2")
        claim = self.service.claim_next("worker", NOW, LATER)
        self.service.create_operation("op-2", "op-cmd-2", "llm", "openrouter", "a" * 64, NOW)
        self.service.mark_external_intent("job-2", "worker", claim.fence_token, "op-2", NOW)
        with self.assertRaises(JobStateError):
            self.service.cancel("job-2", NOW)

    def test_expired_lease_requeues_before_intent_and_marks_unknown_after(self):
        self.enqueue("job-1", "cmd-1")
        self.enqueue("job-2", "cmd-2")
        first = self.service.claim_next("w", NOW, "2026-10-13T12:01:00+00:00")
        second = self.service.claim_next("w", NOW, "2026-10-13T12:01:00+00:00")
        self.service.create_operation("op-2", "op-cmd-2", "llm", "openrouter", "a" * 64, NOW)
        self.service.mark_external_intent(second.id, "w", second.fence_token, "op-2", NOW)
        requeued, unknown = self.service.recover_expired("2026-10-13T12:02:00+00:00")
        self.assertEqual((requeued, unknown), (1, 1))
        self.assertEqual(self.service.get(first.id).status, "queued")
        self.assertEqual(self.service.get(second.id).status, "unknown_outcome")


if __name__ == "__main__":
    unittest.main()
