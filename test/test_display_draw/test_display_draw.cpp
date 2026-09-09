// Hardware-in-the-loop test: draw a known pattern and read the actual
// framebuffer back over SPI (via TFT_MISO=19) to verify it was drawn
// correctly - not just "it compiled and didn't crash".
//
// This only works because MISO is wired (readPixel() needs it). If you
// ever move to a display wired without MISO, this suite can't run.

#include <Arduino.h>
#include <unity.h>
#include <TFT_eSPI.h>

static TFT_eSPI tft = TFT_eSPI();

// Layout of the known pattern drawn onto the 240x320 panel:
//   - whole screen filled with BACKGROUND_COLOR
//   - one SWATCH_SIZE x SWATCH_SIZE square of SWATCH_COLOR at
//     (SWATCH_X, SWATCH_Y)
static const uint16_t BACKGROUND_COLOR = TFT_BLUE;
static const uint16_t SWATCH_COLOR = TFT_RED;
static const int SWATCH_X = 50;
static const int SWATCH_Y = 80;
static const int SWATCH_SIZE = 40;

void setUp(void) {}
void tearDown(void) {}

static void draw_known_pattern(void) {
    tft.fillScreen(BACKGROUND_COLOR);
    tft.fillRect(SWATCH_X, SWATCH_Y, SWATCH_SIZE, SWATCH_SIZE, SWATCH_COLOR);
}

// A point far from the swatch, still inside the panel, should read
// back as plain background colour.
void test_background_fill_readback(void) {
    draw_known_pattern();
    uint16_t pixel = tft.readPixel(5, 5);
    TEST_ASSERT_EQUAL_HEX16(BACKGROUND_COLOR, pixel);
}

// The centre of the swatch must read back as the swatch colour.
void test_swatch_center_readback(void) {
    draw_known_pattern();
    uint16_t pixel = tft.readPixel(SWATCH_X + SWATCH_SIZE / 2, SWATCH_Y + SWATCH_SIZE / 2);
    TEST_ASSERT_EQUAL_HEX16(SWATCH_COLOR, pixel);
}

// One pixel outside each edge of the swatch must still be background -
// this catches off-by-one errors in fillRect/addressing, not just a
// dead bus.
void test_swatch_edges_are_sharp(void) {
    draw_known_pattern();

    uint16_t left_of_swatch = tft.readPixel(SWATCH_X - 1, SWATCH_Y + SWATCH_SIZE / 2);
    uint16_t right_of_swatch = tft.readPixel(SWATCH_X + SWATCH_SIZE, SWATCH_Y + SWATCH_SIZE / 2);
    uint16_t above_swatch = tft.readPixel(SWATCH_X + SWATCH_SIZE / 2, SWATCH_Y - 1);
    uint16_t below_swatch = tft.readPixel(SWATCH_X + SWATCH_SIZE / 2, SWATCH_Y + SWATCH_SIZE);

    TEST_ASSERT_EQUAL_HEX16(BACKGROUND_COLOR, left_of_swatch);
    TEST_ASSERT_EQUAL_HEX16(BACKGROUND_COLOR, right_of_swatch);
    TEST_ASSERT_EQUAL_HEX16(BACKGROUND_COLOR, above_swatch);
    TEST_ASSERT_EQUAL_HEX16(BACKGROUND_COLOR, below_swatch);
}

void setup() {
    delay(2000);
    tft.begin();
    tft.setRotation(0);

    UNITY_BEGIN();
    RUN_TEST(test_background_fill_readback);
    RUN_TEST(test_swatch_center_readback);
    RUN_TEST(test_swatch_edges_are_sharp);
    UNITY_END();
}

void loop() {}
