""" Simulated-fault test for the fridge seq2point model on UK-DALE house 2 (the test house).

Each fault is injected into the fridge's submetered power and the same change is added to the mains. The
trained model then disaggregates the modified mains. Two questions are answered:

1. Sensitivity: how much does the model's estimated fridge energy change compared with the true change?
2. Detectability: a fault is simulated as starting on each eligible day. Its daily energy (on the start day,
   or averaged over the first 7 days) is compared with the healthy days in the preceding baseline period.
   The threshold is calibrated so that the healthy fridge raises a false alarm in a fixed percentage of
   start days, which keeps the comparison fair despite slow seasonal changes in consumption.

Both questions are answered for the model estimate and for the true fridge power, i.e. what a smart plug
on the fridge would see.

Note: the mains meter measures apparent power and the fridge meter active power, so adding the fridge
change to the mains is an approximation.
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd
import tensorflow as tf
import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "dataset_management", "ukdale"))
from ukdale_h5 import load_meter, align_meters
from ukdale_parameters import params_appliance
from appliance_data import mains_data

BUILDING = 2
SAMPLE_SECONDS = 8
SAMPLES_PER_DAY = 24 * 3600 // SAMPLE_SECONDS
MIN_DAY_COVERAGE = 0.9
FRIDGE = params_appliance["fridge"]
ON_POWER = FRIDGE["on_power_threshold"]
DETECTION_WINDOWS = (1, 7)


def get_arguments():
    parser = argparse.ArgumentParser(description="Simulated-fault test for the fridge seq2point model. ")
    parser.add_argument("--data_path", type=str, default=os.path.join(ROOT, "dataset_management", "ukdale", "ukdale.h5"),
                        help="The UKDALE HDF5 file (ukdale.h5). ")
    parser.add_argument("--model_path", type=str, default=os.path.join(ROOT, "saved_models", "fridge_seq2point_model.h5"),
                        help="The trained fridge model. ")
    parser.add_argument("--baseline_days", type=int, default=28,
                        help="Calendar days before a fault starts used as the healthy baseline. Default is 28. ")
    parser.add_argument("--false_alarm_pct", type=float, default=5.0,
                        help="Percentage of start days on which the healthy fridge may be flagged. Default is 5. ")
    parser.add_argument("--input_window_length", type=int, default=599, help="Number of input data points to network. ")
    parser.add_argument("--batch_size", type=int, default=1000, help="Batch size for inference. ")
    parser.add_argument("--save_path", type=str, default="fault_test_results/", help="The directory to store the results. ")
    return parser.parse_args()


# Fault models -------------------------------------------------------------------------------------------------------
# Each takes the fridge power and a context dictionary and returns the faulty fridge power over the whole period.

def longer_runs(fraction):
    """Compressor runs last longer (e.g. worn door seal, dirty condenser): each run is extended by the
    given fraction of its length at its median power, using the following off time."""
    def apply(power, ctx):
        faulty = power.copy()
        starts, ends = ctx["cycles"]
        next_starts = np.append(starts[1:], len(power))
        for start, end, next_start in zip(starts, ends, next_starts):
            # Keep at least one off sample before the next run and never extend across a recording gap.
            limit = min(next_start - 1, ctx["segment_ends"][ctx["segment"][start]])
            new_end = min(end + int(round(fraction * (end - start))), limit)
            faulty[end:new_end] = np.median(power[start:end])
        return faulty
    return apply


def higher_power(fraction):
    """The compressor draws more power while running (e.g. wear, high head pressure)."""
    def apply(power, ctx):
        faulty = power.copy()
        faulty[power >= ON_POWER] *= 1 + fraction
        return faulty
    return apply


def never_stops(power, ctx):
    """The compressor runs continuously (e.g. stuck thermostat relay, refrigerant leak)."""
    return np.maximum(power, ctx["running_power"])


def stopped(power, ctx):
    """The fridge stops drawing power (e.g. failed compressor or power supply)."""
    return np.zeros_like(power)


SCENARIOS = [
    ("Healthy (no fault)", None),
    ("Runs 10% longer", longer_runs(0.10)),
    ("Runs 25% longer", longer_runs(0.25)),
    ("Runs 50% longer", longer_runs(0.50)),
    ("Running power +10%", higher_power(0.10)),
    ("Running power +25%", higher_power(0.25)),
    ("Compressor never stops", never_stops),
    ("Fridge stopped", stopped),
]


# Helpers ------------------------------------------------------------------------------------------------------------

def find_cycles(power, segment):
    """Returns the start and (exclusive) end rows of runs at or above ON_POWER, split at recording gaps."""
    on = power >= ON_POWER
    boundaries = np.flatnonzero((np.diff(on.astype(np.int8)) != 0) | (np.diff(segment) != 0)) + 1
    starts = np.concatenate(([0], boundaries))
    ends = np.concatenate((boundaries, [len(power)]))
    return starts[on[starts]], ends[on[starts]]


def estimate_fridge(model, mains, window_length, batch_size):
    """Disaggregates the fridge power (W) from the mains; the first and last window_length // 2 rows are NaN."""
    offset = window_length // 2
    inputs = ((mains - mains_data["mean"]) / mains_data["std"]).astype(np.float32)
    windows = np.lib.stride_tricks.sliding_window_view(inputs, window_length)
    estimate = np.full(len(mains), np.nan)
    chunk_size = 100 * batch_size
    for start in range(0, len(windows), chunk_size):
        chunk = windows[start:start + chunk_size]
        estimate[offset + start:offset + start + len(chunk)] = model.predict(chunk, batch_size=batch_size, verbose=0)[:, 0]
    return np.clip(estimate * FRIDGE["std"] + FRIDGE["mean"], 0, None)


