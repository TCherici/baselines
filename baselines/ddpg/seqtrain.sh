#!/bin/bash

logdirpath='/home/tcherici/seqrunsnorm2'

for num in $(seq 1 3)
do
    for env in 'Humanoid-v2'
    do

        for aux in '' 'tc' 'prop' 'caus' 'repeat'
        do
            LOG_DIR="$logdirpath/$env""_$aux""_seed$num"
            echo $LOG_DIR
            python -m baselines.ddpg.main --env-id $env --aux-tasks $aux --log-dir $LOG_DIR --seed $num
        done
        LOG_DIR="$logdirpath/$env""_all_seed$num"
        echo $LOG_DIR
        python -m baselines.ddpg.main --env-id $env --aux-tasks 'tc' 'prop' 'caus' 'repeat' --log-dir $LOG_DIR --seed $num
    done
done