#!/bin/bash
for num in $(seq 1 2)
do
    for env in 'Humanoid-v2'
    do

        for aux in 'repeat' 'prop' 'caus' '' 'tc'
        do
            LOG_DIR="/home/tcherici/seqrunsnorm/$env""_$aux""_seed$num"
            echo $LOG_DIR
            python -m baselines.ddpg.main --env-id $env --aux-tasks $aux --log-dir $LOG_DIR --seed $num
        done
        LOG_DIR="/home/tcherici/seqrunsnorm/$env""_all_seed$num"
        echo $LOG_DIR
        python -m baselines.ddpg.main --env-id $env --aux-tasks 'tc' 'prop' 'caus' 'repeat' --log-dir $LOG_DIR --seed $num
    done
done
