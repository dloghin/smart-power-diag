import time
import os
import argparse
from ukdale_parameters import params_appliance
from ukdale_h5 import load_meter, align_meters


DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ukdale.h5')
SAVE_PATH = './'
AGG_MEAN = 522
AGG_STD = 814


def get_arguments():
    parser = argparse.ArgumentParser(description='Create UK-DALE test sets for sequence to point learning')
    parser.add_argument('--data_path', type=str, default=DATA_PATH,
                        help='The UKDALE HDF5 file (ukdale.h5)')
    parser.add_argument('--appliance_name', type=str, default='kettle',
                        help='which appliance: kettle, microwave, fridge, dishwasher, washingmachine')
    parser.add_argument('--aggregate_mean', type=int, default=AGG_MEAN,
                        help='Mean value of aggregated reading (mains)')
    parser.add_argument('--aggregate_std', type=int, default=AGG_STD,
                        help='Std value of aggregated reading (mains)')
    parser.add_argument('--nrows', type=int, default=None,
                        help='Keep only the first NROWS rows of each test set (default: all)')
    parser.add_argument('--save_path', type=str, default=SAVE_PATH,
                        help='The directory to store the test data')
    return parser.parse_args()


args = get_arguments()
start_time = time.time()
appliance_name = args.appliance_name
print(appliance_name)

sample_seconds = 8

print("Starting creating testset...")

for h in params_appliance[appliance_name]['houses']:

    channel = params_appliance[appliance_name]['channels'][params_appliance[appliance_name]['houses'].index(h)]
    print(args.data_path + ': building' + str(h) + '/elec/meter' + str(channel))

    agg_df = load_meter(args.data_path, h, 1, 'aggregate')
    df = load_meter(args.data_path, h, channel, appliance_name)

    print(agg_df.head())
    print(df.head())

    # Align mains and appliance readings on a common time grid
    df = align_meters(agg_df, df, sample_seconds)
    del agg_df
    df = df.head(args.nrows).reset_index(drop=True)

    print(df.head())

    # Normalization
    df['aggregate'] = (df['aggregate'] - args.aggregate_mean) / args.aggregate_std
    df[appliance_name] = \
        (df[appliance_name] - params_appliance[appliance_name]['mean']) / params_appliance[appliance_name]['std']

    # Save
    df.to_csv(args.save_path + appliance_name + '_test_' + 'uk-dale_' + 'H' + str(h) + '.csv', index=False)

    print("Size of test set is {:.3f} M rows (House {:d})."
          .format(df.shape[0] / 10 ** 6, h))

    del df


print("\nNormalization parameters: ")
print("Mean and standard deviation values USED for AGGREGATE are:")
print("    Mean = {:d}, STD = {:d}".format(args.aggregate_mean, args.aggregate_std))

print('Mean and standard deviation values USED for ' + appliance_name + ' are:')
print("    Mean = {:d}, STD = {:d}"
      .format(params_appliance[appliance_name]['mean'], params_appliance[appliance_name]['std']))

print("\nPlease find files in: " + args.save_path)
tot = int(int(time.time() - start_time) / 60)
print("\nTotal elapsed time: " + str(tot) + ' min')
