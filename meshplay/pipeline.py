"""Shared CLI/API processing, original demo, reports and export measurements."""
import copy
import hashlib
import functools
import html
import json
import math
import struct
import time
import zipfile
from pathlib import Path
from . import __version__
from .formats import Model, Primitive, ModelError, finite, load_model, write_glb
from .native import Native
APP = 'Mesh to Play'
PROFILES = {
    'balanced': {'ratios': [0.5, 0.25, 0.1], 'error': 0.01},
    'gentle': {'ratios': [0.75, 0.5, 0.25], 'error': 0.005},
    'compact': {'ratios': [0.35, 0.15, 0.05], 'error': 0.02},
}
LIMITATIONS = [
    'Static triangle meshes only; no skins, animations, morph targets, compression or required extensions.',
    'No texture baking, resizing, atlasing, rigging or collider generation. Embedded PNG/JPEG bytes and material data are preserved.',
    'Ratios are requests: topology, UV seams, material boundaries and the error bound may prevent reaching a target. Each primitive target is floored to an integer with a minimum of one triangle.',
    'Meshoptimizer error is its per-primitive relative geometric/attribute metric, not a measured image difference or engine performance guarantee.',
    'GLB scenes are flattened to world-space instances; output coordinates are y-up. Source GLB units are meters; OBJ units are unknown until explicitly scaled.',
    'The browser shows geometry inspection shading, not textured material appearance.',
    'Unity integration is supplied but has not been executed in Unity.',
]

def sha(data):
    return hashlib.sha256(data).hexdigest()

def validate_options(options):
    if not isinstance(options, dict) or set(options) - {'mode', 'ratios', 'error', 'height', 'bottom_center'}:
        raise ModelError('Unknown job option or invalid options object.')
    validated = {
        'mode': 'prepare',
        'ratios': [0.5, 0.25, 0.1],
        'error': 0.01,
        'height': None,
        'bottom_center': False,
    }
    validated.update(options)
    if validated['mode'] not in ('inspect', 'prepare'):
        raise ModelError('Mode must be inspect or prepare.')
    r = validated['ratios']
    if not isinstance(r, list) or len(r) != 3 or any((not finite(x) or not 0 < x < 1 for x in r)) or (not r[0] > r[1] > r[2]):
        raise ModelError('Supply three strictly decreasing finite ratios between 0 and 1.')
    if not finite(validated['error']) or not 0 <= validated['error'] <= 1:
        raise ModelError('Relative error must be finite and between 0 and 1.')
    if validated['height'] is not None and (not finite(validated['height']) or not 1e-06 <= validated['height'] <= 10000):
        raise ModelError('Longest dimension must be between 0.000001 and 10000 meters.')
    if not isinstance(validated['bottom_center'], bool):
        raise ModelError('bottom_center must be a boolean.')
    return validated

def preview(model):
    """Encode the complete world-space model as exact-length MSH1 buffers."""
    vertex_count = model.vertices
    triangle_count = model.triangles
    out = bytearray(12 + vertex_count * 12 + triangle_count * 12)
    struct.pack_into('<4sII', out, 0, b'MSH1', vertex_count, triangle_count)
    offset = 12
    base = 0
    for p in model.primitives:
        for v in p.attrs['POSITION']:
            struct.pack_into('<3f', out, offset, *v)
            offset += 12
    offset = 12 + vertex_count * 12
    for p in model.primitives:
        for i in p.indices:
            struct.pack_into('<I', out, offset, i + base)
            offset += 4
        base += len(p.attrs['POSITION'])
    return bytes(out)

