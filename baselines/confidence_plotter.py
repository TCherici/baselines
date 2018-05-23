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
COLORS = ['black', 'blue', 'red', 'lime', 'cyan', 'orange', 'lavender', 'magenta', 'yellow', 'coral','purple', 'pink',
        'brown', 'teal', 'green', 'lightblue',  'turquoise',
        'darkgreen', 'tan', 'salmon', 'gold', 'lightpurple', 'darkred', 'darkblue']

def mean_confidence_interval(data, confidence=0.68):
    a = 1.0*np.array(data)
    n = len(a)
    m, se = np.mean(a,axis=0), scipy.stats.sem(a)
    print(se.shape)
    h = se * scipy.stats.t.ppf((1+confidence)/2., n-1)
    return m, m-h, m+h

def get_data(maindir, keyword):
    # read maindir and get a list of all runs with keyword
    fulldirlist = os.listdir(maindir) 
    rewards_list = []
    for datadir in fulldirlist:
        if keyword in datadir: 
            print(datadir)
            csvpath = maindir+"/"+datadir+"/progress.csv"
            print(csvpath)
            with open(csvpath) as f:
                reader = csv.reader(f)
                try:
                    labels = next(reader)
                except:
                    print('!!empty .csv file!!')
                    continue
                steps = []
                rewards = []
                for ind in range(len(labels)):
                    if labels[ind] == 'total/steps':
                        stepsind = ind
                        print("ind:{} -- label:{}".format(ind, labels[ind]))
                        f.seek(0)
                        for row in reader:
                            steps.append(row[stepsind])
                        steps = steps[1:]
                            
                            
                    if labels[ind] == 'rollout/return_history':
                        rewardind = ind
                        print("ind:{} -- label:{}".format(ind, labels[ind]))
                        f.seek(0)
                        for row in reader:
                            rewards.append(row[rewardind])
                        rewards = np.array(rewards[1:]).astype(float)
                        rewards_list.append(np.array(rewards))
    
    return steps, rewards_list


def lineplotCIgroups(maindir, keywords, x_label='steps (thousands)', y_label='reward distribution', title='no title'):
    # Create the plot object
    _, ax = plt.subplots()
    
    for ind,keyword in enumerate(keywords): 
        print('keyword:'+keyword)
        indexes, data = get_data(maindir, keyword)
        med,low,high = mean_confidence_interval(data)
        
        indexes = np.array(indexes).astype(float)/1000
    
        # Plot the data, set the linewidth, color and transparency of the
        # line, provide a label for the legend
        line, = ax.plot(indexes, med, lw = 1, color = COLORS[ind], alpha = 1)
        label = 'no aux' if keyword=='__' else keyword
        line.set_label(label)
        print(ind)
        # Shade the confidence interval
        ax.fill_between(indexes, low, high, color = COLORS[ind], alpha = 0.4)
        
    # Display legend
    ax.legend(loc = 'best')
        
    # Label the axes and provide a title
    ax.set_title(title)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)

def main():
    import argparse
    import os
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--maindir', help='parent folder for runs', default='/home/tcherici/seqrunsnorm5_020418')
    parser.add_argument('-k','--keywords', action='append', help='list of keywords for runs selection (e.g. caus)')
    parser.add_argument('--xaxis', help = 'Varible on X-axis', default = X_TIMESTEPS)
    parser.add_argument('--plot_title', help = 'Title of plot', default = 'no title')
    args = parser.parse_args()
    
    lineplotCIgroups(args.maindir, args.keywords, x_label=args.xaxis, title=args.plot_title)
    
    plt.show()

if __name__ == '__main__':
    main()
