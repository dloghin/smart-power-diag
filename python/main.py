# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

from arduino.app_utils import *
from arduino.app_bricks.web_ui import WebUI
from collections import deque
import time

# Keep enough history for the chart (~100s at a 500ms refresh rate).
SAMPLES_MAX = 200

logger = Logger("ac-power-monitor")
web_ui = WebUI()

samples = deque(maxlen=SAMPLES_MAX)
latest = {
    "t": time.time(),
    "voltage_raw": 0.0,
    "voltage_cal": 0.0,
    "current1_raw": 0.0,
    "current1_cal": 0.0,
    "current2_raw": 0.0,
    "current2_cal": 0.0,
    "frequency": 0.0,
    "power1": 0.0,
    "power2": 0.0,
}


def _get_samples():
    # Return a list copy so the deque isn't exposed directly.
    return list(samples)


web_ui.expose_api("GET", "/samples", _get_samples)

led1_state = {"on": True}
led2_state = {"on": True}


def _set_led1(client, data):
    """WebSocket handler for the 'set_led1' message sent when the LED 1 button is clicked."""
    led1_state["on"] = bool(data.get("on")) if isinstance(data, dict) else False
    Bridge.call("set_led1", led1_state["on"])
    web_ui.send_message("led1", led1_state)


def _get_led1(client, data):
    """WebSocket handler for the 'get_led1' message sent once the UI connects."""
    web_ui.send_message("led1", led1_state, client)

def _set_led2(client, data):
    """WebSocket handler for the 'set_led2' message sent when the LED 2 button is clicked."""
    led2_state["on"] = bool(data.get("on")) if isinstance(data, dict) else False
    Bridge.call("set_led2", led2_state["on"])
    web_ui.send_message("led2", led2_state)


def _get_led2(client, data):
    """WebSocket handler for the 'get_led2' message sent once the UI connects."""
    web_ui.send_message("led2", led2_state, client)


web_ui.on_message("set_led1", _set_led1)
web_ui.on_message("get_led1", _get_led1)

web_ui.on_message("set_led2", _set_led2)
web_ui.on_message("get_led2", _get_led2)

# Send the current reading immediately to any newly connected client
# ('led' state is sent in response to the client's 'get_led' request instead).
web_ui.on_connect(lambda sid: web_ui.send_message("reading", latest))


def sensor_reading(
    voltage_raw: float,
    voltage_cal: float,
    current1_raw: float,
    current1_cal: float,
    current2_raw: float,
    current2_cal: float,
    frequency: float,
    power1: float,
    power2: float,
):
    """Bridge handler: called from the sketch via Bridge.notify("sensor_reading", ...)."""
    global latest
    latest = {
        "t": time.time(),
        "voltage_raw": float(voltage_raw),
        "voltage_cal": float(voltage_cal),
        "current1_raw": float(current1_raw),
        "current1_cal": float(current1_cal),
        "current2_raw": float(current2_raw),
        "current2_cal": float(current2_cal),
        "frequency": float(frequency),
        "power1": float(power1),
        "power2": float(power2),
    }
    samples.append(latest)
    try:
        web_ui.send_message("reading", latest)
    except Exception as e:
        logger.debug(f"Failed to broadcast 'reading' message: {e}")


Bridge.provide("sensor_reading", sensor_reading)

App.run()
