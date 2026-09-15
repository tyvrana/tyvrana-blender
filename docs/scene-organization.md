# Scene organization and coherent object sets

Use native collections to organize assemblies, rooms and helper structures.
Object parenting describes transform dependencies; collection membership describes
organization. An object can belong to several collections, and a collection can
have several parents. These relationships are independent of editor selection.

Discover `blender.collection.` and `blender.object_set.` summaries, then request
only the selected operation schemas. All eight operations are synchronous and
headless-capable. They require Object Mode outside rendering; mutation additionally
requires editable local scene data.

| Operation | Purpose | Bounds and result |
|---|---|---|
| `collection.create_hierarchy` | Stage nested collections with explicit parent memberships and visibility | 1–32 declarations; returns collection summaries |
| `collection.configure` | Rename, replace parent memberships, patch global visibility | 1–32 patches; omitted values remain unchanged |
| `collection.inspect` | Query collections or a subtree | Default 32 / maximum 128 rows; name/prefix filters and pagination |
| `collection.remove` | Remove an empty collection or explicitly rehome its contents | One collection; retains objects, meshes and materials |
| `object_set.create` | Stage a coherent assembly with local transforms, parents, materials, memberships and roles | 1–64 declarations; returns request-key/name/type/parent mappings |
| `object_set.configure` | Rename, change/clear parents, replace memberships, set/clear roles and tags | 1–64 patches; preserves world transforms during parenting |
| `object_set.inspect` | Inspect a named set or collection subtree with selected fields | Default 32 / maximum 128 rows; role/tag filters |
| `object_set.remove` | Delete a bounded set after dependency checks | 1–64 names; exact deleted/remaining results on native failure |

Operation names in the table have the `blender.` prefix. All collection membership
lists accept up to 16 distinct entries; `null` denotes the scene master collection.
Exact membership replacement expresses linking, unlinking and moving in one call.
An empty membership list is rejected to prevent accidentally orphaning objects.
Inspection caps nested membership/parent/child/material lists at 16 and reports
counts and truncation. Organization operations reject files exceeding 4,096
collection datablocks or 100,000 object datablocks; filters limit returned payloads.

## Author a small assembly

First call `blender.collection.create_hierarchy`:

```json
{
  "collections": [
    {"name": "Assembly"},
    {"name": "Hardware", "parents": ["Assembly"]}
  ]
}
```

Then call `blender.object_set.create` (the material `Steel` must already exist):

```json
{
  "collections": ["Assembly"],
  "objects": [
    {"key": "pivot", "name": "Pivot", "kind": "empty", "role": "joint"},
    {
      "key": "arm", "name": "Arm", "kind": "primitive", "primitive": "cube",
      "parent": {"key": "pivot"}, "location": [0, 0, 2], "scale": [0.2, 0.2, 2],
      "material": "Steel", "role": "arm"
    },
    {
      "key": "bolt", "name": "Bolt", "kind": "primitive", "primitive": "cylinder",
      "scale": [0.1, 0.1, 0.2], "material": "Steel",
      "collections": ["Hardware"], "role": "fastener", "tags": ["metal"]
    },
    {
      "key": "bolt-copy", "name": "Bolt Copy", "kind": "copy",
      "source": {"key": "bolt"}, "location": [1, 0, 0], "scale": [0.1, 0.1, 0.2],
      "collections": ["Hardware"], "role": "fastener", "data": "linked"
    }
  ]
}
```

Keys are unique within the request and may refer forward to other declarations.
They are returned for correlation and are not stored as persistent identities.
References to existing objects use `{"name": "Existing object"}`. Dependencies
are validated and created in deterministic order. Cycles and missing references
fail before publication. Existing object and collection names must be unique;
automatic suffixes and rename swaps are rejected. Names have at most 128 characters
and must fit Blender's 255 UTF-8 bytes without NUL.

Supported new geometry is a size-2 cube, size-2 plane, radius-1 UV sphere (32 × 16),
or radius-1/depth-2 cylinder (32 sides). Plain axes empties have a configurable
display size. Use existing mesh, camera, light and material operations for richer
resource creation. Transforms default to zero location/XYZ Euler rotation and unit
scale, always relative to the declared parent. Copies also use the declared
transforms rather than inheriting source placement. Negative scale is allowed;
scale magnitudes below 1e-6 and transform components outside ±1e6 are rejected.
The evaluated world transform must remain finite and invertible.

