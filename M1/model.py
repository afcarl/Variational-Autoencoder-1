# -*- coding: utf-8 -*-
import numpy as np
import chainer.functions as F
import chainer.links as L
from chainer import cuda, optimizers, serializers, Variable
import os, sys, time
sys.path.append(os.path.split(os.getcwd())[0])
from args import args
from config import config
from build import build

# 設定変更
config.n_z = 10
config.n_dis_inputs = config.n_z
config.n_gen_hidden_units = [2000, 1000, 500]
config.n_dis_hidden_units = [500, 250, 50]
config.n_dec_hidden_units = [500, 1000, 2000]
config.gen_encoder_type = "deterministic"
config.gen_enable_dropout = False
config.dis_enable_dropout = False
config.dec_enable_dropout = False

gen, dis, dec = build(config)

if args.load_epoch > 0:
	gen_filename = "%s/gen_epoch_%s.model" % (args.model_dir, args.load_epoch)
	if os.path.isfile(gen_filename):
		serializers.load_hdf5(gen_filename, gen)
		print gen_filename
	else:
		raise Exception("Failed to load generator.")

	dis_filename = "%s/dis_epoch_%s.model" % (args.model_dir, args.load_epoch)
	if os.path.isfile(dis_filename):
		serializers.load_hdf5(dis_filename, dis)
		print dis_filename
	else:
		raise Exception("Failed to load discriminator.")

	dec_filename = "%s/dec_epoch_%s.model" % (args.model_dir, args.load_epoch)
	if os.path.isfile(dec_filename):
		serializers.load_hdf5(dec_filename, dec)
		print dec_filename
	else:
		raise Exception("Failed to load decoder.")

if config.use_gpu:
	gen.to_gpu()
	dis.to_gpu()
	dec.to_gpu()