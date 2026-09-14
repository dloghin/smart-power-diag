# ⚡ Smart AC Power Monitor and Diagnosis

An Arduino App Lab app for the Arduino UNO Q. It measures the mains voltage and the current drawn through two plugs, and charts them live in a web UI refreshed every 500 ms. It also detects which appliance is plugged into each plug, and switches each plug on and off through a relay.

## Hardware

| Signal | UNO Q pin | Part |
|---|---|---|
| Mains voltage | A0 | ZMPT101B AC voltage sensor module |
| Plug 1 current | A4 | ACS712 AC current sensor |
| Plug 2 current | A3 | ACS712 AC current sensor |
| Plug 1 switch | D2 (LED), D4 (relay) | |
| Plug 2 switch | D3 (LED), D5 (relay) | |

The UNO Q's ADC reference is 3.3 V, so every sensor output must stay within 0–3.3 V at its pin. Pick ACS712 modules rated for your loads. A 5 A module clips at about 800 W on 230 V mains, while kettles and dishwasher or washing machine heaters draw 2–3 kW and need the 20 A or 30 A version.

## How it works

- **`sketch/sketch.ino`** (microcontroller) samples the three channels 1000 times per block. It computes RMS voltage and current, mains frequency (from zero crossings) and real power for each plug, and sends a reading to Python every 500 ms with `Bridge.notify("sensor_reading", ...)`.
- **`python/main.py`** (Linux) broadcasts each reading to the web UI, runs appliance detection for both plugs, and keeps the last 200 readings so a newly opened page can fill its charts (`GET /samples`). Plug toggles from the UI are forwarded to the sketch with `Bridge.call("set_plug1"/"set_plug2", on)`.
- **`python/detector.py`** identifies the appliance on each plug (see [Appliance detection](#appliance-detection)).
- **`assets/`** is the web UI: plain HTML, CSS and JavaScript with no build step.

Run the app from Arduino App Lab on the UNO Q.

## Web UI

- **Readings:** voltage, the two currents, mains frequency, and real power for each plug.
- **Detected appliances:** one card per plug.
- **Plug 1 / Plug 2 buttons:** switch the plug's LED and relay together. Both plugs are off when the app starts.
- **Two charts per sensor:**
  - **Raw ADC (V):** a moving average of the sensor's mean ADC voltage, before centering and calibration. With no signal this is the sensor's DC bias. It can be hidden with the *Raw ADC charts* button.
  - **Calibrated:** RMS volts or amps. The dashed line is a 10-sample average.
- **Data table:** the latest calibrated readings, under *Show data table*.

## Calibration

All calibration constants are at the top of `sketch/sketch.ino`:

- **`VOLTAGE_CALIBRATION`, `CURRENT1_CALIBRATION`, `CURRENT2_CALIBRATION`:** volts or amps per ADC-referred RMS volt. Adjust them until the calibrated readings match a multimeter (voltage) or a clamp meter (current). The two current channels are calibrated separately, since the two sensor boards may not scale identically.
- **`ZERO_OFFSET_CURRENT1`, `ZERO_OFFSET_CURRENT2`:** the zero-current bias of each current sensor, in ADC codes, measured from the midpoint (8192). With nothing plugged in, set each one to `Raw ADC reading (V) / 3.3 × 16383 − 8192`, rounded.

Real power is reported as a magnitude, so the direction the current sensor is fitted doesn't matter. To print the uncalibrated RMS values, frequency and power over Serial once a second (115200 baud), add `#define LOGGING` at the top of the sketch.

Appliance detection works in watts, so calibrate the sensors before relying on it.

## Appliance detection

Each plug is identified as a **kettle**, **microwave**, **fridge**, **dishwasher** or **washing machine**. Anything else is reported as an unknown appliance. The card shows one of:

| Card | Meaning |
|---|---|
| *Kettle*, *Fridge*, … | The appliance was identified. The bar shows the confidence; below 60% it reads "Low confidence". |
| *Identifying…* | The plug is drawing power, but it is too early to tell what it is. |
| *Unknown appliance* | The plug is drawing power, but it doesn't match any known appliance. |
| *Fridge · Compressor off*, *Kettle · Off* | The appliance isn't drawing power now, but was identified recently. |
| *Standby draw* | A small constant draw (3 W or more) and nothing identified: something is plugged in but not running. |
| *Nothing plugged in* | No power draw and no appliance seen recently. |

**How it works.** Readings are averaged over 6 seconds and grouped into runs (the plug drawing power) and programs (runs close together in time). Each appliance scores the latest program on:

- its power level
- how long it runs
- how much its power fluctuates (this is what picks out a washing machine drum)
- for a fridge, whether similar compressor runs keep repeating

Each plug learns its own standby draw, so an appliance's electronics idling at a few watts don't count as running. The thresholds were derived from the UK-DALE appliance data used in [`ai/seq2point`](ai/seq2point/README.md). They are constants at the top of `python/detector.py`.

**Accuracy.** Replaying about six weeks of UK-DALE data per appliance from two houses, the label given after a run ends was correct for:

| Appliance | Correct |
|---|---:|
| Fridge | 98–100% |
| Kettle | 98–100% |
| Microwave | 98% |
| Dishwasher | 94–98% |
| Washing machine | 90–100% |

It has not yet been validated against readings from this hardware.

**Limitations.**

- An appliance that is plugged in but switched off draws nothing, so it looks exactly like an empty socket. The card keeps showing the last appliance for a while (10 minutes, 20 minutes for a dishwasher or washing machine, 2 hours for a fridge), then shows *Nothing plugged in*.
- Switching a plug off in the UI cuts its power, so it also reads as no load.
- A fridge is only named once its compressor has cycled two or three times, about 1–1.5 hours after the app starts.
- A dishwasher that starts by heating water shows as a kettle for its first few minutes.
- Only real power is used, so appliances with similar power patterns get confused. Toasters, hair dryers and coffee makers read as a microwave, some TVs as a washing machine, and some monitors or laptop chargers as a fridge.

## Repository layout

- `app.yaml`, `sketch/`, `python/`, `assets/`: the App Lab app.
- `ai/seq2point/`: seq2point load-disaggregation models trained on UK-DALE, with scripts for dataset creation, training, testing, daily energy accuracy and simulated fridge faults. See [its README](ai/seq2point/README.md).
