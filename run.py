#!/usr/bin/env python3
"""Mesh to Play CLI; Python 3.10+ and standard library only."""
import argparse
import json
import sys
from pathlib import Path
from meshplay.formats import MAX_BYTES, ModelError
from meshplay.pipeline import create_demo, inspect_model, prepare_model, process

def read_input(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ModelError('Input must be a file of at most 64 MiB.')
    return path.read_bytes()

def worker(folder):
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (150, 150))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
        if sys.platform != 'darwin':
            resource.setrlimit(resource.RLIMIT_AS, (2 * 1024 ** 3, 2 * 1024 ** 3))
    except (ImportError, OSError, ValueError):
        pass
    folder = Path(folder)
    req = json.loads((folder / 'request.json').read_text(encoding='utf8'))
    process(read_input(folder / req['input']), req['filename'], folder / 'result', req['options'])

def main():
    p = argparse.ArgumentParser(description='Inspect static GLB/OBJ geometry and prepare measured Unity LOD candidates.')
    commands = p.add_subparsers(dest='command', required=True)
    for mode in ('inspect', 'prepare'):
        sub = commands.add_parser(mode)
        sub.add_argument('input')
        sub.add_argument('--out', required=True)
        if mode == 'prepare':
            sub.add_argument('--ratios', default='0.5,0.25,0.1')
            sub.add_argument('--error', type=float, default=0.01)
            sub.add_argument('--height', type=float, help='Set longest dimension in meters (uniform scaling).')
            sub.add_argument('--bottom-center', action='store_true')
            sub.add_argument('--native-library')
    demo = commands.add_parser('demo')
    demo.add_argument('--out', required=True)
    server = commands.add_parser('serve')
    server.add_argument('--port', type=int, default=0)
    server.add_argument('--no-browser', action='store_true')
    server.add_argument('--workspace')
    clean = commands.add_parser('cleanup')
    clean.add_argument('--workspace', required=True)
    internal = commands.add_parser('_worker', help=argparse.SUPPRESS)
    internal.add_argument('folder')
    a = p.parse_args()
    try:
        if a.command == '_worker':
            worker(a.folder)
            return 0
        if a.command == 'serve':
            from meshplay.server import serve
            if not 0 <= a.port <= 65535:
                raise ModelError('Port must be between 0 and 65535.')
            serve(a.port, a.no_browser, a.workspace)
            return 0
        if a.command == 'cleanup':
            from meshplay.server import cleanup
            print(f'Removed {cleanup(a.workspace)} generated job directories.')
            return 0
        if a.command == 'demo':
            out = Path(a.out)
            try:
                out.mkdir(parents=True, exist_ok=False)
            except FileExistsError as e:
                raise ModelError('Demo output directory already exists; choose a new one.') from e
            path = out / 'original-reactor.glb'
            path.write_bytes(create_demo())
            print(str(path.resolve()))
            return 0
        data = read_input(a.input)
        if a.command == 'inspect':
            report = inspect_model(data, Path(a.input).name, a.out)
        else:
            try:
                ratios = [float(v) for v in a.ratios.split(',')]
            except ValueError as e:
                raise ModelError('Ratios must be comma-separated finite numbers.') from e
            report = prepare_model(
                data,
                Path(a.input).name,
                a.out,
                ratios,
                a.error,
                a.height,
                a.bottom_center,
                a.native_library,
            )
        print(json.dumps(
            {
                'report': str((Path(a.out) / 'report.json').resolve()),
                'source_triangles': report['summary']['triangles'],
                'results': [{'name': r['name'], 'triangles': r['triangles'], 'bytes': r['bytes']} for r in report['results']],
            },
            indent=2,
        ))
        return 0
    except (ModelError, OSError, MemoryError) as e:
        print('Mesh to Play: ' + str(e), file=sys.stderr)
        return 2
if __name__ == '__main__':
    sys.exit(main())
