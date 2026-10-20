#!/usr/bin/env python3
"""Capture the watch menu, detect rendering errors, optionally compare a hardware PNG."""
import argparse
import json
import os
from pathlib import Path
import sys

from PIL import Image

from headless_check import QemuSession, check_serial, compare_reference, parse_ppm


def rendering_errors(serial, emulator):
    errors = []
    for line in serial.splitlines():
        if 'error in ft_' in line.lower():
            errors.append(line.strip())
    for line in emulator.splitlines():
        if 'sifli-ezip:' in line or 'sifli-epic:' in line:
            errors.append(line.strip())
    return sorted(set(errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--kernel', required=True)
    parser.add_argument('--ezip-tool', required=True, type=Path)
    parser.add_argument('--machine', default='sf32lb52x')
    parser.add_argument('--device', action='append', default=[])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--reference', type=Path, help='Reviewed hardware screenshot of the same build')
    mode.add_argument('--capture-only', action='store_true', help='Diagnostic capture; does not certify visual correctness')
    parser.add_argument('--output', type=Path, default=Path('watch-visual'))
    parser.add_argument('--settle', type=float, default=20)
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args()
    if not os.access(args.ezip_tool, os.X_OK):
        parser.error('--ezip-tool must be an executable vendor decoder')
    if args.reference:
        with Image.open(args.reference) as image:
            if image.format != 'PNG' or image.size != (390, 450):
                parser.error('Reference must be a 390x450 PNG')
            image.load()
    args.output.mkdir(parents=True, exist_ok=True)
    serial_log = str(args.output / 'serial.log')
    # The decoder runs from its own temporary directory, so use an absolute path.
    previous = os.environ.get('SIFLI_EZIP_TOOL')
    os.environ['SIFLI_EZIP_TOOL'] = str(args.ezip_tool.resolve())
    errors, shots = [], []
    q = None
    try:
        q = QemuSession(args.qemu, args.kernel, serial_log, str(args.output),
                        timeout=args.timeout, machine=args.machine,
                        extra_devices=args.device, debug_guest_errors=True)
        q.wait(args.settle)
        for sample in range(2):
            path = args.output / f'shot_{sample}.ppm'
            q.screendump(str(path))
            w, h, pixels = parse_ppm(path)
            Image.frombytes('RGB', (w, h), pixels).save(args.output / f'shot_{sample}.png')
            shots.append((str(path), w, h, pixels))
            if (w, h) != (390, 450):
                errors.append(f'Unexpected screen size: {w}x{h}')
            if len(set(zip(pixels[::3], pixels[1::3], pixels[2::3]))) < 16:
                errors.append('Screen has fewer than 16 colors')
            if sample == 0:
                q.wait(1)
        if shots[0][3] != shots[1][3]:
            errors.append('Watch menu is not stable; refusing visual comparison')
        if args.reference and not errors:
            errors.extend(message for _, ok, message in
                          compare_reference(shots, str(args.reference), str(args.output)) if not ok)
    except (OSError, RuntimeError, TimeoutError) as exc:
        errors.append(str(exc))
    finally:
        if q:
            q.close()
        if previous is None:
            os.environ.pop('SIFLI_EZIP_TOOL', None)
        else:
            os.environ['SIFLI_EZIP_TOOL'] = previous
    if Path(serial_log).exists():
        ok, message = check_serial(serial_log)
        if not ok:
            errors.append(message)
        emulator_log = Path(serial_log + '.qemu.log')
        errors.extend(rendering_errors(Path(serial_log).read_text(errors='replace'),
                                       emulator_log.read_text(errors='replace') if emulator_log.exists() else ''))
    report = {'mode': 'capture-only' if args.capture_only else 'hardware-comparison',
              'visual_verified': bool(args.reference and len(shots) == 2 and not errors),
              'errors': sorted(set(errors))}
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    for error in report['errors']:
        print('FAIL: ' + error)
    print('Artifacts: ' + str(args.output))
    if not errors:
        print('Diagnostic capture complete; visual correctness unverified.' if args.capture_only
              else 'PASS: watch menu matches hardware reference.')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
