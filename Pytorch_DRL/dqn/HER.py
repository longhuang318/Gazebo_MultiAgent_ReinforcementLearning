#!/usr/bin/env python
# -*- coding: utf-8 -*-

import tensorflow as tf
import numpy as np
import random
import os

from matplotlib import pyplot as plt
from tqdm import tqdm

# Bit flipping environment
class Env():
    def __init__(self, size=8, shaped_reward=False):
        self.size = size
        self.shaped_reward = shaped_reward
        self.state = np.random.randint(2, size=size)
        self.target = np.random.randint(2, size=size)
        # in case state is the same as target
        while np.sum(self.state == self.target) == size:
            self.target = np.random.randint(2, size=size)

    def step(self, action):
        self.state[action] = 1 - self.state[action]
        if self.shaped_reward:
            return np.copy(self.state), -np.sum(np.square(self.state - self.target))
        else:
            if not np.sum(self.state == self.target) == self.size:
                return np.copy(self.state), -1
            else:
                return np.copy(self.state), 0

    def reset(self, size = None):
        if size is None:
            size = self.size
        self.state = np.random.randint(2, size=size)
        self.target = np.random.randint(2, size=size)

# Experience replay buffer
class Buffer():
    def __init__(self, buffer_size=50000):
        self.buffer = []
        self.buffer_size = buffer_size

    def add(self, experience):
        self.buffer.append(experience)
        if len(self.buffer) > self.buffer_size:
            self.buffer = self.buffer[int(0.0001 * self.buffer_size):]

    # random sample
    def sample(self,size):
        if len(self.buffer) >= size:
            experience_buffer = self.buffer
        else:
            experience_buffer = self.buffer * size
        return np.copy(np.reshape(np.array(random.sample(experience_buffer,size)),[size,4]))

# Simple 1 layer feed forward neural network
class Model():
    def __init__(self, size, name):
        with tf.variable_scope(name):
            self.size = size
            self.inputs = tf.placeholder(shape = [None, self.size * 2], dtype = tf.float32)
            init = tf.contrib.layers.variance_scaling_initializer(factor = 1.0, mode = "FAN_AVG", uniform = False)
            self.hidden = fully_connected_layer(self.inputs, 256, activation = tf.nn.relu, init = init, scope = "fc")
            self.Q_ = fully_connected_layer(self.hidden, self.size, activation = None, scope = "Q", bias = False)
            self.predict = tf.argmax(self.Q_, axis = -1)

            # action is one-hot
            self.action = tf.placeholder(shape = None, dtype = tf.int32)
            self.action_onehot = tf.one_hot(self.action, self.size, dtype = tf.float32)
            
            self.Q = tf.reduce_sum(tf.multiply(self.Q_, self.action_onehot), axis = 1)
            self.Q_next = tf.placeholder(shape=None, dtype=tf.float32)
            self.loss = tf.reduce_sum(tf.square(self.Q_next - self.Q))
            self.optimizer = tf.train.AdamOptimizer(learning_rate=1e-3)

            # grads = self.optimizer.compute_gradients(self.loss)
            # capped = []
            # for g,v in grads:
            #     if g is not None:
            #         capped.append((tf.clip_by_value(g, -1., 1.), v))
            # self.train_op = self.optimizer.apply_gradients(capped)
            self.train_op = self.optimizer.minimize(self.loss)
            self.init_op = tf.global_variables_initializer()

def fully_connected_layer(inputs, dim, activation = None, scope = "fc", reuse = None, init = tf.contrib.layers.xavier_initializer(), bias = True):
    with tf.variable_scope(scope, reuse = reuse):
        w_ = tf.get_variable("W_", [inputs.shape[-1], dim], initializer = init)
        outputs = tf.matmul(inputs, w_)
        if bias:
            b = tf.get_variable("b_", dim, initializer = tf.zeros_initializer())
            outputs += b
        if activation is not None:
            outputs = activation(outputs)
        return outputs

