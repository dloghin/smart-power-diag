// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

#include <Arduino_RouterBridge.h>

// LM358 v3 AC voltage sensor on A0, two ACS712T AC current sensors on A4 and A3.
const int VOLTAGE_PIN = A0;
const int CURRENT1_PIN = A4;
const int CURRENT2_PIN = A3;

// External LED, controlled from the web UI.
const int LED1_PIN = D2;
const int LED2_PIN = D3;
const int RELAY1_PIN = D4;
const int RELAY2_PIN = D5;


// UNO Q's default ADC reference is 3.3V. All three sensor outputs MUST stay
// within 0-3.3V at these pins - check that against your sensor boards'
// wiring/bias circuit before powering them up. ACS712 modules are typically
// biased around Vcc/2; if a module is run from 5V without a divider down to
// 3.3V, its output can exceed the ADC's safe input range.
const int ADC_BITS = 14;
const float ADC_MAX = (1 << ADC_BITS) - 1;
const float VREF = 3.3;

// Calibration factors: real_units_rms = adc_rms_volts * CALIBRATION.
// The raw ADC-referred RMS (before calibration) is printed over Serial every
// second, and also charted on its own in the web UI, so you can derive these
// by comparing against a multimeter (for voltage) or a clamp meter (for
// current). The two current channels are calibrated independently in case
// the two sensor boards don't have identical scaling.
float VOLTAGE_CALIBRATION = 100.0;  // volts per ADC-volt
float CURRENT1_CALIBRATION = 10.0;  // amps per ADC-volt (A4)
float CURRENT2_CALIBRATION = 10.0;  // amps per ADC-volt (A3)

const unsigned long SAMPLE_INTERVAL_US = 200;  // 5 kHz per channel
const unsigned long SEND_INTERVAL_MS = 500;

// Slow-moving average used to track each channel's DC bias, so the RMS is
// computed on the AC component only regardless of where the sensor's
// conditioning circuit centers its output.
double voltageBaseline = ADC_MAX / 2.0;
double current1Baseline = ADC_MAX / 2.0;
double current2Baseline = ADC_MAX / 2.0;
const double BASELINE_ALPHA = 0.0005;

double voltageSumSq = 0;
double current1SumSq = 0;
double current2SumSq = 0;
unsigned long sampleCount = 0;

unsigned long lastSampleMicros = 0;
unsigned long lastSendMillis = 0;
unsigned long lastLogMillis = 0;

double vMA = 0;
double c1MA = 0;
double c2MA = 0;

// Called from Python via Bridge.call("set_led", on) when the web UI button is pressed.
void setLed1(bool on) {
  digitalWrite(LED1_PIN, on ? HIGH : LOW);
  digitalWrite(RELAY1_PIN, on ? HIGH : LOW);
}

void setLed2(bool on) {
  digitalWrite(LED2_PIN, on ? HIGH : LOW);
  digitalWrite(RELAY2_PIN, on ? HIGH : LOW);
}

void setup() {
  Bridge.begin();
  Bridge.provide("set_led1", setLed1);
  Bridge.provide("set_led2", setLed2);
  Serial.begin(115200);
  
  analogReadResolution(ADC_BITS);

  pinMode(LED1_PIN, OUTPUT);
  pinMode(RELAY1_PIN, OUTPUT);
  digitalWrite(LED1_PIN, LOW);
  digitalWrite(RELAY1_PIN, LOW);

  pinMode(LED2_PIN, OUTPUT);
  pinMode(RELAY2_PIN, OUTPUT);
  digitalWrite(LED2_PIN, LOW);
  digitalWrite(RELAY2_PIN, LOW);
}

void loop() {
  unsigned long nowMicros = micros();
  if (nowMicros - lastSampleMicros >= SAMPLE_INTERVAL_US) {
    lastSampleMicros = nowMicros;

    int vRaw = analogRead(VOLTAGE_PIN);
    int c1Raw = analogRead(CURRENT1_PIN);
    int c2Raw = analogRead(CURRENT2_PIN);

    vMA = 0.2 * vMA + 0.8 * vRaw;
    c1MA = 0.2 * c1MA + 0.8 * c1Raw;
    c2MA = 0.2 * c2MA + 0.8 * c2Raw;
    
    sampleCount++;
  }

  unsigned long nowMillis = millis();
  if (nowMillis - lastSendMillis >= SEND_INTERVAL_MS && sampleCount > 0) {
    lastSendMillis = nowMillis;

   sampleCount = 0;    
    
    float voltageCal = (float)(vMA * VOLTAGE_CALIBRATION);
    float current1Cal = (float)(c1MA * CURRENT1_CALIBRATION);
    float current2Cal = (float)(c2MA * CURRENT2_CALIBRATION);

    Bridge.notify("sensor_reading", vMA, voltageCal, c1MA, current1Cal, c2MA, current2Cal);

    if (nowMillis - lastLogMillis >= 1000) {
      lastLogMillis = nowMillis;
      Serial.print("adc_rms_volts: voltage=");
      Serial.print(vMA, 4);
      Serial.print(" current1=");
      Serial.print(c1MA, 4);
      Serial.print(" current2=");
      Serial.println(c2MA, 4);
    }
  }
}
