"""Appliance detection for one plug, from the readings the sketch sends every 500 ms.

Each plug gets its own PlugDetector, fed with the mains RMS voltage and the plug's RMS current and real power.
Detection is rule-based. Readings are averaged over BIN_S, the sampling interval of the UK-DALE submeters the
signatures were derived from (see ai/seq2point). The plug's activity is split into runs (the plug drawing
power, allowing short dips) and programs (runs close together in time), and each known appliance scores how
well the latest program matches its signature.

An appliance that is plugged in but draws no power cannot be told apart from an empty socket, so a plug
keeps reporting the last appliance for a while after it stops (longer for appliances that cycle, like a
fridge) and is then reported as empty.
"""

import math
from collections import deque

# Power thresholds, in W. ON_W and OFF_W are above the plug's standby base (see BASE_WINDOW_S).
NO_LOAD_W = 3.0  # below this the plug draws nothing (or only sensor noise)
ON_W = 15.0  # a run starts at or above this...
OFF_W = 10.0  # ...and continues while power stays at or above this
HEAVY_W = 500.0  # heating elements and magnetrons draw at least this
INRUSH_S = 15  # heavy-band time a run may have without using a heater (a compressor or motor start surge)

# Standby base: the lowest average power over BASE_WINDOW_S, capped at MAX_BASE_W, so that an appliance's
# constant standby draw (electronics, a display) doesn't look like a run that never ends.
BASE_WINDOW_S = 10 * 60
MAX_BASE_W = 25.0

# Timing, in seconds.
BIN_S = 6  # readings are averaged over this before run detection
MAX_DT_S = 10  # longest gap between readings counted as continuous
MERGE_GAP_S = 30  # dips shorter than this don't end a run (drum pauses, a kettle re-boiled straight away)
PROGRAM_GAP_S = 20 * 60  # runs closer than this belong to one program (dishwasher and washing machine pauses)
HISTORY_S = 3 * 3600  # finished runs are kept this long, e.g. to see a fridge cycling
UNKNOWN_AFTER_S = 60  # an unidentified draw is reported as unknown after this
IDENTIFY_S = 180  # while a new program is younger than this and unidentified, the previous appliance is reported
STANDBY_SMOOTHING_S = 3  # time constant of the power average that separates standby from no load

# Runs too short or too weak to identify anything (a fridge or microwave light, a switch-on blip) are ignored.
SIGNIFICANT_S = 10
SIGNIFICANT_WH = 1.0
SIGNIFICANT_MEAN_W = 40.0

# Consecutive averages that differ by more than this count as a jump. Jumps measure how much an appliance's
# power fluctuates: a washing machine's drum jumps on 20-80% of steps, other appliances on under 15%.
LIGHT_JUMP = (8.0, 0.10)  # (W, fraction of the previous average), below HEAVY_W
HEAVY_JUMP = (40.0, 0.04)  # at or above HEAVY_W
MIN_STEPS = 10  # jump fractions are taken over at least this many steps, so the first jump of a run is not 100%

# Programs whose best score is below this are reported as "unknown".
MIN_CONFIDENCE = 0.35

# How long an appliance is still reported after its last run; after that an idle plug is reported as empty.
REMEMBER_S = {"fridge": 2 * 3600, "dishwasher": PROGRAM_GAP_S, "washingmachine": PROGRAM_GAP_S}
REMEMBER_DEFAULT_S = 10 * 60

# The RMS current from the sketch includes sensor noise and any DC offset from the ADC midpoint. Both add in
# quadrature, so this floor is learned while the plug draws no real power and then removed.
FLOOR_LEARN_AFTER_S = 5
FLOOR_ALPHA = 0.05
MAX_CURRENT_FLOOR_A = 1.0

# Power factor is only computed with mains present and enough apparent power to be meaningful.
MIN_MAINS_V = 50.0
MIN_PF_VA = 30.0


def _up(x, a, b):
    """0 at or below a, rising linearly to 1 at or above b."""
    return min(max((x - a) / (b - a), 0.0), 1.0)


def _down(x, c, d):
    """1 at or below c, falling linearly to 0 at or above d."""
    return 1.0 - _up(x, c, d)


def _band(x, a, b, c, d):
    """1 between b and c, falling linearly to 0 at a and d."""
    return _up(x, a, b) * _down(x, c, d)


