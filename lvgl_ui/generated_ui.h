#pragma once

#include <lvgl.h>
#include "ui_theme.h"  // UI_LIGHT_THEME: 0 = dark colours, 1 = light colours

// Interface between the fixed firmware (main.cpp) and the Gemini-generated UI.
// generated_ui.cpp is (re)written by tools/ui_generator; this header never is.

// Builds the whole UI on the active screen. Called once after LVGL, the
// display and the touch input are initialised.
void generated_ui_create(void);

// Removes everything generated_ui_create() built.
void generated_ui_destroy(void);

// Gives a widget a stable name so automated tests (ui_spec.json) can find it.
// The id is copied. Implemented in test_hooks.cpp; does nothing unless the
// firmware is built with TEST_HOOKS.
void ui_register(lv_obj_t *obj, const char *id);
