# Reproduce the original reactor benchmark

No binary model is downloaded. `create_demo()` in `meshplay/pipeline.py` generates an original toroidal reactor shell with 24 repeated ribs, 512 angular segments, 128 cross-section segments, normals and UV seams. The source has 131,072 triangles and 66,177 vertices. Its embedded material is a teal metallic roughness material; the geometry preview does not render that material.

```sh
python3 tools/build_native.py
python3 run.py demo --out results/demo
python3 run.py prepare results/demo/original-reactor.glb --out results/reactor
```

Source SHA256: `083dcdcb9bd0d64aff160a07bfed067c8dd1429d22fc5d64f48f06e9279bb03a`.

`reactor-report.json` is from an actual macOS native run, with default ratios `[0.5,0.25,0.1]`, relative error `0.01`, no scaling and no pivot conversion. Counts are obtained by re-reading each exported GLB. Small floating point/compiler differences can change collapse choices; reproduce your own measured report for your environment. Native error is the algorithm's relative metric, not a visual image difference.

The recorded engine processing duration was 1.8856 seconds on the development machine; it excludes final report/ZIP packaging and does not establish a throughput promise. The output GLBs measured 65,536 / 32,768 / 13,106 triangles. The README comparison image was rendered from actual exported geometry under the same camera/scale. It shows geometry shading rather than a texture-equivalence or Unity test.
