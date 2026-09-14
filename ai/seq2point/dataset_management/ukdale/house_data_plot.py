import os
import matplotlib.pyplot as plt
plt.rcParams.update({'font.size': 13})
from ukdale_parameters import params_appliance
from ukdale_h5 import load_meter


appliance_name = 'washingmachine'
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ukdale.h5')

# Change house number from here
building = 1

channel = params_appliance[appliance_name]['channels'][params_appliance[appliance_name]['houses'].index(building)]

# Number of sample to plot
chunksize = 10**3

# The appliance meter may start later than the mains, so plot the mains over the
# same time range as the first chunksize appliance readings
df = load_meter(path, building, channel, appliance_name, nrows=chunksize)
agg_df = load_meter(path, building, 1, 'aggregate', start=df.index[0], end=df.index[-1])

fig = plt.figure(num='Figure {:}'.format(appliance_name))
ax1 = fig.add_subplot(111)

ax1.plot(agg_df.index, agg_df['aggregate'])
ax1.plot(df.index, df[appliance_name])

ax1.set_title('building{:}/elec/meter{:}'.format(building, channel), fontsize=14, fontweight='bold')
ax1.set_ylabel('Power [W]')
ax1.set_xlabel('time (UTC)')
ax1.legend(['aggregate', 'appliance'])
#ax1.grid()

plt.show()
