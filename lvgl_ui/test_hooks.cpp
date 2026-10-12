// Test hooks: a line-based serial protocol that lets tools/ui_generator drive
// and inspect the generated UI. Fixed code, never generated.
//
// Only compiled in with TEST_HOOKS (env:lvgl_ui_test). Otherwise ui_register()
// and the hooks are empty, so production firmware has no remote control.
//
// Every reply line starts with "@@" so the PC can tell it apart from other
// serial output (for example the touch calibration messages at boot).
//
//   PING              -> @@OK
//   TREE              -> @@TREE {"widgets":[...],"overflow":false}
//   TAP_ID <id>       -> @@OK | @@ERR <reason>     (press + release at the widget centre)
//   TAP <x> <y>       -> @@OK
//   DRAG <id> <value> -> @@OK value=<n> | @@ERR    (slider: real drag; arc: set + VALUE_CHANGED)
//   WAIT_IDLE         -> @@IDLE | @@TIMEOUT        (no pending redraws or animations)
//   RESET_UI          -> @@OK                      (destroy + create the UI)

#include "test_hooks.h"
#include "generated_ui.h"

#include <Arduino.h>
#include <lvgl.h>
#include <string.h>
#include <stdlib.h>

#ifndef TEST_HOOKS

void ui_register(lv_obj_t *, const char *) {}
void test_hooks_init(void) {}
void test_hooks_poll(void) {}

#else

static const int MAX_WIDGETS = 128;
static const int MAX_ID_LEN = 24;  // including the terminating 0

struct Entry {
    lv_obj_t *obj;
    char id[MAX_ID_LEN];
};

static Entry registry[MAX_WIDGETS];
static int registry_count = 0;
static bool registry_overflow = false;

// Virtual pointer device: taps and drags are injected here, not into the touch panel.
static lv_point_t virt_point = {0, 0};
static bool virt_pressed = false;

static char line_buf[96];
static size_t line_len = 0;

// ---------- registry ----------

static void on_registered_delete(lv_event_t *e) {
    lv_obj_t *obj = lv_event_get_target(e);
    for (int i = 0; i < registry_count; i++) {
        if (registry[i].obj == obj) {
            registry[i] = registry[--registry_count];
            return;
        }
    }
}

static Entry *find_entry(const char *id) {
    for (int i = 0; i < registry_count; i++) {
        if (strcmp(registry[i].id, id) == 0) return &registry[i];
    }
    return nullptr;
}

void ui_register(lv_obj_t *obj, const char *id) {
    if (!obj || !id || !id[0]) return;
    Entry *e = find_entry(id);
    if (!e) {
        if (registry_count >= MAX_WIDGETS) {
            registry_overflow = true;
            return;
        }
        e = &registry[registry_count++];
        strncpy(e->id, id, MAX_ID_LEN - 1);
        e->id[MAX_ID_LEN - 1] = '\0';
    }
    e->obj = obj;
    lv_obj_add_event_cb(obj, on_registered_delete, LV_EVENT_DELETE, nullptr);
}

// ---------- LVGL helpers ----------

static void virt_read(lv_indev_drv_t *, lv_indev_data_t *data) {
    data->point = virt_point;
    data->state = virt_pressed ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
}

static void pump_ms(uint32_t ms) {
    uint32_t start = millis();
    do {
        lvgl_pump();
        delay(5);
    } while (millis() - start < ms);
}

static void press_at(lv_coord_t x, lv_coord_t y) {
    virt_point.x = x;
    virt_point.y = y;
    virt_pressed = true;
}

static void release(void) {
    virt_pressed = false;
}

static void tap_at(lv_coord_t x, lv_coord_t y) {
    press_at(x, y);
    pump_ms(120);
    release();
    pump_ms(120);
}

static bool wait_idle(uint32_t timeout_ms) {
    lv_disp_t *disp = lv_disp_get_default();
    uint32_t start = millis();
    int quiet = 0;
    while (millis() - start < timeout_ms) {
        lvgl_pump();
        delay(5);
        if (disp->inv_p == 0 && lv_anim_count_running() == 0) {
            if (++quiet >= 8) return true;  // ~40 ms, longer than one refresh period
        } else {
            quiet = 0;
        }
    }
    return false;
}

static lv_coord_t clamp_coord(lv_coord_t v, lv_coord_t lo, lv_coord_t hi) {
    return v < lo ? lo : (v > hi ? hi : v);
}

