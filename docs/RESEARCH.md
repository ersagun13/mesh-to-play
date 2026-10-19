# Research and engineering decisions

Research checked on 2026-10-18 (Europe/Istanbul). These are capability/competition notes, not evidence of market size or a claim of being first.

## Existing capabilities
- [Meshy Remesh](https://docs.meshy.ai/en/webapp/guides/3d-model/remesh) already changes topology and polygon count. Our product must offer a coherent measurable preparation workflow; simplification itself is not our invention.
- [Meshy game preparation guidance](https://help.meshy.ai/en/articles/15723950-how-to-make-meshy-models-game-ready) documents additional work after generation, including LOD preparation and normal/texture checks. Specific triangle budgets are scene-dependent, not universal performance promises.
- [AssetForge](https://github.com/InFaNsO/AssetForge) is an existing Blender-oriented multi-stage asset pipeline. Its roadmap spans generation, topology, UVs, baking, rigging, LOD and export. We keep a smaller static-prop workflow and separate source inspection from output claims.
- [meshoptimizer](https://github.com/zeux/meshoptimizer) provides the actual native simplifier; target ratios can be constrained by topology, seams and error settings. The exact vendored commit and licensing are recorded in third_party/meshoptimizer/UPSTREAM.json.
- [Unity glTFast installation](https://docs.unity3d.com/Packages/com.unity.cloud.gltfast@6.1/manual/installation.html) is an explicit importer dependency for the GLB handoff; stock Unity import is not assumed. [LODGroup.SetLODs](https://docs.unity3d.com/ScriptReference/LODGroup.SetLODs.html) supplies the Unity assembly interface.

## Local runtime finding
Installed Blender5.2.2 LTS reports its version, but command-line startup exits139 before Python during Metal initialization on this host. Repeating the same failing launch would not verify processing. Core v1 therefore does not depend on Blender. No system security settings or Blender installation were modified. Baking, automatic retopology and animated-character preparation are not claimed.

## Review principles
Validate untrusted buffers before calling native code. Preserve materials/embedded image data and reject unsupported features instead of silently dropping them. Show actual exported counts and file bytes, with source/output hashes. The inspection preview uses flat geometry shading and does not prove textured visual equivalence. Triangle reduction alone is not an FPS benchmark.