def daily_features(index, power):
    """Energy (kWh/day) and duty cycle (% of time at or above ON_POWER) per UTC day. Days with less than
    MIN_DAY_COVERAGE of their samples are NaN."""
    series = pd.Series(power, index=index)
    days = index.normalize()
    coverage = series.groupby(days).count() / SAMPLES_PER_DAY
    energy = series.groupby(days).mean() * 24 / 1000
    duty = (series >= ON_POWER).astype(float).where(series.notna()).groupby(days).mean() * 100
    complete = coverage >= MIN_DAY_COVERAGE
    return energy.where(complete), duty.where(complete)


def fault_start_days(complete_days, baseline_days):
    """Complete days with at least 75% complete days in their baseline and 5 complete days in the next week."""
    starts = []
    for day in complete_days:
        in_baseline = ((complete_days >= day - pd.Timedelta(days=baseline_days)) & (complete_days < day)).sum()
        in_week = ((complete_days >= day) & (complete_days < day + pd.Timedelta(days=7))).sum()
        if in_baseline >= 0.75 * baseline_days and in_week >= 5:
            starts.append(day)
    return pd.DatetimeIndex(starts)


def z_scores(energy, healthy_energy, starts, baseline_days, window_days):
    """For a fault starting on each day, the mean energy over the first window_days calendar days as a number of
    standard deviations from the healthy daily energy in the preceding baseline_days calendar days."""
    scores = []
    for start in starts:
        baseline = healthy_energy[start - pd.Timedelta(days=baseline_days):start - pd.Timedelta(days=1)].dropna()
        after = energy[start:start + pd.Timedelta(days=window_days - 1)].dropna()
        scores.append((after.mean() - baseline.mean()) / baseline.std())
    return pd.Series(scores, index=starts)


# Main ---------------------------------------------------------------------------------------------------------------

