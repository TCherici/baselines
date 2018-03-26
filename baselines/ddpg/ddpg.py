from copy import copy, deepcopy
from functools import reduce

import numpy as np
import tensorflow as tf
import tensorflow.contrib as tc

from baselines import logger
from baselines.common.mpi_adam import MpiAdam
import baselines.common.tf_util as U
from baselines.common.mpi_running_mean_std import RunningMeanStd
from baselines.ddpg.models import Representation
from mpi4py import MPI

def normalize(x, stats):
    if stats is None:
        return x
    return (x - stats.mean) / stats.std


def denormalize(x, stats):
    if stats is None:
        return x
    return x * stats.std + stats.mean

def reduce_std(x, axis=None, keepdims=False):
    return tf.sqrt(reduce_var(x, axis=axis, keepdims=keepdims))

def reduce_var(x, axis=None, keepdims=False):
    m = tf.reduce_mean(x, axis=axis, keep_dims=True)
    devs_squared = tf.square(x - m)
    return tf.reduce_mean(devs_squared, axis=axis, keep_dims=keepdims)

def get_target_updates(vars, target_vars, tau):
    logger.info('setting up target updates ...')
    soft_updates = []
    init_updates = []
    assert len(vars) == len(target_vars)
    for var, target_var in zip(vars, target_vars):
        logger.info('  {} <- {}'.format(target_var.name, var.name))
        init_updates.append(tf.assign(target_var, var))
        soft_updates.append(tf.assign(target_var, (1. - tau) * target_var + tau * var))
    assert len(init_updates) == len(vars)
    assert len(soft_updates) == len(vars)
    return tf.group(*init_updates), tf.group(*soft_updates)


def get_perturbed_actor_updates(actor, perturbed_actor, param_noise_stddev):
    assert len(actor.vars) == len(perturbed_actor.vars)
    assert len(actor.perturbable_vars) == len(perturbed_actor.perturbable_vars)

    updates = []
    for var, perturbed_var in zip(actor.vars, perturbed_actor.vars):
        if var in actor.perturbable_vars:
            logger.info('  {} <- {} + noise'.format(perturbed_var.name, var.name))
            updates.append(tf.assign(perturbed_var, var + tf.random_normal(tf.shape(var), mean=0., stddev=param_noise_stddev)))
        else:
            logger.info('  {} <- {}'.format(perturbed_var.name, var.name))
            updates.append(tf.assign(perturbed_var, var))
    assert len(updates) == len(actor.vars)
    return tf.group(*updates)
    
def normalize_loss(loss):
    normloss = loss/(tf.stop_gradient(tf.abs(loss))+1e-9)
    return normloss


