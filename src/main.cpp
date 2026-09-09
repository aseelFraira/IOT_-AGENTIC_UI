#include <Arduino.h>
#include <TFT_eSPI.h>

TFT_eSPI tft = TFT_eSPI();

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("=== ILI9341 Validation Test ===");

    // Test 1: Init + ID readback
    tft.init();
    uint8_t id = tft.readcommand8(0x04);
    Serial.print("Display ID (0x04): 0x");
    Serial.println(id, HEX);
    if (id != 0x00 && id != 0xFF) {
        Serial.println("PASS: Display responded on SPI bus");
    } else {
        Serial.println(
            "FAIL: No response - check wiring (MISO/MOSI/SCLK/CS/DC/RST)");
    }

    // Test 2: Draw pattern
    tft.setRotation(1);
    tft.fillScreen(TFT_RED);
    delay(500);
    tft.fillScreen(TFT_BLACK);
    tft.setTextColor(TFT_WHITE);
    tft.setTextSize(2);
    tft.setCursor(10, 10);
    tft.println("If you see this,");
    tft.setCursor(10, 40);
    tft.println("display PASSED");
    Serial.println("Check screen: should show white text on black");

    // Test 3: Touch idle reading
    uint16_t x, y;
    bool touched = tft.getTouch(&x, &y);
    Serial.print("Touch idle state: ");
    Serial.println(touched ? "TOUCH DETECTED (unexpected at idle)"
                           : "no touch (expected)");
}

void loop() {
    uint16_t x, y;
    if (tft.getTouch(&x, &y)) {
        Serial.print("Touch at x=");
        Serial.print(x);
        Serial.print(" y=");
        Serial.println(y);
        delay(200);
    }
}