@functools.lru_cache(maxsize=1)
def create_demo():
    """Original toroidal reactor shell, 131072 triangles; no downloaded assets."""
    major = 512
    minor = 128
    positions = []
    norm = []
    uv = []
    indices = []
    for j in range(minor + 1):
        v = 2 * math.pi * j / minor
        cv = math.cos(v)
        sv = math.sin(v)
        for i in range(major + 1):
            u = 2 * math.pi * i / major
            cu = math.cos(u)
            su = math.sin(u)
            rib = 0.035 * math.cos(24 * u) * (0.65 + 0.35 * cv)
            rad = 1.3 + (0.42 + rib) * cv
            positions.append((rad * cu, (0.58 + rib) * sv, rad * su))
            norm.append((cv * cu, sv, cv * su))
            uv.append((i / major, j / minor))
    for j in range(minor):
        for i in range(major):
            a = j * (major + 1) + i
            b = a + major + 1
            indices.extend((a, b, a + 1, a + 1, b, b + 1))
    p = Primitive(
        {'POSITION': positions, 'NORMAL': norm, 'TEXCOORD_0': uv},
        indices,
        0,
        'Original ribbed reactor shell',
    )
    return write_glb(Model(
        [p],
        materials=[
            {
                'name': 'Oxidized teal alloy',
                'pbrMetallicRoughness': {'baseColorFactor': [0.08, 0.5, 0.42, 1], 'metallicFactor': 0.65, 'roughnessFactor': 0.38},
            },
        ],
    ))

