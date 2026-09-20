#pragma once

#include <Arduino.h>
#include <Preferences.h>
#include <TFT_eSPI.h>

// Measured on this board at rotation 0 with tft.calibrateTouch():
// {x0, x1(span), y0, y1(span), flags: bit0=rotate, bit1=invert_x, bit2=invert_y}
static const uint16_t TOUCH_CAL_ROTATION_0[5] = {418, 3255, 338, 3334, 2};

// Rotation 0: uses the hardcoded values above (no corner taps needed).
// Other rotations: loads a calibration saved in flash (NVS), or runs the
// interactive 4-corner calibration and saves it. Pass forceRecalibrate=true
// to skip the hardcoded values and recalibrate (e.g. for a different panel).
inline void loadOrCalibrateTouch(TFT_eSPI &tft, bool forceRecalibrate = false) {
    Serial.begin(115200);

    if (!forceRecalibrate && tft.getRotation() == 0) {
        uint16_t calData[5];
        memcpy(calData, TOUCH_CAL_ROTATION_0, sizeof(calData));
        tft.setTouch(calData);
        Serial.printf("Touch calibration (hardcoded): {%u, %u, %u, %u, %u}\n",
                      calData[0], calData[1], calData[2], calData[3], calData[4]);
        return;
    }

    Preferences prefs;
    prefs.begin("touchcal", false);

    uint16_t calData[5];
    uint8_t rotation = tft.getRotation();
    bool haveStored = !forceRecalibrate &&
                      prefs.getBytesLength("data") == sizeof(calData) &&
                      prefs.getUChar("rot", 255) == rotation;

    if (haveStored) {
        prefs.getBytes("data", calData, sizeof(calData));
        tft.setTouch(calData);
        Serial.printf("Touch calibration loaded from flash: {%u, %u, %u, %u, %u}\n",
                      calData[0], calData[1], calData[2], calData[3], calData[4]);
    } else {
        tft.fillScreen(TFT_BLACK);
        tft.setTextColor(TFT_WHITE, TFT_BLACK);
        tft.setTextSize(2);
        tft.setTextDatum(MC_DATUM);
        tft.drawString("CALIBRATE TOUCH", tft.width() / 2, tft.height() / 2);
        tft.setTextDatum(TL_DATUM);
        tft.setTextSize(1);
        delay(1500);

        tft.calibrateTouch(calData, TFT_MAGENTA, TFT_BLACK, 15);
        prefs.putBytes("data", calData, sizeof(calData));
        prefs.putUChar("rot", rotation);
        Serial.printf("Touch calibration saved to flash: {%u, %u, %u, %u, %u}\n",
                      calData[0], calData[1], calData[2], calData[3], calData[4]);
    }

    prefs.end();
}
