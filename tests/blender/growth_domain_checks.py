"""Continuous fields and ordered domains on disposable synthetic surfaces."""

import copy
import importlib
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.blender.growth_checks import GrowthTests, errors, growth, models  # noqa:E402

domains = importlib.import_module(
    "bl_ext.user_default.tyvrana_blender" + ".growth_domain_models"
)


class GrowthDomainTests(GrowthTests):
    def setUp(self) -> None:
        super().setUp()
        self.region: dict[str, Any] = dict(
            name="Panel",
            family="Fiber",
            guides=0,
            field=dict(
                controls=[
                    dict(uv=[0.1, 0.2], direction=[1, 0], length_scale=1),
                    dict(uv=[0.4, 0.2], direction=[0, 1], length_scale=2),
                ]
            ),
            rows=[
                dict(
                    name="Lower",
                    path=[[0.1, 0.2], [0.4, 0.2]],
                    count=None,
                    spacing=0.2,
                    mirror="u",
                    order=0,
                ),
                dict(
                    name="Upper",
                    path=[[0.1, 0.4], [0.4, 0.4]],
                    count=7,
                    mirror="u",
                    order=1,
                    layer=1,
                    overlap="over_previous",
                ),
            ],
        )
        self.spec = models.GrowthCreateArguments(
            name="Field",
            surface="Surface",
            families=self.spec.families,
            regions=[self.region],
        )

    def test_continuity_mirroring_spacing_and_revision(self) -> None:
        self.create()
        report = self.inspect(
            guide_limit=32,
            include_points=True,
            include_rows=True,
            field_samples=[
                dict(region="Panel", uv=[u, 0.2]) for u in [0.1, 0.2499, 0.2501, 0.4]
            ],
        )
        self.assertEqual(report.summary.guides, 28)
        self.assertEqual(len(report.rows), 4)
        self.assertEqual(len({g.root_id for g in report.guides}), 28)
        for row in report.rows:
            self.assertAlmostEqual(row.minimum_spacing, 0.2, places=5)
            self.assertAlmostEqual(row.maximum_spacing, 0.2, places=5)
        samples = report.field_samples
        self.assertAlmostEqual(samples[0].direction[0], 1, places=5)
        self.assertAlmostEqual(samples[-1].direction[1], 1, places=5)
        self.assertAlmostEqual(samples[-1].length_scale, 2, places=5)
        self.assertLess(
            sum(
                (a - b) ** 2
                for a, b in zip(samples[1].direction, samples[2].direction, strict=True)
            ),
            1e-4,
        )
        guides = report.guides
        for a, b in zip(guides[:7], guides[7:14], strict=True):
            self.assertAlmostEqual(a.uv[0] + b.uv[0], 1, places=5)
            self.assertAlmostEqual(
                a.points[-1][0] - a.points[0][0],
                -(b.points[-1][0] - b.points[0][0]),
                places=5,
            )
        before = {(g.row, g.mirrored, g.sequence): g.root_id for g in guides}
        changed = copy.deepcopy(self.region)
        changed["rows"].reverse()
        changed["rows"][0]["path"] = [[0.1, 0.45], [0.4, 0.45]]
        growth.configure(
            models.GrowthConfigureArguments(object_name="Field", regions=[changed])
        )
        after = self.inspect(guide_limit=32, include_rows=True)
        self.assertEqual(
            before, {(g.row, g.mirrored, g.sequence): g.root_id for g in after.guides}
        )
        obj, _, _, group = growth.owned("Field")
        with growth.evaluated_path(obj, group) as data:
            self.assertEqual(
                set(before.values()),
                {x.value for x in data.attributes["growth_root_id"].data},
            )
            self.assertEqual(
                {0, 1}, {x.value for x in data.attributes["growth_layer"].data}
            )
        self.surface.shape_key_add(name="Basis")
        bend = self.surface.shape_key_add(name="Bend")
        for vertex in bend.data:
            vertex.co.z += 0.1 * vertex.co.x**2
        bend.value = 0.8
        bpy.context.view_layer.update()
        self.assertLess(self.inspect().summary.qa.maximum_root_error, 1e-4)

    def test_domain_field_and_transaction_failures_are_atomic(self) -> None:
        self.create()
        obj = bpy.data.objects["Field"]
        before = obj[growth.KEY]
        data = obj.data
        changed = copy.deepcopy(self.region)
        changed["rows"][0]["path"][0] = [-0.2, 0.2]
        with self.assertRaises(errors.OperationError):
            growth.configure(
                models.GrowthConfigureArguments(object_name="Field", regions=[changed])
            )
        self.assertEqual(obj[growth.KEY], before)
        changed = copy.deepcopy(self.region)
        changed["field"]["controls"][1]["direction"] = [-1, 0]
        with self.assertRaises(errors.OperationError):
            growth.configure(
                models.GrowthConfigureArguments(object_name="Field", regions=[changed])
            )
        self.assertEqual(obj[growth.KEY], before)
        with patch.object(growth, "summary", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                growth.configure(
                    models.GrowthConfigureArguments(object_name="Field", neighbors=2)
                )
        self.assertIs(obj.data, data)
        self.assertEqual(obj[growth.KEY], before)
        ids = {g.root_id for g in self.inspect(guide_limit=32).guides}
        growth.configure(
            models.GrowthConfigureArguments(object_name="Field", rebind=True)
        )
        self.assertTrue(
            ids.isdisjoint({g.root_id for g in self.inspect(guide_limit=32).guides})
        )


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(
            GrowthDomainTests(name)
            for name in GrowthDomainTests.__dict__
            if name.startswith("test_")
        )
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("GROWTH_DOMAIN_NATIVE_PASSED")
