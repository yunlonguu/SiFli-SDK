#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
headless_check.py — QEMU 无头画面流转断言工具（SiFli SDK CI 用）

职责：启动 QEMU（无头、QMP unix socket）→ 连续 screendump 多张 →
      断言画面流转和 serial 无死机；可选等待完整 benchmark 串口报告。

用法示例：
  python3 tools/qemu/headless_check.py \
      --qemu tools/qemu/qemu-system-arm \
      --kernel example/xxx/project/build_sf32lb52-lcd_a128r16_hcpu/main.elf \
      --expect-size 390x450 \
      --expect-nonblack 0.5 \
      --expect-colors 16 \
      --min-diff-cells 8 \
      --shots 6 --shot-interval 1.5 --settle 14 \
      --serial-log /tmp/qemu_serial.log \
      --ppm-dir /tmp/shots

参数：
  --qemu            QEMU 可执行文件路径（必填）
  --kernel          固件 ELF 路径（必填）
  --machine         QEMU machine 字符串，如 sf32lb52x 或 sf32lb52x,flash-base=0x12020000
                    （默认 sf32lb52x；板子 flash 基址不同时需覆盖）
  --expect-size     期望分辨率 "WxH"，默认 390x450
  --expect-nonblack 期望非黑像素比例下限（0~1），默认 0.5
  --expect-colors   期望颜色桶数下限，默认 16
  --min-diff-cells  期望相邻帧差异格子数下限（16x16=256 格），默认 8
  --shots           连续截图张数，默认 6
  --shot-interval   截图间隔秒，默认 1.5
  --settle          启动后等待秒（让固件 boot+进入测试画面），默认 14
  --serial-log      串口日志输出路径（QEMU -serial file:）
  --ppm-dir         截图输出目录（默认 /tmp）
  --timeout         整体超时秒，默认 60
  --no-flow         跳过画面流转断言（仅检查非黑+死机），用于静态画面例程
  --benchmark-source benchmark C 源文件；提供后等待报告中的全部场景结果
                     （完整运行建议配合 --timeout 240；不检查性能阈值）

退出码：
  0 = 全部断言通过；1 = 任一断言失败；2 = 运行错误（QEMU 起不来等）
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time


# ---------------------------------------------------------------- PPM 解析
def parse_ppm(path):
    """解析 P6 PPM，返回 (w, h, pixel_bytes)。支持注释行。"""
    with open(path, 'rb') as f:
        d = f.read()
    if d[:2] != b'P6':
        raise ValueError(f"{path} not P6 PPM")
    j = 2
    toks = []
    while len(toks) < 3:
        if j >= len(d):
            raise ValueError(f"{path} header truncated")
        if d[j:j+1] == b'#':
            while j < len(d) and d[j:j+1] != b'\n':
                j += 1
            continue
        if d[j:j+1].isspace():
            j += 1
            continue
        k = j
        while k < len(d) and d[k:k+1].isdigit():
            k += 1
        toks.append(int(d[j:k]))
        j = k
    w, h, maxval = toks
    if w <= 0 or h <= 0 or maxval != 255:
        raise ValueError(f"{path} unsupported PPM dimensions/maxval")
    if d[j:j+1] not in (b' ', b'\t', b'\r', b'\n', b'\v', b'\f'):
        raise ValueError(f"{path} missing PPM raster separator")
    # P6 头部只消费一个分隔符，像素本身也可能是空白字节。
    data = d[j+1:]
    # 本地 SiFli QEMU 会输出按 4 字节补齐的 RGB 行（390 宽为 1172 字节）。
    # 同时接受标准紧密 PPM，只在长度精确匹配补齐布局时移除行尾填充。
    row_bytes = w * 3
    stride = (row_bytes + 3) & ~3
    if stride != row_bytes and len(data) == stride * h:
        data = b''.join(data[y*stride:y*stride+row_bytes] for y in range(h))
    if len(data) != w*h*3:
        raise ValueError(f"{path} invalid PPM raster length: {len(data)} != {w*h*3}")
    return w, h, data


