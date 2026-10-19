#include "rtthread.h"

#ifdef LV_DEMO_QEMU_SNAPSHOT
#include "rthw.h"
#include <stdlib.h>
#include "lv_demos.h"
#include "lv_draw_sw.h"
#include "lv_gpu_sifli_epic.h"
#include <string.h>

/* The shell only posts a request; LVGL objects belong to the main thread. */
static volatile int pending_scene = -1;
static bool pending_gpu = true;
static volatile bool pending_dump;
static bool snapshot_ready;

static void lv_snapshot(int argc, char **argv)
{
    char *end;
    long scene;
    rt_base_t level;

    bool gpu = true;
    if (argc < 2 || argc > 3) return;
    if (argc == 3)
    {
        if (strcmp(argv[2], "sw") == 0) gpu = false;
        else if (strcmp(argv[2], "epic") != 0) return;
    }
    scene = strtol(argv[1], &end, 10);
    if (*end || scene < 0 || scene > 32767) return;

    level = rt_hw_interrupt_disable();
    if (pending_scene < 0)
    {
        pending_gpu = gpu;
        pending_scene = (int)scene;
    }
    rt_hw_interrupt_enable(level);
}
MSH_CMD_EXPORT(lv_snapshot, Freeze benchmark scene: N [epic|sw]);

static void lv_frame_dump(void)
{
    rt_base_t level = rt_hw_interrupt_disable();
    pending_dump = true;
    rt_hw_interrupt_enable(level);
}
MSH_CMD_EXPORT(lv_frame_dump, Export frozen RGB565 draw buffer over UART);

static void dump_frame(void)
{
#if defined(LV_FB_ONE_SCREEN_SIZE) && defined(LCD_FB_USING_NONE) && LV_COLOR_DEPTH == 16
    lv_disp_t *disp = lv_disp_get_default();
    lv_disp_draw_buf_t *buf = lv_disp_get_draw_buf(disp);
    if (!snapshot_ready || !disp->driver->direct_mode || buf->buf2 ||
        buf->cf != LV_IMG_CF_TRUE_COLOR || buf->width < lv_disp_get_hor_res(disp) ||
        buf->height < lv_disp_get_ver_res(disp))
    {
        rt_kprintf("LV_FRAME_ERROR full-screen snapshot required\n");
        return;
    }

    if (disp->driver->draw_ctx->wait_for_finish)
        disp->driver->draw_ctx->wait_for_finish(disp->driver->draw_ctx);
    rt_tick_t start = rt_tick_get();
    while (buf->flushing)
    {
        if (rt_tick_get() - start > rt_tick_from_millisecond(2000))
        {
            rt_kprintf("LV_FRAME_ERROR flush timeout\n");
            return;
        }
        rt_thread_mdelay(1);
    }

    const uint8_t *pixels = buf->buf1;
    uint32_t size = (uint32_t)buf->width * lv_disp_get_ver_res(disp) * sizeof(lv_color_t);
    uint32_t checksum = 2166136261u;
    static const char hex[] = "0123456789abcdef";
    char line[97];
    rt_kprintf("\nLV_FRAME_BEGIN %d %d %d %d %u\n", lv_disp_get_hor_res(disp),
               lv_disp_get_ver_res(disp), buf->width, LV_COLOR_16_SWAP, size);
    for (uint32_t offset = 0; offset < size; offset += 48)
    {
        uint32_t count = size - offset;
        if (count > 48) count = 48;
        for (uint32_t i = 0; i < count; i++)
        {
            uint8_t value = pixels[offset + i];
            line[2 * i] = hex[value >> 4];
            line[2 * i + 1] = hex[value & 15];
            checksum = (checksum ^ value) * 16777619u;
        }
        line[2 * count] = '\0';
        rt_kprintf("LV_FRAME_DATA %u %s\n", offset, line);
    }
    rt_kprintf("LV_FRAME_END %08x\n", checksum);
#else
    rt_kprintf("LV_FRAME_ERROR requires one full-screen RGB565 buffer without LCD framebuffer\n");
#endif
}

void lv_demo_qemu_poll(void)
{
    int scene;
    bool gpu;
    rt_base_t level = rt_hw_interrupt_disable();
    scene = pending_scene;
    gpu = pending_gpu;
    pending_scene = -1;
    bool dump = pending_dump;
    pending_dump = false;
    rt_hw_interrupt_enable(level);

    if (scene >= 0)
    {
        lv_gpu_set_enable(gpu);
        lv_img_cache_invalidate_src(NULL);
        bool ok = lv_demo_benchmark_snapshot(scene);
        lv_refr_now(NULL);
        snapshot_ready = ok;
        rt_kprintf("\nQEMU_SNAPSHOT %d %s\n", scene, ok ? "READY" : "INVALID");
        rt_kprintf("QEMU_RENDERER %s\n", gpu ? "EPIC" : "SW");
    }
    if (dump) dump_frame();
}
#endif
