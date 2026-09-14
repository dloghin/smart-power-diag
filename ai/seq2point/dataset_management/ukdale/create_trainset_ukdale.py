from ukdale_parameters import *
import pandas as pd
import matplotlib.pyplot as plt
import time
import argparse
import os
from ukdale_h5 import load_meter, align_meters


DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ukdale.h5')
SAVE_PATH = 'kettle/'
AGG_MEAN = 522
AGG_STD = 814


def get_arguments():
    parser = argparse.ArgumentParser(description='sequence to point learning \
                                     example for NILM')
    parser.add_argument('--data_path', type=str, default=DATA_PATH,
                          help='The UKDALE HDF5 file (ukdale.h5)')
    parser.add_argument('--appliance_name', type=str, default='kettle',
                          help='which appliance you want to train: kettle,\
                          microwave,fridge,dishwasher,washingmachine')
    parser.add_argument('--aggregate_mean',type=int,default=AGG_MEAN,
                        help='Mean value of aggregated reading (mains)')
    parser.add_argument('--aggregate_std',type=int,default=AGG_STD,
                        help='Std value of aggregated reading (mains)')
    parser.add_argument('--save_path', type=str, default=SAVE_PATH,
                          help='The directory to store the training data')
    parser.add_argument('--training_building_percent', type=int, default=95,
                          help='Percentage of the training building data to drop from its end (default 95, i.e. keep the first 5%%)')
    return parser.parse_args()


args = get_arguments()
appliance_name = args.appliance_name
print(appliance_name)


def main():

    start_time = time.time()
    sample_seconds = 8
    training_building_percent = args.training_building_percent
    validation_percent = 13
    debug = False

    train_dfs = []

    for h in params_appliance[appliance_name]['houses']:
        channel = params_appliance[appliance_name]['channels'][params_appliance[appliance_name]['houses'].index(h)]
        print('    ' + args.data_path + ': building' + str(h) + '/elec/meter' + str(channel))

        mains_df = load_meter(args.data_path, h, 1, 'aggregate')
        app_df = load_meter(args.data_path, h, channel, appliance_name)

        if debug:
            print("    mains_df:")
            print(mains_df.head())
            plt.plot(mains_df.index, mains_df['aggregate'])
            plt.show()

        if debug:
            print("app_df:")
            print(app_df.head())
            plt.plot(app_df.index, app_df[appliance_name])
            plt.show()

        # the timestamps of mains and appliance are not the same, we need to align them
        df_align = align_meters(mains_df, app_df, sample_seconds)
        df_align.reset_index(drop=True, inplace=True)

        del mains_df, app_df

        if debug:
            # plot the dtaset
            print("df_align:")
            print(df_align.head())
            plt.plot(df_align['aggregate'].values)
            plt.plot(df_align[appliance_name].values)
            plt.show()

        # Normilization ----------------------------------------------------------------------------------------------
        mean = params_appliance[appliance_name]['mean']
        std = params_appliance[appliance_name]['std']

        df_align['aggregate'] = (df_align['aggregate'] - args.aggregate_mean) / args.aggregate_std
        df_align[appliance_name] = (df_align[appliance_name] - mean) / std

        if h == params_appliance[appliance_name]['test_build']:
            # Test CSV
            df_align.to_csv(args.save_path + appliance_name + '_test_.csv', mode='a', index=False, header=False)
            print("    Size of test set is {:.4f} M rows.".format(len(df_align) / 10 ** 6))
            continue

        train_dfs.append(df_align)
        del df_align

    train = pd.concat(train_dfs, ignore_index=True)
    del train_dfs

    # Crop dataset
    if training_building_percent != 0:
        train.drop(train.index[-int((len(train)/100)*training_building_percent):], inplace=True)


    # Validation CSV
    val_len = int((len(train)/100)*validation_percent)
    val = train.tail(val_len)
    val.reset_index(drop=True, inplace=True)
    train.drop(train.index[-val_len:], inplace=True)
    # Validation CSV
    val.to_csv(args.save_path + appliance_name + '_validation_' + '.csv', mode='a', index=False, header=False)

    # Training CSV
    train.to_csv(args.save_path + appliance_name + '_training_.csv', mode='a', index=False, header=False)

    print("    Size of total training set is {:.4f} M rows.".format(len(train) / 10 ** 6))
    print("    Size of total validation set is {:.4f} M rows.".format(len(val) / 10 ** 6))
    del train, val


    print("\nPlease find files in: " + args.save_path)
    print("Total elapsed time: {:.2f} min.".format((time.time() - start_time) / 60))


if __name__ == '__main__':
    main()
