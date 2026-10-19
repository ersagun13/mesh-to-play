# Mesh to Play — product design

## Intent
Turn a dense AI-generated static prop into reviewable LOD candidates and a Unity handoff, with real measurements. We own the inspection workflow, formats, integration and user experience; simplification credits meshoptimizer. No new generative AI model, no paid API.

## Audience and scope
Independent game developers using static environment assets. First supported inputs: embedded, uncompressed static GLB 2.0 and single-file geometry OBJ. Reject skins, animations, morph targets, required unsupported extensions, non-triangle modes, sparse accessors unless implemented, remote/external resources, malformed buffers and nonfinite geometry. Support indexed/non-indexed primitives, interleaved accessor strides, normalized UV/color data, scene selection, parent transforms and instances. OBJ materials from external MTL are not loaded and this is visibly reported.

## Workflow
Inspect input; select relative triangle targets or a static-prop preset; choose relative geometric error bound; optionally select a longest dimension in meters and bottom-center pivot. Create LOD0/1/2 from original primitives independently, preserving material boundaries and existing vertex attributes/UVs. Native meshoptimizer may stop above requested target because of topology/error constraints; report requested vs achieved. Compact vertices/attributes for each output. Preserve embedded textures and materials without claiming rebaking, texture equivalence or texture reduction. Output standard uncompressed self-contained GLB. Preserve original file.

## Technical approach
Pure Python stdlib parsing/writing, ctypes to a reproducibly built local meshoptimizer native library. Root integrator owns `third_party/meshoptimizer/` and `tools/build_native.py`, pinned source and MIT attribution. Processor should find explicit `--native-library`, env MESHOPT_LIBRARY, then own `native/` build; do not load arbitrary system libs automatically. No Blender dependency in v1 due to verified local startup crash. Use native `meshopt_simplify` or attribute-aware function with exact signatures from vendored header; keep safe validated buffers alive. Diagnostics/demo/report work without native. Simplification is unavailable with a clean actionable instruction until library is built.

## CLI
`python3 run.py demo --out DIR` generates original detailed procedural prop. `python3 run.py inspect INPUT --out DIR`. `python3 run.py prepare INPUT --out DIR [--ratios 0.5,0.25,0.1] [--error 0.01] [--height METERS] [--bottom-center] [--native-library PATH]`. Never overwrite an existing output directory or source; use a new generated result directory or reject explicitly. `serve` implements shared contract. UI options `{mode:inspect|prepare,ratios:[0.5,0.25,0.1],error:0.01,height:null,bottom_center:false}` with profile selection reflected in explicit values. Source/candidate dimensions and triangle/byte reductions are measured, no FPS or detail-preservation guarantee.

## Outputs
LOD0.glb, LOD1.glb, LOD2.glb when prepared, source/candidate binary previews, report.json, report.html, bundle.zip, settings/provenance with input and outputs SHA256. Unity Editor integration under integrations/unity with clear official glTFast dependency and LODGroup builder; no claim tested in Unity unless run. Include desktop screenshots/demo artifacts only from actual outputs.

## Review and acceptance
Validate geometry roundtrip for multiple nodes/primitives, negative and nonuniform transforms, missing attrs, textures byte-preserved and referenced, accessor ranges/strides/component types, triangle budget monotonicity, invalid options, no overwrites, request origin/host/token protections and traversal. Native acceptance uses a genuinely detailed original fixture and asserts achieved reduction and valid exported buffers. Small cube is an honest no-benefit case. Batch benchmark original procedural model at >=100k triangles; preserve exact source hash and metrics. Include run logs and limitations.

## Explicit next versions
Texture baking/atlas/resize, animation retopology and rigging, automatic convex decomposition and target-engine performance recommendations require their own validated implementation. Do not expose unimplemented buttons or silently drop unsupported source features.
