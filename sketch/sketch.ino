// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

#include <Arduino_RouterBridge.h>

// ZMP101B AC voltage sensor on A0, two ACS712 AC current sensors on A4 and A3.
const int VOLTAGE_PIN = A0;
const int CURRENT1_PIN = A4;
const int CURRENT2_PIN = A3;

// External LEDs and relays, controlled from the web UI.
const int LED1_PIN = D2;
const int LED2_PIN = D3;
const int RELAY1_PIN = D4;
const int RELAY2_PIN = D5;


// UNO Q's default ADC reference is 3.3V. ACS712 modules are typically
// biased around Vcc/2. ADC resolution set to 14 bits.
const int ADC_BITS = 14;
const float ADC_MAX = (1 << ADC_BITS) - 1;
const float VREF = 3.3;


// Calibration factors
float VOLTAGE_CALIBRATION = 648.1;  // volts per ADC-volt
float CURRENT1_CALIBRATION = 11.2;  // amps per ADC-volt (A4)
float CURRENT2_CALIBRATION = 10.6;  // amps per ADC-volt (A3)

const int ZERO_OFFSET_CURRENT1 = -170;
const int ZERO_OFFSET_CURRENT2 = -250;

// Sampling config
const unsigned long SAMPLE_INTERVAL_US = 100;  // 10 kHz per channel
const unsigned long SEND_INTERVAL_MS = 500;
const unsigned long SAMPLE_COUNT = 1000;

unsigned long lastSampleMicros = 0;
unsigned long lastSendMillis = 0;
unsigned long lastLogMillis = 0;

// Raw and calibrated values
double voltageRaw = 0;
double current1Raw = 0;
double current2Raw = 0;

// Moving average of the raw (uncentered) ADC reading for each channel, used
// for the "raw" charts. Updated once per sampling block (every SEND check),
// smoothed further across blocks with MA_ALPHA.
double voltageMA = 0;
double current1MA = 0;
double current2MA = 0;
bool movingAvgInitialized = false;
const double MA_ALPHA = 0.3;

// Mains frequency, from timing zero-crossings of the voltage channel across
// each sampling block, smoothed across blocks with FREQ_ALPHA.
double mainsFrequency = 0;
bool frequencyInitialized = false;
const double FREQ_ALPHA = 0.3;

// Real (active) power = average(v(t) * i(t)), computed from the synchronized
// voltage/current samples taken in the same loop pass - this accounts for
// any phase shift between voltage and current, unlike a plain Vrms * Irms
// (apparent power) estimate.
double power1 = 0;
double power2 = 0;

// Called from Python via Bridge.call("set_plug1"/"set_plug2", on) when the web UI button is pressed.
void setPlug1(bool on) {
  digitalWrite(LED1_PIN, on ? HIGH : LOW);
  digitalWrite(RELAY1_PIN, on ? HIGH : LOW);
}

void setPlug2(bool on) {
  digitalWrite(LED2_PIN, on ? HIGH : LOW);
  digitalWrite(RELAY2_PIN, on ? HIGH : LOW);
}

