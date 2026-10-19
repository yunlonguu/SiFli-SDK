#!/usr/bin/env python3
"""Compare hardware and QEMU captures for both rendering backends (diagnostic only)."""
import argparse
import html
import json
from pathlib import Path

from PIL import Image, ImageChops


def frame_metrics(hardware, qemu):
    if hardware.size != qemu.size:
        raise ValueError('Frame dimensions differ')
    rgb_changes = native_changes = 0
    maximum = 0
    for hw, sim in zip(hardware.getdata(), qemu.getdata()):
        rgb_changes += hw != sim
        # Compare in the original RGB565 precision, independent of 565->888 expansion.
        native_changes += (hw[0] >> 3, hw[1] >> 2, hw[2] >> 3) != (
            sim[0] >> 3, sim[1] >> 2, sim[2] >> 3)
        maximum = max(maximum, *(abs(a - b) for a, b in zip(hw, sim)))
    return {'different_rgb888_pixels': rgb_changes,
            'different_rgb565_pixels': native_changes,
            'total_pixels': hardware.width * hardware.height,
            'max_channel_difference': maximum}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hardware', required=True, type=Path, help='Contains epic/ and sw/ captures')
    parser.add_argument('--qemu', required=True, type=Path, help='Contains epic/ and sw/ captures')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    manifests = [json.loads((root / backend / 'manifest.json').read_text())
                 for root in (args.hardware, args.qemu) for backend in ('epic', 'sw')]
    if any(manifest != manifests[0] for manifest in manifests[1:]):
        parser.error('All four manifests must match')
    args.output.mkdir(parents=True, exist_ok=True)
    rows, metrics = [], {}
    for filename, name in manifests[0].items():
        case = args.output / Path(filename).stem
        case.mkdir(exist_ok=True)
        metrics[filename] = {'name': name}
        cells = []
        for backend in ('epic', 'sw'):
            with Image.open(args.hardware / backend / filename) as image:
                hardware = image.convert('RGB')
            with Image.open(args.qemu / backend / filename) as image:
                qemu = image.convert('RGB')
            result = frame_metrics(hardware, qemu)
            metrics[filename][backend] = result
            for kind, image in (('hardware', hardware), ('qemu', qemu),
                                ('diff', ImageChops.difference(hardware, qemu).point(
                                    lambda value: min(value * 4, 255)))):
                path = case / f'{backend}_{kind}.png'
                image.save(path)
                relative = path.relative_to(args.output).as_posix()
                cells.append(f'<td><a href="{relative}"><img width="195" src="{relative}"></a></td>')
        counts = ', '.join(f'{backend}: {metrics[filename][backend]["different_rgb565_pixels"]}'
                           for backend in ('epic', 'sw'))
        rows.append(f'<tr><th>{html.escape(filename + ": " + name)}<br>'
                    f'RGB565 differences<br>{counts}</th>{"".join(cells)}</tr>')
    (args.output / 'metrics.json').write_text(json.dumps(metrics, indent=2) + '\n')
    (args.output / 'index.html').write_text(
        '<!doctype html><meta charset="utf-8"><title>Hardware / QEMU</title>'
        '<p>Same-scene diagnostic comparison, not automatic visual approval. '
        'Hardware images are draw buffers, not panel photographs. '
        'Counts use RGB565 precision; diff images use RGB888 brightness ×4.</p>'
        '<table><tr><th>Scene</th><th>Hardware EPIC</th><th>QEMU EPIC</th><th>Diff ×4</th>'
        '<th>Hardware SW</th><th>QEMU SW</th><th>Diff ×4</th></tr>'
        + '\n'.join(rows) + '</table>', encoding='utf-8')
    print(f'Compared {len(manifests[0])} scenes: {args.output / "index.html"}')


if __name__ == '__main__':
    main()
