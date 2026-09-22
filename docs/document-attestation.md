# Document content attestation

`blender.document.attest` provides compact read-only evidence for Core's document
continuity policy. It never saves or changes authored data. The process owns one
random host-session UUID in its `bpy` runtime namespace, outside reloadable addon
modules and saved files. A second UUID identifies the loaded document session.
Load/new lifecycle notifications replace the latter, including unsaved documents.
Extension reload and transport reconnect preserve both. A new process creates both.
Both identities are exposed by typed `blender.extension.inspect` identity metadata
and each content attestation, separately from adapter instance and connection IDs.

SHA256 hashes a canonical length-delimited stream. RNA property names and native
resource names are ordered; integer values have exact decimal encoding, scalar
floats use IEEE754 binary64 in network byte order, and bulk native mesh/image arrays
use their exact binary32/integer storage in network byte order. No display rounding
or process pointers enter the digest. Format identity includes Blender's version
and the hashing implementation identity; different formats are not interchangeable.

The traversal covers retained native data: object identity/type, parenting and
transforms, collection membership, scene/render settings, mesh topology and point,
edge, corner and polygon data, material assignments, attributes, UV selection for
rendering, deform weights, normals, curves, rest bones/poses, modifiers, constraints,
shape keys, action data, node topology and input values, custom properties and
supported native resources. Embedded node trees are traversed, not reduced to a
name. Used images include packed/external bytes and current pixel content; external
font content is included. Saved document/resource UUID markers identify bindings
separately and are excluded from material content.

UI workspaces/screens, evaluated dependency-graph caches, selection and zero-user resources without a
fake user are excluded. Such orphan resources are not retained by ordinary save/load.
Nonmaterial UI fields may conservatively affect some RNA resource hashes; a digest
mismatch never proves a particular visual defect or grants acceptance.

Linked/override resources, external color configurations, non-simple scripted
drivers, simulation/bake caches, referenced transient render images and unsupported
external resource categories fail explicitly. Unreadable values, nonfinite floats,
unknown value classes and exceeded bounds also produce **no digest**. Complete
equivalence must never be inferred from an incomplete result.

Dense numerical inputs use native `foreach_get`. Blender requires a contiguous
full-length target and does not provide offset reads; repeated pixel slices can
repeatedly materialize the underlying image. Arrays up to 8 MiB use one native
array buffer. Larger inputs use anonymous disk-backed scratch, then stream through
1 MiB endian-conversion/hash chunks. There are no full-array byte/string copies or
Python lists. Scratch closes on success and failure. Memory-mapped pages remain
subject to the operating system's paging; mapped span is not a measured RSS bound.
Packed bytes remain bounded native allocations; external files stream in chunks.

Blob type and total byte length are encoded before content. Changing chunk size
cannot change a digest. Unordered resource/membership/node enumerations are sorted;
ordered modifier stacks, sockets and geometry indices retain their semantic order.

Bounds: 4096 retained resources, 4 million structural stream items, 4 GiB actual
streamed bytes, 1,073,741,824 bulk components, 1 GiB per contiguous native/scratch
buffer, 40 nested RNA levels and a 30-second checked work deadline. Streamed-byte
capacity is separate from bounded buffering, not a permission to construct a 4 GiB
Python value. Hashing native bulk data does not count each scalar as an RNA visit.
The contiguous native call itself is bounded by buffer size; deadline checks occur
before/after it and at each hash chunk/structural feed.

Results include exact exhausted-budget identity, bytes/items/bulk components,
completed resources, current category/resource, configured limits, peak contiguous
buffer span, bounded category totals and eight heaviest resource timings. Buffer
span includes mapped scratch; it does not claim to measure Python heap or peak RSS.

Results contain identities,
format, status, a 64-character digest, counts, bounded omissions, elapsed time and an
optional current saved-file SHA256. They contain no mesh arrays or image pixels.
File SHA256 identifies saved bytes; it does not alone prove the live scene matches.

The result schema is generated from `tyvrana_protocol.DocumentAttestation` and
distributed as `attestation.schema.json`. Core validates this canonical model.
An observation contract can thus be deployed without replacing the extension's
unchanged transport/dependency wheels in a running host.

Core owns acceptance and continuity policy. Attestation is content evidence, not
an artistic, anatomical, physical or milestone acceptance judgment.