// Presses on the slider track and nudges the finger until the value matches.
static int32_t drag_slider(lv_obj_t *s, int32_t target) {
    int32_t mn = lv_slider_get_min_value(s);
    int32_t mx = lv_slider_get_max_value(s);
    if (target < mn) target = mn;
    if (target > mx) target = mx;
    int32_t range = mx - mn;
    if (range <= 0) return lv_slider_get_value(s);

    lv_obj_update_layout(s);
    lv_area_t a;
    lv_obj_get_coords(s, &a);
    bool horizontal = lv_area_get_width(&a) >= lv_area_get_height(&a);
    int32_t len = horizontal ? lv_area_get_width(&a) : lv_area_get_height(&a);
    int32_t pos = (int32_t)((int64_t)(target - mn) * (len - 1) / range);

    // Vertical sliders grow upwards.
    lv_coord_t x = horizontal ? a.x1 + pos : (a.x1 + a.x2) / 2;
    lv_coord_t y = horizontal ? (a.y1 + a.y2) / 2 : a.y2 - pos;
    press_at(x, y);
    pump_ms(80);

    for (int i = 0; i < 12; i++) {
        int32_t v = lv_slider_get_value(s);
        if (v == target) break;
        int32_t step = (int32_t)((int64_t)(target - v) * len / range);
        if (step == 0) step = target > v ? 1 : -1;
        if (horizontal) x = clamp_coord(x + step, a.x1, a.x2);
        else y = clamp_coord(y - step, a.y1, a.y2);
        press_at(x, y);
        pump_ms(60);
    }
    release();
    pump_ms(100);
    return lv_slider_get_value(s);
}

// ---------- TREE ----------

static const char *type_name(lv_obj_t *obj) {
    if (lv_obj_check_type(obj, &lv_btn_class)) return "btn";
    if (lv_obj_check_type(obj, &lv_label_class)) return "label";
    if (lv_obj_check_type(obj, &lv_slider_class)) return "slider";
    if (lv_obj_check_type(obj, &lv_switch_class)) return "switch";
    if (lv_obj_check_type(obj, &lv_checkbox_class)) return "checkbox";
    if (lv_obj_check_type(obj, &lv_bar_class)) return "bar";
    if (lv_obj_check_type(obj, &lv_arc_class)) return "arc";
    if (lv_obj_check_type(obj, &lv_led_class)) return "led";
    return "obj";
}

static const char *widget_text(lv_obj_t *obj) {
    if (lv_obj_check_type(obj, &lv_label_class)) return lv_label_get_text(obj);
    if (lv_obj_check_type(obj, &lv_checkbox_class)) return lv_checkbox_get_text(obj);
    if (lv_obj_check_type(obj, &lv_btn_class)) {
        uint32_t n = lv_obj_get_child_cnt(obj);
        for (uint32_t i = 0; i < n; i++) {
            lv_obj_t *child = lv_obj_get_child(obj, i);
            if (lv_obj_check_type(child, &lv_label_class)) return lv_label_get_text(child);
        }
    }
    return nullptr;
}

static bool has_value(lv_obj_t *obj, int32_t *value) {
    if (lv_obj_check_type(obj, &lv_slider_class)) *value = lv_slider_get_value(obj);
    else if (lv_obj_check_type(obj, &lv_bar_class)) *value = lv_bar_get_value(obj);
    else if (lv_obj_check_type(obj, &lv_arc_class)) *value = lv_arc_get_value(obj);
    else if (lv_obj_check_type(obj, &lv_led_class)) *value = lv_led_get_brightness(obj);
    else return false;
    return true;
}

static bool is_visible(lv_obj_t *obj) {
    for (lv_obj_t *o = obj; o; o = lv_obj_get_parent(o)) {
        if (lv_obj_has_flag(o, LV_OBJ_FLAG_HIDDEN)) return false;
    }
    lv_area_t a;
    lv_obj_get_coords(obj, &a);
    return a.x2 >= 0 && a.y2 >= 0 && a.x1 < LV_HOR_RES && a.y1 < LV_VER_RES;
}

static void print_json_str(const char *s) {
    Serial.print('"');
    for (; s && *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') {
            Serial.print('\\');
            Serial.print((char)c);
        } else if (c < 0x20) {
            Serial.printf("\\u%04x", c);
        } else {
            Serial.print((char)c);  // UTF-8 bytes pass through unchanged
        }
    }
    Serial.print('"');
}

