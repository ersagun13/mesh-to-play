import json, math, struct, tempfile, unittest
from pathlib import Path
from meshplay.formats import ModelError, load_model, write_glb, Primitive, Model
from meshplay.pipeline import inspect_model, prepare_model, create_demo, validate_options

class FormatsTest(unittest.TestCase):

    def test_obj_negative_indices_and_bounds(self):
        model = load_model(b'v 0 0 0\nv 1 0 0\nv 0 1 0\nf -3 -2 -1\n', 'a.obj')
        self.assertEqual(model.triangles, 1)
        with self.assertRaises(ModelError):
            load_model(b'v 0 0 0\nf 1 2 3\n', 'bad.obj')

    def test_roundtrip_attributes(self):
        p = Primitive({'POSITION': [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 3.0, 0.0)], 'TEXCOORD_0': [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]}, [0, 1, 2], None)
        m = load_model(write_glb(Model([p])), 'test.glb')
        self.assertEqual(m.dimensions, [2.0, 3.0, 0.0])
        self.assertEqual(m.primitives[0].attrs['TEXCOORD_0'][1], (1.0, 0.0))

    def test_invalid_glb_and_external_obj(self):
        with self.assertRaises(ModelError):
            load_model(b'glTF' + b'\x00' * 20, 'bad.glb')
        with self.assertRaises(ModelError):
            load_model(b'v nan 0 0\n', 'bad.obj')

    def test_pipeline_no_overwrite_and_source_hash(self):
        data = b'v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'result'
            r = inspect_model(data, 'x.obj', out)
            self.assertEqual(r['summary']['triangles'], 1)
            self.assertTrue((out / 'bundle.zip').exists())
            with self.assertRaises(ModelError):
                inspect_model(data, 'x.obj', out)

    def test_demo_detailed(self):
        m = load_model(create_demo(), 'original.glb')
        self.assertGreaterEqual(m.triangles, 100000)

    def test_options_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ModelError):
                prepare_model(b'', 'x.glb', Path(d) / 'x', ratios=[float('nan')])
    def test_oversized_integer_options_are_clean_model_errors(self):
        enormous = 10 ** 1000
        invalid_options = [
            {'ratios': [enormous, 0.25, 0.1]},
            {'error': enormous},
            {'height': enormous},
        ]
        for options in invalid_options:
            with self.subTest(option=next(iter(options))):
                with self.assertRaises(ModelError):
                    validate_options(options)

if __name__ == '__main__':
    unittest.main()