void setup() {
  Bridge.begin();
  Bridge.provide("set_plug1", setPlug1);
  Bridge.provide("set_plug2", setPlug2);
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

int sample(const pin_size_t APIN, const int offset, int64_t* sumOfSquares, int64_t* sumRaw) {
    int raw = analogRead(APIN);
    int centered = raw - (1 << (ADC_BITS-1)) - offset;
    *sumOfSquares += (int64_t)(centered * centered);
    *sumRaw += raw;
    return centered;
}

void loop() {

  // Sample raw
  int64_t s2Voltage = 0;
  int64_t s2Current1 = 0;
  int64_t s2Current2 = 0;

  int64_t sRawVoltage = 0;
  int64_t sRawCurrent1 = 0;
  int64_t sRawCurrent2 = 0;

  int64_t sViCurrent1 = 0;
  int64_t sViCurrent2 = 0;

  int prevVoltageCentered = 0;
  unsigned long crossingCount = 0;
  unsigned long firstCrossingMicros = 0;
  unsigned long lastCrossingMicros = 0;

  for (int i = 0; i < SAMPLE_COUNT; i++) {
    int vCentered = sample(VOLTAGE_PIN, 0, &s2Voltage, &sRawVoltage);
    int c1Centered = sample(CURRENT1_PIN, ZERO_OFFSET_CURRENT1, &s2Current1, &sRawCurrent1);
    int c2Centered = sample(CURRENT2_PIN, ZERO_OFFSET_CURRENT2, &s2Current2, &sRawCurrent2);

    sViCurrent1 += (int64_t)vCentered * c1Centered;
    sViCurrent2 += (int64_t)vCentered * c2Centered;

    // Rising zero-crossing of the voltage channel, used for mains frequency.
    if (i > 0 && prevVoltageCentered < 0 && vCentered >= 0) {
      unsigned long nowMicros = micros();
      if (crossingCount == 0) firstCrossingMicros = nowMicros;
      lastCrossingMicros = nowMicros;
      crossingCount++;
    }
    prevVoltageCentered = vCentered;

    delayMicroseconds(SAMPLE_INTERVAL_US);
  }

  // Calculate RMS value
  double mean = s2Voltage / (double)SAMPLE_COUNT;
  voltageRaw = sqrt(mean) * VREF/ADC_MAX;

  mean = s2Current1 / (double)SAMPLE_COUNT;
  current1Raw = sqrt(mean) * VREF/ADC_MAX;

  mean = s2Current2 / (double)SAMPLE_COUNT;
  current2Raw = sqrt(mean) * VREF/ADC_MAX;

  // Moving average of the raw ADC reading
  double avgVoltageRaw = (sRawVoltage / (double)SAMPLE_COUNT) * VREF / ADC_MAX;
  double avgCurrent1Raw = (sRawCurrent1 / (double)SAMPLE_COUNT) * VREF / ADC_MAX;
  double avgCurrent2Raw = (sRawCurrent2 / (double)SAMPLE_COUNT) * VREF / ADC_MAX;

  if (!movingAvgInitialized) {
    voltageMA = avgVoltageRaw;
    current1MA = avgCurrent1Raw;
    current2MA = avgCurrent2Raw;
    movingAvgInitialized = true;
  } else {
    voltageMA = (1.0 - MA_ALPHA) * voltageMA + MA_ALPHA * avgVoltageRaw;
    current1MA = (1.0 - MA_ALPHA) * current1MA + MA_ALPHA * avgCurrent1Raw;
    current2MA = (1.0 - MA_ALPHA) * current2MA + MA_ALPHA * avgCurrent2Raw;
  }

  // Mains frequency = (number of full cycles between the first and last
  // zero-crossing seen this block) / (time between them). Needs at least two
  // crossings; skip the update otherwise (e.g. no AC signal present).
  if (crossingCount >= 2) {
    double elapsedSeconds = (lastCrossingMicros - firstCrossingMicros) / 1e6;
    double periods = crossingCount - 1;
    double freqEstimate = periods / elapsedSeconds;
    if (!frequencyInitialized) {
      mainsFrequency = freqEstimate;
      frequencyInitialized = true;
    } else {
      mainsFrequency = (1.0 - FREQ_ALPHA) * mainsFrequency + FREQ_ALPHA * freqEstimate;
    }
  }

  // Real (active) power in Watts, converting the ADC-code product to real
  // volts*amps via the same per-channel calibration used for the RMS values.
  double instantScale = (VREF / ADC_MAX) * (VREF / ADC_MAX);
  power1 = (sViCurrent1 / (double)SAMPLE_COUNT) * instantScale * VOLTAGE_CALIBRATION * CURRENT1_CALIBRATION;
  power2 = (sViCurrent2 / (double)SAMPLE_COUNT) * instantScale * VOLTAGE_CALIBRATION * CURRENT2_CALIBRATION;

  // send and log
  unsigned long nowMillis = millis();
  if (nowMillis - lastSendMillis >= SEND_INTERVAL_MS) {
    lastSendMillis = nowMillis;

    float voltageCal = (float)(voltageRaw * VOLTAGE_CALIBRATION);
    float current1Cal = (float)(current1Raw * CURRENT1_CALIBRATION);
    float current2Cal = (float)(current2Raw * CURRENT2_CALIBRATION);

    // "raw" fields carry the moving average; "cal" fields carry the RMS-calibrated reading.
    Bridge.notify("sensor_reading", (float)voltageMA, voltageCal, (float)current1MA, current1Cal, (float)current2MA, current2Cal,
                   (float)mainsFrequency, (float)power1, (float)power2);

#ifdef LOGGING
    if (nowMillis - lastLogMillis >= 1000) {
      lastLogMillis = nowMillis;
      Serial.print("adc_rms_volts: voltage=");
      Serial.print(voltageRaw, 4);
      Serial.print(" current1=");
      Serial.print(current1Raw, 4);
      Serial.print(" current2=");
      Serial.println(current2Raw, 4);
      Serial.print("frequency_hz=");
      Serial.print(mainsFrequency, 3);
      Serial.print(" power1_w=");
      Serial.print(power1, 2);
      Serial.print(" power2_w=");
      Serial.println(power2, 2);
    }
#endif // LOGGING
  }
}
