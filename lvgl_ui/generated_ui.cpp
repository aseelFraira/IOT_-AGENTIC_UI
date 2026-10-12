#include "lvgl.h"
#include "generated_ui.h"
#include <stdio.h>
#include <string.h>

#define COL_BG      lv_color_hex(0x101418)
#define COL_PANEL   lv_color_hex(0x1B242C)
#define COL_BORDER  lv_color_hex(0x3B4A55)
#define COL_KEY     lv_color_hex(0x2563A8)
#define COL_KEY_PR  lv_color_hex(0x2F7DCC)
#define COL_TEXT    lv_color_hex(0xF4F7FA)
#define COL_MUTED   lv_color_hex(0xAEB8C2)

static lv_obj_t *lbl_note = NULL;
static lv_obj_t *key_btns[26];
static char note_text[80];
static unsigned int note_len = 0;

static const char key_letters[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

static void key_event_cb(lv_event_t *e)
{
    if(lv_event_get_code(e) != LV_EVENT_CLICKED) return;

    const char *p = (const char *)lv_event_get_user_data(e);
    if(p == NULL || lbl_note == NULL) return;

    if(note_len < sizeof(note_text) - 1) {
        note_text[note_len++] = *p;
        note_text[note_len] = '\0';
        lv_label_set_text(lbl_note, note_text);
    }
}

static lv_obj_t *create_key(lv_obj_t *parent, int index)
{
    const int cols = 10;
    const int margin = 6;
    const int top = 118;
    const int key_w = 21;
    const int key_h = 44;
    const int gap = 2;

    int row = index / cols;
    int col = index % cols;

    lv_obj_t *btn = lv_btn_create(parent);
    lv_obj_set_size(btn, key_w, key_h);
    lv_obj_set_pos(btn, margin + col * (key_w + gap), top + row * (key_h + gap));
    lv_obj_clear_flag(btn, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(btn, COL_KEY, 0);
    lv_obj_set_style_bg_color(btn, COL_KEY_PR, LV_STATE_PRESSED);
    lv_obj_set_style_radius(btn, 5, 0);
    lv_obj_set_style_border_width(btn, 0, 0);
    lv_obj_set_style_shadow_width(btn, 0, 0);
    lv_obj_set_style_pad_all(btn, 0, 0);

    char id[16];
    snprintf(id, sizeof(id), "key_%c", (char)('a' + index));
    ui_register(btn, id);

    lv_obj_add_event_cb(btn, key_event_cb, LV_EVENT_CLICKED, (void *)&key_letters[index]);

    lv_obj_t *lab = lv_label_create(btn);
    char txt[2];
    txt[0] = key_letters[index];
    txt[1] = '\0';
    lv_label_set_text(lab, txt);
    lv_obj_set_style_text_color(lab, COL_TEXT, 0);
    lv_obj_center(lab);

    return btn;
}

void generated_ui_create(void)
{
    generated_ui_destroy();

    lv_obj_t *scr = lv_scr_act();
    lv_obj_set_style_bg_color(scr, COL_BG, 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);
    lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);

    note_text[0] = '\0';
    note_len = 0;

    lv_obj_t *note_box = lv_obj_create(scr);
    lv_obj_set_pos(note_box, 6, 8);
    lv_obj_set_size(note_box, 228, 80);
    lv_obj_clear_flag(note_box, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(note_box, COL_PANEL, 0);
    lv_obj_set_style_bg_opa(note_box, LV_OPA_COVER, 0);
    lv_obj_set_style_border_color(note_box, COL_BORDER, 0);
    lv_obj_set_style_border_width(note_box, 2, 0);
    lv_obj_set_style_radius(note_box, 8, 0);
    lv_obj_set_style_pad_all(note_box, 6, 0);

    lv_obj_t *lbl_title = lv_label_create(note_box);
    lv_label_set_text(lbl_title, "NOTE");
    lv_obj_set_pos(lbl_title, 6, 4);
    lv_obj_set_style_text_color(lbl_title, COL_MUTED, 0);

    lbl_note = lv_label_create(note_box);
    lv_label_set_text(lbl_note, "");
    lv_label_set_long_mode(lbl_note, LV_LABEL_LONG_WRAP);
    lv_obj_set_pos(lbl_note, 6, 28);
    lv_obj_set_size(lbl_note, 210, 42);
    lv_obj_set_style_text_color(lbl_note, COL_TEXT, 0);
    ui_register(lbl_note, "lbl_note");

    lv_obj_t *lbl_kb = lv_label_create(scr);
    lv_label_set_text(lbl_kb, "English Keyboard");
    lv_obj_set_pos(lbl_kb, 58, 96);
    lv_obj_set_style_text_color(lbl_kb, COL_TEXT, 0);

    for(int i = 0; i < 26; i++) {
        key_btns[i] = create_key(scr, i);
    }
}

void generated_ui_destroy(void)
{
    lv_obj_clean(lv_scr_act());

    lbl_note = NULL;
    for(int i = 0; i < 26; i++) {
        key_btns[i] = NULL;
    }

    note_text[0] = '\0';
    note_len = 0;
}