class DDPG(object):
    def __init__(self, actor, critic, memory, observation_shape, action_shape, param_noise=None, action_noise=None,
        gamma=0.99, tau=0.001, normalize_returns=False, enable_popart=False, normalize_observations=True,
        batch_size=128, observation_range=(-5., 5.), action_range=(-1., 1.), return_range=(-np.inf, np.inf),
        adaptive_param_noise=True, adaptive_param_noise_policy_threshold=.1,
        critic_l2_reg=0., actor_lr=1e-4, critic_lr=1e-3, clip_norm=None, reward_scale=1.,
        aux_apply='both', aux_tasks=[], aux_lambdas={}):
        # Inputs.
        self.obs0 = tf.placeholder(tf.float32, shape=(None,) + observation_shape, name='obs0')
        self.obs1 = tf.placeholder(tf.float32, shape=(None,) + observation_shape, name='obs1')
        self.terminals1 = tf.placeholder(tf.float32, shape=(None, 1), name='terminals1')
        self.rewards = tf.placeholder(tf.float32, shape=(None, 1), name='rewards')
        self.actions = tf.placeholder(tf.float32, shape=(None,) + action_shape, name='actions')
        self.critic_target = tf.placeholder(tf.float32, shape=(None, 1), name='critic_target')
        self.param_noise_stddev = tf.placeholder(tf.float32, shape=(), name='param_noise_stddev')
        

        # Parameters.
        self.gamma = gamma
        self.tau = tau
        self.memory = memory
        self.normalize_observations = normalize_observations
        self.normalize_returns = normalize_returns
        self.action_noise = action_noise
        self.param_noise = param_noise
        self.action_range = action_range
        self.return_range = return_range
        self.observation_range = observation_range
        self.critic = critic
        self.actor = actor
        self.actor_lr = actor_lr
        self.critic_lr = critic_lr
        self.clip_norm = clip_norm
        self.enable_popart = enable_popart
        self.reward_scale = reward_scale
        self.batch_size = batch_size
        self.stats_sample = None
        self.critic_l2_reg = critic_l2_reg

        # Observation normalization.
        if self.normalize_observations:
            with tf.variable_scope('obs_rms'):
                self.obs_rms = RunningMeanStd(shape=observation_shape)
        else:
            self.obs_rms = None
        
        self.norm_obs0 = tf.clip_by_value(normalize(self.obs0, self.obs_rms),
            self.observation_range[0], self.observation_range[1])
        self.norm_obs1 = tf.clip_by_value(normalize(self.obs1, self.obs_rms),
            self.observation_range[0], self.observation_range[1])

        # Return normalization.
        if self.normalize_returns:
            with tf.variable_scope('ret_rms'):
                self.ret_rms = RunningMeanStd()
        else:
            self.ret_rms = None
        
        # Aux Inputs.        
        self.aux_apply = aux_apply
        self.aux_tasks = aux_tasks
        self.aux_lambdas = aux_lambdas

        if 'prop' in self.aux_tasks or 'caus' in self.aux_tasks or 'repeat' in self.aux_tasks:
            self.obs100 = tf.placeholder(tf.float32, shape=(None,) + observation_shape, name='obs100')
            self.obs101 = tf.placeholder(tf.float32, shape=(None,) + observation_shape, name='obs101')
            self.actions100 = tf.placeholder(tf.float32, shape=(None,) + action_shape, name='actions100')
            self.norm_obs100 = tf.clip_by_value(normalize(self.obs100, self.obs_rms),
            self.observation_range[0], self.observation_range[1])
            self.norm_obs101 = tf.clip_by_value(normalize(self.obs101, self.obs_rms),
            self.observation_range[0], self.observation_range[1])
        if 'caus' in self.aux_tasks:
            self.rewards100 = tf.placeholder(tf.float32, shape=(None, 1), name='rewards100')
        
        
        
        # Create target networks.
        target_actor = deepcopy(actor)
        target_actor.name = 'target_actor'
        target_actor.repr.name = 'target_actor_repr'
        self.target_actor = target_actor
        target_critic = deepcopy(critic)
        target_critic.name = 'target_critic'
        target_critic.repr.name = 'target_critic_repr'
        self.target_critic = target_critic
        
        # Create networks and core TF parts that are shared across setup parts.
        self.actor_tf = actor(self.norm_obs0)
        self.normalized_critic_tf = critic(self.norm_obs0, self.actions)
        self.critic_tf = denormalize(tf.clip_by_value(self.normalized_critic_tf, self.return_range[0], self.return_range[1]), self.ret_rms)
        self.normalized_critic_with_actor_tf = critic(self.norm_obs0, self.actor_tf, reuse=True)
        self.critic_with_actor_tf = denormalize(tf.clip_by_value(self.normalized_critic_with_actor_tf, self.return_range[0], self.return_range[1]), self.ret_rms)
        
        Q_obs1 = denormalize(target_critic(self.norm_obs1, target_actor(self.norm_obs1)), self.ret_rms)
        self.target_Q = self.rewards + (1. - self.terminals1) * gamma * Q_obs1
        
        # Set up parts.
        if self.param_noise is not None:
            self.setup_param_noise(self.norm_obs0)
        self.setup_actor_optimizer()
        self.setup_critic_optimizer()
        if self.normalize_returns and self.enable_popart:
            self.setup_popart()
        self.setup_stats()
        self.setup_target_network_updates()


        if self.aux_tasks:
            logger.info("aux_tasks:{}".format(self.aux_tasks))
            self.setup_aux_optimizer()
    
    def setup_aux_optimizer(self):
        logger.info('setting up aux optimizer for actor...')
        self.aux_ops = []
        self.aux_losses = tf.Variable(tf.zeros([], dtype=np.float32), name="loss")
        self.aux_vars = set([])
        if self.aux_apply is 'actor' or 'both':
            for auxtask in self.aux_tasks:
                logger.info('actor - aux task: {}'.format(auxtask))
                if auxtask == 'tc':
                    act_tc_repr = Representation(name=self.actor.repr.name, layer_norm=self.actor.layer_norm)
                    act_tc_repr0 = act_tc_repr(self.norm_obs0, reuse=True)
                    act_tc_repr1 = act_tc_repr(self.norm_obs1, reuse=True)
                    self.act_tc_loss = tf.nn.l2_loss(act_tc_repr1-act_tc_repr0) * self.aux_lambdas['tc']
                    self.aux_losses += normalize_loss(self.act_tc_loss)
                    self.aux_vars.update(set(act_tc_repr.trainable_vars))
                    
                elif auxtask == 'prop':
                    act_prop_repr = Representation(name=self.actor.repr.name, layer_norm=self.actor.layer_norm)
                    act_prop_repr0 = act_prop_repr(self.norm_obs0, reuse=True)
                    act_prop_repr1 = act_prop_repr(self.norm_obs1, reuse=True)
                    act_prop_repr100 = act_prop_repr(self.norm_obs100, reuse=True)
                    act_prop_repr101 = act_prop_repr(self.norm_obs101, reuse=True)
                    act_prop_dstatemag = tf.norm(tf.clip_by_value(act_prop_repr1-act_prop_repr0,1e-10,1e10),axis=1)
                    act_prop_dstatemag100 = tf.norm(tf.clip_by_value(act_prop_repr101-act_prop_repr100,1e-10,1e10),axis=1)
                    act_prop_dstatemagdiff = tf.square(act_prop_dstatemag100-act_prop_dstatemag)
                    act_prop_actionsimilarity = tf.exp(-tf.norm(tf.clip_by_value(self.actions100-self.actions,1e-10,1e10), axis=1))
                    self.act_prop_loss = tf.reduce_mean(tf.multiply(act_prop_dstatemagdiff,act_prop_actionsimilarity)) * self.aux_lambdas['prop']
                    self.aux_losses += normalize_loss(self.act_prop_loss)
                    self.aux_vars.update(set(act_prop_repr.trainable_vars))
                
                elif auxtask == 'caus':
                    act_caus_repr = Representation(name=self.actor.repr.name, layer_norm=self.actor.layer_norm)
                    act_caus_repr0 = act_caus_repr(self.norm_obs0, reuse=True)
                    act_caus_repr100 = act_caus_repr(self.norm_obs100, reuse=True)
                    act_caus_statesimilarity = tf.exp(-tf.square(act_caus_repr100-act_caus_repr0))
                    act_caus_actionsimilarity = tf.exp(-tf.norm(tf.clip_by_value(self.actions100-self.actions,1e-10,1e10), axis=1))
                    act_caus_rewarddiff = tf.square(self.rewards100-self.rewards)
                    self.act_caus_loss = tf.reduce_mean(tf.multiply(act_caus_statesimilarity,tf.multiply(act_caus_actionsimilarity,act_caus_rewarddiff))) * self.aux_lambdas['caus']
                    self.aux_losses += normalize_loss(self.act_caus_loss)
                    self.aux_vars.update(set(act_caus_repr.trainable_vars))
                
                elif auxtask == 'repeat':
                    act_repeat_repr = Representation(name=self.actor.repr.name, layer_norm=self.actor.layer_norm)
                    act_repeat_repr0 = act_repeat_repr(self.norm_obs0, reuse=True)
                    act_repeat_repr1 = act_repeat_repr(self.norm_obs1, reuse=True)
                    act_repeat_repr100 = act_repeat_repr(self.norm_obs100, reuse=True)
                    act_repeat_repr101 = act_repeat_repr(self.norm_obs101, reuse=True)
                    act_repeat_ds = act_repeat_repr1-act_repeat_repr0
                    act_repeat_ds100 = act_repeat_repr101-act_repeat_repr100
                    act_repeat_statesimilarity = tf.exp(-tf.norm(tf.clip_by_value(act_repeat_repr100-act_repeat_repr0,1e-10,1e10),axis=1))
                    
                    act_repeat_dstatediff = tf.square(act_repeat_ds100-act_repeat_ds)
                    act_repeat_actionsimilarity = tf.exp(-tf.norm(tf.clip_by_value(self.actions100-self.actions,1e-10,1e10),axis=1))
                    self.act_repeat_loss = tf.reduce_mean(tf.multiply(act_repeat_statesimilarity,tf.multiply(act_repeat_dstatediff,act_repeat_actionsimilarity))) * self.aux_lambdas['repeat']
                    self.aux_losses += normalize_loss(self.act_repeat_loss)
                    self.aux_vars.update(set(act_repeat_repr.trainable_vars))
                
                elif auxtask == 'predict':
                    act_pred = Predictor(name=self.actor.repr.name, layer_norm=self.actor.layer_norm)
                    act_pred_reconstruction = act_pred(self.norm_obs0, self.actions, reuse=True)
                    self.act_pred_loss = tf.nn.l2_loss(act_pred_reconstruction-self.norm_obs1)
                    self.aux_losses += normalize_loss(self.act_pred_loss)
                    self.aux_vars.update(set(act_pred.trainable_vars))
                
                else:
                    raise ValueError('task {} not recognized'.format(auxtask))
                
                

        if self.aux_apply == 'critic' or self.aux_apply == 'both':
            for auxtask in self.aux_tasks:
                logger.info('critic - aux task: ' + auxtask)
                if auxtask == 'tc':
                    cri_tc_repr = Representation(name=self.critic.repr.name, layer_norm=self.actor.layer_norm)
                    cri_repr0 = cri_tc_repr(self.norm_obs0, reuse=True)
                    cri_repr1 = cri_tc_repr(self.norm_obs1, reuse=True)
                    self.cri_tc_loss = tf.nn.l2_loss(cri_repr1-cri_repr0) * self.aux_lambdas['tc']
                    self.aux_losses += normalize_loss(self.cri_tc_loss)
                    self.aux_vars.update(set(cri_tc_repr.trainable_vars))
                
                elif auxtask == 'prop':
                    cri_prop_repr = Representation(name=self.critic.repr.name, layer_norm=self.critic.layer_norm)
                    cri_prop_repr0 = cri_prop_repr(self.norm_obs0, reuse=True)
                    cri_prop_repr1 = cri_prop_repr(self.norm_obs1, reuse=True)
                    cri_prop_repr100 = cri_prop_repr(self.norm_obs100, reuse=True)
                    cri_prop_repr101 = cri_prop_repr(self.norm_obs101, reuse=True)
                    cri_prop_dstatemag = tf.norm(tf.clip_by_value(cri_prop_repr1-cri_prop_repr0,1e-10,1e10),axis=1)
                    cri_prop_dstatemag100 = tf.norm(tf.clip_by_value(cri_prop_repr101-cri_prop_repr100,1e-10,1e10),axis=1)
                    cri_prop_dstatemagdiff = tf.square(cri_prop_dstatemag100-cri_prop_dstatemag)
                    cri_prop_actionsimilarity = tf.exp(-tf.norm(tf.clip_by_value(self.actions100-self.actions,1e-10,1e10), axis=1))
                    self.cri_prop_loss = tf.reduce_mean(tf.multiply(cri_prop_dstatemagdiff,cri_prop_actionsimilarity)) * self.aux_lambdas['prop']
                    self.aux_losses += normalize_loss(self.cri_prop_loss)
                    self.aux_vars.update(set(cri_prop_repr.trainable_vars))
                
                elif auxtask == 'caus':
                    cri_caus_repr = Representation(name=self.critic.repr.name, layer_norm=self.critic.layer_norm)
                    cri_caus_repr0 = cri_caus_repr(self.norm_obs0, reuse=True)
                    cri_caus_repr100 = cri_caus_repr(self.norm_obs100, reuse=True)
                    cri_caus_statesimilarity = tf.exp(-tf.square(cri_caus_repr100-cri_caus_repr0))
                    cri_caus_actionsimilarity = tf.exp(-tf.norm(tf.clip_by_value(self.actions100-self.actions,1e-10,1e10), axis=1))
                    cri_caus_rewarddiff = tf.square(self.rewards100-self.rewards)
                    self.cri_caus_loss = tf.reduce_mean(tf.multiply(cri_caus_statesimilarity,tf.multiply(cri_caus_actionsimilarity,cri_caus_rewarddiff))) * self.aux_lambdas['caus']
                    self.aux_losses += normalize_loss(self.cri_caus_loss)
                    self.aux_vars.update(set(cri_caus_repr.trainable_vars))
                                    
                elif auxtask == 'repeat':
                    cri_repeat_repr = Representation(name=self.critic.repr.name, layer_norm=self.critic.layer_norm)
                    cri_repeat_repr0 = cri_repeat_repr(self.norm_obs0, reuse=True)
                    cri_repeat_repr1 = cri_repeat_repr(self.norm_obs1, reuse=True)
                    cri_repeat_repr100 = cri_repeat_repr(self.norm_obs100, reuse=True)
                    cri_repeat_repr101 = cri_repeat_repr(self.norm_obs101, reuse=True)
                    cri_repeat_ds = cri_repeat_repr1-cri_repeat_repr0
                    cri_repeat_ds100 = cri_repeat_repr101-cri_repeat_repr100
                    cri_repeat_statesimilarity = tf.exp(-tf.norm(tf.clip_by_value(cri_repeat_repr100-cri_repeat_repr0,1e-10,1e10), axis=1))
                    cri_repeat_dstatediff = tf.square(cri_repeat_ds100-cri_repeat_ds)
                    cri_repeat_actionsimilarity = tf.exp(-tf.norm(tf.clip_by_value(self.actions100-self.actions,1e-10,1e10), axis=1))
                    self.cri_repeat_loss = tf.reduce_mean(tf.multiply(cri_repeat_statesimilarity,tf.multiply(cri_repeat_dstatediff,cri_repeat_actionsimilarity))) * self.aux_lambdas['repeat']
                    self.aux_losses += normalize_loss(self.cri_repeat_loss)
                    self.aux_vars.update(set(cri_repeat_repr.trainable_vars))
                    
                elif auxtask == 'predict':
                    cri_pred = Predictor(name=self.critic.repr.name, layer_norm=self.critic.layer_norm)
                    cri_pred_reconstruction = cri_pred(self.norm_obs0, self.actions, reuse=True)
                    self.cri_pred_loss = tf.nn.l2_loss(cri_pred_reconstruction-self.norm_obs1)
                    self.aux_losses += normalize_loss(self.cri_pred_loss)
                    self.aux_vars.update(set(cri_pred.trainable_vars))
                else:
                    raise ValueError('task {} not recognized'.format(auxtask))
                
        self.aux_losses = self.aux_losses / (2 * len(self.aux_tasks))
        self.aux_vars = list(self.aux_vars)
        self.aux_grads = U.flatgrad(self.aux_losses, self.aux_vars, clip_norm=self.clip_norm)
        self.aux_optimizer = MpiAdam(var_list=self.aux_vars,
                           beta1=0.9, beta2=0.999, epsilon=1e-08)

    def setup_target_network_updates(self):
        actor_init_updates, actor_soft_updates = get_target_updates(self.actor.vars, self.target_actor.vars, self.tau)
        critic_init_updates, critic_soft_updates = get_target_updates(self.critic.vars, self.target_critic.vars, self.tau)
        self.target_init_updates = [actor_init_updates, critic_init_updates]
        self.target_soft_updates = [actor_soft_updates, critic_soft_updates]

    def setup_param_noise(self, normalized_obs0):
        assert self.param_noise is not None

        # Configure perturbed actor.
        param_noise_actor = copy(self.actor)
        param_noise_actor.name = 'param_noise_actor'
        param_noise_actor.repr.name = 'param_noise_actor_repr'
        self.perturbed_actor_tf = param_noise_actor(normalized_obs0)
        logger.info('setting up param noise')
        self.perturb_policy_ops = get_perturbed_actor_updates(self.actor, param_noise_actor, self.param_noise_stddev)

        # Configure separate copy for stddev adoption.
        adaptive_param_noise_actor = copy(self.actor)
        adaptive_param_noise_actor.name = 'adaptive_param_noise_actor'
        adaptive_param_noise_actor.repr.name = 'adaptive_param_noise_actor_repr'
        adaptive_actor_tf = adaptive_param_noise_actor(normalized_obs0)
        self.perturb_adaptive_policy_ops = get_perturbed_actor_updates(self.actor, adaptive_param_noise_actor, self.param_noise_stddev)
        self.adaptive_policy_distance = tf.sqrt(tf.reduce_mean(tf.square(self.actor_tf - adaptive_actor_tf)))

    def setup_actor_optimizer(self):
        logger.info('setting up actor optimizer')
        self.actor_loss = -tf.reduce_mean(self.critic_with_actor_tf)
        actor_shapes = [var.get_shape().as_list() for var in self.actor.trainable_vars]
        actor_nb_params = sum([reduce(lambda x, y: x * y, shape) for shape in actor_shapes])
        logger.info('  actor shapes: {}'.format(actor_shapes))
        logger.info('  actor params: {}'.format(actor_nb_params))
        self.actor_grads = U.flatgrad(normalize_loss(self.actor_loss), self.actor.trainable_vars, clip_norm=self.clip_norm)
        self.actor_optimizer = MpiAdam(var_list=self.actor.trainable_vars,
            beta1=0.9, beta2=0.999, epsilon=1e-08)

    def setup_critic_optimizer(self):
        logger.info('setting up critic optimizer')
        normalized_critic_target_tf = tf.clip_by_value(normalize(self.critic_target, self.ret_rms), self.return_range[0], self.return_range[1])
        self.critic_loss = tf.reduce_mean(tf.square(self.normalized_critic_tf - normalized_critic_target_tf))        
        if self.critic_l2_reg > 0.:
            critic_reg_vars = [var for var in self.critic.trainable_vars if 'kernel' in var.name and 'output' not in var.name]
            for var in critic_reg_vars:
                logger.info('  regularizing: {}'.format(var.name))
            logger.info('  applying l2 regularization with {}'.format(self.critic_l2_reg))
            critic_reg = tc.layers.apply_regularization(
                tc.layers.l2_regularizer(self.critic_l2_reg),
                weights_list=critic_reg_vars
            )
            self.critic_loss += critic_reg
            
        critic_shapes = [var.get_shape().as_list() for var in self.critic.trainable_vars]
        critic_nb_params = sum([reduce(lambda x, y: x * y, shape) for shape in critic_shapes])
        logger.info('  critic shapes: {}'.format(critic_shapes))
        logger.info('  critic params: {}'.format(critic_nb_params))
        self.critic_grads = U.flatgrad(normalize_loss(self.critic_loss), self.critic.trainable_vars, clip_norm=self.clip_norm)
        self.critic_optimizer = MpiAdam(var_list=self.critic.trainable_vars,
            beta1=0.9, beta2=0.999, epsilon=1e-08)

    def setup_popart(self):
        # See https://arxiv.org/pdf/1602.07714.pdf for details.
        self.old_std = tf.placeholder(tf.float32, shape=[1], name='old_std')
        new_std = self.ret_rms.std
        self.old_mean = tf.placeholder(tf.float32, shape=[1], name='old_mean')
        new_mean = self.ret_rms.mean

        self.renormalize_Q_outputs_op = []
        for vs in [self.critic.output_vars, self.target_critic.output_vars]:
            assert len(vs) == 2
            M, b = vs
            assert 'kernel' in M.name
            assert 'bias' in b.name
            assert M.get_shape()[-1] == 1
            assert b.get_shape()[-1] == 1
            self.renormalize_Q_outputs_op += [M.assign(M * self.old_std / new_std)]
            self.renormalize_Q_outputs_op += [b.assign((b * self.old_std + self.old_mean - new_mean) / new_std)]

    def setup_stats(self):
        ops = []
        names = []

        if self.normalize_returns:
            ops += [self.ret_rms.mean, self.ret_rms.std]
            names += ['ret_rms_mean', 'ret_rms_std']

        if self.normalize_observations:
            ops += [tf.reduce_mean(self.obs_rms.mean), tf.reduce_mean(self.obs_rms.std)]
            names += ['obs_rms_mean', 'obs_rms_std']

        ops += [tf.reduce_mean(self.critic_tf)]
        names += ['reference_Q_mean']
        ops += [reduce_std(self.critic_tf)]
        names += ['reference_Q_std']

        ops += [tf.reduce_mean(self.critic_with_actor_tf)]
        names += ['reference_actor_Q_mean']
        ops += [reduce_std(self.critic_with_actor_tf)]
        names += ['reference_actor_Q_std']

        ops += [tf.reduce_mean(self.actor_tf)]
        names += ['reference_action_mean']
        ops += [reduce_std(self.actor_tf)]
        names += ['reference_action_std']

        if self.param_noise:
            ops += [tf.reduce_mean(self.perturbed_actor_tf)]
            names += ['reference_perturbed_action_mean']
            ops += [reduce_std(self.perturbed_actor_tf)]
            names += ['reference_perturbed_action_std']

        self.stats_ops = ops
        self.stats_names = names

    def pi(self, obs, apply_noise=True, compute_Q=True):
        if self.param_noise is not None and apply_noise:
            actor_tf = self.perturbed_actor_tf
        else:
            actor_tf = self.actor_tf
        feed_dict = {self.obs0: [obs]}
        if compute_Q:
            action, q = self.sess.run([actor_tf, self.critic_with_actor_tf], feed_dict=feed_dict)
        else:
            action = self.sess.run(actor_tf, feed_dict=feed_dict)
            q = None
        action = action.flatten()
        if self.action_noise is not None and apply_noise:
            noise = self.action_noise()
            assert noise.shape == action.shape
            action += noise
        action = np.clip(action, self.action_range[0], self.action_range[1])
        return action, q

    def store_transition(self, obs0, action, reward, obs1, terminal1):
        reward *= self.reward_scale
        self.memory.append(obs0, action, reward, obs1, terminal1)
        if self.normalize_observations:
            self.obs_rms.update(np.array([obs0]))

    def train(self):
        # Get a batch.
        if self.aux_tasks is not None:
            batch = self.memory.sampletwice(batch_size=self.batch_size)
        else:
            batch = self.memory.sample(batch_size=self.batch_size)
        

        if self.normalize_returns and self.enable_popart:
            old_mean, old_std, target_Q = self.sess.run([self.ret_rms.mean, self.ret_rms.std, self.target_Q], feed_dict={
                self.obs1: batch['obs1'],
                self.rewards: batch['rewards'],
                self.terminals1: batch['terminals1'].astype('float32'),
            })
            self.ret_rms.update(target_Q.flatten())
            self.sess.run(self.renormalize_Q_outputs_op, feed_dict={
                self.old_std : np.array([old_std]),
                self.old_mean : np.array([old_mean]),
            })

            # Run sanity check. Disabled by default since it slows down things considerably.
            # print('running sanity check')
            # target_Q_new, new_mean, new_std = self.sess.run([self.target_Q, self.ret_rms.mean, self.ret_rms.std], feed_dict={
            #     self.obs1: batch['obs1'],
            #     self.rewards: batch['rewards'],
            #     self.terminals1: batch['terminals1'].astype('float32'),
            # })
            # print(target_Q_new, target_Q, new_mean, new_std)
            # assert (np.abs(target_Q - target_Q_new) < 1e-3).all()
        else:
            target_Q = self.sess.run(self.target_Q, feed_dict={
                self.obs1: batch['obs1'],
                self.rewards: batch['rewards'],
                self.terminals1: batch['terminals1'].astype('float32'),
            })

        # Get gradients DDPG
        ops = [self.actor_grads, self.actor_loss, self.critic_grads, self.critic_loss]
        feed_dict = { self.obs0: batch['obs0'], 
                      self.actions: batch['actions'], 
                      self.critic_target: target_Q}
        actor_grads, actor_loss, critic_grads, critic_loss = self.sess.run(ops, feed_dict=feed_dict)
        
        # Perform a synced update.
        self.actor_optimizer.update(actor_grads, stepsize=self.actor_lr)
        self.critic_optimizer.update(critic_grads, stepsize=self.critic_lr)
        
        auxoutputs = []
        # Get gradients AUX
        if self.aux_tasks:
            aux_dict = {}
            aux_ops = {'grads':self.aux_grads}
            for index, auxtask in enumerate(self.aux_tasks):
                if auxtask == 'tc':
                    aux_dict.update({
                        self.obs0: batch['obs0'],
                        self.obs1: batch['obs1']})
                    # add a tc loss for tensorboard
                    if self.aux_apply == 'actor' or self.aux_apply == 'both':
                        aux_ops.update({'tc':self.act_tc_loss})
                    elif self.aux_apply == 'critic':
                        aux_ops.update({'tc':self.cri_tc_loss})
                if auxtask == 'prop':
                    aux_dict.update({
                        self.obs0: batch['obs0'],
                        self.obs1: batch['obs1'],
                        self.obs100: batch['obs100'],
                        self.obs101: batch['obs101'],
                        self.actions: batch['actions'],
                        self.actions100: batch['actions100']})
                    # add a tc loss for tensorboard
                    if self.aux_apply == 'actor' or self.aux_apply == 'both':
                        aux_ops.update({'prop':self.act_prop_loss})
                    elif self.aux_apply == 'critic':
                        aux_ops.update({'prop':self.cri_prop_loss})
                if auxtask == 'caus':
                    aux_dict.update({
                        self.obs0: batch['obs0'],
                        self.obs100: batch['obs100'],
                        self.actions: batch['actions'],
                        self.actions100: batch['actions100'],
                        self.rewards: batch['rewards'],
                        self.rewards100: batch['rewards100']})
                    # add a tc loss for tensorboard
                    if self.aux_apply == 'actor' or self.aux_apply == 'both':
                        aux_ops.update({'caus':self.act_caus_loss})
                    elif self.aux_apply == 'critic':
                        aux_ops.update({'caus':self.cri_caus_loss})
                if auxtask == 'repeat':
                    aux_dict.update({
                        self.obs0: batch['obs0'],
                        self.obs1: batch['obs1'],
                        self.obs100: batch['obs100'],
                        self.obs101: batch['obs101'],
                        self.actions: batch['actions'],
                        self.actions100: batch['actions100']})
                    # add a tc loss for tensorboard
                    if self.aux_apply == 'actor' or self.aux_apply == 'both':
                        aux_ops.update({'repeat':self.act_repeat_loss})
                    elif self.aux_apply == 'critic':
                        aux_ops.update({'repeat':self.cri_repeat_loss})
                if auxtask == 'predict':
                    aux_dict.update({
                        self.obs0: batch['obs0'],
                        self.obs1: batch['obs1'],
                        self.actions: batch['actions']})
                    # add a tc loss for tensorboard
                    if self.aux_apply == 'actor' or self.aux_apply == 'both':
                        aux_ops.update({'predict':self.act_predict_loss})
                    elif self.aux_apply == 'critic':
                        aux_ops.update({'repeat':self.cri_predict_loss})
            auxoutputs = self.sess.run(aux_ops, feed_dict=aux_dict)
            auxgrads = auxoutputs['grads']
            self.aux_optimizer.update(auxgrads, stepsize=self.actor_lr)
        
        return critic_loss, actor_loss, auxoutputs

    def initialize(self, sess):
        self.sess = sess
        self.sess.run(tf.global_variables_initializer())
        self.actor_optimizer.sync()
        self.critic_optimizer.sync()
        self.sess.run(self.target_init_updates)

    def update_target_net(self):
        self.sess.run(self.target_soft_updates)

    def get_stats(self):
        if self.stats_sample is None:
            # Get a sample and keep that fixed for all further computations.
            # This allows us to estimate the change in value for the same set of inputs.
            self.stats_sample = self.memory.sample(batch_size=self.batch_size)
        values = self.sess.run(self.stats_ops, feed_dict={
            self.obs0: self.stats_sample['obs0'],
            self.actions: self.stats_sample['actions'],
        })

        names = self.stats_names[:]
        assert len(names) == len(values)
        stats = dict(zip(names, values))

        if self.param_noise is not None:
            stats = {**stats, **self.param_noise.get_stats()}

        return stats

    def adapt_param_noise(self):
        if self.param_noise is None:
            return 0.

        # Perturb a separate copy of the policy to adjust the scale for the next "real" perturbation.
        batch = self.memory.sample(batch_size=self.batch_size)
        self.sess.run(self.perturb_adaptive_policy_ops, feed_dict={
            self.param_noise_stddev: self.param_noise.current_stddev,
        })
        distance = self.sess.run(self.adaptive_policy_distance, feed_dict={
            self.obs0: batch['obs0'],
            self.param_noise_stddev: self.param_noise.current_stddev,
        })

        mean_distance = MPI.COMM_WORLD.allreduce(distance, op=MPI.SUM) / MPI.COMM_WORLD.Get_size()
        self.param_noise.adapt(mean_distance)
        return mean_distance

    def reset(self):
        # Reset internal state after an episode is complete.
        if self.action_noise is not None:
            self.action_noise.reset()
        if self.param_noise is not None:
            self.sess.run(self.perturb_policy_ops, feed_dict={
                self.param_noise_stddev: self.param_noise.current_stddev,
            })
