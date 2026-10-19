import base64, copy, json, math, struct, tempfile, unittest
from pathlib import Path
from meshplay.formats import Model, Primitive, ModelError, load_model, write_glb
from meshplay.pipeline import preview, prepare_model, create_demo
from meshplay.native import capability

def triangle():
    return Model([Primitive({'POSITION': [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], 'NORMAL': [(0.0, 0.0, 1.0)] * 3, 'TEXCOORD_0': [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]}, [0, 1, 2])])

def unpack(raw):
    size = struct.unpack_from('<I', raw, 12)[0]
    return (json.loads(raw[20:20 + size]), raw[28 + size:])

def pack(doc, blob):
    jb = json.dumps(doc).encode()
    jb += b' ' * (-len(jb) % 4)
    blob += b'\x00' * (-len(blob) % 4)
    return struct.pack('<4sII', b'glTF', 2, 28 + len(jb) + len(blob)) + struct.pack('<II', len(jb), 1313821514) + jb + struct.pack('<II', len(blob), 5130562) + blob

class GLBContract(unittest.TestCase):

    def test_hierarchy_instances_mirror_and_nonuniform_normal(self):
        doc, blob = unpack(write_glb(triangle()))
        doc['nodes'] = [{'translation': [4, 2, 1], 'children': [1, 2]}, {'mesh': 0, 'scale': [-2, 3, 1]}, {'mesh': 0, 'translation': [10, 0, 0]}]
        doc['scenes'] = [{'nodes': [0]}]
        m = load_model(pack(doc, blob), 'scene.glb')
        self.assertEqual(m.triangles, 2)
        self.assertEqual(m.dimensions, [13.0, 3.0, 0.0])
        self.assertEqual(m.primitives[0].indices, [0, 2, 1])
        self.assertEqual(m.primitives[0].attrs['POSITION'][0], (4.0, 2.0, 1.0))
        n = load_model(write_glb(m), 'export.glb')
        self.assertEqual(n.dimensions, m.dimensions)
        self.assertEqual(n.triangles, 2)

    def test_normal_inverse_transpose(self):
        m = triangle()
        m.primitives[0].attrs['NORMAL'] = [(1 / math.sqrt(2), 1 / math.sqrt(2), 0)] * 3
        doc, blob = unpack(write_glb(m))
        doc['nodes'][0]['scale'] = [2, 1, 1]
        n = load_model(pack(doc, blob), 'x.glb').primitives[0].attrs['NORMAL'][0]
        self.assertAlmostEqual(n[1] / n[0], 2, places=5)

    def test_embedded_texture_material_preserved(self):
        m = triangle()
        img = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==')
        m.images = [('image/png', img, 'tiny')]
        m.textures = [{'source': 0}]
        m.materials = [{'pbrMetallicRoughness': {'baseColorTexture': {'index': 0}}}]
        m.primitives[0].material = 0
        n = load_model(write_glb(m), 'x.glb')
        self.assertEqual(n.images[0][1], img)
        self.assertEqual(n.materials, m.materials)
        self.assertEqual(n.primitives[0].material, 0)

    def test_interleaved_normalized_uv_and_nonindexed(self):
        doc, blob = unpack(write_glb(triangle()))
        off = len(blob)
        blob += b''.join((struct.pack('<3f2H', *p, *uv) for p, uv in zip([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 0), (65535, 0), (0, 65535)])))
        doc['buffers'][0]['byteLength'] = len(blob)
        vi = len(doc['bufferViews'])
        doc['bufferViews'].append({'buffer': 0, 'byteOffset': off, 'byteLength': 48, 'byteStride': 16})
        pi = len(doc['accessors'])
        doc['accessors'] += [{'bufferView': vi, 'componentType': 5126, 'count': 3, 'type': 'VEC3'}, {'bufferView': vi, 'byteOffset': 12, 'componentType': 5123, 'normalized': True, 'count': 3, 'type': 'VEC2'}]
        p = doc['meshes'][0]['primitives'][0]
        p['attributes'] = {'POSITION': pi, 'TEXCOORD_0': pi + 1}
        p.pop('indices')
        m = load_model(pack(doc, blob), 'interleaved.glb')
        self.assertEqual(m.primitives[0].attrs['TEXCOORD_0'][1], (1.0, 0.0))
        self.assertEqual(m.primitives[0].indices, [0, 1, 2])

    def test_unsupported_and_malformed_before_native(self):
        original, blob = unpack(write_glb(triangle()))
        mutations = [lambda d: d.update(animations=[{}]), lambda d: d.update(extensionsRequired=['KHR_draco_mesh_compression']), lambda d: d['accessors'][0].update(sparse={}), lambda d: d['accessors'][0].update(count=2000001), lambda d: d['accessors'][0].update(byteOffset=100000), lambda d: d['bufferViews'][0].update(byteStride=13), lambda d: d['nodes'][0].update(children=[0]), lambda d: d['buffers'][0].update(uri='file:///etc/passwd'), lambda d: d['meshes'][0]['primitives'][0].update(mode=1), lambda d: d['nodes'][0].update(scale=[0, 1, 1])]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                d = copy.deepcopy(original)
                mutate(d)
                with self.assertRaises(ModelError):
                    load_model(pack(d, blob), 'bad.glb')

    def test_preview_is_complete_exact_and_in_bounds(self):
        model = triangle()
        raw = preview(model)
        magic, nv, nt = struct.unpack_from('<4sII', raw)
        self.assertEqual((magic, nv, nt), (b'MSH1', 3, 1))
        self.assertEqual(len(raw), 12 + nv * 12 + nt * 12)
        self.assertEqual(struct.unpack_from('<3I', raw, 12 + nv * 12), (0, 1, 2))

    @unittest.skipUnless(capability()['available'], 'Build native meshoptimizer to run reduction acceptance.')
    def test_actual_detailed_native_export_and_compaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = create_demo()
            report = prepare_model(data, 'reactor.glb', Path(tmp) / 'result', height=2, bottom_center=True)
            counts = [r['triangles'] for r in report['results']]
            self.assertTrue(all((a > b for a, b in zip([report['summary']['triangles']] + counts, counts))))
            for row in report['results']:
                m = load_model((Path(tmp) / 'result' / row['name']).read_bytes(), row['name'])
                self.assertEqual(m.triangles, row['triangles'])
                self.assertLessEqual(max(m.dimensions), 2.00001)
                self.assertGreaterEqual(m.bounds[0][1], -1e-06)
                for p in m.primitives:
                    self.assertEqual(set(p.indices), set(range(len(p.attrs['POSITION']))))
            self.assertLess(counts[-1], 20000)
