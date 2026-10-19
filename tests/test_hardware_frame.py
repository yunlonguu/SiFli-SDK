"""Validate hardware frame transport before interpreting rendering differences."""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/qemu'))
from hardware_visual_check import read_frame
from compare_hardware import frame_metrics
from PIL import Image


class FrameConsole:
    def __init__(self, lines):
        self.lines = iter(lines)

    def wait(self, pattern, deadline):
        for line in self.lines:
            match = re.search(pattern, line)
            if match:
                return match
        raise TimeoutError('Incomplete frame')


class HardwareFrameTests(unittest.TestCase):
    def frame(self, swap=0, offset=0, corrupt=False):
        raw = bytes.fromhex('f8001234' if swap else '00f83412')
        checksum = 2166136261
        for value in raw:
            checksum = ((checksum ^ value) * 16777619) & 0xffffffff
        if corrupt:
            checksum ^= 1
        return FrameConsole([
            f'LV_FRAME_BEGIN 1 1 2 {swap} 4',
            f'LV_FRAME_DATA {offset} {raw.hex()}',
            f'LV_FRAME_END {checksum:08x}',
        ])

    def test_stride_padding_is_cropped(self):
        image, raw, metadata = read_frame(self.frame(), 0)
        self.assertEqual(image.size, (1, 1))
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(len(raw), 4)
        self.assertEqual(metadata['stride'], 2)

    def test_swapped_rgb565(self):
        image, _, _ = read_frame(self.frame(swap=1), 0)
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))

    def test_missing_data_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'out of order'):
            read_frame(self.frame(offset=48), 0)

    def test_corruption_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'checksum'):
            read_frame(self.frame(corrupt=True), 0)

    def test_color_expansion_difference_is_separate_from_native_difference(self):
        hardware = Image.new('RGB', (1, 1), (8, 4, 8))
        qemu = Image.new('RGB', (1, 1), (9, 5, 9))
        result = frame_metrics(hardware, qemu)
        self.assertEqual(result['different_rgb888_pixels'], 1)
        self.assertEqual(result['different_rgb565_pixels'], 0)
        qemu.putpixel((0, 0), (16, 4, 8))
        self.assertEqual(frame_metrics(hardware, qemu)['different_rgb565_pixels'], 1)


if __name__ == '__main__':
    unittest.main()