def signature(px, w, h, grid=16):
    """16x16 网格平均色签名，用于帧间差异比较。"""
    out = []
    for gy in range(grid):
        for gx in range(grid):
            r = g = b = c = 0
            y0, y1 = gy*h//grid, (gy+1)*h//grid
            x0, x1 = gx*w//grid, (gx+1)*w//grid
            for y in range(y0, y1, max(1, h//64)):
                for x in range(x0, x1, max(1, w//64)):
                    o = (y*w + x) * 3
                    r += px[o]; g += px[o+1]; b += px[o+2]; c += 1
            if c:
                out.append((r//c, g//c, b//c))
            else:
                out.append((0, 0, 0))
    return out


def nonblack_ratio(px, w, h, threshold=20, step=3):
    n = tot = 0
    for y in range(0, h, step):
        for x in range(0, w, step):
            o = (y*w + x) * 3
            tot += 1
            if px[o] > threshold or px[o+1] > threshold or px[o+2] > threshold:
                n += 1
    return n / tot if tot else 0.0


def color_bucket_count(px, w, h, step=3, bucket=16):
    """颜色桶数（粗量化后不同颜色的数量），用于检测纯色/花屏。"""
    seen = set()
    for y in range(0, h, step):
        for x in range(0, w, step):
            o = (y*w + x) * 3
            seen.add((px[o]//bucket, px[o+1]//bucket, px[o+2]//bucket))
    return len(seen)


# ---------------------------------------------------------------- QEMU 驱动
class QemuSession:
    def __init__(self, qemu, kernel, serial_log, ppm_dir, timeout=60, machine='sf32lb52x'):
        self.deadline = time.monotonic() + timeout
        self.serial_log = serial_log
        self.buffer = b''
        self.sock_path = os.path.join(ppm_dir, 'qemu_mon.sock')
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)
        os.makedirs(ppm_dir, exist_ok=True)
        cmd = [
            qemu, '-M', machine, '-nographic', '-display', 'none',
            '-device', 'sifli-lcd-panel',
            '-device', 'ft6146,id=touch,bus=i2c1,irqchip=gpio1,irq-pin=31',
            '-qmp', f'unix:{self.sock_path},server=on,wait=off',
            '-monitor', 'none',
            '-serial', f'file:{serial_log}',
            '-kernel', kernel,
        ]
        with open(serial_log + '.qemu.log', 'wb') as stderr:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=stderr)
        self.sock = None
        try:
            self._connect()
            if 'QMP' not in self._receive():
                raise RuntimeError('Missing QMP greeting')
            self._command('qmp_capabilities')
        except Exception:
            self.close()
            raise

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Overall test timeout')
        if self.proc.poll() is not None:
            raise RuntimeError(f'QEMU exited unexpectedly: {self.proc.returncode}')
        return remaining

    def wait(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            remaining = self.remaining()
            if os.path.exists(self.serial_log) and os.path.getsize(self.serial_log):
                ok, msg = check_serial(self.serial_log)
                if not ok:
                    raise RuntimeError(msg)
            time.sleep(min(0.2, remaining, max(0, end - time.monotonic())))
        self.remaining()

    def _connect(self):
        while True:
            self.remaining()
            if os.path.exists(self.sock_path):
                try:
                    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.sock.settimeout(self.remaining())
                    self.sock.connect(self.sock_path)
                    return
                except OSError:
                    self.sock.close()
                    self.sock = None
            self.wait(0.2)

    def _receive(self):
        while b'\n' not in self.buffer:
            self.sock.settimeout(self.remaining())
            data = self.sock.recv(65536)
            if not data:
                raise RuntimeError('QMP connection closed')
            self.buffer += data
        line, self.buffer = self.buffer.split(b'\n', 1)
        self.remaining()
        return json.loads(line)

    def _command(self, command, arguments=None):
        self.sock.settimeout(self.remaining())
        self.sock.sendall((json.dumps({'execute': command, 'arguments': arguments or {}}) + '\n').encode())
        while True:
            reply = self._receive()
            if 'error' in reply:
                raise RuntimeError(f'QMP {command}: {reply["error"]}')
            if 'return' in reply:
                return reply['return']

    def screendump(self, path):
        if os.path.exists(path):
            os.unlink(path)
        self._command('screendump', {'filename': os.path.abspath(path)})
        # 保存为标准 PPM，保证上传的截图也能由普通图片查看器正确打开。
        w, h, px = parse_ppm(path)
        with open(path, 'wb') as f:
            f.write(f'P6\n{w} {h}\n255\n'.encode() + px)
        return os.path.getsize(path) if os.path.exists(path) else 0

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


# ---------------------------------------------------------------- 断言
def check_serial(serial_log, fatal_patterns=None):
    """serial 死机兜底：无 assert / HardFault / timeout / fatal。"""
    fatal_patterns = fatal_patterns or [
        'Assertion failed', 'assert', 'HardFault', 'hardfault',
        'fatal error', 'FATAL', 'draw_core timeout', 'panic', 'Oops',
    ]
    if not os.path.exists(serial_log):
        return False, "serial log 不存在"
    with open(serial_log, 'r', errors='replace') as f:
        text = f.read()
    if not text.strip():
        return False, "serial log 为空"
    hits = [p for p in fatal_patterns if p.lower() in text.lower()]
    if hits:
        return False, f"serial 出现致命模式: {hits}"
    return True, "serial 无致命错误"


def benchmark_cases(source):
    """从本次构建的源码读取场景名，避免写死 94/96 或只匹配最后一行。"""
    with open(source, encoding='utf-8') as f:
        source_text = f.read()
    match = re.search(r'static\s+scene_dsc_t\s+scenes\[\]\s*=\s*\{(.*?)\};',
                      source_text, re.S)
    if not match:
        raise ValueError('Benchmark scenes[] not found')
    names = re.findall(r'\.name\s*=\s*"([^"]+)"', match.group(1))
    if not names:
        raise ValueError('Benchmark scene list is empty')
    return [case for name in names for case in (name, name + ' + opa')]


def benchmark_progress(text, expected):
    """只接受报告头之后、顺序匹配且换行完整的 CSV 结果；返回已完成项数。"""
    header = ' Benchmark (in csv format)'
    if header not in text:
        return 0
    report = text.rsplit(header, 1)[1]
    lines = report.split('\n')[:-1]  # 串口可能正写到一行中间
    if not any(re.fullmatch(r'Weighted FPS: [0-9]+', line.strip()) for line in lines):
        return 0
    if not any(re.fullmatch(r'Opa\. speed: [0-9]+%', line.strip()) for line in lines):
        return 0
    completed = 0
    for line in lines:
        line = line.strip()
        if ',' not in line:
            continue
        name, fps = line.rsplit(',', 1)
        if completed >= len(expected) or name != expected[completed] or not re.fullmatch(r'[0-9]+', fps):
            raise ValueError(f'Unexpected benchmark result #{completed}: {line}')
        completed += 1
    return completed


def wait_benchmark(q, serial_log, expected):
    print(f'Waiting for benchmark report: {len(expected)} cases', flush=True)
    previous = -1
    while True:
        q.remaining()
        ok, msg = check_serial(serial_log)
        if not ok:
            raise RuntimeError(msg)
        with open(serial_log, errors='replace') as f:
            completed = benchmark_progress(f.read(), expected)
        if completed != previous:
            print(f'Benchmark report: {completed}/{len(expected)}', flush=True)
            previous = completed
        if completed == len(expected):
            return
        q.wait(0.2)


def main():
    ap = argparse.ArgumentParser(description='QEMU headless frame-flow check')
    ap.add_argument('--qemu', required=True)
    ap.add_argument('--kernel', required=True)
    ap.add_argument('--machine', default='sf32lb52x')
    ap.add_argument('--expect-size', default='390x450')
    ap.add_argument('--expect-nonblack', type=float, default=0.5)
    ap.add_argument('--expect-colors', type=int, default=16)
    ap.add_argument('--min-diff-cells', type=int, default=8)
    ap.add_argument('--shots', type=int, default=6)
    ap.add_argument('--shot-interval', type=float, default=1.5)
    ap.add_argument('--settle', type=float, default=14)
    ap.add_argument('--serial-log', default='/tmp/qemu_serial.log')
    ap.add_argument('--ppm-dir', default='/tmp')
    ap.add_argument('--timeout', type=int, default=60)
    ap.add_argument('--no-flow', action='store_true')
    ap.add_argument('--benchmark-source', help='Wait for all benchmark CSV results from this C source')
    args = ap.parse_args()
    if args.shots < (1 if args.no_flow else 2):
        ap.error('--shots must be >= 1 (--no-flow) or >= 2 (frame-flow)')
    if args.timeout <= 0 or args.settle < 0 or args.shot_interval < 0:
        ap.error('timeout must be positive; settle/shot-interval must be nonnegative')
    if not 0 <= args.expect_nonblack <= 1:
        ap.error('--expect-nonblack must be between 0 and 1')

    if not os.path.exists(args.qemu):
        print(f"FAIL: qemu not found: {args.qemu}")
        return 2
    if not os.path.exists(args.kernel):
        print(f"FAIL: kernel not found: {args.kernel}")
        return 2

    try:
        ew, eh = (int(v) for v in args.expect_size.lower().split('x'))
        if ew <= 0 or eh <= 0:
            raise ValueError('expect-size must be positive')
        expected = benchmark_cases(args.benchmark_source) if args.benchmark_source else None
    except (OSError, ValueError) as e:
        ap.error(str(e))

    # 启动 QEMU
    try:
        q = QemuSession(args.qemu, args.kernel, args.serial_log,
                        args.ppm_dir, timeout=args.timeout, machine=args.machine)
    except (OSError, RuntimeError, ValueError) as e:
        print(f"ERROR: {e}")
        return 2

    results = []
    try:
        # 等待固件 boot + 进入测试画面
        q.wait(args.settle)

        shots = []
        for i in range(args.shots):
            p = os.path.join(args.ppm_dir, f'shot_{i}.ppm')
            sz = q.screendump(p)
            if sz <= 0:
                print(f"shot{i}: FAIL (no file)")
                return 1
            w, h, px = parse_ppm(p)
            shots.append((p, w, h, px))
            print(f"shot{i}: {w}x{h} {sz}B")
            if i + 1 < args.shots:
                q.wait(args.shot_interval)

        # 1) 尺寸断言
        ok = all(w == ew and h == eh for _, w, h, _ in shots)
        results.append(("size", ok, f"expect {ew}x{eh}"))

        # 2) 非黑断言（所有帧）
        ratios = [nonblack_ratio(px, w, h) for _, w, h, px in shots]
        ok = all(r >= args.expect_nonblack for r in ratios)
        results.append(("nonblack", ok,
                        f"min={min(ratios):.1%} expect>={args.expect_nonblack:.0%}"))

        # 3) 颜色多样性断言
        colors = [color_bucket_count(px, w, h) for _, w, h, px in shots]
        ok = all(c >= args.expect_colors for c in colors)
        results.append(("colors", ok,
                        f"min={min(colors)} expect>={args.expect_colors}"))

        # 4) 画面流转断言（默认；静态画面用 --no-flow）
        if not args.no_flow:
            sigs = [signature(px, w, h) for _, w, h, px in shots]
            diffs = [
                sum(1 for a, b in zip(sigs[i], sigs[i+1]) if a != b)
                for i in range(len(sigs) - 1)
            ]
            ok = all(d >= args.min_diff_cells for d in diffs)
            results.append(("frame-flow", ok,
                            f"diffs={diffs} expect>={args.min_diff_cells}/256"))
        else:
            results.append(("frame-flow", True, "skipped (--no-flow)"))

        # 全量模式仍保留前面的截图检查，不以串口报告代替显示检查。
        if expected and all(ok for _, ok, _ in results):
            wait_benchmark(q, args.serial_log, expected)
            results.append(('benchmark', True, f'{len(expected)}/{len(expected)} CSV results'))
            p = os.path.join(args.ppm_dir, 'benchmark_final.ppm')
            q.screendump(p)
            w, h, _ = parse_ppm(p)
            results.append(('final-size', (w, h) == (ew, eh), f'{w}x{h}'))

        # 5) serial 死机兜底
        ok, msg = check_serial(args.serial_log)
        results.append(("serial", ok, msg))
        q.remaining()

    except TimeoutError as e:
        print(f"FAIL: {e} (budget {args.timeout}s)")
        return 1
    except Exception as e:
        print(f"ERROR: {e}")
        return 2
    finally:
        q.close()

    # 汇总
    failed = [(name, msg) for name, ok, msg in results if not ok]
    for name, ok, msg in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {msg}")
    if failed:
        print(f"FAILED: {len(failed)} check(s)")
        return 1
    print("PASS: all checks")
    return 0


if __name__ == '__main__':
    sys.exit(main())