def normalized(model, height, bottom):
    """Apply uniform longest-dimension scaling and an explicit bottom-center pivot."""
    model = copy.deepcopy(model)
    lo, hi = model.bounds
    dims = [hi[i] - lo[i] for i in range(3)]
    scale = height / max(dims) if height is not None and max(dims) > 0 else 1
    if height is not None and max(dims) <= 0:
        raise ModelError('Cannot scale zero-size geometry.')
    pivot = [(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2] if bottom else [0, 0, 0]
    for p in model.primitives:
        p.attrs['POSITION'] = [tuple(((v[k] - pivot[k]) * scale for k in range(3))) for v in p.attrs['POSITION']]
    return (model, {
        'uniform_scale': scale,
        'pivot_subtracted': pivot,
        'requested_longest_dimension_meters': height,
    })

def findings(model):
    out = []
    if model.unreferenced_vertices_removed:
        out.append({
            'id': 'unreferenced-vertices',
            'severity': 'info',
            'title': 'Unreferenced vertices excluded from geometry measurements',
            'detail': 'Only triangle-referenced vertices determine bounds, scale and previews. All supported streams use the same compaction remap; original source bytes are unchanged.',
            'count': model.unreferenced_vertices_removed,
            'method': 'Counted unreferenced slots across expanded GLB primitives, or unused OBJ position records.',
        })
    degenerate = 0
    for primitive in model.primitives:
        positions = primitive.attrs['POSITION']
        for offset in range(0, len(primitive.indices), 3):
            a, b, c = [positions[primitive.indices[offset + i]] for i in range(3)]
            u = [b[i] - a[i] for i in range(3)]
            v = [c[i] - a[i] for i in range(3)]
            cross = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            if sum((x * x for x in cross)) == 0:
                degenerate += 1
    if degenerate:
        out.append({
            'id': 'degenerate',
            'severity': 'warning',
            'title': 'Zero-area triangles',
            'detail': 'Exactly zero-area triangles were counted. No repair or visual-quality guarantee is implied.',
            'count': degenerate,
            'method': 'Exact zero cross-product area in decoded world coordinates.',
        })
    if model.triangles >= 100000:
        out.append({
            'id': 'dense',
            'severity': 'info',
            'title': 'Dense source geometry',
            'detail': f'{model.triangles:,} source triangles. Review achieved reduction and silhouette before engine use.',
            'count': model.triangles,
            'method': 'Counted validated triangle indices.',
        })
    if not all(('TEXCOORD_0' in p.attrs for p in model.primitives)):
        out.append({
            'id': 'uv',
            'severity': 'info',
            'title': 'Some primitives have no UV0',
            'detail': 'Existing attributes are preserved; no UV unwrap or texture baking is performed.',
        })
    for note in model.notes:
        out.append({
            'id': 'obj-materials',
            'severity': 'warning',
            'title': 'Geometry-only OBJ import',
            'detail': note,
        })
    return out

def process(data, name, out, options, native_library=None):
    """Process a validated static model into a newly created output directory.

    Each LOD starts from the original primitive and is measured by decoding its
    exported GLB. Failures never advertise partial output as a completed job.
    """
    options = validate_options(options)
    started = time.monotonic()
    original = load_model(data, name)
    model, conversion = normalized(original, options['height'], options['bottom_center'])
    native = Native(native_library) if options['mode'] == 'prepare' else None
    out = Path(out)
    try:
        out.mkdir(parents=True, exist_ok=False)
    except FileExistsError as e:
        raise ModelError('Output directory already exists. Choose a new directory; originals and previous results are never overwritten.') from e
    report = {
        'schema_version': 1,
        'app': APP,
        'version': __version__,
        'source': {
            'name': str(name)[:240],
            'sha256': sha(data),
            'format': 'GLB' if name.lower().endswith('.glb') else 'OBJ',
            'bytes': len(data),
        },
        'summary': {
            'vertices': original.vertices,
            'triangles': original.triangles,
            'dimensions': original.dimensions,
            'units': 'meters' if name.lower().endswith('.glb') else 'unknown OBJ units',
        },
        'findings': findings(original),
        'results': [],
        'limitations': LIMITATIONS.copy(),
        'preview_note': 'Complete source and candidate geometry; no preview subset or decimation.',
        'settings': options,
        'conversion': dict(conversion, applied_to_outputs=options['mode'] == 'prepare'),
        'provenance': {
            'processor': 'meshoptimizer attribute-aware simplifier' if native else 'inspection only',
            'native_library_sha256': sha(native.path.read_bytes()) if native else None,
            'input_sha256': sha(data),
            'unreferenced_vertices_removed': original.unreferenced_vertices_removed,
            'bounds_method': 'World coordinates of triangle-referenced vertices only.',
            'outputs': {},
        },
    }
    source_name = 'source.glb' if name.lower().endswith('.glb') else 'source.obj'
    (out / source_name).write_bytes(data)
    (out / 'before.msh').write_bytes(preview(original))
    if native:
        previous = original.triangles
        for level, ratio in enumerate(options['ratios']):
            primitives = []
            stats = []
            for p in model.primitives:
                mesh_primitives, primitive_statistics = native.simplify(p, ratio, options['error'])
                primitives.append(mesh_primitives)
                stats.append(primitive_statistics)
            candidate = Model(
                primitives,
                model.materials,
                model.images,
                model.textures,
                model.samplers,
                model.notes,
                model.extensions_used,
            )
            raw_bytes = write_glb(candidate)
            filename = f'LOD{level}.glb'
            measured = load_model(raw_bytes, filename)
            if measured.triangles > previous:
                raise ModelError('Simplification produced non-monotonic LOD counts; adjust targets/error and retry.')
            previous = measured.triangles
            (out / filename).write_bytes(raw_bytes)
            row = {
                'name': filename,
                'triangles': measured.triangles,
                'vertices': measured.vertices,
                'bytes': len(raw_bytes),
                'dimensions': measured.dimensions,
                'requested_ratio': ratio,
                'requested_triangles': sum((s['requested_triangles'] for s in stats)),
                'achieved_ratio': measured.triangles / original.triangles,
                'triangle_reduction': 1 - measured.triangles / original.triangles,
                'byte_reduction': 1 - len(raw_bytes) / len(data),
                'primitives': stats,
                'sha256': sha(raw_bytes),
            }
            report['results'].append(row)
            if row['triangles'] > row['requested_triangles']:
                report['findings'].append({
                    'id': f'lod{level}-target',
                    'severity': 'info',
                    'title': f'LOD{level} stopped above target',
                    'detail': f"Requested {row['requested_triangles']:,}; achieved {row['triangles']:,}. Topology, seam and error constraints remain active.",
                })
            if level == 0:
                (out / 'after.msh').write_bytes(preview(measured))
    elif options['height'] is not None or options['bottom_center']:
        report['limitations'].append('Inspect mode reports source geometry only; conversion settings are applied only during preparation.')
    report['elapsed_seconds'] = round(time.monotonic() - started, 4)
    finish_artifacts(out, report)
    return report


def file_hash(path):
    """Hash large artifacts with bounded memory."""
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def report_html(report):
    """Render a standalone review report; all model-derived text is escaped."""
    escape = html.escape
    source = report['source']
    summary = report['summary']
    rows = []
    for result in report['results']:
        rows.append(
            '<tr><th>' + escape(result['name']) + '</th><td>'
            + f"{result['requested_triangles']:,}</td><td>{result['triangles']:,}</td>"
            + f"<td>{result['vertices']:,}</td><td>{result['bytes']:,}</td>"
            + f"<td>{result['triangle_reduction']:.1%}</td></tr>"
        )
    findings_html = ''.join(
        '<li><strong>' + escape(item['title']) + '</strong> — '
        + escape(item['detail']) + '</li>' for item in report['findings']
    ) or '<li>No additional findings from these limited static checks.</li>'
    limitations_html = ''.join('<li>' + escape(item) + '</li>' for item in report['limitations'])
    dimensions = ' × '.join(f'{dimension:.5g}' for dimension in summary['dimensions'])
    stylesheet = (
        'body{font:16px/1.6 system-ui;max-width:1080px;margin:40px auto;padding:24px;'
        'background:#101b20;color:#e3eeeb}h1,h2{line-height:1.2;color:#98edcc}'
        'table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:10px;'
        'border-bottom:1px solid #49625d}pre{white-space:pre-wrap;overflow-wrap:anywhere}'
        'code{overflow-wrap:anywhere}.meta{color:#b5c6c0}.table{overflow-x:auto}'
    )
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width">'
        '<title>Mesh to Play report</title><style>' + stylesheet + '</style>'
        '<h1>Mesh to Play</h1><h2>' + escape(source['name']) + '</h2>'
        '<p class="meta">Measured static geometry. Review silhouette, materials and shading '
        'in your target engine; no FPS or image-equivalence guarantee.</p>'
        + f"<p><strong>{summary['triangles']:,} triangles · {summary['vertices']:,} vertices · "
        + f"{source['bytes']:,} input bytes</strong></p><p>Source dimensions: "
        + escape(dimensions + ' (' + summary['units'] + ')') + '</p>'
        '<h2>LOD candidates</h2><div class="table"><table><thead><tr><th>File</th>'
        '<th>Requested triangles</th><th>Achieved triangles</th><th>Vertices</th>'
        '<th>Bytes</th><th>Triangle reduction</th></tr></thead><tbody>'
        + (''.join(rows) or '<tr><td colspan="6">Inspect only — no LODs generated.</td></tr>')
        + '</tbody></table></div><h2>Findings</h2><ul>' + findings_html
        + '</ul><h2>Scope and limitations</h2><ul>' + limitations_html
        + '</ul><h2>Provenance</h2><p>Original SHA256: <code>' + escape(source['sha256'])
        + '</code></p><p>checksums.json covers the report and payload files. '
        'bundle.zip.sha256 covers the ZIP wrapper. No checksum includes itself.</p>'
        '<details><summary>Full structured report</summary><pre>'
        + escape(json.dumps(report, indent=2, allow_nan=False)) + '</pre></details></html>'
    )


