// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

#include <Arduino_RouterBridge.h>

// LM358 AC voltage sensor on A0, OPCT10ATL AC current sensor on A5.
const int VOLTAGE_PIN = A0;
const int CURRENT_PIN = A5;

// UNO Q's default ADC reference is 3.3V. Both sensor outputs MUST stay
// within 0-3.3V at this pin - check that against your sensor board's
// wiring/bias circuit before powering it up.
const int ADC_BITS = 12;
const float ADC_MAX = (1 << ADC_BITS) - 1;
const float VREF = 3.3;

// Calibration factors: mains_units = adc_rms_volts * CALIBRATION.
// The raw ADC-referred RMS (before calibration) is printed over Serial
// every second so you can derive these by comparing against a multimeter
// (for voltage) or a clamp meter (for current).
float VOLTAGE_CALIBRATION = 100.0; // volts per ADC-volt
float CURRENT_CALIBRATION = 10.0;  // amps per ADC-volt

const unsigned long SAMPLE_INTERVAL_US = 200; // 5 kHz per channel
const unsigned long SEND_INTERVAL_MS = 500;

// Slow-moving average used to track each channel's DC bias, so the RMS
// is computed on the AC component only regardless of where the sensor's
// conditioning circuit centers its output.
double voltageBaseline = ADC_MAX / 2.0;
double currentBaseline = ADC_MAX / 2.0;
const double BASELINE_ALPHA = 0.0005;

double voltageSumSq = 0;
double currentSumSq = 0;
unsigned long sampleCount = 0;

unsigned long lastSampleMicros = 0;
unsigned long lastSendMillis = 0;
unsigned long lastLogMillis = 0;

void setup() {
  Bridge.begin();
  Serial.begin(115200);
  analogReadResolution(ADC_BITS);
}

void loop() {
  unsigned long nowMicros = micros();
  if (nowMicros - lastSampleMicros >= SAMPLE_INTERVAL_US) {
    lastSampleMicros = nowMicros;

    int vRaw = analogRead(VOLTAGE_PIN);
    int cRaw = analogRead(CURRENT_PIN);

    voltageBaseline += (vRaw - voltageBaseline) * BASELINE_ALPHA;
    currentBaseline += (cRaw - currentBaseline) * BASELINE_ALPHA;

    double vAc = vRaw - voltageBaseline;
    double cAc = cRaw - currentBaseline;

    voltageSumSq += vAc * vAc;
    currentSumSq += cAc * cAc;
    sampleCount++;
  }

  unsigned long nowMillis = millis();
  if (nowMillis - lastSendMillis >= SEND_INTERVAL_MS && sampleCount > 0) {
    lastSendMillis = nowMillis;

    double voltageRmsAdc = sqrt(voltageSumSq / sampleCount) * (VREF / ADC_MAX);
    double currentRmsAdc = sqrt(currentSumSq / sampleCount) * (VREF / ADC_MAX);

    voltageSumSq = 0;
    currentSumSq = 0;
    sampleCount = 0;

    float voltageRms = (float)(voltageRmsAdc * VOLTAGE_CALIBRATION);
    float currentRms = (float)(currentRmsAdc * CURRENT_CALIBRATION);

    Bridge.notify("sensor_reading", voltageRms, currentRms);

    if (nowMillis - lastLogMillis >= 1000) {
      lastLogMillis = nowMillis;
      Serial.print("adc_rms_volts: voltage=");
      Serial.print(voltageRmsAdc, 4);
      Serial.print(" current=");
      Serial.println(currentRmsAdc, 4);
    }
  }
}
