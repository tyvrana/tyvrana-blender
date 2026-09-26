# Image artifact ingestion

`blender.image.create_from_artifact` atomically creates 1..8 packed native images.
Pass `images: [{artifact_id, name?, color_space?, alpha_mode?}, ...]` and attach the
unique artifact IDs. Core imports `files: [{path, name?, media_type?}, ...]` in
manifests of up to64 files, returning ordered `artifacts`. Attach at most eight
inputs to each native image batch. Release inputs together
with `artifact_ids` when finished. Single-image requests use the same list shape.
The result is ordered `images` metadata. Existing explicit names conflict;
duplicate explicit names fail validation. Any failure removes every new image
from that batch and preserves existing images. Artifact ownership remains with Core.

For reference sets, import/create images in batches, then use `reference.create`
for combined placement, labels, categories and collections; `reference.register`
already batches metric calibration and provenance. `reference.inspect` retrieves
a filtered page after setup or reopening. Each batch is atomic; separate image
and reference calls are not a cross-call transaction. There is no conversion or intermediate image
inspection step for a valid supported input.

## Formats and bounds

Supported inputs are static PNG and 8-bit DCT JPEG (baseline, extended sequential
and progressive; RGB, grayscale and CMYK). WebP and other formats are not part of
this operation's contract even when the host supports them elsewhere.

Before native decode, admission enforces all of:

- At most **67,108,864 encoded bytes (64 MiB)** per image and combined batch.
- Width and height from **1 through 16,384**.
- At most **33,554,432 decoded pixels** per image and combined batch.
- Matching MIME/signature, valid dimension header, complete declared segments and
  required terminal marker. JPEG metadata is skipped by its encoded segment length,
  without a separate hidden metadata-size cutoff.

The pixel bound limits one four-channel float representation to 512 MiB; native
buffers, encoded/packed bytes, caches and existing images consume additional memory.
It is an admission bound, not a process-memory reservation. Core artifact
quotas may reject admission earlier when storage is full or locally configured
limits are lower. Generated images retain their separate existing size contract.

Blender is the single pixel decoder. No Pillow/runtime decoder, full Python pixel
array, resize, re-encoding or extra adapter-local image copy is used. Admission reads
bounded headers from the request-scoped file already materialized by the transport.
Transport verifies complete byte count and SHA-256 before dispatch; admission checks
materialized length again. The packed image retains the original encoded bytes.
EXIF/ICC metadata remains in those bytes; EXIF orientation is not applied by this
operation. Explicit image color-space arguments remain authoritative. The header
check is not a forensic validation of every compressed sample; decoding also depends
on the native codec's handling of damaged content.

## Failures and ownership

Errors use the existing operation error object (`code`, `message`, `details`).
`details.stage` identifies the failing stage without exposing internal paths:

| Code | Meaning |
|---|---|
| `artifact_not_attached`, `artifact_not_found` | Required input was not attached or is unavailable. |
| `artifact_read_failed` | Materialized input could not be read. |
| `artifact_truncated`, `artifact_integrity_failed` | Incomplete header/segment/end marker, or materialized size differs from its verified descriptor. |
| `unsupported_artifact_media_type`, `artifact_media_type_mismatch` | Unsupported MIME type or signature mismatch. |
| `unsupported_image_encoding` | Unsupported JPEG precision/frame encoding. |
| `artifact_too_large` | Encoded byte bound exceeded; details include actual/maximum bytes. |
| `image_dimensions_exceeded` | Side or total-pixel bound exceeded; details include dimensions and limits. |
| `image_decode_failed` | Invalid header or failed native decoding; stage distinguishes them. |
| `image_pack_failed`, `image_datablock_failed` | Native packing or final resource construction failed. |
| `image_memory_exhausted` | Native/Python allocation raised a recoverable memory error. |

Transport failures occur before image dispatch and retain their existing
`artifact_transfer_failed` error with a specific integrity/storage/transfer message.
Successful artifact import alone does not create a Blender image. Check the image
operation result before invoking dependent reference or material operations.

Failed image creation removes all native images allocated by its batch; request-scoped
input files are released on completion/cancellation. Existing images remain intact.
Original Core artifacts retain their normal explicit-release lifetime. Packed bytes
remain independent of temporary files after success and through native save/reopen.
Reference objects and image-texture nodes share these image resources;
there is no separate decoder in those consumers. Reference batches validate all
inputs before mutation and remain atomic when an image prerequisite is missing.
