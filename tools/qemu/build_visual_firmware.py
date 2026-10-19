#!/usr/bin/env python3
"""Build the snapshot demo with temporary SDK fixes and test configuration."""
import argparse
from contextlib import contextmanager
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PROJECT = Path('example/multimedia/lvgl/lvgl_v8_demos/project')
PATCH = Path('tools/qemu/patches/sdk-visual-test.patch')
CONFIG = (
    'CONFIG_LCD_FB_USING_NONE=y\n'
    'CONFIG_LV_FB_ONE_SCREEN_SIZE=y\n'
    '# CONFIG_LV_USE_PERF_MONITOR is not set\n'
    'CONFIG_LV_DEMO_QEMU_SNAPSHOT=y\n'
    'CONFIG_LV_USE_FONT_COMPRESSED=y\n'
    'CONFIG_LV_COLOR_SCREEN_TRANSP=y\n'
)


@contextmanager
def visual_overlay(root, board):
    patch = root / PATCH
    files = [root / line[6:] for line in patch.read_text().splitlines()
             if line.startswith('+++ b/')]
    config = root / PROJECT / (board + '_hcpu') / 'proj.conf'
    subprocess.run(['git', 'apply', '--check', str(patch)], cwd=root, check=True)
    originals = {path: path.read_bytes() for path in [*files, config]}
    try:
        subprocess.run(['git', 'apply', str(patch)], cwd=root, check=True)
        config.write_bytes(originals[config] + b'\n' + CONFIG.encode())
        yield
    finally:
        for path, content in originals.items():
            path.write_bytes(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board', required=True,
                        choices=('sf32lb52-lchspi-ulp', 'sf32lb52-lcd_a128r16'))
    parser.add_argument('-j', '--jobs', type=int, default=8)
    args = parser.parse_args()
    with visual_overlay(ROOT, args.board):
        subprocess.run(['scons', '--board=' + args.board, '-j' + str(args.jobs)],
                       cwd=ROOT / PROJECT, check=True)


if __name__ == '__main__':
    main()
