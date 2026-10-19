"""Strict, bounded static GLB 2.0 and geometry-only OBJ support."""
import copy
import json
import math
import struct
from dataclasses import dataclass, field
MAX_BYTES = 64 * 1024 * 1024
MAX_VERTICES = 2000000
MAX_TRIANGLES = 2000000
MAX_NODES = 4096
MAX_DECODED_SCALARS = 16_000_000
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942

class ModelError(ValueError):
    pass

def reject_json_constant(value):
    raise ModelError('Nonfinite JSON is unsupported: ' + value)


def integer(x, label, minimum=0, maximum=2 ** 31):
    if isinstance(x, bool) or not isinstance(x, int) or (not minimum <= x <= maximum):
        raise ModelError(f'Invalid {label}.')
    return x

def finite(value):
    """Accept finite machine-representable numbers without overflowing on JSON integers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False

def ref(seq, index, label):
    integer(index, label)
    if index >= len(seq):
        raise ModelError(f'{label} is out of range.')
    return seq[index]

def floats(values, n, label):
    if not isinstance(values, list) or len(values) != n or (not all((finite(x) and abs(x) < 1e+30 for x in values))):
        raise ModelError(f'Invalid finite {label}.')
    return values

@dataclass
class Primitive:
    attrs: dict
    indices: list
    material: object = None
    name: str = 'Primitive'

@dataclass
class Model:
    primitives: list
    materials: list = field(default_factory=list)
    images: list = field(default_factory=list)
    textures: list = field(default_factory=list)
    samplers: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    extensions_used: list = field(default_factory=list)
    unreferenced_vertices_removed: int = 0

    @property
    def triangles(self):
        return sum((len(p.indices) // 3 for p in self.primitives))

    @property
    def vertices(self):
        return sum((len(p.attrs['POSITION']) for p in self.primitives))

    @property
    def bounds(self):
        if not self.primitives:
            raise ModelError('The model has no vertices.')
        minimum = [float('inf')] * 3
        maximum = [float('-inf')] * 3
        for primitive in self.primitives:
            for position in primitive.attrs['POSITION']:
                for axis in range(3):
                    minimum[axis] = min(minimum[axis], position[axis])
                    maximum[axis] = max(maximum[axis], position[axis])
        if not all(finite(value) for value in minimum + maximum):
            raise ModelError('The model has no finite vertices.')
        return minimum, maximum

    @property
    def dimensions(self):
        lo, hi = self.bounds
        return [hi[i] - lo[i] for i in range(3)]

def load_model(data, name):
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise ModelError('Input must contain 1 byte to 64 MiB.')
    try:
        if name.lower().endswith('.glb'):
            return compact_referenced_vertices(read_glb(data))
        if name.lower().endswith('.obj'):
            return compact_referenced_vertices(read_obj(data))
        raise ModelError('Choose an embedded static GLB 2.0 or geometry OBJ file.')
    except ModelError:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, IndexError, struct.error, UnicodeError, OverflowError, RecursionError) as e:
        raise ModelError('Malformed model structure: ' + str(e)[:120]) from e

def compact_referenced_vertices(model):
    """Discard only unreferenced stream slots before bounds, previews and conversion.

    One remap is shared by every attribute. No vertex welding or triangle removal
    occurs, so UV seams, material boundaries and renderable geometry are unchanged.
    The source file bytes are retained separately by the processing pipeline.
    """
    compacted = []
    for primitive in model.primitives:
        used_vertices = sorted(set(primitive.indices))
        unused_count = len(primitive.attrs['POSITION']) - len(used_vertices)
        model.unreferenced_vertices_removed += unused_count
        if not unused_count:
            compacted.append(primitive)
            continue
        remap = {old_index: new_index for new_index, old_index in enumerate(used_vertices)}
        compacted.append(Primitive(
            {key: [values[index] for index in used_vertices]
             for key, values in primitive.attrs.items()},
            [remap[index] for index in primitive.indices],
            primitive.material, primitive.name,
        ))
    model.primitives = compacted
    return model


def read_obj(data):
    positions = []
    uv = []
    normal = []
    vertices = []
    tex = []
    normals = []
    faces = []
    mapping = {}
    notes = []

    def idx(s, arr):
        try:
            i = int(s)
        except ValueError as e:
            raise ModelError('Invalid OBJ face index.') from e
        j = i - 1 if i > 0 else len(arr) + i
        if i == 0 or j < 0 or j >= len(arr):
            raise ModelError('OBJ face index is out of range.')
        return j
    for line in data.decode('utf-8-sig').splitlines():
        a = line.split('#', 1)[0].split()
        if not a:
            continue
        if a[0] in ('v', 'vt', 'vn'):
            count = 2 if a[0] == 'vt' else 3
            if len(a) != count + 1:
                raise ModelError('OBJ requires XYZ vertices, UV coordinates and XYZ normals without extra components.')
            try:
                v = tuple((float(x) for x in a[1:]))
            except ValueError as e:
                raise ModelError('Invalid OBJ number.') from e
            if not all((finite(x) and abs(x) < 1e+30 for x in v)):
                raise ModelError('OBJ geometry must be finite.')
            {'v': positions, 'vt': uv, 'vn': normal}[a[0]].append(v)
            if len(positions) + len(uv) + len(normal) > MAX_VERTICES * 3:
                raise ModelError('Too many OBJ attribute entries.')
        elif a[0] == 'f':
            if len(a) != 4:
                raise ModelError('OBJ faces must be triangles; triangulate polygons before import.')
            for item in a[1:]:
                k = item.split('/')
                if len(k) > 3 or not k[0]:
                    raise ModelError('Invalid OBJ face tuple.')
                key = (idx(k[0], positions), idx(k[1], uv) if len(k) > 1 and k[1] else None, idx(k[2], normal) if len(k) > 2 and k[2] else None)
                if key not in mapping:
                    mapping[key] = len(vertices)
                    vertices.append(positions[key[0]])
                    tex.append(uv[key[1]] if key[1] is not None else None)
                    normals.append(normal[key[2]] if key[2] is not None else None)
                faces.append(mapping[key])
            if len(faces) > MAX_TRIANGLES * 3 or len(vertices) > MAX_VERTICES:
                raise ModelError('OBJ exceeds geometry limits.')
        elif a[0] in ('mtllib', 'usemtl'):
            if 'OBJ material references are not loaded; geometry only.' not in notes:
                notes.append('OBJ material references are not loaded; geometry only.')
        elif a[0] not in ('o', 'g', 's'):
            raise ModelError(f'Unsupported OBJ record: {a[0][:40]}.')
    if not faces:
        raise ModelError('OBJ contains no triangle faces.')
    attrs = {'POSITION': vertices}
    for key, arr in [('TEXCOORD_0', tex), ('NORMAL', normals)]:
        if any((v is not None for v in arr)):
            if any((v is None for v in arr)):
                raise ModelError('Mixed missing OBJ vertex attributes are unsupported.')
            attrs[key] = arr
    referenced_positions = {key[0] for key in mapping}
    return Model(
        [Primitive(attrs, faces)], notes=notes,
        unreferenced_vertices_removed=len(positions) - len(referenced_positions),
    )
IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]

def mul(a, b):
    """Multiply glTF column-major matrices, retaining parent-before-child order."""
    return [sum((a[k * 4 + r] * b[c * 4 + k] for k in range(4))) for c in range(4) for r in range(4)]

def node_matrix(n):
    if 'matrix' in n:
        if any((k in n for k in ('translation', 'rotation', 'scale'))):
            raise ModelError('Node cannot combine matrix and TRS.')
        m = floats(n['matrix'], 16, 'node matrix')
        if any((abs(m[i]) > 1e-08 for i in (3, 7, 11))) or abs(m[15] - 1) > 1e-08:
            raise ModelError('Only affine node transforms are supported.')
        return m
    t = floats(n.get('translation', [0, 0, 0]), 3, 'translation')
    s = floats(n.get('scale', [1, 1, 1]), 3, 'scale')
    q = floats(n.get('rotation', [0, 0, 0, 1]), 4, 'rotation')
    if abs(sum((x * x for x in q)) - 1) > 0.0001:
        raise ModelError('Node quaternion must have unit length.')
    x, y, z, w = q
    r = [
        1 - 2 * y * y - 2 * z * z,
        2 * x * y + 2 * w * z,
        2 * x * z - 2 * w * y,
        0,
        2 * x * y - 2 * w * z,
        1 - 2 * x * x - 2 * z * z,
        2 * y * z + 2 * w * x,
        0,
        2 * x * z + 2 * w * y,
        2 * y * z - 2 * w * x,
        1 - 2 * x * x - 2 * y * y,
        0,
        *t,
        1,
    ]
    return [r[c * 4 + i] * (s[c] if c < 3 else 1) for c in range(4) for i in range(4)]

def transform(p, m):
    """Bake an affine transform, use inverse-transpose normals and repair mirrored winding.

    Tangents are vectors rather than normals; their handedness changes under reflection.
    Vertex streams remain synchronized and materials remain primitive boundaries.
    """
    a, b, c = (m[0], m[4], m[8])
    d, e, f = (m[1], m[5], m[9])
    g, h, i = (m[2], m[6], m[10])
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if not finite(det) or det == 0:
        raise ModelError('Singular or unbounded transforms are unsupported.')
    cof = [
        e * i - f * h,
        f * g - d * i,
        d * h - e * g,
        c * h - b * i,
        a * i - c * g,
        b * g - a * h,
        b * f - c * e,
        c * d - a * f,
        a * e - b * d,
    ]
    out = copy.deepcopy(p)
    out.attrs['POSITION'] = [tuple((sum((m[j * 4 + k] * v[j] for j in range(3))) + m[12 + k] for k in range(3))) for v in p.attrs['POSITION']]
    for key in ('NORMAL', 'TANGENT'):
        if key not in p.attrs:
            continue
        vals = []
        for v in p.attrs[key]:
            if key == 'NORMAL':
                xyz = [sum((cof[k * 3 + j] * v[j] for j in range(3))) / det for k in range(3)]
            else:
                xyz = [sum((m[j * 4 + k] * v[j] for j in range(3))) for k in range(3)]
            length = math.hypot(*xyz)
            if not finite(length) or length == 0:
                raise ModelError('Zero length normals or tangents are unsupported.')
            vals.append(tuple((x / length for x in xyz)) + ((v[3] * (-1 if det < 0 else 1),) if key == 'TANGENT' else ()))
        out.attrs[key] = vals
    if det < 0:
        out.indices = [p.indices[j + k] for j in range(0, len(p.indices), 3) for k in (0, 2, 1)]
    if not all((finite(x) and abs(x) < 1e+30 for v in out.attrs['POSITION'] for x in v)):
        raise ModelError('Transformed coordinates exceed supported range.')
    return out

def validate_vertex_accessor(semantic, component_type, accessor_type, normalized):
    """Accept supported glTF stream types; reject custom integers to avoid ID rounding."""
    float_stream = component_type == 5126 and not normalized
    normalized_unsigned = component_type in (5121, 5123) and normalized is True
    if semantic in ('POSITION', 'NORMAL'):
        valid = accessor_type == 'VEC3' and float_stream
    elif semantic == 'TANGENT':
        valid = accessor_type == 'VEC4' and float_stream
    elif semantic.startswith('TEXCOORD_'):
        valid = accessor_type == 'VEC2' and (float_stream or normalized_unsigned)
    elif semantic.startswith('COLOR_'):
        valid = accessor_type in ('VEC3', 'VEC4') and (float_stream or normalized_unsigned)
    else:
        valid = float_stream
    if not valid:
        raise ModelError('Unsupported component/type for ' + semantic + '. Custom attributes require FLOAT.')


def read_glb(data):
    """Decode a bounded embedded static scene without reading any external resources.

    View and accessor bounds are validated before unpacking. A cumulative scalar
    budget prevents shared buffers from causing unbounded repeated allocations.
    The node graph is validated before recursively expanding instances.
    """
    if len(data) < 20:
        raise ModelError('Truncated GLB.')
    magic, version, length = struct.unpack_from('<4sII', data)
    if magic != b'glTF' or version != 2 or length != len(data):
        raise ModelError('Expected a complete GLB 2.0 file.')
    chunks = []
    offset = 12
    while offset < len(data):
        if offset + 8 > len(data):
            raise ModelError('Truncated GLB chunk.')
        size, kind = struct.unpack_from('<II', data, offset)
        offset += 8
        if size % 4 or offset + size > len(data):
            raise ModelError('Invalid GLB chunk length.')
        chunks.append((kind, data[offset:offset + size]))
        offset += size
    if not chunks or chunks[0][0] != JSON_CHUNK or len(chunks) > 2 or (len(chunks) == 2 and chunks[1][0] != BIN_CHUNK):
        raise ModelError('GLB requires JSON followed by one embedded BIN chunk.')
    try:
        document = json.loads(
            chunks[0][1].decode('utf8'),
            parse_constant=reject_json_constant,
        )
    except (ValueError, UnicodeError) as e:
        raise ModelError('Invalid GLB JSON.') from e

    def validate_json(value, depth=0):
        if depth > 128:
            raise ModelError('JSON nesting exceeds 128 levels.')
        if isinstance(value, float) and (not math.isfinite(value)):
            raise ModelError('JSON contains nonfinite numbers.')
        if isinstance(value, dict):
            for v in value.values():
                validate_json(v, depth + 1)
        elif isinstance(value, list):
            for v in value:
                validate_json(v, depth + 1)
    validate_json(document)
    if not isinstance(document, dict) or document.get('asset', {}).get('version') != '2.0':
        raise ModelError('GLB asset version must be 2.0.')
    if document.get('cameras') or document.get('extensions'):
        raise ModelError('Cameras and document-level extensions are unsupported.')
    if document.get('animations') or document.get('skins'):
        raise ModelError('Animated or skinned assets are unsupported.')
    if document.get('extensionsRequired'):
        raise ModelError('Required glTF extensions are unsupported: ' + str(document['extensionsRequired']))
    binary = chunks[1][1] if len(chunks) > 1 else b''
    buffers = document.get('buffers', [])
    if len(buffers) != 1 or 'uri' in buffers[0]:
        raise ModelError('GLB must have one embedded buffer; external resources are never read.')
    buffer_length = integer(buffers[0]['byteLength'], 'buffer length', 0, MAX_BYTES)
    if not buffer_length <= len(binary) <= buffer_length + 3:
        raise ModelError('GLB buffer length mismatch.')
    views = document.get('bufferViews', [])
    accessors = document.get('accessors', [])
    for v in views:
        if v.get('buffer', 0) != 0 or v.get('extensions'):
            raise ModelError('Unsupported buffer view.')
        offset = integer(v.get('byteOffset', 0), 'view offset')
        byte_size = integer(v['byteLength'], 'view length')
        if offset + byte_size > buffer_length:
            raise ModelError('Buffer view is outside its buffer.')

    def view(index):
        v = ref(views, index, 'buffer view')
        offset = v.get('byteOffset', 0)
        return (v, binary[offset:offset + v['byteLength']])
    sizes = {
        5120: ('b', 1),
        5121: ('B', 1),
        5122: ('h', 2),
        5123: ('H', 2),
        5125: ('I', 4),
        5126: ('f', 4),
    }
    counts = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}

    decoded_scalar_count = 0

    def accessor(index, indices=False):
        nonlocal decoded_scalar_count
        accessor_definition = ref(accessors, index, 'accessor')
        if 'sparse' in accessor_definition or 'bufferView' not in accessor_definition or accessor_definition.get('extensions'):
            raise ModelError('Sparse, compressed or missing accessor buffers are unsupported.')
        count = integer(
            accessor_definition['count'],
            'accessor count',
            1,
            MAX_TRIANGLES * 3 if indices else MAX_VERTICES,
        )
        component_type = accessor_definition['componentType']
        accessor_type = accessor_definition['type']
        if component_type not in sizes or accessor_type not in counts:
            raise ModelError('Unsupported accessor component/type.')
        if indices and (accessor_type != 'SCALAR' or component_type not in (5121, 5123, 5125) or accessor_definition.get('normalized', False)):
            raise ModelError('Indices must be unsigned integer scalars.')
        fmt, byte_size = sizes[component_type]
        n = counts[accessor_type]
        decoded_scalar_count += count * n
        if decoded_scalar_count > MAX_DECODED_SCALARS:
            raise ModelError('Decoded accessor budget exceeded (16 million scalar values).')
        v, b = view(accessor_definition['bufferView'])
        offset = integer(accessor_definition.get('byteOffset', 0), 'accessor offset')
        stride = v.get('byteStride', byte_size * n)
        integer(stride, 'accessor stride', byte_size * n, 252)
        if offset % byte_size or stride % byte_size or (v.get('byteOffset', 0) + offset) % byte_size or (offset + (count - 1) * stride + byte_size * n > len(b)):
            raise ModelError('Accessor range, alignment or stride is invalid.')
        if 'byteStride' in v and (stride % 4 or stride < 4):
            raise ModelError('Vertex byte stride must be a multiple of four.')
        if accessor_definition.get('normalized', False) and component_type not in (5120, 5121, 5122, 5123):
            raise ModelError('Invalid normalized accessor.')
        out = []
        for k in range(count):
            val = struct.unpack_from('<' + fmt * n, b, offset + k * stride)
            if accessor_definition.get('normalized', False):
                denom = {5120: 127, 5121: 255, 5122: 32767, 5123: 65535}[component_type]
                val = tuple((max(x / denom, -1) for x in val))
            if not all((finite(x) and abs(x) < 1e+30 for x in val)):
                raise ModelError('Accessor contains nonfinite or excessive values.')
            out.append(val[0] if indices else val)
        return (out, component_type, accessor_type)
    meshes = document.get('meshes', [])
    parsed = []
    parsed_vertices = 0
    parsed_triangles = 0
    for mesh in meshes:
        if mesh.get('weights') or mesh.get('extensions'):
            raise ModelError('Morph weights or mesh extensions are unsupported.')
        mesh_primitives = []
        if len(mesh.get('primitives', [])) > 4096:
            raise ModelError('Too many mesh primitives.')
        for prim in mesh.get('primitives', []):
            if prim.get('mode', 4) != 4 or prim.get('targets') or prim.get('extensions'):
                raise ModelError('Only uncompressed static TRIANGLES primitives are supported.')
            attrs = {}
            if len(prim.get('attributes', {})) > 64:
                raise ModelError('Too many vertex streams.')
            for key, accessor_index in prim.get('attributes', {}).items():
                if key.startswith(('JOINTS_', 'WEIGHTS_')):
                    raise ModelError('Skin attributes are unsupported.')
                if key not in ('POSITION', 'NORMAL', 'TANGENT') and (not key.startswith(('TEXCOORD_', 'COLOR_', '_'))):
                    raise ModelError('Unsupported vertex attribute ' + key)
                arr, component_type, accessor_type = accessor(accessor_index)
                definition = accessors[accessor_index]
                validate_vertex_accessor(key, component_type, accessor_type, definition.get('normalized', False))
                attrs[key] = arr
            if 'POSITION' not in attrs:
                raise ModelError('Primitive requires POSITION.')
            vertex_count = len(attrs['POSITION'])
            if any((len(a) != vertex_count for a in attrs.values())):
                raise ModelError('Attribute count mismatch.')
            indices = accessor(prim['indices'], True)[0] if 'indices' in prim else list(range(vertex_count))
            if not indices or len(indices) % 3 or len(indices) > MAX_TRIANGLES * 3 or (max(indices) >= vertex_count):
                raise ModelError('Invalid triangle indices/count.')
            parsed_vertices += vertex_count
            parsed_triangles += len(indices) // 3
            if parsed_vertices > MAX_VERTICES or parsed_triangles > MAX_TRIANGLES:
                raise ModelError('Decoded mesh collection exceeds geometry limits.')
            mat = prim.get('material')
            if mat is not None:
                ref(document.get('materials', []), mat, 'material')
            mesh_primitives.append(Primitive(attrs, indices, mat, str(mesh.get('name', 'Primitive'))[:200]))
        parsed.append(mesh_primitives)
    nodes = document.get('nodes', [])
    if len(nodes) > MAX_NODES:
        raise ModelError('Too many nodes.')
    parents = {}
    state = {}

    def validate(k, depth=0):
        if depth > 128:
            raise ModelError('Node hierarchy exceeds 128 levels.')
        if state.get(k) == 1:
            raise ModelError('Cyclic node hierarchy.')
        if state.get(k) == 2:
            return
        n = ref(nodes, k, 'node')
        state[k] = 1
        if any((x in n for x in ('skin', 'weights', 'extensions'))):
            raise ModelError('Node skin, weights or extensions are unsupported.')
        node_matrix(n)
        for child in n.get('children', []):
            ref(nodes, child, 'child node')
            if child in parents:
                raise ModelError('Node has multiple parents or duplicate child references.')
            parents[child] = k
            validate(child, depth + 1)
        state[k] = 2
    for k in range(len(nodes)):
        validate(k)
    scenes = document.get('scenes', [])
    roots = ref(scenes, document.get('scene', 0), 'scene').get('nodes', []) if scenes else [i for i in range(len(nodes)) if i not in parents]
    if len(set(roots)) != len(roots) or any((r in parents for r in roots)):
        raise ModelError('Invalid scene root nodes.')
    output = []
    vertex_count = triangle_count = 0

    def walk(k, parent, depth=0):
        nonlocal vertex_count, triangle_count
        if depth > 128:
            raise ModelError('Node hierarchy is too deep.')
        n = ref(nodes, k, 'node')
        m = mul(parent, node_matrix(n))
        if 'mesh' in n:
            for p in ref(parsed, n['mesh'], 'mesh'):
                vertex_count += len(p.attrs['POSITION'])
                triangle_count += len(p.indices) // 3
                if vertex_count > MAX_VERTICES or triangle_count > MAX_TRIANGLES:
                    raise ModelError('Instanced scene exceeds geometry limits.')
                output.append(transform(p, m))
        for child in n.get('children', []):
            walk(child, m, depth + 1)
    for r in roots:
        walk(r, IDENTITY)
    if not output:
        raise ModelError('Selected glTF scene contains no triangle geometry.')
    images = []
    image_byte_count = 0
    for img in document.get('images', []):
        if 'uri' in img or 'bufferView' not in img or img.get('mimeType') not in ('image/png', 'image/jpeg') or img.get('extensions'):
            raise ModelError('Images must be PNG/JPEG embedded buffer views; external resources are never read.')
        image_view = ref(views, img['bufferView'], 'image buffer view')
        image_byte_count += image_view['byteLength']
        if image_byte_count > MAX_BYTES:
            raise ModelError('Repeated image buffers exceed the 64 MiB decoded image budget.')
        _, raw_bytes = view(img['bufferView'])
        images.append((img['mimeType'], raw_bytes, str(img.get('name', 'Texture'))[:200]))
    textures = copy.deepcopy(document.get('textures', []))
    samplers = copy.deepcopy(document.get('samplers', []))
    materials = copy.deepcopy(document.get('materials', []))
    for tex in textures:
        if tex.get('extensions'):
            raise ModelError('Texture extensions are unsupported.')
        ref(images, tex['source'], 'texture source')
        if 'sampler' in tex:
            ref(samplers, tex['sampler'], 'texture sampler')
    texture_keys = {'baseColorTexture', 'metallicRoughnessTexture', 'normalTexture', 'occlusionTexture', 'emissiveTexture'}

    def validate_material(obj):
        if isinstance(obj, dict):
            if 'index' in obj and ('texCoord' in obj or set(obj).issubset({'index', 'extensions', 'extras'})):
                ref(textures, obj['index'], 'material texture')
            for k, v in obj.items():
                if k in texture_keys and isinstance(v, dict):
                    ref(textures, v['index'], 'material texture')
                validate_material(v)
        elif isinstance(obj, list):
            for v in obj:
                validate_material(v)
    validate_material(materials)
    return Model(
        output,
        materials,
        images,
        textures,
        samplers,
        extensions_used=document.get('extensionsUsed', []),
    )

def write_glb(model):
    """Write uncompressed float streams and compact indices into a self-contained GLB.

    All buffers are four-byte aligned. Embedded image bytes and material records
    are copied; no texture recompression or material baking is performed.
    """
    binary = bytearray()
    views = []
    accessors = []

    def add(raw_bytes):
        while len(binary) % 4:
            binary.append(0)
        i = len(views)
        views.append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(raw_bytes)})
        binary.extend(raw_bytes)
        return i

    def acc(arr, accessor_type, component_type):
        n = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}[accessor_type]
        fmt = 'I' if component_type == 5125 else 'f'
        raw_bytes = bytearray(len(arr) * n * 4)
        for i, v in enumerate(arr):
            struct.pack_into(
                '<' + fmt * n,
                raw_bytes,
                i * n * 4,
                *((v if isinstance(v, (tuple, list)) else (v,)) if n == 1 else v),
            )
        accessor_definition = {
            'bufferView': add(raw_bytes),
            'componentType': component_type,
            'count': len(arr),
            'type': accessor_type,
        }
        if accessor_type == 'VEC3' and component_type == 5126:
            accessor_definition['min'] = [min((v[k] for v in arr)) for k in range(3)]
            accessor_definition['max'] = [max((v[k] for v in arr)) for k in range(3)]
        accessors.append(accessor_definition)
        return len(accessors) - 1
    meshes = []
    nodes = []
    for p in model.primitives:
        attrs = {k: acc(v, ('SCALAR', 'VEC2', 'VEC3', 'VEC4')[len(v[0]) - 1], 5126) for k, v in p.attrs.items()}
        primitive_definition = {'attributes': attrs, 'indices': acc(p.indices, 'SCALAR', 5125), 'mode': 4}
        if p.material is not None:
            primitive_definition['material'] = p.material
        nodes.append({'mesh': len(meshes), 'name': p.name})
        meshes.append({'primitives': [primitive_definition], 'name': p.name})
    imgs = [{'bufferView': add(raw_bytes), 'mimeType': mime, 'name': name} for mime, raw_bytes, name in model.images]
    document = {
        'asset': {'version': '2.0', 'generator': 'Mesh to Play 0.1.0'},
        'scene': 0,
        'scenes': [{'nodes': list(range(len(nodes)))}],
        'nodes': nodes,
        'meshes': meshes,
        'buffers': [{'byteLength': len(binary)}],
        'bufferViews': views,
        'accessors': accessors,
    }
    for key, val in [
        ('materials', model.materials),
        ('images', imgs),
        ('textures', model.textures),
        ('samplers', model.samplers),
        ('extensionsUsed', model.extensions_used),
    ]:
        if val:
            document[key] = val
    json_bytes = json.dumps(document, separators=(',', ':'), allow_nan=False).encode()
    json_bytes += b' ' * (-len(json_bytes) % 4)
    binary.extend(b'\x00' * (-len(binary) % 4))
    return struct.pack('<4sII', b'glTF', 2, 28 + len(json_bytes) + len(binary)) + struct.pack('<II', len(json_bytes), 1313821514) + json_bytes + struct.pack('<II', len(binary), 5130562) + binary
