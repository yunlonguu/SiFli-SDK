#!/usr/bin/env python3
"""Build a side-by-side software/EPIC gallery; differences are diagnostic, not pass/fail."""
import argparse
import html
import json
from pathlib import Path

from PIL import Image, ImageChops


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--software', required=True, type=Path)
    parser.add_argument('--epic', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.software / 'manifest.json').read_text())
    if manifest != json.loads((args.epic / 'manifest.json').read_text()):
        parser.error('Software and EPIC manifests must match')
    args.output.mkdir(parents=True, exist_ok=True)
    rows, metrics = [], {}
    for filename, name in manifest.items():
        case = args.output / Path(filename).stem
        case.mkdir(exist_ok=True)
        with Image.open(args.software / filename) as image:
            sw = image.convert('RGB')
        with Image.open(args.epic / filename) as image:
            epic = image.convert('RGB')
        if sw.size != epic.size:
            raise ValueError(f'{filename}: dimensions differ')
        diff = ImageChops.difference(sw, epic)
        changed = sum(pixel != (0, 0, 0) for pixel in diff.getdata())
        metrics[filename] = {'name': name, 'different_pixels': changed,
                             'total_pixels': sw.width * sw.height}
        sw.save(case / 'software.png')
        epic.save(case / 'epic.png')
        diff.point(lambda value: min(value * 4, 255)).save(case / 'diff.png')
        cells = ''.join(f'<td><a href="{case.name}/{kind}.png">'
                        f'<img width="260" src="{case.name}/{kind}.png"></a></td>'
                        for kind in ('software', 'epic', 'diff'))
        rows.append(f'<tr><th>{html.escape(filename + ": " + name)}'
                    f'<br>{changed} pixels differ</th>{cells}</tr>')
    (args.output / 'metrics.json').write_text(json.dumps(metrics, indent=2) + '\n')
    (args.output / 'index.html').write_text(
        '<!doctype html><meta charset="utf-8"><title>Software / EPIC comparison</title>'
        '<p>Diagnostic comparison. Differences include valid rounding differences; '
        'software output also needs review. Diff brightness is multiplied by 4.</p>'
        '<table><tr><th>Scene</th><th>Software</th><th>EPIC</th><th>Diff ×4</th></tr>'
        + '\n'.join(rows) + '</table>', encoding='utf-8')
    print(f'Compared {len(manifest)} scenes: {args.output / "index.html"}')


if __name__ == '__main__':
    main()
