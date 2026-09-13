# ⚡ AC Power Monitor

Reads an LM358 v3 AC voltage sensor (A0) and two ACS712T AC current sensors (A4, A3) via the ADC and plots RMS values live in a web UI, refreshed every 500ms.

For each of the 3 sensors the UI shows two charts:
- **Raw ADC** — the RMS of the raw ADC signal, in ADC-referred volts, before calibration. Use this (alongside the values the sketch prints over Serial once a second) to derive your calibration factors against a multimeter (voltage) or clamp meter (current).
- **Calibrated** — the raw value scaled by `VOLTAGE_CALIBRATION` / `CURRENT1_CALIBRATION` / `CURRENT2_CALIBRATION` in `sketch/sketch.ino`, giving real-world AC volts and amps.

Current 1 is wired to A4, Current 2 to A3. Both channels are calibrated independently since the two sensor boards may not have identical scaling.

The UI also has an On/Off button that drives an external LED on pin 3 (via `Bridge.call("set_led", ...)` from Python to the sketch).


