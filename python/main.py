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
latest = {"t": time.time(), "voltage": 0.0, "current": 0.0}


def _get_samples():
    # Return a list copy so the deque isn't exposed directly.
    return list(samples)


web_ui.expose_api("GET", "/samples", _get_samples)

# Send the current reading immediately to any newly connected client.
web_ui.on_connect(lambda sid: web_ui.send_message("reading", latest))


def sensor_reading(voltage: float, current: float):
    """Bridge handler: called from the sketch via Bridge.notify("sensor_reading", ...)."""
    global latest
    latest = {"t": time.time(), "voltage": float(voltage), "current": float(current)}
    samples.append(latest)
    try:
        web_ui.send_message("reading", latest)
    except Exception as e:
        logger.debug(f"Failed to broadcast 'reading' message: {e}")


Bridge.provide("sensor_reading", sensor_reading)

App.run()
