#!/usr/bin/env python3
"""Build graphical examples with temporary test configuration only."""
import argparse
from contextlib import contextmanager
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PROJECT = Path('example/multimedia/lvgl/lvgl_v8_demos/project')
CONFIG = (
    'CONFIG_LCD_FB_USING_NONE=y\n'
    'CONFIG_LV_FB_ONE_SCREEN_SIZE=y\n'
    '# CONFIG_LV_USE_PERF_MONITOR is not set\n'
    'CONFIG_LV_DEMO_QEMU_SNAPSHOT=y\n'
    'CONFIG_LV_USE_FONT_COMPRESSED=y\n'
)


@contextmanager
def visual_overlay(root, board, project=PROJECT, settings=CONFIG):
    config = root / project / (board + '_hcpu') / 'proj.conf'
    directory_existed = config.parent.exists()
    original = config.read_bytes() if config.exists() else None
    config.parent.mkdir(parents=True, exist_ok=True)
    try:
        config.write_bytes((original or b'') + b'\n' + settings.encode())
        yield
    finally:
        if original is None:
            config.unlink()
            if not directory_existed:
                config.parent.rmdir()
        else:
            config.write_bytes(original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--example', choices=('lvgl_v8_demos', 'watch'), default='lvgl_v8_demos')
    parser.add_argument('--board', required=True,
                        choices=('sf32lb52-lchspi-ulp', 'sf32lb52-lcd_a128r16'))
    parser.add_argument('-j', '--jobs', type=int, default=8)
    args = parser.parse_args()
    project = Path('example/multimedia/lvgl') / args.example / 'project'
    settings = CONFIG
    if args.example == 'watch':
        settings = (
            'CONFIG_LCD_FB_USING_NONE=y\n'
            'CONFIG_LV_FB_TWO_NOT_SCREEN_SIZE=y\n'
            '# CONFIG_LV_USE_PERF_MONITOR is not set\n'
            'CONFIG_APP_TRANS_ANIMATION_NONE=y\n'
        )
    with visual_overlay(ROOT, args.board, project, settings):
        subprocess.run(['scons', '--board=' + args.board, '-j' + str(args.jobs)],
                       cwd=ROOT / project, check=True)


if __name__ == '__main__':
    main()