def main():
    args = get_arguments()
    os.makedirs(args.save_path, exist_ok=True)

    channel = FRIDGE["channels"][FRIDGE["houses"].index(BUILDING)]
    df = align_meters(load_meter(args.data_path, BUILDING, 1, "aggregate"),
                      load_meter(args.data_path, BUILDING, channel, "fridge"), SAMPLE_SECONDS)
    index = df.index
    mains = df["aggregate"].to_numpy()
    fridge = df["fridge"].to_numpy()

    segment = np.concatenate(([0], np.cumsum(np.diff(index.asi8) != SAMPLE_SECONDS * 10 ** 9)))
    ctx = {
        "segment": segment,
        "segment_ends": np.append(np.flatnonzero(np.diff(segment)) + 1, len(fridge)),
        "cycles": find_cycles(fridge, segment),
        "running_power": np.median(fridge[fridge >= ON_POWER]),
    }
    print("House {}: {} to {}".format(BUILDING, index[0], index[-1]))
    print("Compressor runs: {}, median running power {:.0f} W".format(len(ctx["cycles"][0]), ctx["running_power"]))

    model = tf.keras.models.load_model(args.model_path, compile=False)

    daily = {}
    for name, fault in SCENARIOS:
        print("Scenario: " + name)
        faulty = fridge if fault is None else fault(fridge, ctx)
        faulty_mains = np.maximum(mains + faulty - fridge, 0)
        estimate = estimate_fridge(model, faulty_mains, args.input_window_length, args.batch_size)
        for source, power in (("plug", faulty), ("model", estimate)):
            daily[(name, source, "energy_kwh")], daily[(name, source, "duty_pct")] = daily_features(index, power)
    daily = pd.DataFrame(daily)
    daily.index.name = "day"

    healthy = SCENARIOS[0][0]
    complete_days = daily.index[daily[(healthy, "plug", "energy_kwh")].notna() & daily[(healthy, "model", "energy_kwh")].notna()]
    starts = fault_start_days(complete_days, args.baseline_days)
    print("\n{} complete days; {} fault start days from {:%Y-%m-%d} to {:%Y-%m-%d}".format(
        len(complete_days), len(starts), starts[0], starts[-1]))

    healthy_plug = daily[(healthy, "plug", "energy_kwh")].loc[complete_days]
    healthy_model = daily[(healthy, "model", "energy_kwh")].loc[complete_days]
    print("Healthy fridge: {:.2f} kWh/day on average; model daily energy error: median {:.0f}%".format(
        healthy_plug.mean(), (100 * (healthy_model - healthy_plug).abs() / healthy_plug).median()))

    scores = {}
    thresholds = {}
    for source in ("plug", "model"):
        healthy_energy = daily[(healthy, source, "energy_kwh")].loc[complete_days]
        for window in DETECTION_WINDOWS:
            for name, _ in SCENARIOS:
                energy = daily[(name, source, "energy_kwh")].loc[complete_days]
                scores[(name, source, window)] = z_scores(energy, healthy_energy, starts, args.baseline_days, window)
            thresholds[(source, window)] = np.percentile(scores[(healthy, source, window)].abs(), 100 - args.false_alarm_pct)
    scores = pd.DataFrame(scores)
    scores.index.name = "fault_start"

    rows = []
    for name, _ in SCENARIOS:
        row = {"scenario": name}
        for source in ("plug", "model"):
            energy = daily[(name, source, "energy_kwh")].loc[complete_days]
            healthy_energy = daily[(healthy, source, "energy_kwh")].loc[complete_days]
            row[source + "_energy_change_pct"] = 100 * (energy.sum() / healthy_energy.sum() - 1)
        for source in ("plug", "model"):
            for window in DETECTION_WINDOWS:
                flagged = scores[(name, source, window)].abs() > thresholds[(source, window)]
                row["{}_detected_{}d_pct".format(source, window)] = 100 * flagged.mean()
        rows.append(row)
    summary = pd.DataFrame(rows).set_index("scenario")

    print("\nThresholds for {:g}% false alarms (standard deviations from the baseline mean): ".format(args.false_alarm_pct)
          + ", ".join("{} {}-day {:.1f}".format(source, window, value) for (source, window), value in thresholds.items()))
    print("\nEnergy change vs healthy, and % of fault start days on which the fault is detected on the first day (1d) "
          "or from the first week (7d):")
    print(summary.drop(index=healthy).round(1).to_string())

    summary.round(2).to_csv(os.path.join(args.save_path, "fridge_fault_summary.csv"))
    daily.round(4).to_csv(os.path.join(args.save_path, "fridge_fault_daily.csv"))
    scores.round(3).to_csv(os.path.join(args.save_path, "fridge_fault_z_scores.csv"))
    plot_scenarios(daily, summary, complete_days, args.false_alarm_pct,
                   os.path.join(args.save_path, "fridge_fault_daily_energy.png"))
    print("\nPlease find results in: " + args.save_path)


