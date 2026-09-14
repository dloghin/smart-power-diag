# Seq2point on UK-DALE: training, testing and fridge fault simulation

This folder contains everything needed to reproduce the UK-DALE experiments:

1. Build training, validation and test sets from the UK-DALE HDF5 file (`ukdale.h5`).
2. Train seq2point models for kettle, microwave, fridge, dishwasher and washing machine on the GPU.
3. Test the models on house 2.
4. Measure how accurately the models estimate daily appliance energy.
5. Simulate fridge faults and check whether the fridge model can detect them.

Training uses house 1, and house 2 is the test house. All commands below are run from this folder unless stated otherwise.

This code is based on https://github.com/MingjunZhong/seq2point-nilm.

## Contents

```
├── README.md
├── requirements.txt
├── train_main.py                 # train a model (command line)
├── test_main.py                  # test a model (command line)
├── seq2point_train.py            # training loop
├── seq2point_test.py             # testing and metrics
├── data_feeder.py                # sliding-window batch generators
├── model_structure.py            # seq2point network, save / load
├── appliance_data.py             # normalisation constants
├── remove_space.py
├── appliance_daily_energy.py     # daily energy accuracy on house 2
├── fridge_fault_test.py          # simulated fridge faults
├── dataset_management/ukdale/
│   ├── ukdale.h5                 # UK-DALE data (NILMTK HDF5 format, 6.3 GB)
│   ├── ukdale_h5.py              # reads meters from ukdale.h5 and aligns them
│   ├── ukdale_parameters.py      # houses, meters and normalisation per appliance
│   ├── create_trainset_ukdale.py # creates training / validation / test CSVs
│   ├── create_test_set.py        # optional: normalised test CSVs for houses 1 and 2
│   ├── house_data_plot.py        # optional: plot raw mains and appliance power
│   └── <appliance>/              # generated CSVs (50% datasets)
├── saved_models/                 # trained 50% models, training logs (*_train.log), test logs (*_seq2point_.log)
│   └── 5pct/                     # the same for the 5% models
└── fault_test_results/           # output of fridge_fault_test.py
```

## 1. Setup

Tested with Python 3.12.3 on Ubuntu with an NVIDIA GPU (driver 595.58.03).

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

The repository already has a virtual environment with these packages in `../venv`
(`source ../venv/bin/activate`).

`tensorflow[and-cuda]` installs the CUDA and cuDNN libraries TensorFlow needs, so no system-wide CUDA
installation is required. Check that the GPU is visible:

```bash
python -c "import tensorflow as tf; print(tf.config.list_physical_devices('GPU'))"
```

If this prints an empty list, TensorFlow will silently train on the CPU, which is far slower.

`test_main.py` opens a plot window at the end. On a machine without a display, run
`export MPLBACKEND=Agg` first.

## 2. Data

