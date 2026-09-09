// Hardware-in-the-loop test: a minimal touch UI - three labeled
// buttons, bounds-check which one was touched, redraw it highlighted.
//
// Responsibility of this suite: prove the combination of "draw known
// rectangles" + "read touch coordinates" + "bounds-check against the
// known rectangles" actually works end to end, the way a real UI
// screen would use it. It reuses the same readPixel() verification
// idea as test_display_draw (so pass/fail doesn't rely purely on a
// human eyeballing the highlight) but the highlight step is still left
// on screen afterwards so you can visually confirm it too.
//
// This suite has one INTERACTIVE test - watch the Serial Monitor:
//   pio test -e esp32doit-devkit-v1 -f test_ui_buttons -v

#include <Arduino.h>
#include <unity.h>
#include <TFT_eSPI.h>

static TFT_eSPI tft = TFT_eSPI();

struct Button {
    int x, y, w, h;
    const char *label;
};

static const int BUTTON_COUNT = 3;
static Button buttons[BUTTON_COUNT] = {
    {5, 40, 70, 60, "A"},
    {85, 40, 70, 60, "B"},
    {165, 40, 70, 60, "C"},
};

static const uint16_t BACKGROUND_COLOR = TFT_BLACK;
static const uint16_t NORMAL_FILL = TFT_NAVY;
static const uint16_t NORMAL_TEXT = TFT_WHITE;
static const uint16_t HIGHLIGHT_FILL = TFT_YELLOW;
static const uint16_t HIGHLIGHT_TEXT = TFT_BLACK;
static const uint16_t BORDER_COLOR = TFT_WHITE;

static const uint32_t TOUCH_WAIT_TIMEOUT_MS = 20000;

static void drawButton(int index, bool highlighted) {
    Button &b = buttons[index];
    uint16_t fill = highlighted ? HIGHLIGHT_FILL : NORMAL_FILL;
    uint16_t text = highlighted ? HIGHLIGHT_TEXT : NORMAL_TEXT;

    tft.fillRect(b.x, b.y, b.w, b.h, fill);
    tft.drawRect(b.x, b.y, b.w, b.h, BORDER_COLOR);

    tft.setTextColor(text, fill);
    tft.setTextDatum(MC_DATUM);
    tft.drawString(b.label, b.x + b.w / 2, b.y + b.h / 2);
    tft.setTextDatum(TL_DATUM); // restore default so other code isn't surprised
}

static void drawAllButtonsNormal(void) {
    tft.fillScreen(BACKGROUND_COLOR);
    tft.setTextColor(TFT_WHITE, BACKGROUND_COLOR);
    tft.setTextDatum(TC_DATUM);
    tft.drawString("TAP A BUTTON", tft.width() / 2, 10);
    tft.setTextDatum(TL_DATUM);
    for (int i = 0; i < BUTTON_COUNT; i++) drawButton(i, false);
}

// Returns the index of the button containing (x, y), or -1 if the
// point falls outside all of them.
static int findButtonAt(uint16_t x, uint16_t y) {
    for (int i = 0; i < BUTTON_COUNT; i++) {
        Button &b = buttons[i];
        if (x >= b.x && x < b.x + b.w && y >= b.y && y < b.y + b.h) return i;
    }
    return -1;
}

static bool waitForTouch(uint32_t timeoutMs, uint16_t *x, uint16_t *y) {
    uint32_t deadline = millis() + timeoutMs;
    while (millis() < deadline) {
        if (tft.getTouch(x, y)) return true;
        delay(20);
    }
    return false;
}

void setUp(void) {}
void tearDown(void) {}

// Automated, no touch needed: confirms the 3 buttons actually landed
// on screen where we think they did, by reading the real framebuffer
// back (same technique as test_display_draw).
// Sampled near a corner of the button, inset past the border stroke
// but away from the centered text label - reading dead-center would
// sometimes land on a white pixel of the glyph itself instead of the
// fill color.
static uint16_t readButtonFillColor(int index) {
    Button &b = buttons[index];
    return tft.readPixel(b.x + 8, b.y + 8);
}

void test_buttons_render_at_known_positions(void) {
    drawAllButtonsNormal();

    for (int i = 0; i < BUTTON_COUNT; i++) {
        uint16_t fill = readButtonFillColor(i);
        TEST_ASSERT_EQUAL_HEX16(NORMAL_FILL, fill);
    }

    // A point in the gap between button A and button B must still be
    // plain background, not bleed from either button.
    uint16_t gap = tft.readPixel(80, 70);
    TEST_ASSERT_EQUAL_HEX16(BACKGROUND_COLOR, gap);
}

// Interactive: wait for a real touch, bounds-check it against the
// known button rectangles, then redraw only that button highlighted.
void test_touch_selects_and_highlights_button(void) {
    drawAllButtonsNormal();
    TEST_MESSAGE("Touch one of the three buttons (A/B/C) on screen now (20s timeout)...");

    int touchedIndex = -1;
    uint32_t deadline = millis() + TOUCH_WAIT_TIMEOUT_MS;
    while (millis() < deadline) {
        uint16_t x, y;
        if (waitForTouch(200, &x, &y)) {
            int idx = findButtonAt(x, y);
            if (idx >= 0) {
                touchedIndex = idx;
                char msg[48];
                snprintf(msg, sizeof(msg), "touch (%u, %u) -> button %s", x, y, buttons[idx].label);
                TEST_MESSAGE(msg);
                break;
            }
            char msg[48];
            snprintf(msg, sizeof(msg), "touch (%u, %u) missed all buttons, try again", x, y);
            TEST_MESSAGE(msg);
        }
    }

    if (touchedIndex < 0) {
        TEST_FAIL_MESSAGE("No on-button touch detected within 20s timeout");
        return;
    }

    drawButton(touchedIndex, true);

    // Verify the highlight actually landed (not just "we meant to draw
    // it"), and that the other buttons were left alone.
    uint16_t touchedFill = readButtonFillColor(touchedIndex);
    TEST_ASSERT_EQUAL_HEX16(HIGHLIGHT_FILL, touchedFill);

    for (int i = 0; i < BUTTON_COUNT; i++) {
        if (i == touchedIndex) continue;
        uint16_t fill = readButtonFillColor(i);
        TEST_ASSERT_EQUAL_HEX16(NORMAL_FILL, fill);
    }
}

void setup() {
    delay(2000);
    tft.begin();
    tft.setRotation(0);

    // See test_touch.cpp for why this is needed: the library's default
    // touch calibration constants assume an axis mapping that doesn't
    // match every board, so a real touch can land on the wrong on-
    // screen spot unless we calibrate for this specific board/rotation.
    tft.fillScreen(TFT_BLACK);
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.setTextSize(2);
    tft.setTextDatum(MC_DATUM);
    tft.drawString("CALIBRATE TOUCH", tft.width() / 2, tft.height() / 2);
    tft.setTextDatum(TL_DATUM);
    tft.setTextSize(1);
    delay(1500);

    uint16_t calData[5];
    tft.calibrateTouch(calData, TFT_MAGENTA, TFT_BLACK, 15);
    Serial.printf("Touch calibration data: {%u, %u, %u, %u, %u}\n",
                  calData[0], calData[1], calData[2], calData[3], calData[4]);

    UNITY_BEGIN();
    RUN_TEST(test_buttons_render_at_known_positions);
    RUN_TEST(test_touch_selects_and_highlights_button);
    UNITY_END();
    // Highlighted button (if the interactive test ran) is left on
    // screen here on purpose, as the visual confirmation.
}

void loop() {}
