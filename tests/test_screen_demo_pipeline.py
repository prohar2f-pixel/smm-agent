import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from domain.errors import ValidationError
from domain.qa_service import TechnicalQA
from workers.screen_demo import (FfmpegScreenDemoRenderer, ScreenDemoJobHandler,
                                 ScreenDemoPlanner)
from workers.contracts import canonical_manifest_hash


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
            payload["render_profile"] = "screen-demo-preview-v1"
            payload["input_sha256"] = canonical_manifest_hash(payload)
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

    def test_renderer_rejects_output_alias_and_non_job_namespace(self):
        for output in ("inputs/demo.mp4", ".accepted/value", "renders/preview.mp4"):
            broken = copy.deepcopy(self.payload)
            broken["output_path"] = output
            manifest = self.planner.plan(broken, stage="preview")
            with self.subTest(output=output), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValidationError):
                    FfmpegScreenDemoRenderer(directory).validate_manifest_paths(manifest)

    def test_renderer_uses_every_clip_and_exact_trim_intervals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "inputs").mkdir()
            (root / "inputs/demo.mp4").write_bytes(b"clip")
            (root / "inputs/voice.wav").write_bytes(b"audio")
            (root / "inputs/subtitles.srt").write_bytes(b"subs")
            payload = copy.deepcopy(self.payload)
            payload.update({
                "audio_sha256": hashlib.sha256(b"audio").hexdigest(),
                "subtitles_sha256": hashlib.sha256(b"subs").hexdigest(),
                "output_path": "jobs/render-1/preview.mp4",
                "clips": [
                    {"path":"inputs/demo.mp4", "start_sec":1, "end_sec":19,
                     "sha256":hashlib.sha256(b"clip").hexdigest()},
                    {"path":"inputs/demo.mp4", "start_sec":20, "end_sec":38,
                     "sha256":hashlib.sha256(b"clip").hexdigest()},
                ],
            })
            manifest = self.planner.plan(payload, stage="preview")
            renderer = FfmpegScreenDemoRenderer(root)
            renderer.probe = lambda _path: {"duration_ms":36000,"width":1080,
                                             "height":1920,"mime_type":"video/mp4",
                                             "has_audio":True,"has_subtitles":True}
            captured = []
            with patch("workers.screen_demo.subprocess.run",
                       side_effect=lambda command, **_kwargs: captured.append(command)):
                renderer.render(manifest)
            command = " ".join(captured[0])
            self.assertEqual(command.count(str(root / "inputs/demo.mp4")), 2)
            self.assertIn("trim=start=1:end=19", command)
            self.assertIn("trim=start=20:end=38", command)
            self.assertRegex(command, r"-f mp4 /proc/self/fd/\d+$")

    def test_probe_inherits_a_proc_file_descriptor(self):
        renderer = FfmpegScreenDemoRenderer("/tmp")
        completed = SimpleNamespace(stdout=json.dumps({
            "streams": [{"codec_type":"video", "width":1080, "height":1920}],
            "format": {"duration":"36.0"},
        }))
        with patch("workers.screen_demo.subprocess.run", return_value=completed) as run:
            metadata = renderer.probe(Path("/proc/self/fd/17/preview.mp4"))
        self.assertEqual(run.call_args.kwargs["pass_fds"], (17,))
        self.assertEqual(metadata["duration_ms"], 36000)


if __name__ == "__main__":
    unittest.main()
