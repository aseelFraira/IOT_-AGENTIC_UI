// Fixed firmware: owns the hardware (display, touch, LVGL). The UI itself lives
// in generated_ui.cpp, which tools/ui_generator rewrites. Never put UI code here.

#include <Arduino.h>
#include <lvgl.h>
#include <TFT_eSPI.h>
#include <touch_calibration.h>
#include "generated_ui.h"
#include "test_hooks.h"

static TFT_eSPI tft = TFT_eSPI();

static const uint16_t SCREEN_W = 240;
static const uint16_t SCREEN_H = 320;

static lv_disp_draw_buf_t draw_buf;
static lv_color_t buf[SCREEN_W * 10];

static void flush_cb(lv_disp_drv_t *drv, const lv_area_t *area, lv_color_t *color_p) {
    uint32_t w = area->x2 - area->x1 + 1;
    uint32_t h = area->y2 - area->y1 + 1;
    tft.startWrite();
    tft.setAddrWindow(area->x1, area->y1, w, h);
    tft.pushColors((uint16_t *)&color_p->full, w * h, true);
    tft.endWrite();
    lv_disp_flush_ready(drv);
}

static void touch_cb(lv_indev_drv_t *, lv_indev_data_t *data) {
    uint16_t x, y;
    if (tft.getTouch(&x, &y)) {
        data->state = LV_INDEV_STATE_PRESSED;
        data->point.x = x;
        data->point.y = y;
    } else {
        data->state = LV_INDEV_STATE_RELEASED;
    }
}

void setup() {
    tft.begin();
    tft.setRotation(0);
    loadOrCalibrateTouch(tft);

    lv_init();

    lv_disp_draw_buf_init(&draw_buf, buf, nullptr, SCREEN_W * 10);
    static lv_disp_drv_t disp_drv;
    lv_disp_drv_init(&disp_drv);
    disp_drv.hor_res = SCREEN_W;
    disp_drv.ver_res = SCREEN_H;
    disp_drv.flush_cb = flush_cb;
    disp_drv.draw_buf = &draw_buf;
    lv_disp_drv_register(&disp_drv);

    static lv_indev_drv_t indev_drv;
    lv_indev_drv_init(&indev_drv);
    indev_drv.type = LV_INDEV_TYPE_POINTER;
    indev_drv.read_cb = touch_cb;
    lv_indev_drv_register(&indev_drv);

    test_hooks_init();
    generated_ui_create();
}

void lvgl_pump(void) {
    static uint32_t lastTick = millis();
    uint32_t now = millis();
    lv_tick_inc(now - lastTick);
    lastTick = now;

    lv_timer_handler();
}

void loop() {
    lvgl_pump();
    test_hooks_poll();
    delay(5);
}
