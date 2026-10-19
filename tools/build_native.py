#!/usr/bin/env python3
"""Build the pinned meshoptimizer subset locally, without downloads or package installs."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ('allocator.cpp', 'simplifier.cpp', 'indexgenerator.cpp',
           'vcacheoptimizer.cpp', 'vfetchoptimizer.cpp', 'quantization.cpp')


class BuildError(RuntimeError):
    pass


def library_name(system):
    names = {'Darwin': 'libmeshoptimizer.dylib', 'Linux': 'libmeshoptimizer.so',
             'Windows': 'meshoptimizer.dll'}
    if system not in names:
        raise BuildError('Supported build platforms: macOS, Linux and Windows.')
    return names[system]


def verify_sources(vendor):
    manifest = json.loads((vendor / 'UPSTREAM.json').read_text(encoding='utf-8'))
    for item in manifest['files']:
        path = (vendor / item['path']).resolve()
        if not path.is_relative_to(vendor.resolve()):
            raise BuildError('Invalid upstream manifest path.')
        if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise BuildError('Source checksum mismatch: ' + item['path'])


def check_library(path):
    library = ctypes.CDLL(str(path.resolve()))
    for name in ('meshopt_simplify', 'meshopt_simplifyWithAttributes',
                 'meshopt_optimizeVertexCache', 'meshopt_optimizeVertexFetchRemap'):
        getattr(library, name)
    return path


def validate_in_subprocess(path):
    # A child unloads its DLL on exit, so Windows can move the checked file.
    code = ('import ctypes,sys; lib=ctypes.CDLL(sys.argv[1]); '
            '[getattr(lib,n) for n in ("meshopt_simplify",'
            '"meshopt_simplifyWithAttributes","meshopt_optimizeVertexCache",'
            '"meshopt_optimizeVertexFetchRemap")]')
    result = subprocess.run([sys.executable, '-c', code, str(path.resolve())],
                            text=True, capture_output=True, timeout=15)
    if result.returncode:
        raise BuildError('Native library validation failed: ' + result.stderr.strip())


def build(root=ROOT, compiler=None):
    vendor = root / 'third_party' / 'meshoptimizer'
    verify_sources(vendor)
    system = platform.system()
    output = root / 'native' / library_name(system)
    compiler = compiler or shutil.which('c++') or shutil.which('clang++') or shutil.which('g++')
    if system == 'Windows':
        compiler = compiler or shutil.which('cl')
    if not compiler:
        raise BuildError('A C++ compiler is required. macOS: Xcode command line tools; '
                         'Linux: g++; Windows: an MSVC Developer Command Prompt or MinGW.')
    output.parent.mkdir(parents=True, exist_ok=True)
    files = [str(vendor / 'src' / name) for name in SOURCES]
    with tempfile.TemporaryDirectory(prefix='meshopt-build-', dir=output.parent) as directory:
        target = Path(directory) / output.name
        if Path(compiler).name.lower() in ('cl', 'cl.exe'):
            args = [compiler, '/nologo', '/LD', '/O2', '/EHsc', '/std:c++14',
                    '/DMESHOPTIMIZER_API=__declspec(dllexport)', *files, '/link', '/OUT:' + str(target)]
        else:
            args = [compiler, '-std=c++11', '-O3', '-DNDEBUG', '-shared', '-fPIC', *files, '-o', str(target)]
        process = subprocess.run(args, cwd=directory, text=True, capture_output=True, timeout=240)
        if process.returncode:
            raise BuildError('Native compilation failed:\n' + (process.stderr or process.stdout).strip())
        # Check before moving: a failed build never replaces a working library.
        validate_in_subprocess(target)
        target.replace(output)
    return check_library(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', help='Path/name of a C++ compiler executable; no shell command.')
    parser.add_argument('--check', action='store_true', help='Verify pinned sources and existing library exports.')
    args = parser.parse_args()
    try:
        verify_sources(ROOT / 'third_party' / 'meshoptimizer')
        if args.check:
            path = check_library(ROOT / 'native' / library_name(platform.system()))
        else:
            path = build(compiler=args.compiler)
        print('Native simplifier ready: ' + str(path))
    except (BuildError, OSError, ValueError, AttributeError, subprocess.SubprocessError) as error:
        print('Build failed: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