class _Stats:
    """Time-weighted power statistics and jump counts, split into a light (< HEAVY_W) and a heavy band."""

    __slots__ = (
        "light_s", "light_ws", "light_steps", "light_jumps",
        "heavy_s", "heavy_ws", "heavy_steps", "heavy_jumps",
        "pf_s", "pf_sum",
    )

    def __init__(self):
        for name in self.__slots__:
            setattr(self, name, 0.0)

    def extend(self, other):
        for name in self.__slots__:
            setattr(self, name, getattr(self, name) + getattr(other, name))

    @property
    def on_s(self):
        return self.light_s + self.heavy_s

    @property
    def mean(self):
        return (self.light_ws + self.heavy_ws) / self.on_s if self.on_s else 0.0

    @property
    def light_mean(self):
        return self.light_ws / self.light_s if self.light_s else 0.0

    @property
    def heavy_mean(self):
        return self.heavy_ws / self.heavy_s if self.heavy_s else 0.0

    @property
    def light_jumpiness(self):
        return self.light_jumps / max(self.light_steps, MIN_STEPS)

    @property
    def heavy_jumpiness(self):
        return self.heavy_jumps / max(self.heavy_steps, MIN_STEPS)

    @property
    def jumpiness(self):
        return (self.light_jumps + self.heavy_jumps) / max(self.light_steps + self.heavy_steps, MIN_STEPS)

    @property
    def pf(self):
        """Time-weighted power factor, or None if it was never available."""
        return self.pf_sum / self.pf_s if self.pf_s else None


class _Run:
    """A stretch of time the plug drew power, allowing dips shorter than MERGE_GAP_S."""

    def __init__(self, t):
        self.start = t
        self.end = t  # end of the last average above the run threshold
        self.stats = _Stats()
        self.light_before_heavy_s = 0.0  # light-band on-time before the first heavy average
        self._last_power = None

    def add(self, start, end, power, pf):
        s, dt = self.stats, end - start
        heavy = power >= HEAVY_W
        if heavy:
            s.heavy_s += dt
            s.heavy_ws += power * dt
        else:
            s.light_s += dt
            s.light_ws += power * dt
            if not s.heavy_s:
                self.light_before_heavy_s += dt
        if pf is not None:
            s.pf_s += dt
            s.pf_sum += pf * dt

        # Count steps between consecutive averages in the same band.
        last = self._last_power
        if last is not None and start - self.end < 1.0 and (last >= HEAVY_W) == heavy:
            min_w, fraction = HEAVY_JUMP if heavy else LIGHT_JUMP
            jump = abs(power - last) >= max(min_w, fraction * last)
            if heavy:
                s.heavy_steps += 1
                s.heavy_jumps += jump
            else:
                s.light_steps += 1
                s.light_jumps += jump
        self._last_power = power
        self.end = end

    @property
    def significant(self):
        s = self.stats
        return s.on_s >= SIGNIFICANT_S and s.mean * s.on_s / 3600 >= SIGNIFICANT_WH and s.mean >= SIGNIFICANT_MEAN_W


class _Program:
    """Features of consecutive runs that belong to one use of an appliance."""

    def __init__(self, runs, active, now):
        self.runs = runs
        self.active = active  # the last run is still in progress
        self.last = runs[-1]
        self.span_s = (now if active else self.last.end) - runs[0].start
        self.stats = _Stats()
        for run in runs:
            self.stats.extend(run.stats)
        self.light_before_heavy_s = 0.0
        for run in runs:
            if run.stats.heavy_s:
                self.light_before_heavy_s += run.light_before_heavy_s
                break
            self.light_before_heavy_s += run.stats.light_s


# Signatures -------------------------------------------------------------------------------------------------------
# Each returns a score between 0 and 1 for the latest program. Typical UK-DALE values are in the docstrings.


def _pf_score(pf, a, b, c, d):
    return 1.0 if pf is None else _band(pf, a, b, c, d)


def _kettle(program, compressor_cycles):
    """Heating element, 1.8-3 kW, steady, on for about 1-5 minutes from a cold start."""
    s = program.stats
    if not s.heavy_s:
        return 0.0
    score = (
        _band(s.heavy_mean, 1300, 1800, 3200, 3600)
        * _up(s.heavy_s / s.on_s, 0.5, 0.85)
        * _down(program.light_before_heavy_s, 30, 90)
        * _down(s.jumpiness, 0.15, 0.3)
        * _pf_score(s.pf, 0.75, 0.92, 1.1, 1.2)
    )
    if program.active:
        return score * _down(s.heavy_s, 300, 600)
    return score * _band(s.heavy_s, 5, 20, 360, 720)


def _microwave(program, compressor_cycles):
    """Magnetron, 0.8-1.6 kW, on for seconds to minutes; lower power levels switch it on and off."""
    s = program.stats
    if not s.heavy_s:
        return 0.0
    score = (
        _band(s.heavy_mean, 500, 750, 1700, 2000)
        * _down(s.light_s / s.heavy_s, 1.5, 4)
        * _down(program.light_before_heavy_s, 60, 180)
        * _down(s.heavy_s, 1200, 2400)
        * _pf_score(s.pf, 0.6, 0.8, 1.1, 1.2)
    )
    return score if program.active else score * _up(s.heavy_s, 3, 10)


