import unittest

from domain.errors import ValidationError
from workers.carousel import CarouselPlanner
from workers.generative_video import GenerativeVideoPlanner
from workers.production_video import ProductionVideoPlanner

HASH = "a" * 64


class MediaFactoryContractTests(unittest.TestCase):
    def test_carousel_is_offline_safe_and_avatar_only_first_last(self):
        slides = [{"asset_path":"in/1.png","avatar":True},
                  {"asset_path":"in/2.png"},
                  {"asset_path":"in/3.png","avatar":True}]
        manifest = CarouselPlanner().plan(slides, "out/carousel.zip")
        self.assertFalse(manifest.network_enabled)
        for bad in ([{"asset_path":"../x"},{"asset_path":"in/y"}],
                    [{"asset_path":"in/x"},{"asset_path":"in/y","avatar":True}]):
            with self.assertRaises(ValidationError):
                CarouselPlanner().plan(bad, "out/x.zip")
        with self.assertRaises(ValidationError):
            CarouselPlanner().plan(slides, "out/x.zip", network_enabled=True)

    def test_production_master_requires_preview_approval(self):
        segments = [{"path":"in/a.mp4","adapter":"avatar"}]
        ProductionVideoPlanner().plan(stage="preview", segments=segments, output_path="out/p.mp4")
        with self.assertRaises(ValidationError):
            ProductionVideoPlanner().plan(stage="master", segments=segments, output_path="out/m.mp4")
        result = ProductionVideoPlanner().plan(stage="master", segments=segments, output_path="out/m.mp4", approved_preview_sha256=HASH)
        self.assertEqual(result["stage"], "master")

    def test_generative_spend_requires_animatic_budget_and_two_aspects(self):
        planner = GenerativeVideoPlanner()
        with self.assertRaises(ValidationError):
            planner.plan_shot(feature_enabled=False, shot_id="s", prompt="p", approved_animatic_sha256=HASH, cost_upper_bound_minor=100, currency="RUB")
        with self.assertRaises(ValidationError):
            planner.plan_shot(feature_enabled=True, shot_id="s", prompt="p", approved_animatic_sha256=None, cost_upper_bound_minor=100, currency="RUB")
        tasks = planner.plan_shot(feature_enabled=True, shot_id="s", prompt="p", approved_animatic_sha256=HASH, cost_upper_bound_minor=100, currency="RUB")
        self.assertEqual({task.aspect_ratio for task in tasks}, {"16:9", "9:16"})
        with self.assertRaises(ValidationError):
            planner.validate_attempt(cost_minor=10, currency="RUB", cost_evidence_ref="")


if __name__ == "__main__":
    unittest.main()
