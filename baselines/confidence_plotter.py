import numpy as np
import matplotlib
import csv
matplotlib.use('TkAgg') # Can change to 'Agg' for non-interactive mode

import matplotlib.pyplot as plt
plt.rcParams['svg.fonttype'] = 'none'

import numpy as np
import scipy.stats
import os

from baselines.bench.monitor import load_results
    
X_TIMESTEPS = 'timesteps'
X_EPISODES = 'episodes'
X_WALLTIME = 'walltime_hrs'
POSSIBLE_X_AXES = [X_TIMESTEPS, X_EPISODES, X_WALLTIME]
EPISODES_WINDOW = 100
COLORS = ['blue', 'green', 'red', 'cyan', 'magenta', 'yellow', 'black', 'purple', 'pink',
        'brown', 'orange', 'teal', 'coral', 'lightblue', 'lime', 'lavender', 'turquoise',
        'darkgreen', 'tan', 'salmon', 'gold', 'lightpurple', 'darkred', 'darkblue']

def mean_confidence_interval(data, confidence=0.95):
    a = 1.0*np.array(data)
    print(a.shape)
    n = len(a)
    m, se = np.mean(a), scipy.stats.sem(a)
    h = se * sp.stats.t.ppf((1+confidence)/2., n-1)
    return m, m-h, m+h

def get_data(maindir, keyword, num_timesteps, xaxis, plot_title):
    # read maindir and get a list of all runs with keyword
    fulldirlist = os.listdir(maindir) 
    rewards = []
    for datadir in fulldirlist:
        if keyword in datadir: 
            print(datadir)
            csvpath = maindir+"/"+datadir+"/progress.csv"
            print(csvpath)
            with open(csvpath) as f:
                reader = csv.reader(f)
                labels = next(reader)
                steps = []
                rewards = []
                for ind in range(len(labels)):
                    if labels[ind] == 'total/steps':
                        stepsind = ind
                        print("ind:{} -- label:{}".format(ind, labels[ind]))
                        f.seek(0)
                        for row in reader:
                            steps.append(row[stepsind])
                            
                            
                    if labels[ind] == 'rollout/return_history':
                        rewardind = ind
                        print("ind:{} -- label:{}".format(ind, labels[ind]))
                        f.seek(0)
                        for row in reader:
                            rewards.append(row[rewardind])
    
    return steps, rewards


# Define a function for the line plot with intervals
def lineplotCI(x_data, y_data, sorted_x, low_CI, upper_CI, x_label, y_label, title, colorind=0):
    # Create the plot object
    _, ax = plt.subplots()

    # Plot the data, set the linewidth, color and transparency of the
    # line, provide a label for the legend
    ax.plot(x_data, y_data, lw = 1, color = COLORS[colorind], alpha = 1)
    # Shade the confidence interval
    ax.fill_between(sorted_x, low_CI, upper_CI, color = COLORS[colorind], alpha = 0.4)
    # Label the axes and provide a title
    ax.set_title(title)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)

    # Display legend
    ax.legend(loc = 'best')

def main():
    import argparse
    import os
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--maindir', help='parent folder for runs', default='/home/tcherici/seqrunsnorm5_020418')
    parser.add_argument('--keyword', type=str, help='keyword for runs selection (e.g. caus)', default='__')
    parser.add_argument('--num_timesteps', type=int, default=int(10e6))
    parser.add_argument('--xaxis', help = 'Varible on X-axis', default = X_TIMESTEPS)
    parser.add_argument('--plot_title', help = 'Title of plot', default = 'no title')
    args = parser.parse_args()
    index, data = get_data(args.maindir, args.keyword, args.num_timesteps, args.xaxis, args.plot_title)
    
    med,low,high = mean_confidence_interval(data)
    lineplotCI(index, med, index, low, high, xlabel='epoch', ylabel='base')
    
    plt.show()

if __name__ == '__main__':
    main()
