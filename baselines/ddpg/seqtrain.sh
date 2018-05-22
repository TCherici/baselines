#!/bin/bash

logdirpath='/home/tcherici/seqruns_standup_070518'

for num in $(seq 0 2)
do
    for env in 'HumanoidStandup-v2'
    do
        for aux in '' 'tc' 'prop' 'caus' 'repeat' 'predict'
        do
            LOG_DIR="$logdirpath/$env""_$aux""_seed$num"
            echo "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            echo $LOG_DIR
            python -m baselines.ddpg.main --env-id $env --aux-tasks $aux --log-dir $LOG_DIR --seed $num
        done
        LOG_DIR="$logdirpath/$env""_all_seed$num"
        echo "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        echo $LOG_DIR
        python -m baselines.ddpg.main --env-id $env --aux-tasks 'tc' 'prop' 'caus' 'repeat' --log-dir $LOG_DIR --seed $num
    done
done
