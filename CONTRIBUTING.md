# Contributing

A useful bug report includes the exact command or UI settings, Python/OS/compiler versions, and `report.json` if one was produced. Describe the expected result and what you observed. A reduced model that you own or may redistribute is especially helpful. Do not upload confidential client assets.

For geometry changes, add a small deterministic fixture that reproduces the problem and verify the emitted GLB by reading it again. Include all affected streams: positions, normals, UVs, custom attributes and material references. A lower triangle count alone does not demonstrate a correct result.

Build the pinned native source and run the gates in README before proposing a change. Changes to the vendored source require an explicit upstream revision, license review and updated checksums. Keep external resources and unsupported animated features rejected until a complete supported path exists.

If testing in Unity, record the Unity version, glTFast package version, render pipeline, import messages and whether LOD transitions/materials actually work. Current documentation does not claim Unity execution.
