"""Visual regression checks: tolerance boundaries and failure artifacts."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

from PIL import Image


SPEC = importlib.util.spec_from_file_location(
    'headless_check', Path(__file__).resolve().parents[1] / 'tools/qemu/headless_check.py')
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class ScreenshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.reference = self.output / 'baseline.png'
        # 包含空白字节值，保证像素数据不会作为头部分隔符被丢弃。
        self.pixels = bytes([10, 13, 32, 255, 128, 0])
        Image.frombytes('RGB', (2, 1), self.pixels).save(self.reference)

    def compare(self, pixels, tolerance=0, size=(2, 1)):
        shots = [('shot_0.ppm', *size, pixels)]
        return CHECK.compare_reference(shots, self.reference, self.output, tolerance)[0]

    def test_identical_pixels_pass(self):
        _, ok, message = self.compare(self.pixels)
        self.assertTrue(ok)
        self.assertIn('0/2 pixels differ', message)
        self.assertTrue((self.output / 'reference.png').exists())
        with Image.open(self.output / 'shot_0_actual.png') as image:
            self.assertEqual(image.tobytes(), self.pixels)

    def test_benchmark_ignores_disabled_scenes(self):
        source = self.output / 'benchmark.c'
        source.write_text('''static scene_dsc_t scenes[] = {
            // {.name = "Disabled"},
            /* {.name = "Also disabled"}, */
            {.name = "Rectangle"},
            {.name = "", .create_cb = NULL}
        };''')
        self.assertEqual(CHECK.benchmark_cases(source), ['Rectangle', 'Rectangle + opa'])

    def test_one_channel_one_pixel_change_fails(self):
        changed = bytes([11]) + self.pixels[1:]
        _, ok, message = self.compare(changed)
        self.assertFalse(ok)
        self.assertIn('1/2 pixels differ', message)
        with Image.open(self.output / 'shot_0_mask.png') as image:
            self.assertEqual(list(image.getdata()), [255, 0])
        with Image.open(self.output / 'shot_0_diff.png') as image:
            self.assertEqual(image.getpixel((0, 0)), (1, 0, 0))

    def test_tolerance_boundary(self):
        changed = bytes([12]) + self.pixels[1:]
        self.assertTrue(self.compare(changed, tolerance=2)[1])
        self.assertFalse(self.compare(changed, tolerance=1)[1])

    def test_dimensions_fail(self):
        self.assertFalse(self.compare(self.pixels, size=(1, 2))[1])

    def test_missing_reference_fails_without_creating_it(self):
        self.reference = self.output / 'missing.png'
        with self.assertRaises(FileNotFoundError):
            self.compare(self.pixels)
        self.assertFalse(self.reference.exists())

    def test_each_sample_is_compared(self):
        shots = [('shot_0.ppm', 2, 1, self.pixels),
                 ('shot_1.ppm', 2, 1, bytes([11]) + self.pixels[1:])]
        results = CHECK.compare_reference(shots, self.reference, self.output)
        self.assertEqual([ok for _, ok, _ in results], [True, False])


if __name__ == '__main__':
    unittest.main()
