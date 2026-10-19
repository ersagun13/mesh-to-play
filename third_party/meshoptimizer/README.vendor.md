# Vendored meshoptimizer subset

Unmodified MIT-licensed upstream source from [zeux/meshoptimizer](https://github.com/zeux/meshoptimizer), commit `f0a684f11ead064e176775c9b796f82a55910e92` (header version 1.3).

This repository owns the local inspection workflow, model format handling, reports, application and integration. The native simplification algorithm is meshoptimizer by Arseny Kapoulkine and contributors; see LICENSE.md.

UPSTREAM.json records the pinned revision, upstream Git blob hashes and SHA-256 of every vendored file. `python3 tools/build_native.py` checks all source hashes before compiling a small CPU-only shared library into native/. It performs no downloads and changes no system installation.

The source subset includes simplification, allocator, index remapping, vertex-cache/fetch optimization and quantization. Upstream documentation and source remain authoritative for its error metric and topology-dependent target behavior. Source updates must be explicit: replace the pin, record hashes, compile, run fixture and attribute-preservation checks, and review changed API signatures before using a new revision.