static void print_tree(void) {
    lv_obj_update_layout(lv_scr_act());
    Serial.print("@@TREE {\"screen\":[");
    Serial.print(LV_HOR_RES);
    Serial.print(',');
    Serial.print(LV_VER_RES);
    Serial.print("],\"widgets\":[");
    for (int i = 0; i < registry_count; i++) {
        lv_obj_t *obj = registry[i].obj;
        lv_area_t a;
        lv_obj_get_coords(obj, &a);
        if (i) Serial.print(',');
        Serial.print("{\"id\":");
        print_json_str(registry[i].id);
        Serial.printf(",\"type\":\"%s\",\"x\":%d,\"y\":%d,\"w\":%d,\"h\":%d", type_name(obj),
                      (int)a.x1, (int)a.y1, (int)lv_area_get_width(&a), (int)lv_area_get_height(&a));
        Serial.printf(",\"visible\":%s,\"checked\":%s,\"disabled\":%s",
                      is_visible(obj) ? "true" : "false",
                      lv_obj_has_state(obj, LV_STATE_CHECKED) ? "true" : "false",
                      lv_obj_has_state(obj, LV_STATE_DISABLED) ? "true" : "false");
        const char *text = widget_text(obj);
        if (text) {
            Serial.print(",\"text\":");
            print_json_str(text);
        }
        int32_t value;
        if (has_value(obj, &value)) Serial.printf(",\"value\":%ld", (long)value);
        Serial.print('}');
    }
    Serial.printf("],\"overflow\":%s}\n", registry_overflow ? "true" : "false");
}

// ---------- commands ----------

static void handle_command(char *line) {
    char *cmd = strtok(line, " ");
    if (!cmd) return;

    if (strcmp(cmd, "PING") == 0) {
        Serial.println("@@OK");
    } else if (strcmp(cmd, "TREE") == 0) {
        print_tree();
    } else if (strcmp(cmd, "TAP") == 0) {
        char *xs = strtok(nullptr, " ");
        char *ys = strtok(nullptr, " ");
        if (!xs || !ys) {
            Serial.println("@@ERR usage: TAP x y");
            return;
        }
        tap_at((lv_coord_t)atoi(xs), (lv_coord_t)atoi(ys));
        Serial.println("@@OK");
    } else if (strcmp(cmd, "TAP_ID") == 0) {
        char *id = strtok(nullptr, " ");
        Entry *e = id ? find_entry(id) : nullptr;
        if (!e) {
            Serial.println("@@ERR unknown widget id");
            return;
        }
        lv_obj_update_layout(lv_scr_act());
        if (!is_visible(e->obj)) {
            Serial.println("@@ERR widget is not visible");
            return;
        }
        lv_area_t a;
        lv_obj_get_coords(e->obj, &a);
        tap_at((a.x1 + a.x2) / 2, (a.y1 + a.y2) / 2);
        Serial.println("@@OK");
    } else if (strcmp(cmd, "DRAG") == 0) {
        char *id = strtok(nullptr, " ");
        char *vs = strtok(nullptr, " ");
        Entry *e = id ? find_entry(id) : nullptr;
        if (!e || !vs) {
            Serial.println("@@ERR unknown widget id or missing value");
            return;
        }
        int32_t target = atol(vs);
        if (lv_obj_check_type(e->obj, &lv_slider_class)) {
            Serial.printf("@@OK value=%ld\n", (long)drag_slider(e->obj, target));
        } else if (lv_obj_check_type(e->obj, &lv_arc_class)) {
            lv_arc_set_value(e->obj, (int16_t)target);
            lv_event_send(e->obj, LV_EVENT_VALUE_CHANGED, nullptr);
            pump_ms(60);
            Serial.printf("@@OK value=%ld\n", (long)lv_arc_get_value(e->obj));
        } else {
            Serial.println("@@ERR only sliders and arcs can be dragged");
        }
    } else if (strcmp(cmd, "WAIT_IDLE") == 0) {
        Serial.println(wait_idle(3000) ? "@@IDLE" : "@@TIMEOUT");
    } else if (strcmp(cmd, "RESET_UI") == 0) {
        release();
        generated_ui_destroy();
        registry_count = 0;
        registry_overflow = false;
        generated_ui_create();
        pump_ms(50);
        Serial.println("@@OK");
    } else {
        Serial.println("@@ERR unknown command");
    }
}

void test_hooks_init(void) {
    static lv_indev_drv_t virt_drv;
    lv_indev_drv_init(&virt_drv);
    virt_drv.type = LV_INDEV_TYPE_POINTER;
    virt_drv.read_cb = virt_read;
    lv_indev_drv_register(&virt_drv);
    Serial.println("@@READY");
}

void test_hooks_poll(void) {
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\r') continue;
        if (c == '\n') {
            line_buf[line_len] = '\0';
            line_len = 0;
            handle_command(line_buf);
            return;  // one command per loop() pass keeps the UI responsive
        }
        if (line_len < sizeof(line_buf) - 1) line_buf[line_len++] = c;
    }
}

#endif
