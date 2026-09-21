"""Contract boundaries for coherent authoring and bounded organization queries."""

import pytest
from pydantic import ValidationError

from tyvrana_blender.operations import OPERATIONS, REGISTRY, registration
from tyvrana_blender.organization_models import (
    CollectionConfigureArguments,
    CollectionCreateArguments,
    ObjectSetConfigureArguments,
    ObjectSetCreateArguments,
    ObjectSetInspectArguments,
)
from tyvrana_blender.transport import MAX_FRAME


def member(**changes: object) -> dict[str, object]:
    return {
        "key": "part",
        "name": "Part",
        "kind": "primitive",
        "primitive": "cube",
        **changes,
    }


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"objects": []},
        {"objects": [member()] * 65},
        {"objects": [member(), member()]},
        {"objects": [member(parent={"key": "missing"})]},
        {"objects": [member(kind="script", code="pass")]},
        {"objects": [member(properties={"anything": 42})]},
        {"objects": [member(scale=[1, 0, 1])]},
        {"objects": [member(location=[float("inf"), 0, 0])]},
        {"objects": [member(location=[1e7, 0, 0])]},
        {"objects": [member(tags=["x", "x"])]},
        {"objects": [member(role="anything with spaces")]},
        {"objects": [member(collections=[])]},
        {"objects": [member(name="a" * 129)]},
        {
            "objects": [
                dict(
                    key="c",
                    name="Copy",
                    kind="copy",
                    source={"name": "Part"},
                    materials="independent",
                )
            ]
        },
    ],
)
def test_invalid_authoring(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ObjectSetCreateArguments.model_validate(values)


@pytest.mark.parametrize(
    "values",
    [
        {"objects": [{"name": "Part"}]},
        {"objects": [{"name": "Part", "tags": None}]},
        {"objects": [{"name": "Part", "rename": None}]},
        {"objects": [{"name": "Part", "collections": ["A", "A"]}]},
    ],
)
def test_invalid_patch(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ObjectSetConfigureArguments.model_validate(values)


def test_omission_and_explicit_clearing() -> None:
    patch = ObjectSetConfigureArguments.model_validate(
        {"objects": [{"name": "Part", "parent": None, "role": None, "tags": []}]}
    )
    assert patch.objects[0].model_fields_set == {"name", "parent", "role", "tags"}
    args = ObjectSetCreateArguments.model_validate({"objects": [member()]})
    assert args.collections == [None] and args.objects[0].scale == [1, 1, 1]


@pytest.mark.parametrize(
    "values",
    [
        {"collections": [{"name": "A", "parents": [None, None]}]},
        {"collections": [{"name": "A"}, {"name": "A"}]},
        {"collections": []},
    ],
)
def test_invalid_collections(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CollectionCreateArguments.model_validate(values)


def test_collection_patch_and_inspection_bounds() -> None:
    with pytest.raises(ValidationError):
        CollectionConfigureArguments.model_validate(
            {"collections": [{"name": "A", "hide_render": None}]}
        )
    for values in (
        {"limit": 129},
        {"fields": ["metadata", "metadata"]},
        {"tags": ["x"] * 17},
    ):
        with pytest.raises(ValidationError):
            ObjectSetInspectArguments.model_validate(values)


def test_all_organization_contracts_registered_and_bounded() -> None:
    names = [
        n
        for n in OPERATIONS
        if n.startswith(("blender.collection.", "blender.object_set."))
    ]
    assert len(names) == 9
    for name in names:
        contract = REGISTRY[name].contract
        assert contract.arguments_schema and contract.result_schema
        assert contract.effect == (
            "read_only" if name.endswith("inspect") else "mutating"
        )
        assert not contract.requires_interactive
        assert (
            contract.input_artifacts == "none" and contract.output_artifacts == "none"
        )
    assert (
        len(registration("fixture", "5.2.1", "").model_dump_json().encode()) < MAX_FRAME
    )


def test_removal_bounds_and_unique_names() -> None:
    from tyvrana_blender.organization_models import ObjectSetRemoveArguments

    assert (
        len(ObjectSetRemoveArguments(names=[f"Part{i}" for i in range(256)]).names)
        == 256
    )
    for names in [["Repeated", "Repeated"], [f"Part{i}" for i in range(257)]]:
        with pytest.raises(ValueError):
            ObjectSetRemoveArguments(names=names)