def finish_artifacts(output_directory, report):
    """Write reports, independent checksums, Unity handoff and the portable ZIP."""
    for path in sorted(output_directory.iterdir()):
        report['provenance']['outputs'][path.name] = {
            'sha256': file_hash(path), 'bytes': path.stat().st_size,
        }
    report_path = output_directory / 'report.json'
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf8')
    (output_directory / 'report.html').write_text(report_html(report), encoding='utf8')
    checksums = {
        path.name: {'sha256': file_hash(path), 'bytes': path.stat().st_size}
        for path in sorted(output_directory.iterdir()) if path.is_file()
    }
    (output_directory / 'checksums.json').write_text(
        json.dumps(checksums, indent=2) + '\n', encoding='utf8',
    )
    archive_path = output_directory / 'bundle.zip'
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_directory.iterdir()):
            if path != archive_path:
                archive.write(path, path.name)
        unity_directory = Path(__file__).resolve().parents[1] / 'integrations' / 'unity'
        for path in sorted(unity_directory.glob('*')):
            if path.is_file():
                archive.write(path, 'unity/' + path.name)
    (output_directory / 'bundle.zip.sha256').write_text(
        file_hash(archive_path) + '  bundle.zip\n', encoding='ascii',
    )


def inspect_model(data, name, out):
    return process(data, name, out, {'mode': 'inspect'})

def prepare_model(data, name, out, ratios=None, error=0.01, height=None, bottom_center=False, native_library=None):
    return process(
        data,
        name,
        out,
        {
            'mode': 'prepare',
            'ratios': ratios if ratios is not None else [0.5, 0.25, 0.1],
            'error': error,
            'height': height,
            'bottom_center': bottom_center,
        },
        native_library,
    )
