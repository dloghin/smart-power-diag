import numpy as np
import pandas as pd
import tables


def load_meter(h5_path, building, meter, col_name='power', start=None, end=None, nrows=None):
    """Load one meter from the UK-DALE HDF5 file (NILMTK format, e.g. ukdale.h5).

    The file is read with PyTables directly: it is Blosc-compressed and its pandas
    metadata was written by Python 2, which recent pandas versions cannot parse.

    Parameters:
    h5_path (string): Path to ukdale.h5.
    building (int): House number.
    meter (int): Meter number, the same as the channel number of the raw .dat files
        (meter 1 is the 6-second whole-house mains).
    col_name (string): Name of the returned power column.
    start, end (timestamp-like): Optional UTC time range [start, end] to load.
    nrows (int): Optional maximum number of readings to load.

    Returns:
    pandas.DataFrame indexed by UTC timestamp ('time') with a single column col_name.

    """
    with tables.open_file(h5_path, mode='r') as h5:
        table = h5.get_node('/building{}/elec/meter{}/table'.format(building, meter))
        if start is None and end is None:
            data = table.read(stop=nrows)
        else:
            conditions = []
            condvars = {}
            if start is not None:
                conditions.append('(index >= start)')
                condvars['start'] = pd.Timestamp(start).value
            if end is not None:
                conditions.append('(index <= end)')
                condvars['end'] = pd.Timestamp(end).value
            data = table.read_where(' & '.join(conditions), condvars)[:nrows]

    # The index holds nanoseconds since the epoch (UTC); for multi-column meters
    # (the 1-second sound card mains) the first column is active power.
    index = pd.DatetimeIndex(data['index'].astype('datetime64[ns]'), name='time')
    return pd.DataFrame({col_name: data['values_block_0'][:, 0].astype(np.float64)}, index=index)


def align_meters(mains_df, app_df, sample_seconds=8):
    """Align mains and appliance readings, whose timestamps differ, on a common time grid.

    Readings are averaged into sample_seconds bins, an empty bin is back-filled from the
    next bin (at most one) and bins still missing either reading are dropped.

    """
    freq = '{}s'.format(sample_seconds)
    df_align = mains_df.resample(freq).mean().join(app_df.resample(freq).mean(), how='outer')
    return df_align.asfreq(freq).bfill(limit=1).dropna()
