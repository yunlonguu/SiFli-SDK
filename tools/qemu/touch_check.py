#!/usr/bin/env python3
"""Exercise scroll_1 by dragging left and right via the QEMU touch controller."""
import argparse
import os
from pathlib import Path
import sys

from PIL import Image

from headless_check import QemuSession, compare_reference, parse_ppm
from demo_visual_check import gallery


def pointer(q, x, y, down):
    # QMP absolute coordinates span 0..32767; this test uses a 390x450 display.
    q._command('input-send-event', {'events': [
        {'type': 'abs', 'data': {'axis': 'x', 'value': round(x * 32767 / 389)}},
        {'type': 'abs', 'data': {'axis': 'y', 'value': round(y * 32767 / 449)}},
        {'type': 'btn', 'data': {'button': 'left', 'down': down}},
    ]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--kernel', required=True)
    parser.add_argument('--machine', default='sf32lb52x')
    parser.add_argument('--initial-reference', required=True, type=Path)
    parser.add_argument('--references', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--serial-log', default='qemu-touch.log')
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    if args.record:
        if args.references.exists() and any(args.references.iterdir()):
            parser.error('--record requires an empty reference directory')
        args.references.mkdir(parents=True, exist_ok=True)
    for reference in ([args.initial_reference] if args.record else
                      [args.initial_reference, args.references / 'scrolled.png']):
        with Image.open(reference) as image:
            if image.format != 'PNG' or image.size != (390, 450):
                parser.error(f'Invalid reference: {reference}')
            image.load()
    args.output.mkdir(parents=True, exist_ok=True)
    q = QemuSession(args.qemu, args.kernel, args.serial_log, str(args.output),
                    timeout=60, machine=args.machine)
    actuals, failures, pixels = [], [], {}

    def capture(name, reference):
        folder = args.output / name
        folder.mkdir(exist_ok=True)
        shots = []
        for sample in range(2):
            path = folder / f'shot_{sample}.ppm'
            q.screendump(str(path))
            w, h, px = parse_ppm(path)
            if (w, h) != (390, 450):
                raise RuntimeError('Unexpected screen dimensions')
            shots.append((str(path), w, h, px))
            q.wait(0.2)
        if shots[0][3] != shots[1][3]:
            raise RuntimeError(f'{name}: screen is not stable')
        pixels[name] = px
        actual = folder / 'actual.png'
        Image.frombytes('RGB', (w, h), px).save(actual)
        actuals.append((name, actual))
        if args.record and name == 'scrolled':
            Image.frombytes('RGB', (w, h), px).save(reference)
        for _, ok, message in compare_reference(shots, reference, str(folder)):
            if not ok:
                failures.append(f'{name}: {message}')
        print(f'Captured {name}', flush=True)

    try:
        q.wait(14)
        capture('initial', args.initial_reference)
        pointer(q, 250, 225, True)
        q.wait(0.1)
        for x in range(240, 139, -10):
            pointer(q, x, 225, True)
            q.wait(0.05)
        pointer(q, 140, 225, False)
        q.wait(2)
        capture('scrolled', args.references / 'scrolled.png')
        pointer(q, 140, 225, True)
        q.wait(0.1)
        for x in range(150, 271, 10):
            pointer(q, x, 225, True)
            q.wait(0.05)
        pointer(q, 270, 225, False)
        q.wait(2)
        capture('restored', args.initial_reference)
        if pixels['initial'] == pixels['scrolled']:
            failures.append('Drag did not change the screen')
        if pixels['initial'] != pixels['restored']:
            failures.append('Reverse drag did not restore the initial screen')
    finally:
        q.close()
        if actuals:
            gallery(args.output, actuals)
    for failure in failures:
        print(f'FAIL: {failure}')
    print(f'Touch drag left/right: {len(failures)} failures')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write(f'\n### QEMU touch interaction\n\nDrag left and right: '
                          f'{len(failures)} failures. See `touch-visual/index.html` in the artifact.\n')
    return 1 if failures else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, TimeoutError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(2)
