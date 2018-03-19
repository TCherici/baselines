#!/bin/bash
for num in $(seq 1 3)
do
    for env in 'InvertedPendulum-v2' 'HalfCheetah-v2' 'Humanoid-v2'
    do

        for aux in '' 'tc' 'prop' 'caus' 'repeat'
        do
            LOG_DIR="/home/tcherici/seqruns/$env""_$aux""_$num"
            echo $LOG_DIR
            python -m baselines.ddpg.main --env-id $env --aux-tasks $aux --log-dir $LOG_DIR --seed $num
        done
        LOG_DIR="/home/tcherici/seqruns/$env""_all_$num"
        echo $LOG_DIR
        python -m baselines.ddpg.main --env-id $env --aux-tasks 'tc' 'prop' 'caus' 'repeat' --log-dir $LOG_DIR --seed $num
    done
done
