# Unity handoff

This Editor helper has **not been compiled or executed in Unity**. Validate it with your Unity version, render pipeline and glTFast package before production use.

1. In Unity Package Manager, install the official package by name: `com.unity.cloud.gltfast`. Unity's package owns GLB importing; this project does not implement a Unity GLB importer. Follow the package's Unity-version and shader setup requirements.
2. Copy `LOD0.glb`, `LOD1.glb` and `LOD2.glb` together into one folder under your project's `Assets/` directory. Wait for import and resolve Console errors. The output already contains world-space transforms and any requested scale/pivot conversion; do not apply conversion twice.
3. Copy `MeshToPlayLODGroup.cs` into `Assets/Editor/`.
4. Choose **Tools → Mesh to Play → Build LODGroup from imported GLBs**, then select that asset folder. A new scene object is created with three imported prefab instances and one `LODGroup`; creation supports Undo.
5. Review the initial screen-relative transitions (0.60, 0.25, 0.08), culling below the last transition, shading and material/texture appearance. These are starting settings, not target-device performance recommendations. Save the scene or create a prefab yourself.

`LOD0` is the first requested reduction, not the original high-resolution mesh. The bundle also includes the unchanged original as `source.glb` or `source.obj`. OBJ materials are not imported by Mesh to Play.

Official references (checked 2026-10-17):

- [Unity glTFast package and installation](https://github.com/Unity-Technologies/com.unity.cloud.gltfast/blob/main/Packages/com.unity.cloud.gltfast/README.md)
- [Official Editor import instructions](https://github.com/Unity-Technologies/com.unity.cloud.gltfast/blob/main/Packages/com.unity.cloud.gltfast/Documentation~/ImportEditor.md)
- [Unity LODGroup documentation](https://docs.unity3d.com/Manual/class-LODGroup.html)
