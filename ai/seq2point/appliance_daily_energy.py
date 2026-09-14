""" Precision of the trained models on the UK-DALE test house (house 2) at the granularity a fault detector
would work at: estimated vs actual energy per 24 hours of data, and the error while the appliance is on or off.

Run from this folder after creating the datasets and training the models (see README.md).
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd
import tensorflow as tf

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "dataset_management", "ukdale"))
from ukdale_parameters import params_appliance

OFFSET, WIN, DAY = 299, 599, 24 * 3600 // 8   # 8-second samples -> 10800 rows per 24 h of data

parser = argparse.ArgumentParser(description="Daily energy accuracy of the trained models on the test house. ")
parser.add_argument("--saved_models_dir", type=str, default=os.path.join(ROOT, "saved_models"),
                    help="The directory containing the trained models. ")
args = parser.parse_args()

print(f"{'appliance':15}{'days':>5}{'actual kWh/day':>15}{'median |daily err|':>19}{'90th pct':>9}"
      f"{'on-power W':>11}{'MAE on W':>9}{'MAE off W':>10}")
for a, p in params_appliance.items():
    d = pd.read_csv(os.path.join(ROOT, "dataset_management", "ukdale", a, a + "_test_.csv"), header=None).to_numpy(np.float32)
    x = d[:, 0]
    y = d[OFFSET:len(d) - OFFSET, 1] * p['std'] + p['mean']
    m = tf.keras.models.load_model(os.path.join(args.saved_models_dir, a + "_seq2point_model.h5"), compile=False)
    pred = []
    for s in range(0, len(y), 100000):
        idx = np.arange(s, min(s + 100000, len(y)))
        w = np.lib.stride_tricks.sliding_window_view(x, WIN)[idx]
        pred.append(m.predict(w, batch_size=1000, verbose=0)[:, 0])
    pred = np.clip(np.concatenate(pred) * p['std'] + p['mean'], 0, None)
    y = np.clip(y, 0, None)

    n_days = len(y) // DAY
    act = y[:n_days * DAY].reshape(n_days, DAY).sum(1) * 8 / 3.6e6
    est = pred[:n_days * DAY].reshape(n_days, DAY).sum(1) * 8 / 3.6e6
    keep = act > 0.02                  # days on which the appliance was actually used
    err = np.abs(est[keep] - act[keep]) / act[keep] * 100
    on = y >= p['on_power_threshold']
    print(f"{a:15}{keep.sum():>5}{np.median(act[keep]):>15.2f}{np.median(err):>18.0f}%{np.percentile(err, 90):>8.0f}%"
          f"{np.median(y[on]):>11.0f}{np.abs(pred[on] - y[on]).mean():>9.1f}{np.abs(pred[~on] - y[~on]).mean():>10.1f}")
