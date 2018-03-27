import tensorflow as tf
import tensorflow.contrib as tc


class Model(object):
    def __init__(self, name):
        self.name = name

    @property
    def vars(self):
        repr_vars_ = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope=self.name+'_repr')
        own_vars_ = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope=self.name)
        return repr_vars_+own_vars_

    @property
    def trainable_vars(self):
        repr_vars_ = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope=self.name+'_repr')
        own_vars_ = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope=self.name)
        return repr_vars_+own_vars_
    
    @property
    def repr_vars(self):
        repr_vars_ = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope=self.name+'_repr')
        return repr_vars_
    
    @property
    def perturbable_vars(self):
        return [var for var in self.trainable_vars if 'LayerNorm' not in var.name]

    @property
    def output_vars(self):
        output_vars = [var for var in self.trainable_vars if 'output' in var.name]
        return output_vars
        
class Representation(Model):
    def __init__(self, name=None, layer_norm=True):
        super(Representation, self).__init__(name=name)
        self.layer_norm = layer_norm
        
    def __call__(self, obs, reuse=False):
        with tf.variable_scope(self.name) as scope:
            if reuse:
                scope.reuse_variables()
            x = obs
            x = tf.layers.dense(x, 64)
            if self.layer_norm:
                x = tc.layers.layer_norm(x, center=True, scale=True)
            x = tf.nn.relu(x)
            
            x = tf.layers.dense(x, 64)
            if self.layer_norm:
                x = tc.layers.layer_norm(x, center=True, scale=True)
            x = tf.nn.relu(x)
            
        return x
        
class Actor(Model):
    def __init__(self, nb_actions, name='actor', layer_norm=True):
        super(Actor, self).__init__(name=name)
        self.nb_actions = nb_actions
        self.layer_norm = layer_norm
        repr_name = name + '_repr'
        self.repr = Representation(name=repr_name, layer_norm=self.layer_norm)

    def __call__(self, obs, reuse=False):
        print("name:{} -- reprname:{}".format(self.name, self.repr.name))
        representation = self.repr(obs, reuse=reuse)
        
        with tf.variable_scope(self.name) as scope:
            if reuse:
                scope.reuse_variables()
            
            x = representation
            x = tf.layers.dense(x, self.nb_actions, kernel_initializer=tf.random_uniform_initializer(minval=-3e-3, maxval=3e-3))
            x = tf.nn.tanh(x)
        return x


class Critic(Model):
    def __init__(self, name='critic', layer_norm=True):
        super(Critic, self).__init__(name=name)
        self.layer_norm = layer_norm
        repr_name = name + '_repr'
        self.repr = Representation(name=repr_name, layer_norm=self.layer_norm)

    def __call__(self, obs, action, reuse=False):
        representation = self.repr(obs, reuse=reuse)
        
        with tf.variable_scope(self.name) as scope:
            if reuse:
                scope.reuse_variables()

            x = representation
            x = tf.concat([x, action], axis=-1)
            x = tf.layers.dense(x, 64)
            if self.layer_norm:
                x = tc.layers.layer_norm(x, center=True, scale=True)
            x = tf.nn.relu(x)

            x = tf.layers.dense(x, 1, kernel_initializer=tf.random_uniform_initializer(minval=-3e-3, maxval=3e-3))
        return x

class Predictor(Model):
    @Model.trainable_vars.setter
    def trainable_vars(self):
        repr_name = self.name.replace('_pred', '') + '_repr'
        print("Predictor repr_name:" + repr_name)
        repr_vars_ = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope=repr_name)
        own_vars_ = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope=self.name)
        return repr_vars_+own_vars_
        
    def __init__(self, name, layer_norm=True): 
        super(Predictor, self).__init__(name=name+'_pred')
        self.layer_norm = layer_norm
        repr_name = name + '_repr'
        self.repr = Representation(name=repr_name, layer_norm=self.layer_norm)
        print("predictor repr name:{}".format(self.repr.name))
        
    def __call__(self, obs, action, reuse=False):
        representation = self.repr(obs, reuse=reuse)
        
        with tf.variable_scope(self.name) as scope:
            x = representation
            x = tf.concat([x, action], axis=-1)
            x = tf.layers.dense(x, 64)
            if self.layer_norm:
                x = tc.layers.layer_norm(x, center=True, scale=True)
            x = tf.nn.relu(x)
            x = tf.layers.dense(x, obs.get_shape()[1])
        return x     
         