`dataset_management/ukdale/ukdale.h5` is the UK-DALE HDF5 release in NILMTK format (metadata date
2017-04-26), available from the UK-DALE website (https://jack-kelly.com/data/). Or get it from Kaggle:

```python
import kagglehub

# Download latest version
path = kagglehub.dataset_download("abdelmdz/uk-dale")

print("Path to dataset files:", path)
```

The copy used here is 6,330,639,238 bytes with MD5 `9f2b7f5dfa3a9d6842529d3a138c110a`.

The file is read with PyTables directly. It is Blosc-compressed, which h5py cannot decode, and its
Python 2-era pandas metadata cannot be read by pandas 3. In the file, meter numbers are the same as the
channel numbers of the raw `.dat` release, and meter 1 is the 6-second whole-house mains.

| Appliance | House 1 meter (training) | House 2 meter (test) |
|---|---:|---:|
| kettle | 10 | 8 |
| microwave | 13 | 15 |
| fridge | 12 | 14 |
| dishwasher | 6 | 13 |
| washingmachine | 5 | 12 |

## 3. Create the datasets

`create_trainset_ukdale.py` aligns mains and appliance power on an 8-second grid, normalises it and
writes three CSV files per appliance:

- `<appliance>_training_.csv` and `<appliance>_validation_.csv` come from house 1. After cropping, the
  last 13% of the house 1 data is used for validation.
- `<appliance>_test_.csv` is all of house 2.

`--training_building_percent` is the percentage dropped from the end of the house 1 data. The default
of 95 keeps the first 5%; the main results use 50.

The script **appends** to existing CSVs, so delete old files before re-creating them.

```bash
cd dataset_management/ukdale
for a in kettle microwave fridge dishwasher washingmachine; do
  rm -rf $a && mkdir -p $a
  python create_trainset_ukdale.py --appliance_name $a --save_path $a/ --training_building_percent 50
done
cd ../..
```

This takes about 1 minute. Expected sizes (rows):

| Appliance | Training | Validation | Test |
|---|---:|---:|---:|
| kettle | 7,121,380 | 1,064,114 | 1,607,425 |
| microwave | 7,260,752 | 1,084,939 | 1,293,563 |
| fridge | 7,255,974 | 1,084,225 | 1,294,010 |
| dishwasher | 7,373,426 | 1,101,776 | 1,293,998 |
| washingmachine | 7,305,361 | 1,091,605 | 1,293,583 |

To reproduce the smaller 5% datasets as well, write them to a separate folder:

```bash
cd dataset_management/ukdale
for a in kettle microwave fridge dishwasher washingmachine; do
  rm -rf 5pct/$a && mkdir -p 5pct/$a
  python create_trainset_ukdale.py --appliance_name $a --save_path 5pct/$a/ --training_building_percent 95
done
cd ../..
```

The 5% training sets have 712k–737k rows and the validation sets 106k–110k rows. The test sets are
identical to the 50% ones.

## 4. Train

One epoch is one full pass over the training set: `--crop` is the number of training rows, so the
number of steps per epoch is rows / batch size. `--skip_rows_train 0` is required, because the default
skips the first 10 million rows (a setting meant for REFIT). `--validation_steps 0` validates on the
whole validation set after every epoch. Training stops early when the validation loss has not improved
for 3 epochs.

```bash
mkdir -p saved_models
for a in kettle microwave fridge dishwasher washingmachine; do
  D=dataset_management/ukdale/$a
  python train_main.py --appliance_name $a --epochs 10 --batch_size 1000 \
    --crop $(wc -l < $D/${a}_training_.csv) --skip_rows_train 0 --validation_steps 0 \
    --training_directory $D/${a}_training_.csv --validation_directory $D/${a}_validation_.csv \
    > saved_models/${a}_train.log 2>&1
done
```

Models are saved as `saved_models/<appliance>_seq2point_model.h5`. On the RTX A6000 each epoch takes
about 7 minutes, and all five appliances took 3 h 29 min in total.

Results of the included 50% models (validation loss is the MSE on normalised power):

| Appliance | Epochs run | Best val loss (epoch) | Val loss of saved model | Time |
|---|---:|---:|---:|---:|
| kettle | 6 | 0.0052 (3) | 0.0052 | 40 min |
| microwave | 6 | 0.0115 (3) | 0.0122 | 40 min |
| fridge | 5 | 0.0084 (2) | 0.0096 | 34 min |
| dishwasher | 9 | 0.0065 (6) | 0.0072 | 61 min |
| washingmachine | 5 | 0.0086 (2) | 0.0100 | 34 min |

The saved model is the one from the last epoch, not the best one.

The 5% models were trained with `--crop 740000` (740 steps, about one pass per epoch) and the default
validation of 100 random batches per epoch (about 25 minutes in total):

```bash
mkdir -p saved_models/5pct
for a in kettle microwave fridge dishwasher washingmachine; do
  D=dataset_management/ukdale/5pct/$a
  python train_main.py --appliance_name $a --epochs 10 --batch_size 1000 --crop 740000 --skip_rows_train 0 \
    --training_directory $D/${a}_training_.csv --validation_directory $D/${a}_validation_.csv \
    --saved_models_dir saved_models/5pct/ > saved_models/5pct/${a}_train.log 2>&1
done
```

Training is not seeded (random shuffling, weight initialisation, cuDNN), so retrained models give
slightly different numbers from the ones in this README. Use the included models to reproduce the
test and fault results below.

## 5. Test on house 2

`--crop` must be at least the number of test rows so the whole test set is used. Test logs are
**appended** to `<saved_models_dir>/<appliance>_seq2point_.log`.

```bash
for a in kettle microwave fridge dishwasher washingmachine; do
  python test_main.py --appliance_name $a --crop 2000000 --test_directory dataset_management/ukdale/$a/${a}_test_.csv
  python test_main.py --appliance_name $a --crop 2000000 --test_directory dataset_management/ukdale/$a/${a}_test_.csv \
    --saved_models_dir saved_models/5pct/
done
```

Each test takes about 30 seconds. The logs contain the MSE and MAE on normalised power. Multiply the
MAE by the appliance's standard deviation to get watts: kettle 1000, microwave 800, fridge 400,
dishwasher 1000, washingmachine 700.

| Appliance | MAE (W), 5% | MAE (W), 50% | MSE (norm), 5% | MSE (norm), 50% |
|---|---:|---:|---:|---:|
| kettle | 26.0 | 21.4 | 0.0137 | 0.0161 |
| microwave | 16.8 | 17.6 | 0.0137 | 0.0112 |
| fridge | 29.1 | 21.6 | 0.0126 | 0.0091 |
| dishwasher | 43.1 | 42.7 | 0.0638 | 0.0778 |
| washingmachine | 24.7 | 20.9 | 0.0501 | 0.0371 |

## 6. Daily energy accuracy

```bash
python appliance_daily_energy.py
```

This predicts every window of each house 2 test set with the 50% models (about 2 minutes). It then
compares estimated and actual energy per 24 hours of data, and reports the error while the appliance
is on and off. Only days on which the appliance used more than 0.02 kWh are counted.

| Appliance | Days | Actual kWh/day (median) | Median daily energy error | 90th percentile | Median on-power (W) | MAE while on (W) | MAE while off (W) |
|---|---:|---:|---:|---:|---:|---:|---:|
| kettle | 148 | 0.64 | 14% | 27% | 2955 | 1112.9 | 7.3 |
| microwave | 116 | 0.15 | 83% | 267% | 1309 | 935.2 | 10.2 |
| fridge | 119 | 1.02 | 12% | 21% | 88 | 26.6 | 14.9 |
| dishwasher | 117 | 1.12 | 94% | 98% | 1978 | 1305.1 | 2.1 |
| washingmachine | 119 | 0.09 | 164% | 577% | 183 | 546.9 | 12.8 |

## 7. Fridge fault simulation

```bash
python fridge_fault_test.py
```

This takes about 2 minutes. Options: `--baseline_days` (default 28), `--false_alarm_pct` (default 5),
`--model_path` and `--save_path` (default `fault_test_results/`).

**How it works.** Each fault is applied to house 2's measured fridge power, and the same change is
added to the mains. The fridge model then disaggregates the modified mains. The faults are:

- compressor runs 10%, 25% or 50% longer, using the following off time
- running power +10% or +25%
- the compressor never stops (runs continuously at its median running power, 88 W)
- the fridge stops (0 W)

A fault is simulated as starting on each of 67 eligible days. On each start day, the fridge's energy
on that day (1d), or its average over the first week (7d), is compared with the healthy days in the 28
days before. The comparison uses the number of standard deviations from the baseline mean. The
detection threshold is calibrated so that the healthy fridge raises a false alarm on 5% of start days.
The same rule is applied to the model estimate and to the measured fridge power (what a smart plug on
the fridge would see).

**Results.** There are 117 complete days, and the start days run from 2013-06-11 to 2013-10-05. The
healthy fridge uses 1.10 kWh/day on average, and the model's median daily energy error is 12%. The
thresholds for 5% false alarms are: plug 3.1σ (1d) and 2.7σ (7d), model 2.2σ (1d) and 1.7σ (7d).

| Fault | True energy change | Model energy change | Detected, plug 1d | Detected, plug 7d | Detected, model 1d | Detected, model 7d |
|---|---:|---:|---:|---:|---:|---:|
| Runs 10% longer | +6.7% | +4.5% | 16% | 37% | 15% | 36% |
| Runs 25% longer | +16.4% | +8.4% | 64% | 69% | 27% | 51% |
| Runs 50% longer | +31.7% | +11.3% | 73% | 75% | 40% | 66% |
| Running power +10% | +8.7% | +6.7% | 24% | 49% | 19% | 45% |
| Running power +25% | +21.8% | +10.0% | 70% | 73% | 30% | 70% |
| Compressor never stops | +95.5% | −4.7% | 100% | 100% | 39% | 9% |
| Fridge stopped | −100% | −42.2% | 100% | 100% | 97% | 100% |

- **Energy increases get through only partly.** The model's estimate rises by a third to three-quarters
  of the true change.
- **A compressor that never stops is invisible to the model.** The model learned that fridge power
  switches on and off, so a constant load isn't treated as the fridge.
- **A stopped fridge is caught, but not as "off".** The model still attributes 58% of normal fridge
  energy to it.
- **Even the plug misses many moderate faults.** The healthy fridge's own consumption varies by up to
  ~50% over the summer.

Output files in `fault_test_results/`:

- `fridge_fault_summary.csv`: the table above.
- `fridge_fault_daily.csv`: daily energy and duty cycle for every scenario, from the plug and the model.
- `fridge_fault_z_scores.csv`: the score behind every detection decision.
- `fridge_fault_daily_energy.png`: estimated daily energy per scenario.

**Limitations.** The test covers one fridge in one house over one summer, and the faults are idealised.
The mains meter measures apparent power and the fridge meter active power, so adding the fridge change
to the mains is an approximation. Neighbouring start days share most of their data, so the
percentages are approximate.

## Changes from the original seq2point-nilm code

These changes were needed to use the HDF5 data and current library versions, and to get correct test
results:

- **Loading UK-DALE:** the UK-DALE scripts read `ukdale.h5` through `ukdale_h5.py` instead of the raw
  `.dat` files. Mains and appliance power are aligned by timestamp; `create_test_set.py` used to pair
  them by row position.
- **Library updates:** pandas 3 and Keras 3 fixes. Models are loaded uncompiled and then compiled, because
  Keras 3 cannot deserialise the metrics stored in legacy `.h5` files.
- **New training options:** `train_main.py` has `--skip_rows_train`, `--validation_steps` and
  `--saved_models_dir`; `test_main.py` has `--saved_models_dir`; `create_trainset_ukdale.py` has
  `--training_building_percent`.
- **Test coverage fix:** testing now scores every window of the test set. It used to score only the
  first ~10%, because batches of 100 windows were run for (rows / batch size) steps.
- **Test alignment fix:** test targets are now aligned with the window midpoints. The generator used to
  apply the 299-row offset a second time, so predictions were compared with the power about 40 minutes
  later.
- **NumPy 2 fix:** weight counting in the test code no longer crashes.
