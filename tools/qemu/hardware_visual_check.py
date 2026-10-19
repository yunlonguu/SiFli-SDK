#!/usr/bin/env python3
"""Capture frozen SF32LB52X RGB565 frames through the macOS serial bridge."""
import argparse
import json
import os
from pathlib import Path
import re
import select
import subprocess
import time

from PIL import Image

from demo_visual_check import gallery
from headless_check import benchmark_cases


class Console:
    def __init__(self, bridge, port, baud, log):
        self.log = log.open('ab')
        self.buffer = b''
        self.proc = subprocess.Popen([bridge, port, str(baud)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def line(self, deadline):
        while b'\n' not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Timed out waiting for firmware')
            if not select.select([self.proc.stdout], [], [], remaining)[0]:
                raise TimeoutError('Serial response timeout')
            data = os.read(self.proc.stdout.fileno(), 65536)
            if not data:
                raise RuntimeError('Serial bridge closed')
            self.log.write(data)
            self.log.flush()
            self.buffer += data
        line, self.buffer = self.buffer.split(b'\n', 1)
        return line.decode('ascii', errors='replace').strip()

    def send(self, command):
        self.proc.stdin.write((command + '\r\n').encode())
        self.proc.stdin.flush()

    def wait(self, pattern, deadline):
        while True:
            line = self.line(deadline)
            if 'LV_FRAME_ERROR' in line or ' INVALID' in line:
                raise RuntimeError(line)
            match = re.search(pattern, line)
            if match:
                return match

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.log.close()


def read_frame(console, deadline):
    header = console.wait(r'LV_FRAME_BEGIN (\d+) (\d+) (\d+) ([01]) (\d+)', deadline)
    width, height, stride, swap, size = map(int, header.groups())
    if not 0 < width <= stride <= 4096 or not 0 < height <= 4096 or size != stride * height * 2:
        raise ValueError('Invalid framebuffer dimensions')
    raw = bytearray()
    checksum = 2166136261
    while len(raw) < size:
        match = console.wait(r'LV_FRAME_DATA (\d+) ([0-9a-f]+)', deadline)
        if int(match[1]) != len(raw):
            raise ValueError('Framebuffer data missing or out of order')
        chunk = bytes.fromhex(match[2])
        raw.extend(chunk)
        for value in chunk:
            checksum = ((checksum ^ value) * 16777619) & 0xffffffff
    end = console.wait(r'LV_FRAME_END ([0-9a-f]{8})', deadline)
    if len(raw) != size or int(end[1], 16) != checksum:
        raise ValueError('Framebuffer size/checksum mismatch')
    rgb = bytearray()
    for y in range(height):
        for x in range(width):
            offset = (y * stride + x) * 2
            pixel = int.from_bytes(raw[offset:offset + 2], 'big' if swap else 'little')
            r, g, b = pixel >> 11, (pixel >> 5) & 63, pixel & 31
            # Bit replication preserves the original RGB565 channel values.
            rgb.extend(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)))
    return Image.frombytes('RGB', (width, height), bytes(rgb)), raw, {
        'width': width, 'height': height, 'stride': stride, 'swap': swap,
        'bytes': size, 'fnv1a': f'{checksum:08x}'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bridge', required=True, help='Built sifli-serial-console bridge; SF32LB52X only')
    parser.add_argument('--port', required=True)
    parser.add_argument('--baud', type=int, default=1000000)
    parser.add_argument('--source', required=True)
    parser.add_argument('--scenes', default='32,38,70,84', help='Zero-based scene numbers')
    parser.add_argument('--renderer', choices=('epic', 'sw', 'both'), default='both')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--settle', type=float, default=20, help='Startup allowance after opening UART')
    parser.add_argument('--timeout', type=float, default=90, help='Deadline per scene, including UART export')
    parser.add_argument('--retries', type=int, default=2, help='Retry incomplete/corrupted UART frames')
    parser.add_argument('--resume', action='store_true', help='Validate and reuse previously captured frames')
    args = parser.parse_args()
    if args.retries < 0 or args.timeout <= 0 or args.settle < 0:
        parser.error('Retries/settle must be nonnegative and timeout must be positive')
    names = benchmark_cases(args.source)
    scenes = [int(value) for value in args.scenes.split(',')]
    if not scenes or len(scenes) != len(set(scenes)) or any(n < 0 or n >= len(names) for n in scenes):
        parser.error('Scenes must be unique valid benchmark indices')
    if args.output.exists() and any(args.output.iterdir()) and not args.resume:
        parser.error('Output must be empty to preserve previous captures')
    args.output.mkdir(parents=True, exist_ok=True)
    renderers = ('epic', 'sw') if args.renderer == 'both' else (args.renderer,)
    console = Console(args.bridge, args.port, args.baud, args.output / 'serial.log')
    try:
        # Opening the bridge can release the board's reset line. Drain boot logs.
        until = time.monotonic() + args.settle
        while time.monotonic() < until:
            try:
                console.line(until)
            except TimeoutError:
                break
        for backend in renderers:
            directory = args.output / backend
            directory.mkdir(exist_ok=args.resume)
            manifest = json.loads((directory / 'manifest.json').read_text()) if args.resume and (directory / 'manifest.json').exists() else {}
            metadata = json.loads((directory / 'metadata.json').read_text()) if args.resume and (directory / 'metadata.json').exists() else {}
            cases = []
            if any(filename not in {f'{scene:03d}.png' for scene in scenes} for filename in manifest):
                raise ValueError('Existing manifest contains scenes outside this capture')
            for scene in scenes:
                filename = f'{scene:03d}.png'
                if filename in manifest:
                    raw = (directory / f'{scene:03d}.rgb565').read_bytes()
                    info = metadata[filename]
                    checksum = 2166136261
                    for value in raw:
                        checksum = ((checksum ^ value) * 16777619) & 0xffffffff
                    with Image.open(directory / filename) as image:
                        image.load()
                        valid_image = image.size == (info['width'], info['height'])
                    if (manifest[filename] != names[scene] or len(raw) != info['bytes'] or
                            f'{checksum:08x}' != info['fnv1a'] or not valid_image):
                        raise ValueError(f'Existing capture failed validation: {filename}')
                    cases.append((f'{scene:03d}: {names[scene]}', directory / filename))
                    print(f'Reused {scene:03d} {backend}: checksum verified', flush=True)
                    continue
                for attempt in range(args.retries + 1):
                    deadline = time.monotonic() + args.timeout
                    # A new READY also drains any remaining output of a rejected frame.
                    console.send(f'lv_snapshot {scene} {backend}')
                    console.wait(rf'QEMU_SNAPSHOT {scene} READY', deadline)
                    console.wait(rf'QEMU_RENDERER {backend.upper()}', deadline)
                    console.send('lv_frame_dump')
                    try:
                        image, raw, info = read_frame(console, deadline)
                        break
                    except (ValueError, TimeoutError) as error:
                        if attempt == args.retries:
                            raise
                        print(f'Retrying {scene:03d} {backend}: {error}', flush=True)
                filename = f'{scene:03d}.png'
                image.save(directory / filename)
                (directory / f'{scene:03d}.rgb565').write_bytes(raw)
                manifest[filename] = names[scene]
                metadata[filename] = info
                cases.append((f'{scene:03d}: {names[scene]}', directory / filename))
                (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
                (directory / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
                print(f'Captured {scene:03d} {backend}: {image.size}, checksum verified', flush=True)
            gallery(directory, cases)
    finally:
        console.close()


if __name__ == '__main__':
    main()
