// Hardware-in-the-loop test: XPT2046 touch controller.
//
// Responsibility of this suite: verify the touch chip (TOUCH_CS=21,
// sharing TFT_MOSI/MISO/SCLK) is readable and reports sane data, both
// idle and under an actual finger.
//
// Two of the three tests below are INTERACTIVE - they display a
// prompt ON THE TFT ITSELF (and also log it over Serial) and wait
// (with a timeout) for you to touch the screen. Watch the physical
// screen, not just the terminal, while this suite runs:
//   pio test -e esp32doit-devkit-v1 -f test_touch -v

#include <Arduino.h>
#include <unity.h>
#include <TFT_eSPI.h>
#include <touch_calibration.h>

static TFT_eSPI tft = TFT_eSPI();

static const int IDLE_SAMPLE_COUNT = 20;
static const int IDLE_SAMPLE_DELAY_MS = 20;

static const uint32_t TOUCH_WAIT_TIMEOUT_MS = 20000;
static const uint32_t RELEASE_WAIT_TIMEOUT_MS = 5000;

static const int REPEATABILITY_TAP_COUNT = 5;
// How much a repeated tap at "the same spot" is allowed to drift, in
// pixels, before we call it "wildly" varying. Generous on purpose -
// this also has to absorb normal human finger-placement imprecision,
// not just sensor noise. Tighten it if you want a stricter check.
static const uint16_t REPEATABILITY_TOLERANCE_PX = 60;

void setUp(void) {}
void tearDown(void) {}

// Blocks until getTouch() reports a touch or the timeout elapses.
// Returns true and fills x/y on success.
static bool waitForTouch(uint32_t timeoutMs, uint16_t *x, uint16_t *y) {
    uint32_t deadline = millis() + timeoutMs;
    while (millis() < deadline) {
        if (tft.getTouch(x, y)) return true;
        delay(20);
    }
    return false;
}

// Blocks until getTouch() reports no touch or the timeout elapses.
// Returns true if the finger was lifted before the timeout.
static bool waitForRelease(uint32_t timeoutMs) {
    uint16_t x, y;
    uint32_t deadline = millis() + timeoutMs;
    while (millis() < deadline) {
        if (!tft.getTouch(&x, &y)) return true;
        delay(20);
    }
    return false;
}

// Puts a big, short prompt on the TFT itself so whoever is standing at
// the board (not watching a Serial Monitor) knows what to do right
// now. TEST_MESSAGE only reaches the terminal, which is useless if
// you're looking at the hardware instead.
static void showPrompt(const char *line1, const char *line2 = nullptr) {
    tft.fillScreen(TFT_BLACK);
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.setTextSize(3);
    tft.setTextDatum(MC_DATUM);
    int cy = line2 ? tft.height() / 2 - 20 : tft.height() / 2;
    tft.drawString(line1, tft.width() / 2, cy);
    if (line2) tft.drawString(line2, tft.width() / 2, tft.height() / 2 + 20);
    tft.setTextDatum(TL_DATUM);
    tft.setTextSize(1);
}

// --- Test 1: idle (run this with nothing touching the screen) ---
// A false positive here usually means a floating/mis-wired TOUCH_CS or
// T_IRQ line, or bus contention with the display CS.
void test_idle_no_touch(void) {
    showPrompt("DO NOT", "TOUCH");
    TEST_MESSAGE("Keep your hands off the screen for this test...");
    uint16_t x, y;
    for (int i = 0; i < IDLE_SAMPLE_COUNT; i++) {
        bool touched = tft.getTouch(&x, &y);

        char msg[48];
        snprintf(msg, sizeof(msg), "sample %d: touched=%d raw=(%u,%u)", i, touched, x, y);
        TEST_MESSAGE(msg);

        TEST_ASSERT_FALSE(touched);
        delay(IDLE_SAMPLE_DELAY_MS);
    }
}

