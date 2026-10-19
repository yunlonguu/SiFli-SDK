# QEMU UI 自动化检查

实现参考 LVGL 官方的截图断言与 CI：

- [v8 测试说明及 TEST_ASSERT_EQUAL_SCREENSHOT](https://github.com/lvgl/lvgl/blob/v8.3.11/tests/README.md)
- [官方 CI 的截图失败产物](https://github.com/lvgl/lvgl/blob/master/.github/workflows/ccpp.yml)

## 当前检查范围

| 任务 | push | 每日/手动运行 |
| --- | --- | --- |
| `lvgl_v8_examples` | 静态页面参考图比较 + 触摸左右拖动及截图断言 | 同左 |
| `lvgl_v8_demos` | 动画冒烟检查 + 全部 96 个固定场景截图比较 | 额外等待完整 benchmark CSV 报告 |

截图比较不使用 OCR，也不使用整体相似度代替像素断言。每个像素的 R/G/B
差值均不得超过 `--pixel-tolerance`（默认 0）；任一像素超差即失败。
每张采样图都要通过，参考图不存在或损坏时立即失败，不会自动创建或更新。

benchmark 的性能测试保持动画运行；截图比较在另一个 QEMU 进程执行，
通过串口选择固定场景。其 96 项报告通过只证明全量运行完成。
截图相同证明与基线一致，不能替代首次画面正确性审查。

## Demo 图库：无需逐个手动截图

打开 `ref_imgs/benchmark_sf32lb52-lcd_a128r16/index.html` 查看全部 96 张完整图片；
也可直接查看同目录的 `overview_0.png` 至 `overview_3.png`（每页 24 张）。
`manifest.json` 将文件名对应到场景名称，场景数量从本次构建的 C 源码提取。

**当前 demo 图库是待审查的渲染结果基线，不是全部画面正确的认证。**
自动采集中发现 ARGB 图片杂纹、部分场景内容缺失等现象，详见图库中的 `REVIEW.md`。
CI 逐张比较这些结果可以检测后续变化，但不会把现存异常自动判定为正确。
修复渲染问题后，需要审查差异并显式更新受影响的参考图。

板级配置 `LV_DEMO_QEMU_SNAPSHOT` 默认关闭，仅当前 CI 测试板启用。
测试命令 `lv_snapshot N` 只投递请求；LVGL 主线程清理上一场景并重建指定场景。
沿用 demo 的固定随机数序列，位置取垂直行程中点，圆弧终点固定为 180°，
文字截图只显示一份完整的多行样本，限制宽度以避免重叠和越界；性能动画仍保留全部对象。
不启动场景切换或动画，也不显示动态 FPS。每场景连续采样两张，必须完全一致。
固定截图仅覆盖这一组位置和角度，其他动画时刻由原有运行检查覆盖流程。

```bash
python3 tools/qemu/demo_visual_check.py \
  --qemu tools/qemu/qemu-system-arm \
  --kernel example/multimedia/lvgl/lvgl_v8_demos/project/build_sf32lb52-lcd_a128r16_hcpu/main.elf \
  --source example/multimedia/lvgl/lvgl_v8_demos/src/lv_demos/benchmark/lv_demo_benchmark.c \
  --references tools/qemu/ref_imgs/benchmark_sf32lb52-lcd_a128r16 \
  --output demo-visual --serial-log qemu-demo-visual.log --timeout 180
```

首次采集使用同一命令，增加 `--record`，并将 `--references` 指向一个新的空目录。
该模式批量生成候选 PNG、场景清单、HTML 总览及四张缩略总览图；已有参考目录不会被覆盖。
CI 不使用 `--record`，缺图、场景清单变更、尺寸异常、画面不稳定或像素变化均失败。

## 软件、QEMU EPIC 与硬件对照

使用同一 ELF，串口执行 `lv_snapshot N epic` 或 `lv_snapshot N sw`。
等待 `QEMU_SNAPSHOT N READY` 后画面保持不动；`QEMU_RENDERER` 标识当前绘制后端。
不带后端参数时默认 EPIC。软件模式也会清空图片缓存，让内置解码器展开普通 Alpha/索引图片。
该模式用于本例程的标准 LVGL 资源，不能将 EPIC 专用资源的软件显示作为正确性标准。

分别用上述采集命令加 `--renderer epic` 和 `--renderer sw`，将 `--references`
指向两个不同的空目录并加 `--record`。之后生成并排报告：

```bash
python3 tools/qemu/compare_renderers.py \
  --software /tmp/demo-sw --epic /tmp/demo-epic --output /tmp/demo-renderers
```

打开输出 `index.html`，每项显示软件、EPIC 和放大亮度的差值图，
`metrics.json` 保存差异像素数。跨渲染器可能存在正常的舍入或抗锯齿差异，报告不据此自动判失败。

硬件应烧录同一 ELF 对应的固件，使用相同屏幕分辨率、配置和场景编号。
先对比 `32/33`（ARGB）、`38/39`（Alpha）、`42/43`（重着色）、
`48/49`（RGB 旋转）、`70–75`（压缩字体）和 `82–95`（减色）。
同一编号分别执行 `epic`/`sw`：硬件两者正常、QEMU EPIC 异常，指向模拟器；
硬件 EPIC 异常但软件正常，指向 EPIC 适配/驱动；两者都异常，再检查资源和场景。
照片用于人工识别空白、条纹和缺字；精确像素比较需要从硬件导出 framebuffer，不能直接使用照片。

### 黄山派 ULP 的硬件缓冲采集

`sf32lb52-lchspi-ulp_hcpu` 的例程板级配置也启用了固定场景入口。
SF32LB52X 在 macOS 上可使用 `sifli-serial-console` 技能提供的串口 bridge，
保持 DTR/RTS 低电平。采集工具不执行烧录；使用前应关闭其他串口监视器。

```bash
python3 tools/qemu/hardware_visual_check.py \
  --bridge /tmp/sifli-serial-tools/sifli_serial_bridge \
  --port /dev/cu.usbserial-114110 \
  --source example/multimedia/lvgl/lvgl_v8_demos/src/lv_demos/benchmark/lv_demo_benchmark.c \
  --scenes 32,38,70,84 --output hardware-visual
```

默认分别采集 EPIC 和软件模式。`lv_frame_dump` 将冻结后的 RGB565 绘制缓冲
经串口导出，包含行跨度、字节顺序、数据偏移及 FNV-1a 校验。
仅支持一个整屏绘制缓冲、无额外 LCD framebuffer 的 RGB565 配置；
等待 GPU 完成与 LCD flush 完成后读取，不调用截图 API 重新绘制对象。
工具校验传输完整性后保存原始 `.rgb565`、PNG、元数据及 HTML。
PNG 使用位复制将 RGB565 展开为 RGB888；跨工具比较时应先统一颜色展开方式。
采集失败会保留串口日志并退出，不会将不完整数据作为有效截图。
传输缺字节或校验失败时默认最多重试两次；已校验通过的场景会逐项写入清单。

此方法读取送屏前的绘制结果，不能证明 LCD 接线、传输和实际面板显示正确。
仍需人工观察物理屏幕，必要时拍照确认。

本机 QEMU 运行同一黄山派 NOR ELF 时，需要指定应用向量表所在的 flash-base，
并挂接充电芯片地址的 I2C 应答占位设备，避免启动阶段等待未模拟的外设：

```bash
python3 tools/qemu/demo_visual_check.py \
  --qemu /Volumes/TB/qemu-sifli-macos-arm64/bin/qemu-system-arm \
  --kernel example/multimedia/lvgl/lvgl_v8_demos/project/build_sf32lb52-lchspi-ulp_hcpu/main.elf \
  --machine sf32lb52x,flash-base=0x12020000,flash-size=33554432,psram-size=8388608 \
  --device i2c-echo,bus=i2c2,address=0x49 \
  --source example/multimedia/lvgl/lvgl_v8_demos/src/lv_demos/benchmark/lv_demo_benchmark.c \
  --renderer epic --record --references qemu-lchspi-epic \
  --output qemu-lchspi-capture --serial-log qemu-lchspi.log
```

`flash-size` 对应当前生成配置的 `BSP_QSPI2_MEM_SIZE=32`，是模拟窗口大小。
`i2c-echo` 仅用于让启动继续，不是 AW32001 的功能模型，不能用于验证充电功能。
本例的画面对比使用同一个 `main.elf`；此启动配置尚未加入常规 CI 板型矩阵。

硬件与 QEMU 两边都采集相同场景的 `epic/`、`sw/` 后，生成四组并排报告：

```bash
python3 tools/qemu/compare_hardware.py \
  --hardware hardware-visual --qemu qemu-visual --output hardware-vs-qemu
```

报告分别统计 RGB888 差异和还原到 RGB565 精度后的差异，避免将不同的
RGB565 展开算法误判为绘制差异；仍属于诊断报告，不自动认证画面正确。

## 交互测试与官方实践

官方测试不只比较截图，还包括：

| 内容 | 官方实现参考 | 当前 SDK 的借鉴方式 |
| --- | --- | --- |
| 点击、按住、释放、拖动 | `lv_test_mouse_click_at`、`lv_test_mouse_press/release`、`lv_test_mouse_move_to` | QMP `input-send-event` 注入触摸，验证左右拖动 |
| 键盘、编码器、焦点 | `tests/src/test_cases/widgets/test_slider.c` 等 | 后续可增加对应输入设备用例，当前未接入 |
| 控件状态、事件和边界条件 | `tests/src/test_cases/test_click.c`、`widgets/test_switch.c` 等 | 当前以截图验证滚动结果，尚无点击事件计数断言 |
| 确定性时间与重绘 | `lv_test_wait`、`lv_test_fast_forward` | 嵌入式 RTOS 使用真实时钟，固定场景状态后采样两次验证稳定性 |
| 内存、解析器、布局及绘制单元测试 | 官方 `tests/src/test_cases/` | 这些属于 LVGL 主机单元测试，不能由 QEMU 截图检查替代 |

官方[测试辅助文档](https://github.com/lvgl/lvgl/blob/master/docs/src/debugging/test.mdx)
介绍了鼠标、键盘、编码器及模拟时间；本 SDK v8 不直接引入 v9 的测试框架。

`touch_check.py` 复用现有 `lv_example_scroll_1()`，执行“初始 → 向左拖动 → 向右拖回”。
输入经过 QEMU 的 FT6146 模型、I2C/中断、SDK 触摸驱动及 LVGL 输入设备处理，
没有直接设置滚动位置或直接发送 LVGL 事件。三个状态均采样两次并全屏零容差比较，
还要求第一次拖动确实改变画面，反向拖动恢复初始画面。
当前没有接入按钮点击或长按的事件断言，不能把拖动通过解释为全部交互通过。

```bash
python3 tools/qemu/touch_check.py \
  --qemu tools/qemu/qemu-system-arm \
  --kernel example/multimedia/lvgl/lvgl_v8_examples/project/build_sf32lb52-lcd_a128r16_hcpu/main.elf \
  --initial-reference tools/qemu/ref_imgs/lvgl_v8_examples_sf32lb52-lcd_a128r16.png \
  --references tools/qemu/ref_imgs/scroll_touch_sf32lb52-lcd_a128r16 \
  --output touch-visual --serial-log qemu-touch.log
```

## 运行静态页面比较

先按 SDK 的环境初始化和构建流程生成固件，再运行：

```bash
python3 -m pip install -r tools/qemu/requirements.txt
python3 tools/qemu/headless_check.py \
  --qemu tools/qemu/qemu-system-arm \
  --kernel example/multimedia/lvgl/lvgl_v8_examples/project/build_sf32lb52-lcd_a128r16_hcpu/main.elf \
  --expect-size 390x450 --expect-nonblack 0.15 --expect-colors 12 \
  --shots 3 --shot-interval 1 --settle 14 --no-flow \
  --reference tools/qemu/ref_imgs/lvgl_v8_examples_sf32lb52-lcd_a128r16.png \
  --serial-log qemu.log --ppm-dir shots
```

macOS 请将 `--qemu` 替换为本机可执行的 QEMU 路径。
`--reference` 只适用于稳定的静态页面，要求 `--no-flow`，不能与 benchmark 模式组合。
14 秒用于等待启动稳定；每次检查三张，若页面变成动画，应增加可重复的测试入口，
不要放宽容差来掩盖动画不同步。

## 查看结果

GitHub Actions 的任务日志和 Summary 会显示每张截图的差异像素数、比例及最大通道差。
进入运行 Summary 的 Artifacts，下载对应任务的压缩包，在 `shots/` 查看：

- `reference.png`：本次采用的参考图。
- `shot_N_actual.png`：实际画面；原始采样同时保留为 PPM。
- `shot_N_diff.png`：RGB 绝对差值，一致区域为黑色。
- `shot_N_mask.png`：超过容差的像素为白色，其余为黑色。

尺寸不一致时保留参考图和实际图并判失败，不生成尺寸无意义的差异图。
任务失败时仍上传产物；QEMU 本身无法启动等情况可能没有截图。

demo 和交互的产物分别位于 `demo-visual/`、`touch-visual/`。
下载解压后打开 `index.html` 或 `overview_*.png` 集中查看；
各场景子目录保存全尺寸 `actual.png`、参考图和每张采样的差异图，失败时同样保留。

## 首张参考图及更新规则

`ref_imgs/lvgl_v8_examples_sf32lb52-lcd_a128r16.png` 对应当前
`lv_example_scroll_1()` 的初始页面，分辨率 390×450。
其滚动容器有意包含超出边界的子对象，因此右侧 `Right` 和底部 `Bottom` 的裁剪
属于此例程的滚动展示，不应为了完整显示文字改变参考图。

生成条件：代码基于 `1dff9e1b9`，仅在该测试板级配置中关闭动态性能监视器。
重新构建后，三次连续采样完全相同；与
[Linux CI 39187140405](https://github.com/yunlonguu/SiFli-SDK/actions/runs/39187140405)
的静态截图逐像素核对，变化仅位于原 FPS/CPU 显示区域 `(309,407)-(387,443)`。
参考图覆盖整个屏幕，没有遮罩或忽略区域。

参考图是待人工复核的预期页面，不应因为测试失败而自动重新生成。
有意改变 UI、字体、颜色配置或渲染方式时，应先检查实际图与差异图，确认改变符合预期，
再显式更新 PNG，并在评审中同时说明原因、构建配置与对比结果。
固定 QEMU、固件配置和字体版本；不同渲染后端需要分别确认基线。

## 比较器测试

```bash
python3 -m unittest discover -s tests -p test_qemu_screenshot.py -v
```

覆盖相同画面、单像素变化、容差边界、分辨率变化、缺少参考图和多张采样。

## lchspi-ulp hardware comparison CI

The `lvgl_v8_demos_hardware` job builds firmware and a Linux QEMU from pinned
commit `46044958d5fe38060e2179ffddfd55e500630f4d`, applying
`patches/hardware-epic.patch` (the local hardware-comparison fixes).
It captures all 96 frozen EPIC scenes with two stable samples per scene, then
compares them with `ref_imgs/hardware_lchspi_ulp` at RGB565 precision.
`--record` writes only the temporary QEMU actual images, never the hardware baseline.

Download the `lvgl_v8_demos_hardware-sf32lb52-lchspi-ulp` artifact, extract it,
and open `hardware-comparison/index.html`: hardware, QEMU, difference ×4.
Black difference images indicate equality. A nonzero difference fails the job,
but the report uploads even on failure. There are currently 18 known differences;
this job is diagnostic and is expected to fail until they are fixed.
The hardware reference is a draw-buffer export, not a camera photograph.
The firmware is rebuilt in CI, so future source/toolchain changes may change its
ELF hash or rendered pixels; the built ELF and rtconfig.h are included in artifacts.