def _fridge(program, compressor_cycles):
    """Compressor, 50-250 W, steady runs of 10-40 minutes that repeat every half hour or so."""
    if _used_heater(program.runs):
        return 0.0
    run = program.last.stats
    score = (
        _band(run.light_mean, 30, 50, 250, 400)
        * _down(run.light_jumpiness, 0.1, 0.25)
        * _down(run.on_s, 2700, 5400)
        * _pf_score(run.pf, 0.3, 0.45, 0.93, 0.98)
    )
    score *= _up(run.on_s, 30, 120) if program.active else _up(run.on_s, 60, 240)
    # A single steady run could be many things (a laptop charger, a monitor, a dishwasher's first pump phase);
    # similar runs repeating around the clock are what make it a fridge.
    return score * (0.3, 0.6, 1.0)[min(compressor_cycles, 2)]


def _washing_machine(program, compressor_cycles):
    """Drum motor, 100-500 W and constantly jumping, for 30 minutes to 3 hours, often with a 2 kW heater."""
    s = program.stats
    if s.light_steps + s.heavy_steps < MIN_STEPS:
        return 0.0
    # A TV's power follows the picture and jumps too, though less often than a drum.
    score = _up(s.jumpiness, 0.12, 0.3) * _band(program.span_s, 60, 600, 4 * 3600, 5 * 3600)
    if s.light_s >= 60:
        score *= _band(s.light_mean, 30, 60, 800, 1100)
    if not _used_heater(program.runs):
        return score * 0.6
    return score * _band(s.heavy_mean, 1000, 1600, 2800, 3300)


def _dishwasher(program, compressor_cycles):
    """Steady 2 kW heater for 10-25 minutes, one or more times, between steady 50-150 W pump phases."""
    s = program.stats
    if not s.heavy_s:
        return 0.0
    score = (
        _band(s.heavy_mean, 1200, 1700, 2800, 3300)
        * _down(s.heavy_jumpiness, 0.1, 0.25)
        * _up(s.heavy_s, 180, 480)
        * _up(program.span_s, 120, 900)
    )
    if s.light_s >= 60:
        return score * _band(s.light_mean, 20, 50, 300, 500) * _down(s.light_jumpiness, 0.15, 0.35)
    return score * 0.6


SIGNATURES = {
    "kettle": _kettle,
    "microwave": _microwave,
    "fridge": _fridge,
    "dishwasher": _dishwasher,
    "washingmachine": _washing_machine,
}


def _used_heater(runs):
    return any(run.stats.heavy_s > INRUSH_S for run in runs)


def _is_compressor_run(run):
    s = run.stats
    return (
        not _used_heater([run]) and 60 <= s.on_s <= 5400 and 30 <= s.light_mean <= 400 and s.light_jumpiness <= 0.2
    )


def _group_programs(runs):
    programs = []
    for run in runs:
        if programs and run.start - programs[-1][-1].end <= PROGRAM_GAP_S:
            programs[-1].append(run)
        else:
            programs.append([run])
    return programs


