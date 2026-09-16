"""Bounds and unambiguous edits for reusable native paths."""

from typing import Any

import pytest
from pydantic import ValidationError

from tyvrana_blender.curve_models import (
    CurveBinding,
    CurveConfigureArguments,
    CurveCreateArguments,
    CurveEdit,
    CurveInspectArguments,
    CurvePoint,
    CurveRemoveArguments,
    CurveSettingsPatch,
    CurveSpec,
    SplineSpec,
    SurfaceAnchor,
)


def path(**kwargs: Any) -> dict[str, Any]:
    return dict(
        name="Guide",
        splines=[dict(points=[dict(co=[0, 0, 0]), dict(co=[1, 0, 0])])],
        **kwargs,
    )


@pytest.mark.parametrize(
    "values",
    [
        dict(radius=-1),
        dict(radius=float("inf")),
        dict(tilt=float("nan")),
        dict(weight=0),
        dict(handle_type="FREE"),
        dict(left=[0, 0, 0]),
        dict(handle_type="VECTOR", right=[1, 0, 0]),
    ],
)
def test_invalid_point(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CurvePoint(co=[0, 0, 0], **values)


@pytest.mark.parametrize("weights", [[0, 0, 0], [1, 1, 0], [-1, 1, 1]])
def test_surface_weights(weights: list[float]) -> None:
    with pytest.raises(ValidationError):
        SurfaceAnchor(kind="surface", object="Surface", face=0, barycentric=weights)


@pytest.mark.parametrize(
    "values",
    [
        dict(points=[dict(co=[0, 0, 0])]),
        dict(points=[dict(co=[0, 0, 0])] * 2),
        dict(points=[dict(co=[0, 0, 0]), dict(co=[1, 0, 0])], cyclic=True),
        dict(points=[dict(co=[0, 0, 0]), dict(co=[1, 0, 0])], type="NURBS"),
        dict(
            points=[dict(co=[0, 0, 0], handle_type="VECTOR"), dict(co=[1, 0, 0])],
            type="POLY",
        ),
    ],
)
def test_spline_shape(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SplineSpec.model_validate(values)


def test_overlapping_or_missing_bindings() -> None:
    b = dict(spline=0, target=dict(kind="object", object="Anchor"))
    for bindings in [
        [b, b],
        [dict(b, point=2)],
        [dict(b, follow="spline"), dict(b, point=1)],
    ]:
        with pytest.raises(ValidationError):
            CurveSpec.model_validate(path(bindings=bindings))


def test_batch_budget_and_unique_names() -> None:
    for rows in [[path(), path()], [dict(path(), name=str(i)) for i in range(65)]]:
        with pytest.raises(ValidationError):
            CurveCreateArguments.model_validate(dict(curves=rows))
    points = [dict(co=[i, 0, 0]) for i in range(256)]
    with pytest.raises(ValidationError):
        CurveCreateArguments.model_validate(
            dict(
                curves=[
                    dict(name=str(i), splines=[dict(points=points)]) for i in range(17)
                ]
            )
        )


def test_patch_omission_and_material_clear() -> None:
    assert CurveSettingsPatch().model_dump(exclude_unset=True) == {}
    assert CurveSettingsPatch(material=None).model_dump(exclude_unset=True) == {
        "material": None
    }
    with pytest.raises(ValidationError):
        CurveSettingsPatch.model_validate(dict(profile=None))
    with pytest.raises(ValidationError):
        CurveEdit(name="Guide")
    assert CurveEdit(name="Guide", bindings=[]).bindings == []


def test_ambiguous_edits_and_duplicates() -> None:
    spline = path()["splines"][0]
    with pytest.raises(ValidationError):
        CurveEdit.model_validate(
            dict(
                name="Guide",
                splines=[spline],
                ranges=[dict(spline=0, start=0, points=[dict(co=[1, 0, 0])])],
            )
        )
    with pytest.raises(ValidationError):
        CurveConfigureArguments(curves=[CurveEdit(name="Guide", bindings=[])] * 2)
    with pytest.raises(ValidationError):
        CurveRemoveArguments(names=["Guide", "Guide"])


def test_compact_defaults_and_shared_settings() -> None:
    args = CurveCreateArguments.model_validate(
        dict(
            curves=[path()],
            defaults=dict(spline_type="POLY", profile=dict(kind="circle", radius=0.1)),
        )
    )
    assert args.curves[0].splines[0].type is None
    assert args.defaults.spline_type == "POLY"
    assert CurveInspectArguments().point_limit == CurveInspectArguments().samples == 0
    assert (
        CurveBinding.model_validate(
            dict(spline=0, target=dict(kind="object", object="A"))
        ).follow
        == "point"
    )
