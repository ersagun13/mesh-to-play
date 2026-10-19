"""Validated ctypes boundary to the pinned, locally compiled meshoptimizer.

No library is searched through the operating system's default search path.
An explicit path or MESHOPT_LIBRARY is trusted local configuration, never upload data.
"""
import ctypes
import os
import platform
from pathlib import Path

from .formats import MAX_TRIANGLES, MAX_VERTICES, ModelError, Primitive, finite

ROOT = Path(__file__).resolve().parents[1]
ATTRIBUTE_WEIGHT = 0.1
MAX_ATTRIBUTE_VALUE = 1_000_000


def library_path(explicit=None):
    """Find a configured library or this checkout's platform-specific build."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.environ.get('MESHOPT_LIBRARY'):
        return Path(os.environ['MESHOPT_LIBRARY']).expanduser().resolve()
    if os.name == 'nt':
        filename = 'meshoptimizer.dll'
    elif platform.system() == 'Darwin':
        filename = 'libmeshoptimizer.dylib'
    else:
        filename = 'libmeshoptimizer.so'
    return ROOT / 'native' / filename


def capability():
    """Return the UI capability state, including an actionable missing-build message."""
    try:
        Native()
        return {'available': True, 'message': 'Pinned local meshoptimizer is available.'}
    except ModelError as error:
        return {'available': False, 'message': str(error)}


class Native:
    """Own a ctypes library and keep every input/output buffer alive during a call."""

    def __init__(self, path=None):
        path = library_path(path)
        if not path.is_file():
            raise ModelError(
                'Native simplifier unavailable. Run python3 tools/build_native.py '
                'with a C++ compiler, or pass --native-library /absolute/path/to/library.'
            )
        try:
            self.library = ctypes.CDLL(str(path))
            self.simplify_function = self.library.meshopt_simplifyWithAttributes
        except (OSError, AttributeError) as error:
            raise ModelError(
                'Cannot load meshoptimizer. Rebuild with python3 tools/build_native.py: '
                + str(error)[:160]
            ) from error
        self.path = path
        # Exact signature from the vendored meshoptimizer.h. size_t must not use int.
        uint_pointer = ctypes.POINTER(ctypes.c_uint)
        float_pointer = ctypes.POINTER(ctypes.c_float)
        self.simplify_function.argtypes = [
            uint_pointer, uint_pointer, ctypes.c_size_t,
            float_pointer, ctypes.c_size_t, ctypes.c_size_t,
            float_pointer, ctypes.c_size_t,
            float_pointer, ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
            ctypes.c_float, ctypes.c_uint, float_pointer,
        ]
        self.simplify_function.restype = ctypes.c_size_t

    @staticmethod
    def _validate_primitive(primitive, ratio, error):
        if not finite(ratio) or not 0 < ratio < 1:
            raise ModelError('Native ratio must be finite and between 0 and 1.')
        if not finite(error) or not 0 <= error <= 1:
            raise ModelError('Native relative error must be finite and between 0 and 1.')
        positions = primitive.attrs.get('POSITION', [])
        vertex_count = len(positions)
        index_count = len(primitive.indices)
        if not 0 < vertex_count <= MAX_VERTICES:
            raise ModelError('Native vertex count is outside supported limits.')
        if not 0 < index_count <= MAX_TRIANGLES * 3 or index_count % 3:
            raise ModelError('Native triangle index count is invalid.')
        if any(type(index) is not int or not 0 <= index < vertex_count
               for index in primitive.indices):
            raise ModelError('Native triangle index is out of range.')
        if any(len(position) != 3 or not all(finite(x) and abs(x) < 1e30 for x in position)
               for position in positions):
            raise ModelError('Native positions must be finite XYZ values.')
        attribute_keys = sorted(key for key in primitive.attrs if key != 'POSITION')
        scalar_count = 0
        for key in attribute_keys:
            values = primitive.attrs[key]
            if len(values) != vertex_count or not values or not values[0]:
                raise ModelError('Native attribute count is invalid.')
            width = len(values[0])
            scalar_count += width
            if any(len(value) != width or not all(
                    finite(x) and abs(x) <= MAX_ATTRIBUTE_VALUE for x in value)
                   for value in values):
                raise ModelError(
                    'Preparation requires finite attributes with magnitude at most 1,000,000. '
                    'Inspect remains available.'
                )
        if scalar_count > 32:
            raise ModelError(
                'Preparation supports at most 32 non-position scalar attributes per vertex. '
                'Inspect remains available.'
            )
        return positions, attribute_keys, scalar_count

    def simplify(self, primitive, ratio, error):
        """Return compacted original streams and the native algorithm's measured error.

        Positions are normalized for stable native float arithmetic; returned indices
        still reference original streams. No permissive collapse flag is enabled.
        """
        positions, attribute_keys, scalar_count = self._validate_primitive(primitive, ratio, error)
        vertex_count = len(positions)
        index_count = len(primitive.indices)
        minimum = [min(vertex[axis] for vertex in positions) for axis in range(3)]
        maximum = [max(vertex[axis] for vertex in positions) for axis in range(3)]
        extent = max(maximum[axis] - minimum[axis] for axis in range(3))
        if extent <= 0:
            raise ModelError('Cannot simplify a zero-size primitive.')

        indices = (ctypes.c_uint * index_count)(*primitive.indices)
        # Upstream requires worst-case capacity index_count, never target_index_count.
        destination = (ctypes.c_uint * index_count)()
        native_positions = (ctypes.c_float * (vertex_count * 3))(*(
            (vertex[axis] - minimum[axis]) / extent
            for vertex in positions for axis in range(3)
        ))
        attributes = None
        weights = None
        if scalar_count:
            attributes = (ctypes.c_float * (vertex_count * scalar_count))(*(
                component for vertex in range(vertex_count) for key in attribute_keys
                for component in primitive.attrs[key][vertex]
            ))
            weights = (ctypes.c_float * scalar_count)(*([ATTRIBUTE_WEIGHT] * scalar_count))
        target_indices = max(3, int(index_count // 3 * ratio) * 3)
        result_error = ctypes.c_float()
        result_count = self.simplify_function(
            destination, indices, index_count, native_positions, vertex_count, 12,
            attributes, scalar_count * 4, weights, scalar_count, None,
            target_indices, error, 0, ctypes.byref(result_error),
        )
        if not 3 <= result_count <= index_count or result_count % 3:
            raise ModelError('Native simplifier returned an invalid index count.')
        if not finite(result_error.value):
            raise ModelError('Native simplifier returned a nonfinite error.')
        result_indices = list(destination[:result_count])
        if max(result_indices) >= vertex_count:
            raise ModelError('Native simplifier returned an out-of-range index.')

        # Compact every stream with one remap, including custom vertex attributes.
        used_vertices = list(dict.fromkeys(result_indices))
        remap = {vertex: index for index, vertex in enumerate(used_vertices)}
        compact = Primitive(
            {key: [values[index] for index in used_vertices]
             for key, values in primitive.attrs.items()},
            [remap[index] for index in result_indices], primitive.material, primitive.name,
        )
        statistics = {
            'requested_triangles': target_indices // 3,
            'achieved_triangles': result_count // 3,
            'relative_error': result_error.value,
            'attribute_scalar_count': scalar_count,
        }
        return compact, statistics