def updateTargetGraph(tfVars, tau):
    total_vars = len(tfVars)
    op_holder = []
    for idx,var in enumerate(tfVars[0:total_vars//2]):
        op_holder.append(tfVars[idx+total_vars//2].assign((var.value()*(1. - tau)) + (tau * tfVars[idx+total_vars//2].value())))
    return op_holder

def updateTarget(op_holder,sess):
    for op in op_holder:
        sess.run(op)

def main():
    HER = True                # use HER or not, if set False, only save real target experience
    shaped_reward = False     # use distance of not, if set True, there will be more detail rewards
    size = 10                 # number of bits
    num_epochs = 20
    num_cycles = 50
    num_episodes = 16
    optimisation_steps = 40
    K = 8                     # Best according to the paper
    buffer_size = 1e6         # Buffer size
    tau = 0.95
    gamma = 0.98
    epsilon = 0.0
    batch_size = 128
    add_final = False

    total_rewards = []
    total_loss = []
    success_rate = []
    succeed = 0

    save_model = True
    model_dir = "./train"
    train = True
    num_test = 1000

    if not os.path.isdir(model_dir):
        os.mkdir(model_dir)

    modelNetwork = Model(size = size, name = "model")
    targetNetwork = Model(size = size, name = "target")
    trainables = tf.trainable_variables()
    updateOps = updateTargetGraph(trainables, tau)
    env = Env(size = size, shaped_reward = shaped_reward)
    buff = Buffer(buffer_size)

    if train:
        plt.ion()
        fig = plt.figure()
        ax = fig.add_subplot(211)
        plt.title("Success Rate")
        ax.set_ylim([0,1.])
        # plt.title("Episodic Rewards")
        ax2 = fig.add_subplot(212)
        plt.title("Q Loss")
        line = ax.plot(np.zeros(1), np.zeros(1), 'b-')[0]
        line2 = ax2.plot(np.zeros(1), np.zeros(1), 'b-')[0]
        fig.canvas.draw()

        with tf.Session() as sess:
            sess.run(modelNetwork.init_op)
            sess.run(targetNetwork.init_op)
            for i in tqdm(range(num_epochs), total = num_epochs):
                for j in range(num_cycles):
                    total_reward = 0.0
                    successes = []
                    for n in range(num_episodes):
                        env.reset()
                        episode_experience = []
                        episode_succeeded = False

                        # collecting experience until terminating one episode
                        # for bit clipping, we can flip only one bit one time, so a episode is done when all bits are flipping
                        for t in range(size):
                            s = np.copy(env.state)
                            g = np.copy(env.target)
                            # concat state and goal, and put them into network together
                            inputs = np.concatenate([s,g],axis = -1)
                            action = sess.run(modelNetwork.predict, feed_dict={modelNetwork.inputs:[inputs]})
                            action = action[0]
                            if np.random.rand(1) < epsilon:
                                action = np.random.randint(size)
                            s_next, reward = env.step(action)
                            episode_experience.append((s,action,reward,s_next,g))
                            total_reward += reward

                            # when current state is equal to targte state, reward is 0
                            if reward == 0:
                                # already success before
                                if episode_succeeded:
                                    continue
                                else:
                                    episode_succeeded = True
                                    succeed += 1
                        successes.append(episode_succeeded)

                        # put special experience into buffer
                        # we will pop out every step of this episode
                        for t in range(size):
                            s, a, r, s_n, g = episode_experience[t]
                            # random start state and radom real goal
                            inputs = np.concatenate([s,g],axis = -1)
                            # next state after this eposide and random real goal
                            new_inputs = np.concatenate([s_next,g],axis = -1)
                            # for standard ER, we save [start state] and [end state] and [goal]
                            buff.add(np.reshape(np.array([inputs,a,r,new_inputs]),[1,4]))

                            # use HER
                            if HER:
                                for k in range(K):
                                    future = np.random.randint(t, size)
                                    # get new goal from experience, which is the terminal state
                                    _, _, _, g_n, _ = episode_experience[future]
                                    inputs = np.concatenate([s,g_n], axis=-1)
                                    new_inputs = np.concatenate([s_n,g_n], axis=-1)
                                    final = np.sum(np.array(s_n) == np.array(g_n)) == size
                                    r_n = 0 if final else -1
                                    print(r_n)
                                    # for HER, we save [start state] and [middle state] and [new goal]
                                    buff.add(np.reshape(np.array([inputs,a,r_n,new_inputs]),[1,4]))
                                print('--------')
                                
                                # if add_final:
                                #     _, _, _, g_n, _ = episode_experience[-1]
                                #     inputs = np.concatenate([s,g_n],axis = -1)
                                #     new_inputs = np.concatenate([s_n, g_n],axis = -1)
                                #     final = np.sum(np.array(s_n) == np.array(g_n)) == size
                                #     r_n = 0 if final else -1
                                #     buff.add(np.reshape(np.array([inputs,a,r_n,new_inputs]),[1,4]))

                    mean_loss = []
                    for k in range(optimisation_steps):
                        experience = buff.sample(batch_size)
                        s, a, r, s_next = [np.squeeze(elem, axis = 1) for elem in np.split(experience, 4, 1)]
                        s = np.array([ss for ss in s])
                        s = np.reshape(s, (batch_size, size * 2))
                        s_next = np.array([ss for ss in s_next])
                        s_next = np.reshape(s_next, (batch_size, size * 2))
                        Q1 = sess.run(modelNetwork.Q_, feed_dict = {modelNetwork.inputs: s_next})
                        Q2 = sess.run(targetNetwork.Q_, feed_dict = {targetNetwork.inputs: s_next})
                        doubleQ = Q2[:, np.argmax(Q1, axis = -1)]
                        Q_target = np.clip(r + gamma * doubleQ,  -1. / (1 - gamma), 0)
                        _, loss = sess.run([modelNetwork.train_op, modelNetwork.loss], feed_dict = {modelNetwork.inputs: s, modelNetwork.Q_next: Q_target, modelNetwork.action: a})
                        mean_loss.append(loss)

                    success_rate.append(np.mean(successes))
                    total_loss.append(np.mean(mean_loss))
                    updateTarget(updateOps,sess)
                    total_rewards.append(total_reward)
                    ax.relim()
                    ax.autoscale_view()
                    ax2.relim()
                    ax2.autoscale_view()
                    line.set_data(np.arange(len(success_rate)), np.array(success_rate))
                    # line.set_data(np.arange(len(total_rewards)), np.array(total_rewards))
                    line2.set_data(np.arange(len(total_loss)), np.array(total_loss))
                    fig.canvas.draw()
                    fig.canvas.flush_events()
                    plt.pause(1e-7)

            if save_model:
                saver = tf.train.Saver()
                saver.save(sess, os.path.join(model_dir, "model.ckpt"))
        print("Number of episodes succeeded: {}".format(succeed))
        raw_input("Press enter...")
    
    with tf.Session() as sess:
        saver = tf.train.Saver()
        saver.restore(sess, os.path.join(model_dir, "model.ckpt"))
        for i in range(num_test):
            env.reset()
            print("current state: {}".format(env.state))
            for t in range(size):
                s = np.copy(env.state)
                g = np.copy(env.target)
                inputs = np.concatenate([s,g],axis = -1)
                action = sess.run(targetNetwork.predict,feed_dict = {targetNetwork.inputs:[inputs]})
                action = action[0]
                s_next, reward = env.step(action)
                print("current state: {}".format(env.state))
                if reward == 0:
                    print("Success!")
                    break
            raw_input("Press enter...")

if __name__ == "__main__":
    main()