## Data ownership and repeated parts

Copies support plain local meshes, camera/light datablocks and plain empties.
They copy the object only, not its child hierarchy. `data: "linked"` shares the
underlying datablock while keeping object transforms independent. The explicit
`independent` choice copies that data. Allocation is limited to 250,000 mesh
vertices per authoring request, including independent copies.

Materials are shared by default. `materials: "independent"` requires independent
data and copies material datablocks and their root shader graphs for each new
object; repeated slots in that object retain their common material. Images and
nested shader groups remain shared. Existing typed mesh operations retain their
copy-on-write guards, and appending a material slot isolates shared mesh data.
Editing a named shared material intentionally affects all its users.

Animated, constrained, modified, shape-key, instance, library-linked, override and
domain-owned copy sources are rejected. Nonidentity delta transforms are also
rejected. Image empties use reference operations. Collection instances, recursive
hierarchy duplication, library import and general procedural duplication are
outside these operations; existing surface-instance tools serve their distinct
surface-distribution workflow.

## Relationships, identity and metadata

`object_set.configure` changes parents using existing object names. Omit `parent`
to preserve it; use `null` to clear it. World transforms are checked at native
precision. Reparenting supports plain object parents without animation,
constraints, rigid bodies, bone/vertex parenting or nonidentity delta transforms.
Clearing a parent that would lose affine shear is rejected and rolled back.
Generic authored transforms continue through `blender.object.set_transform`.

Blender stores parenting and membership as native references. They survive
renaming and save/reopen; callers use the returned current names for later calls.
A deleted/replaced object has no continuity guarantee. Collections plus filtered
roles/tags provide scoped rediscovery. There is no UUID service or project database.

Only fixed `role` and `tags` metadata are exposed. Roles/tags are at most 48 ASCII
letters, digits, `_`, `.`, `:`, or `-`, start with a letter/digit, and tags are a
unique list of at most 16 entries. They persist as adapter-owned
`tyvrana_organization_role` and `tyvrana_organization_tags` properties. Null role
and an empty tag list clear values; omitted patch fields remain unchanged.
Copy declarations assign their own metadata rather than inheriting it implicitly.
Arbitrary property keys/values are not accepted. Objects owned by another adapter
domain must use that domain's operations.

For compact verification, use `object_set.inspect` with a collection or names,
optional prefix/role/all-tags filters and selected `fields`: `hierarchy`,
`memberships`, `transforms`, `metadata`, `data`. Transform inspection includes the
world affine matrix and local channels. Data inspection includes datablock names,
user counts and effective material slots. Compare these bounded snapshots in the
client; there is no server history or delta store.

## Visibility, cleanup and failure behavior

Collection flags are **global** viewport, render and selection visibility.
View-layer exclusion and per-path layer visibility are separate Blender concepts
and are not changed by these operations. Multiply parented collections retain
all explicit links. Edits reject collections used outside the current scene or
by collection instances, and objects shared with other scenes.

Creation prevalidates references/ownership, stages native resources, then publishes
all memberships. A failure removes only resources created by that attempt.
Configuration restores names, parent links, transforms, memberships and metadata
on failure. Neither path performs a global orphan purge.

Collection removal defaults to requiring an empty collection. `mode: "rehome"`
links its direct objects and child collections to `target` (null means scene root)
before removing the container, retaining other memberships and native data.

Object-set removal checks external dependencies first. Surviving children block
removal unless `children: "unparent"` is explicit and their world transforms can
be preserved. Other external references still block removal. Mesh/material
resources remain available after object deletion. Native ID deletion cannot be
rolled back: always check `error`, `deleted` and `remaining` before proceeding or
retrying. Do not assume an operation with non-null `error` completed its intent.

## Validation and native references

Headless tests cover mechanical assemblies, a modular room with shared/independent
parts and lights, a generic articulated chain, bounded inspections, rename/save/
reopen, selection preservation, linked-library protection, relationship cycles,
world-transform checks, injected rollback and explicit partial deletion.

Native semantics follow Blender's [collections API](https://docs.blender.org/api/5.2/bpy.types.Collection.html),
[object relationships](https://docs.blender.org/api/5.2/bpy.types.Object.html),
[linked duplication](https://docs.blender.org/manual/id/5.2/scene_layout/object/editing/duplicate_linked.html)
and [datablock ownership](https://docs.blender.org/api/5.2/bpy.types.BlendData.html).