// --- Test 2: active touch, anywhere ---
// Confirms a real touch maps to *plausible screen coordinates* rather
// than garbage. This matters because TFT_eSPI's default calibration
// constants map raw ADC counts with (raw - x0) * width / (x1 - x0): a
// touch that falls outside that assumed raw window underflows the
// unsigned subtraction and wraps to a huge value - i.e. "garbage" is a
// real, catchable failure mode here, not a hypothetical one.
void test_active_touch_within_screen_bounds(void) {
    showPrompt("TOUCH", "ANYWHERE");
    TEST_MESSAGE("Touch anywhere on the screen now (20s timeout)...");

    uint16_t x, y;
    bool touched = waitForTouch(TOUCH_WAIT_TIMEOUT_MS, &x, &y);
    if (!touched) {
        showPrompt("TIMED", "OUT");
        TEST_FAIL_MESSAGE("No touch detected within 20s timeout");
        return;
    }

    char msg[48];
    snprintf(msg, sizeof(msg), "touched at (%u, %u)", x, y);
    TEST_MESSAGE(msg);
    showPrompt("GOT IT");

    TEST_ASSERT_TRUE_MESSAGE(x < tft.width(), "x coordinate outside screen width");
    TEST_ASSERT_TRUE_MESSAGE(y < tft.height(), "y coordinate outside screen height");

    waitForRelease(RELEASE_WAIT_TIMEOUT_MS); // don't leak a held touch into the next test
}

// --- Test 3: repeatability ---
// Tap the SAME spot five separate times. Checks the five readings
// cluster together instead of jumping around - a real symptom of a
// noisy/flaky touch panel or a bad connection.
void test_touch_repeatability(void) {
    char msg[80];
    snprintf(msg, sizeof(msg), "Tap the SAME spot on screen %d times (lift finger between taps)",
              REPEATABILITY_TAP_COUNT);
    TEST_MESSAGE(msg);

    uint16_t xs[REPEATABILITY_TAP_COUNT];
    uint16_t ys[REPEATABILITY_TAP_COUNT];

    for (int i = 0; i < REPEATABILITY_TAP_COUNT; i++) {
        char line2[16];
        snprintf(line2, sizeof(line2), "%d / %d", i + 1, REPEATABILITY_TAP_COUNT);
        showPrompt("TAP", line2);

        snprintf(msg, sizeof(msg), "Tap %d/%d now...", i + 1, REPEATABILITY_TAP_COUNT);
        TEST_MESSAGE(msg);

        if (!waitForTouch(TOUCH_WAIT_TIMEOUT_MS, &xs[i], &ys[i])) {
            showPrompt("TIMED", "OUT");
            snprintf(msg, sizeof(msg), "No touch detected for tap %d/%d within 20s timeout",
                      i + 1, REPEATABILITY_TAP_COUNT);
            TEST_FAIL_MESSAGE(msg);
            return;
        }

        snprintf(msg, sizeof(msg), "  -> (%u, %u)", xs[i], ys[i]);
        TEST_MESSAGE(msg);

        // Require a lift before the next tap so we capture 5 distinct
        // touches rather than 5 samples of one continuous press. If the
        // finger is still down when the timeout hits, we just move on -
        // the next waitForTouch() call will immediately re-sample it.
        waitForRelease(RELEASE_WAIT_TIMEOUT_MS);
    }

    uint16_t minX = xs[0], maxX = xs[0], minY = ys[0], maxY = ys[0];
    for (int i = 1; i < REPEATABILITY_TAP_COUNT; i++) {
        minX = min(minX, xs[i]);
        maxX = max(maxX, xs[i]);
        minY = min(minY, ys[i]);
        maxY = max(maxY, ys[i]);
    }

    uint16_t spreadX = maxX - minX;
    uint16_t spreadY = maxY - minY;
    snprintf(msg, sizeof(msg), "spread: x=%u px, y=%u px (tolerance %u px)",
              spreadX, spreadY, REPEATABILITY_TOLERANCE_PX);
    TEST_MESSAGE(msg);

    TEST_ASSERT_LESS_OR_EQUAL(REPEATABILITY_TOLERANCE_PX, spreadX);
    TEST_ASSERT_LESS_OR_EQUAL(REPEATABILITY_TOLERANCE_PX, spreadY);
}

void setup() {
    delay(2000);
    tft.begin();

    // TFT_eSPI's default touch calibration doesn't match this board, so a
    // real touch can land on the wrong on-screen spot. Calibrate once
    // (4 corner taps); the result is saved in flash and reused on later runs.
    loadOrCalibrateTouch(tft);

    UNITY_BEGIN();
    RUN_TEST(test_idle_no_touch);
    RUN_TEST(test_active_touch_within_screen_bounds);
    RUN_TEST(test_touch_repeatability);
    UNITY_END();
    showPrompt("DONE");
}

void loop() {}
