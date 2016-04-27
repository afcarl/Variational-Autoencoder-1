# -*- coding: utf-8 -*-
import os, sys, time
import numpy as np
from chainer import cuda, Variable
sys.path.append(os.path.split(os.getcwd())[0])
import util
from args import args
from model import conf, vae

def sample_x_variable(batchsize):
	x_batch = np.zeros((batchsize, conf.ndim_x), dtype=np.float32)
	for j in range(batchsize):
		data_index = np.random.randint(len(dataset))
		img = dataset[data_index]
		x_batch[j] = img.reshape((conf.ndim_x,))
	x_batch = Variable(x_batch)
	if conf.use_gpu:
		x_batch.to_gpu()
	return x_batch

vae.load(args.model_dir)

dataset, labels = util.load_labeled_dataset(args)
max_epoch = 1000
num_trains_per_epoch = 5000
batchsize = 128
total_time = 0

for epoch in xrange(max_epoch):
	sum_loss = 0
	epoch_time = time.time()
	for t in xrange(num_trains_per_epoch):
		x = sample_x_variable(batchsize)
		loss = vae.train(x)
		sum_loss += loss
		if t % 100 == 0:
			sys.stdout.write("\rTraining in progress...(%d / %d)" % (t, num_trains_per_epoch))
			sys.stdout.flush()
	epoch_time = time.time() - epoch_time
	total_time += epoch_time
	print "epoch:", epoch, "loss:", sum_loss / num_trains_per_epoch, "time:", epoch_time / 60, "min", "total_time", total_time / 60, "min"
	sys.stdout.flush()
	vae.save(args.model_dir)
