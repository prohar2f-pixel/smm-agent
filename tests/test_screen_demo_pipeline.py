import copy
import hashlib
import json
import unittest
from pathlib import Path

from domain.errors import ValidationError
from domain.qa_service import TechnicalQA
from workers.screen_demo import ScreenDemoPlanner


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


if __name__ == "__main__":
    unittest.main()