if __name__ == '__main__':
    unittest.main()

class PreservationContract(unittest.TestCase):

    def test_custom_float_scalar_and_material_boundaries(self):
        model = triangle()
        model.primitives[0].attrs['_HEAT'] = [(0.1,), (0.5,), (0.9,)]
        second = copy.deepcopy(model.primitives[0])
        second.material = 1
        model.primitives[0].material = 0
        model.primitives.append(second)
        model.materials = [{'name': 'one'}, {'name': 'two'}]
        parsed = load_model(write_glb(model), 'custom.glb')
        self.assertEqual([p.material for p in parsed.primitives], [0, 1])
        self.assertAlmostEqual(parsed.primitives[0].attrs['_HEAT'][1][0], 0.5)

    def test_custom_integer_ids_rejected_instead_of_float_rounding(self):
        document, binary = unpack(write_glb(triangle()))
        document['meshes'][0]['primitives'][0]['attributes']['_ID'] = document['meshes'][0]['primitives'][0]['indices']
        with self.assertRaises(ModelError):
            load_model(pack(document, binary), 'ids.glb')

    def test_missing_native_creates_no_output(self):
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / 'unavailable'
            with self.assertRaisesRegex(ModelError, 'build_native'):
                prepare_model(write_glb(triangle()), 'source.glb', output, native_library=Path(d) / 'missing.so')
            self.assertFalse(output.exists())

    def test_html_escaping_and_external_checksums(self):
        import hashlib
        from meshplay.pipeline import inspect_model
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / 'inspection'
            inspect_model(write_glb(triangle()), '<script>alert(1)</script>.glb', output)
            text = (output / 'report.html').read_text()
            self.assertNotIn('<script>alert', text)
            self.assertIn('&lt;script&gt;', text)
            manifest = json.loads((output / 'checksums.json').read_text())
            self.assertEqual(manifest['report.json']['sha256'], hashlib.sha256((output / 'report.json').read_bytes()).hexdigest())
            self.assertEqual((output / 'bundle.zip.sha256').read_text().split()[0], hashlib.sha256((output / 'bundle.zip').read_bytes()).hexdigest())

    @unittest.skipUnless(capability()['available'], 'Native build required for tiny-ratio acceptance.')
    def test_tiny_ratios_report_actual_minimum_native_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'tiny-targets'
            report = prepare_model(
                write_glb(triangle()), 'triangle.glb', output,
                ratios=[1e-300, 1e-310, 1e-320],
            )
            for row in report['results']:
                self.assertEqual(row['requested_triangles'], 1)
                self.assertEqual(row['triangles'], 1)
                self.assertEqual(row['primitives'][0]['requested_triangles'], 1)
            self.assertTrue(any('minimum of one triangle' in item for item in report['limitations']))

    @unittest.skipUnless(capability()['available'], 'Native build required for cube acceptance.')
    def test_small_cube_with_zero_error_is_honest_no_benefit_case(self):
        positions = [(-1.0, -1.0, -1.0), (1.0, -1.0, -1.0), (1.0, 1.0, -1.0), (-1.0, 1.0, -1.0), (-1.0, -1.0, 1.0), (1.0, -1.0, 1.0), (1.0, 1.0, 1.0), (-1.0, 1.0, 1.0)]
        indices = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4, 3, 7, 6, 3, 6, 2, 0, 4, 7, 0, 7, 3, 1, 2, 6, 1, 6, 5]
        with tempfile.TemporaryDirectory() as d:
            report = prepare_model(write_glb(Model([Primitive({'POSITION': positions}, indices)])), 'cube.glb', Path(d) / 'cube', error=0)
            self.assertEqual([r['triangles'] for r in report['results']], [12, 12, 12])
            self.assertTrue(any(('stopped above target' in f['title'] for f in report['findings'])))

