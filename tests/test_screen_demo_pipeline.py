import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from domain.errors import ValidationError
from domain.qa_service import TechnicalQA
from workers.screen_demo import ScreenDemoJobHandler, ScreenDemoPlanner


FIXTURE = Path(__file__).parent / "fixtures" / "screen_demo_manifest.json"


class ScreenDemoPipelineTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(FIXTURE.read_text())
        self.planner = ScreenDemoPlanner()

    def test_preview_manifest_is_deterministic_and_complete(self):
        first = self.planner.plan(self.payload, stage="preview")
        second = self.planner.plan(copy.deepcopy(self.payload), stage="preview")
        self.assertEqual(first, second)
        self.assertEqual(first.render_profile, "screen-demo-preview-v1")
        self.assertEqual(first.payload["duration_sec"], 36)
        first.validate()

    def test_duration_audio_subtitles_sources_and_caps_are_required(self):
        mutations = [
            ("duration_sec", 20), ("audio_path", None), ("subtitles_path", None),
            ("audio_sha256", None), ("subtitles_sha256", None),
            ("clips", []), ("fps", 60), ("max_memory_mb", 8192),
        ]
        for key, value in mutations:
            broken = copy.deepcopy(self.payload)
            broken[key] = value
            with self.subTest(key=key), self.assertRaises(ValidationError):
                self.planner.plan(broken, stage="preview")

    def test_master_is_blocked_until_exact_preview_approval(self):
        with self.assertRaises(ValidationError):
            self.planner.plan(self.payload, stage="master")
        manifest = self.planner.plan(
            self.payload, stage="master", approved_preview_sha256="c" * 64,
        )
        self.assertEqual(manifest.render_profile, "screen-demo-master-v1")

    def test_technical_qa_distinguishes_verified_artifact_from_mismatch(self):
        data = b"rendered-video"
        digest = hashlib.sha256(data).hexdigest()
        result = TechnicalQA.inspect(
            data, expected_sha256=digest, duration_sec=36,
            width=1080, height=1920, has_audio=True, has_subtitles=True,
        )
        self.assertTrue(result.passed)
        bad = TechnicalQA.inspect(
            data, expected_sha256="0" * 64, duration_sec=36,
            width=1080, height=1920, has_audio=True, has_subtitles=True,
        )
        self.assertFalse(bad.passed)
        self.assertIn("checksum", bad.failures)

    def test_job_handler_renders_and_submits_a_fenced_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = copy.deepcopy(self.payload)
            payload["job_id"] = "job"
            payload["fence_token"] = 7
            payload["stage"] = "preview"
            payload["asset_role"] = "preview"
            payload["mime_type"] = "video/mp4"
            payload["duration_ms"] = 36000
            payload_json = json.dumps(payload)

            class Repo:
                def fetchone(self, _sql, _params):
                    return {"payload_json": payload_json, "revision_id": "revision-1"}

            class Renderer:
                def render(self, manifest):
                    target = root / manifest.output_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"rendered")
                    return {"duration_ms": 36000, "width": 1080, "height": 1920,
                            "mime_type": "video/mp4", "has_audio": True,
                            "has_subtitles": True}

            class Reports:
                def accept(self, report, accepted_at):
                    self.report = report
                    self.accepted_at = accepted_at
                    return report

            reports = Reports()
            handler = ScreenDemoJobHandler(
                Repo(), root, Renderer(), reports,
                now=lambda: "2026-10-13T12:00:01+00:00",
            )
            result = handler(SimpleNamespace(
                id="job", kind="screen_demo", lease_owner="worker", fence_token=7,
            ))
            self.assertEqual(result.status, "succeeded")
            self.assertEqual(result.output_sha256, hashlib.sha256(b"rendered").hexdigest())
            self.assertEqual(reports.accepted_at, "2026-10-13T12:00:01+00:00")


if __name__ == "__main__":
    unittest.main()