class PlugDetector:
    """Tracks one plug's activity and reports what, if anything, is plugged into it."""

    def __init__(self):
        self._last_t = None
        self._running = False
        self._smoothed_power = None
        self._state = None
        self._state_since = None
        self._last_active_t = None
        self._quiet_s = 0.0
        self._current_floor = None

        self._bin_s = self._bin_ws = self._bin_pf_s = self._bin_pf_sum = 0.0
        self._bin_start = None
        self._base = 0.0
        self._recent = deque()  # (end time, average power) of recent bins, for the standby base
        self._bin_running = False
        self._run = None  # the run in progress, if any
        self._runs = deque()  # finished significant runs within HISTORY_S, oldest first
        self._result = (None, 0.0, {})

    def update(self, t, voltage, current, power):
        """Adds one reading (time in s, RMS volts, RMS amps, real watts) and returns the plug's detection."""
        dt = 0.0 if self._last_t is None else min(max(t - self._last_t, 0.0), MAX_DT_S)
        self._last_t = t
        power = abs(power)  # a current sensor fitted the other way round reads negative power
        load_current, pf = self._load_current(voltage, current, power, dt)

        was_running = self._running
        self._running = power >= self._base + ON_W or (self._running and power >= self._base + OFF_W)
        if self._running or was_running or self._smoothed_power is None:
            self._smoothed_power = power
        else:
            self._smoothed_power += (1 - math.exp(-dt / STANDBY_SMOOTHING_S)) * (power - self._smoothed_power)
        if self._running:
            self._last_active_t = t
            state = "running"
        elif self._smoothed_power >= NO_LOAD_W:
            state = "standby"
        else:
            state = "no_load"
        if state != self._state:
            self._state, self._state_since = state, t

        if self._bin_start is None:
            self._bin_start = t
        self._bin_s += dt
        self._bin_ws += power * dt
        if pf is not None:
            self._bin_pf_s += dt
            self._bin_pf_sum += pf * dt
        if t - self._bin_start >= BIN_S:
            if self._bin_s:
                bin_pf = self._bin_pf_sum / self._bin_pf_s if self._bin_pf_s else None
                self._add_bin(t - self._bin_s, t, self._bin_ws / self._bin_s, bin_pf)
                self._result = self._classify(t)
            self._bin_s = self._bin_ws = self._bin_pf_s = self._bin_pf_sum = 0.0
            self._bin_start = t

        appliance, confidence, scores = self._result
        return {
            "state": state,
            "appliance": appliance,
            "confidence": round(confidence, 2),
            "scores": {name: round(score, 2) for name, score in scores.items()},
            "power": round(power, 1),
            "current": round(load_current, 3),
            "power_factor": None if pf is None else round(pf, 2),
            "standby_base": round(self._base, 1),
            "state_s": round(t - self._state_since, 1),
            "last_active_s": None if self._last_active_t is None else round(t - self._last_active_t, 1),
        }

    def _load_current(self, voltage, current, power, dt):
        if power < NO_LOAD_W:
            self._quiet_s += dt
            if self._quiet_s >= FLOOR_LEARN_AFTER_S and current <= MAX_CURRENT_FLOOR_A:
                if self._current_floor is None:
                    self._current_floor = current
                else:
                    self._current_floor += FLOOR_ALPHA * (current - self._current_floor)
        else:
            self._quiet_s = 0.0
        floor = self._current_floor or 0.0
        load_current = math.sqrt(max(current * current - floor * floor, 0.0))
        apparent = voltage * load_current
        if voltage < MIN_MAINS_V or apparent < MIN_PF_VA:
            return load_current, None
        return load_current, min(power / apparent, 1.0)

    def _add_bin(self, start, end, power, pf):
        self._bin_running = power >= self._base + ON_W or (self._bin_running and power >= self._base + OFF_W)
        if self._bin_running:
            if self._run is None:
                self._run = _Run(start)
            self._run.add(start, end, power, pf)
        elif self._run is not None and end - self._run.end > MERGE_GAP_S:
            if self._run.significant:
                self._runs.append(self._run)
            self._run = None
        while self._runs and self._runs[0].end < end - HISTORY_S:
            self._runs.popleft()

        self._recent.append((end, power))
        while self._recent[0][0] < end - BASE_WINDOW_S:
            self._recent.popleft()
        self._base = min(min(p for _, p in self._recent), MAX_BASE_W)

    def _classify(self, t):
        """Returns (appliance, confidence, scores) for the latest program on this plug."""
        result = self._classify_program(t)
        if result is not None:
            return result
        if self._run is not None and self._run.stats.on_s >= UNKNOWN_AFTER_S:
            return "unknown", 0.0, {}
        return None, 0.0, {}  # nothing drawing power, or too early to tell

    def _classify_program(self, t):
        active = self._run is not None and self._run.significant
        runs = list(self._runs) + ([self._run] if active else [])
        if not runs:
            return None
        programs = _group_programs(runs)

        # Consecutive compressor-like runs with a plausible off time in between, outside programs that used
        # a heater (dishwasher pump phases look like compressor runs too).
        compressor_runs = [
            run for program in programs if not _used_heater(program) for run in program if _is_compressor_run(run)
        ]
        compressor_cycles = sum(
            1
            for a, b in zip(compressor_runs, compressor_runs[1:])
            if 120 <= b.start - a.end <= 5400 and 0.65 <= b.stats.light_mean / a.stats.light_mean <= 1.5
        )

        result = self._score(_Program(programs[-1], active, t), compressor_cycles, t)
        if active and result[0] == "unknown" and sum(run.stats.on_s for run in programs[-1]) < IDENTIFY_S:
            # Too early to identify the new program (e.g. the first minutes of a fridge's next cycle): keep
            # reporting the previous appliance, if there is one, or report that it is still being identified.
            if len(programs) > 1:
                previous = self._score(_Program(programs[-2], False, t), compressor_cycles, t)
                if previous is not None and previous[0] != "unknown":
                    return previous
            return None, 0.0, result[2]
        return result

    @staticmethod
    def _score(program, compressor_cycles, t):
        """Returns (appliance, confidence, scores) for a program, or None if it ended too long ago."""
        scores = {name: score(program, compressor_cycles) for name, score in SIGNATURES.items()}
        best = max(scores, key=scores.get)
        confidence = scores[best]
        if confidence < MIN_CONFIDENCE:
            best, confidence = "unknown", 0.0
        if not program.active and t - program.last.end > REMEMBER_S.get(best, REMEMBER_DEFAULT_S):
            return None
        return best, confidence, scores
