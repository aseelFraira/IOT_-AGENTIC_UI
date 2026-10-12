#pragma once

// Serial test protocol used by tools/ui_generator/device_runner.py. Fixed code,
// never generated. Without TEST_HOOKS these functions do nothing.

// Called once after the display and touch drivers are registered.
void test_hooks_init(void);

// Called from loop(): reads and runs one serial command when a full line is in.
void test_hooks_poll(void);

// Advances LVGL time and runs its timers once. Defined in main.cpp so the hooks
// can keep the UI running while they wait.
void lvgl_pump(void);