class ReferencedGeometryBounds(unittest.TestCase):
    @staticmethod
    def cube_with_unused_vertex():
        positions = [(0.,0.,0.),(20.,0.,0.),(20.,20.,0.),(0.,20.,0.),
                     (0.,0.,20.),(20.,0.,20.),(20.,20.,20.),(0.,20.,20.),
                     (1000.,1000.,1000.)]
        indices = [0,2,1,0,3,2,4,5,6,4,6,7,0,1,5,0,5,4,
                   3,7,6,3,6,2,0,4,7,0,7,3,1,2,6,1,6,5]
        attributes = {'POSITION': positions, '_ID_FLOAT': [(float(i),) for i in range(9)]}
        return Model([Primitive(attributes, indices)])

    def test_decoded_bounds_use_referenced_geometry_and_compact_all_streams(self):
        model = load_model(write_glb(self.cube_with_unused_vertex()), 'unused.glb')
        self.assertEqual(model.dimensions, [20.,20.,20.])
        self.assertEqual(model.vertices, 8)
        self.assertEqual(len(model.primitives[0].attrs['_ID_FLOAT']), 8)
        self.assertEqual(model.unreferenced_vertices_removed, 1)

    @unittest.skipUnless(capability()['available'], 'Native build required for scaling acceptance.')
    def test_height_conversion_ignores_unused_vertex_and_preserves_source(self):
        raw = write_glb(self.cube_with_unused_vertex())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'scaled'
            report = prepare_model(raw, 'unused.glb', output, height=2, error=0)
            self.assertEqual(report['summary']['dimensions'], [20.,20.,20.])
            self.assertEqual(report['conversion']['uniform_scale'], 0.1)
            for result in report['results']:
                measured = load_model((output/result['name']).read_bytes(), result['name'])
                self.assertEqual(measured.dimensions, [2.,2.,2.])
            self.assertEqual((output/'source.glb').read_bytes(), raw)
            self.assertEqual(report['provenance']['unreferenced_vertices_removed'], 1)
            finding = next(item for item in report['findings'] if item['id']=='unreferenced-vertices')
            self.assertEqual(finding['count'], 1)

    def test_obj_unused_position_is_reported_without_affecting_bounds(self):
        model = load_model(b'v 0 0 0\nv 20 0 0\nv 0 20 0\nv 1000 1000 1000\nf 1 2 3\n', 'unused.obj')
        self.assertEqual(model.dimensions, [20.,20.,0.])
        self.assertEqual(model.unreferenced_vertices_removed, 1)
