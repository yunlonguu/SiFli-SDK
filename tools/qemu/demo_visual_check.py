#!/usr/bin/env python3
"""Select frozen benchmark scenes over UART, then record or compare PNGs."""
import argparse
import html
import json
import os
from pathlib import Path
import re
import sys

from PIL import Image, ImageDraw

from headless_check import QemuSession, benchmark_cases, compare_reference, parse_ppm


def gallery(directory, cases):
    """Small contact sheets plus an HTML index linking the full-size images."""
    links = []
    for page in range(0, len(cases), 24):
        subset = cases[page:page + 24]
        sheet = Image.new('RGB', (800, ((len(subset) + 3) // 4) * 260), '#dddddd')
        draw = ImageDraw.Draw(sheet)
        for slot, (name, image_path) in enumerate(subset):
            with Image.open(image_path) as image:
                thumb = image.convert('RGB')
                thumb.thumbnail((195, 225))
                x, y = (slot % 4) * 200, (slot // 4) * 260
                sheet.paste(thumb, (x, y))
                draw.text((x, y + 228), name[:30], fill='black')
            relative = Path(image_path).relative_to(directory).as_posix()
            links.append(f'<a href="{html.escape(relative)}"><figure>'
                         f'<img width="195" src="{html.escape(relative)}">'
                         f'<figcaption>{html.escape(name)}</figcaption></figure></a>')
        sheet.save(Path(directory) / f'overview_{page // 24}.png')
    (Path(directory) / 'index.html').write_text(
        '<!doctype html><meta charset="utf-8"><title>QEMU demo screenshots</title>'
        '<style>body{display:flex;flex-wrap:wrap}figure{margin:8px;width:200px}</style>'
        + '\n'.join(links), encoding='utf-8')


def wait_ready(q, serial_log, scene, offset):
    marker = re.compile(rf'QEMU_SNAPSHOT {scene} (READY|INVALID)')
    while True:
        q.remaining()
        text = Path(serial_log).read_text(errors='replace')[offset:]
        match = marker.search(text)
        if match:
            if match[1] != 'READY':
                raise RuntimeError(f'Firmware rejected scene {scene}')
            return
        q.wait(0.05)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--kernel', required=True)
    parser.add_argument('--source', required=True, help='Benchmark C source used to build firmware')
    parser.add_argument('--references', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--serial-log', default='qemu-demo-visual.log')
    parser.add_argument('--machine', default='sf32lb52x')
    parser.add_argument('--device', action='append', default=[], help='Additional QEMU device, repeatable')
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--renderer', choices=('epic',), default='epic')
    parser.add_argument('--record', action='store_true', help='Create candidates in an empty directory; never used in CI')
    args = parser.parse_args()
    names = benchmark_cases(args.source)
    manifest = {f'{i:03d}.png': name for i, name in enumerate(names)}
    if args.record:
        if args.references.exists() and any(args.references.iterdir()):
            parser.error('--record requires an empty reference directory')
        args.references.mkdir(parents=True, exist_ok=True)
    else:
        # A changed scene list or missing baseline is a failure before launching QEMU.
        stored = json.loads((args.references / 'manifest.json').read_text())
        if stored != manifest:
            parser.error('Reference manifest does not match benchmark scenes')
        for filename in manifest:
            with Image.open(args.references / filename) as image:
                if image.format != 'PNG' or image.size != (390, 450):
                    parser.error(f'Invalid reference: {filename}')
                image.load()
    args.output.mkdir(parents=True, exist_ok=True)
    actuals, candidates, failures = [], [], []
    q = QemuSession(args.qemu, args.kernel, args.serial_log, str(args.output),
                    timeout=args.timeout, machine=args.machine, serial_input=True,
                    extra_devices=args.device)
    try:
        q.wait(14)
        for scene, (filename, name) in enumerate(manifest.items()):
            offset = len(Path(args.serial_log).read_text(errors='replace'))
            q.serial.sendall(f'lv_snapshot {scene} {args.renderer}\r'.encode())
            wait_ready(q, args.serial_log, scene, offset)
            q.wait(0.2)
            case_dir = args.output / f'{scene:03d}'
            case_dir.mkdir(exist_ok=True)
            shots = []
            for sample in range(2):
                path = case_dir / f'shot_{sample}.ppm'
                q.screendump(str(path))
                w, h, pixels = parse_ppm(path)
                if (w, h) != (390, 450):
                    raise RuntimeError(f'Unexpected screen size: {w}x{h}')
                shots.append((str(path), w, h, pixels))
                if sample == 0:
                    q.wait(0.15)
            if shots[0][3] != shots[1][3]:
                raise RuntimeError(f'Scene {scene} is not stable; refusing capture')
            actual = case_dir / 'actual.png'
            Image.frombytes('RGB', (w, h), pixels).save(actual)
            actuals.append((f'{scene:03d}: {name}', actual))
            if args.record:
                reference = args.references / filename
                Image.frombytes('RGB', (w, h), pixels).save(reference)
                candidates.append((f'{scene:03d}: {name}', reference))
            else:
                for _, ok, message in compare_reference(shots, args.references / filename, str(case_dir)):
                    if not ok:
                        failures.append(f'{scene:03d}: {name}: {message}')
            print(f'[{scene + 1}/{len(names)}] {name}', flush=True)
        q.remaining()
        if args.record:
            (args.references / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
            gallery(args.references, candidates)
    finally:
        q.close()
        if actuals:
            gallery(args.output, actuals)
    print('\n'.join(f'FAIL: {failure}' for failure in failures))
    print(f'{"RECORDED candidates" if args.record else "Compared"}: {len(names)} scenes; '
          f'{len(failures)} failed samples. Gallery: {args.output / "index.html"}')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write(f'\n### Demo visual regression\n\n{len(names)} scenes, '
                          f'two stable samples each, zero tolerance; {len(failures)} failed samples.\n\n'
                          'Download the artifact and open `demo-visual/index.html` or `overview_*.png`.\n')
            for failure in failures:
                summary.write(f'\n- {failure}\n')
    return 1 if failures else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, TimeoutError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(2)