def plot_scenarios(daily, summary, complete_days, false_alarm_pct, file_name):
    """Small multiples of the model's estimated daily fridge energy per scenario against the healthy estimate."""
    surface, primary, secondary, muted, grid, axis = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
    fault_colour, plug_colour, healthy_colour = "#2a78d6", "#eb6834", "#898781"

    healthy = SCENARIOS[0][0]
    daily = daily.reindex(pd.date_range(daily.index[0], daily.index[-1], freq="D"))
    daily[~daily.index.isin(complete_days)] = np.nan
    healthy_energy = daily[(healthy, "model", "energy_kwh")]

    fig, axes = plt.subplots(4, 2, figsize=(12, 11), sharex=True, sharey=True, facecolor=surface)
    for ax, (name, _) in zip(axes.flat, SCENARIOS):
        ax.set_facecolor(surface)
        ax.plot(healthy_energy.index, healthy_energy, color=healthy_colour, linewidth=1.5, zorder=2)
        row = summary.loc[name]
        if name == healthy:
            plug_energy = daily[(healthy, "plug", "energy_kwh")]
            ax.plot(plug_energy.index, plug_energy, color=plug_colour, linewidth=1.5, zorder=3)
            detail = "Measured {:.2f} kWh/day on average; model estimate {:+.0f}%".format(
                plug_energy.mean(), 100 * (healthy_energy.sum() / plug_energy.sum() - 1))
        else:
            energy = daily[(name, "model", "energy_kwh")]
            ax.plot(energy.index, energy, color=fault_colour, linewidth=1.5, zorder=3)
            detail = "True {:+.0f}%, model {:+.0f}%  ·  detected from first week: model {:.0f}%, plug {:.0f}%".format(
                row["plug_energy_change_pct"], row["model_energy_change_pct"],
                row["model_detected_7d_pct"], row["plug_detected_7d_pct"])
        ax.set_title(name, loc="left", fontsize=11, fontweight="bold", color=primary, pad=18)
        ax.text(0, 1.02, detail, transform=ax.transAxes, fontsize=9, color=secondary, va="bottom")

        ax.grid(axis="y", color=grid, linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(axis)
        ax.tick_params(colors=muted, labelsize=9, length=0)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))

    for ax in axes[:, 0]:
        ax.set_ylabel("kWh/day", color=secondary, fontsize=9)
    axes[0, 0].set_ylim(bottom=0)

    handles = [plt.Line2D([], [], color=plug_colour, linewidth=1.5, label="Measured, healthy fridge"),
               plt.Line2D([], [], color=healthy_colour, linewidth=1.5, label="Model estimate, healthy fridge"),
               plt.Line2D([], [], color=fault_colour, linewidth=1.5, label="Model estimate, with fault")]
    fig.legend(handles=handles, loc="upper left", ncol=3, frameon=False, fontsize=9, labelcolor=secondary,
               bbox_to_anchor=(0.01, 0.955))
    fig.suptitle("Estimated daily fridge energy under simulated faults (UK-DALE house 2, "
                 "detection at {:g}% false alarms)".format(false_alarm_pct),
                 x=0.01, y=0.99, ha="left", fontsize=13, fontweight="bold", color=primary)
    fig.tight_layout(rect=(0, 0, 1, 0.94), h_pad=2.5)
    fig.savefig(file_name, dpi=120, facecolor=surface)
    plt.close(fig)


if __name__ == "__main__":
    main()
