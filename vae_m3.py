# -*- coding: utf-8 -*-
import math
import numpy as np
import chainer, os, collections, six
from chainer import cuda, Variable, optimizers, serializers, function
from chainer.utils import type_check
from chainer import functions as F
from chainer import links as L

activations = {
	"sigmoid": F.sigmoid, 
	"tanh": F.tanh, 
	"softplus": F.softplus, 
	"relu": F.relu, 
	"leaky_relu": F.leaky_relu, 
	"elu": F.elu
}

class Conf():
	def __init__(self):
		self.image_width = 28
		self.image_height = 28
		self.ndim_x = 28 * 28
		self.ndim_y = 10
		self.ndim_z = 100

		# e.g.
		# ndim_x + ndim_y(input) -> 2000 -> 1000 -> 100 (output)
		# encoder_xy_z_hidden_units = [2000, 1000]
		self.encoder_xy_z_hidden_units = [600, 600]
		self.encoder_xy_z_activation_function = "softplus"
		self.encoder_xy_z_output_activation_function = None
		self.encoder_xy_z_apply_dropout = False
		self.encoder_xy_z_apply_batchnorm = False
		self.encoder_xy_z_apply_batchnorm_to_input = False

		self.encoder_x_y_hidden_units = [600, 600]
		self.encoder_x_y_activation_function = "softplus"
		self.encoder_x_y_output_activation_function = None
		self.encoder_x_y_apply_dropout = False
		self.encoder_x_y_apply_batchnorm = False
		self.encoder_x_y_apply_batchnorm_to_input = False

		# e.g.
		# ndim_z + ndim_y(input) -> 2000 -> 1000 -> 100 (output)
		# decoder_hidden_units = [2000, 1000]
		self.decoder_hidden_units = [600, 600]
		self.decoder_activation_function = "softplus"
		self.decoder_output_activation_function = None	# this will be ignored when decoder is BernoulliDecoder
		self.decoder_apply_dropout = False
		self.decoder_apply_batchnorm = False
		self.decoder_apply_batchnorm_to_input = False

		self.use_gpu = True
		self.learning_rate = 0.00003
		self.gradient_momentum = 0.9

	def check(self):
		pass

def sum_sqnorm(arr):
	sq_sum = collections.defaultdict(float)
	for x in arr:
		with cuda.get_device(x) as dev:
			x = x.ravel()
			s = x.dot(x)
			sq_sum[int(dev)] += s
	return sum([float(i) for i in six.itervalues(sq_sum)])
	
class GradientClipping(object):
	name = "GradientClipping"

	def __init__(self, threshold):
		self.threshold = threshold

	def __call__(self, opt):
		norm = np.sqrt(sum_sqnorm([p.grad for p in opt.target.params()]))
		if norm == 0:
			norm = 1
		rate = self.threshold / norm
		if rate < 1:
			for param in opt.target.params():
				grad = param.grad
				with cuda.get_device(grad):
					grad *= rate

class VAE():
	# name is used for the filename when you save the model
	def __init__(self, conf, name="vae"):
		conf.check()
		self.encoder_xy_z, self.encoder_x_y, self.decoder = self.build(conf)
		self.name = name

		self.optimizer_encoder_xy_z = optimizers.Adam(alpha=conf.learning_rate, beta1=conf.gradient_momentum)
		self.optimizer_encoder_xy_z.setup(self.encoder_xy_z)
		# self.optimizer_encoder_xy_z.add_hook(GradientClipping(1.0))

		self.optimizer_encoder_x_y = optimizers.Adam(alpha=conf.learning_rate, beta1=conf.gradient_momentum)
		self.optimizer_encoder_x_y.setup(self.encoder_x_y)
		# self.optimizer_encoder_x_y.add_hook(GradientClipping(1.0))

		self.optimizer_decoder = optimizers.Adam(alpha=conf.learning_rate, beta1=conf.gradient_momentum)
		self.optimizer_decoder.setup(self.decoder)
		# self.optimizer_decoder.add_hook(GradientClipping(1.0))

	def build(self, conf):
		raise Exception()

	def train(self, x, L=1, test=False):
		raise Exception()

	@property
	def xp(self):
		return self.encoder_xy_z.xp

	@property
	def gpu(self):
		if cuda.available is False:
			return False
		return True if self.xp is cuda.cupy else False

	def zero_grads(self):
		self.optimizer_encoder_xy_z.zero_grads()
		self.optimizer_encoder_x_y.zero_grads()
		self.optimizer_decoder.zero_grads()

	def update(self):
		self.optimizer_encoder_xy_z.update()
		self.optimizer_encoder_x_y.update()
		self.optimizer_decoder.update()

	def encode_xy_z(self, x, y, test=False):
		return self.encoder_xy_z(x, y, test=test)

	def encode_x_y(self, x, test=False, softmax=True):
		return self.encoder_x_y(x, test=test, softmax=softmax)

	def sample_x_y(self, x, argmax=False, test=False):
		batchsize = x.data.shape[0]
		y_distribution = self.encoder_x_y(x, test=test, softmax=True).data
		print y_distribution
		n_labels = y_distribution.shape[1]
		if self.gpu:
			y_distribution = cuda.to_cpu(y_distribution)
		sampled_y = np.zeros((batchsize, n_labels), dtype=np.float32)
		if argmax:
			args = np.argmax(y_distribution, axis=1)
			for b in xrange(batchsize):
				sampled_y[b, args[b]] = 1
		else:
			for b in xrange(batchsize):
				label_id = np.random.choice(np.arange(n_labels), p=y_distribution[b])
				sampled_y[b, label_id] = 1
		sampled_y = Variable(sampled_y)
		if self.gpu:
			sampled_y.to_gpu()
		return sampled_y

	def sample_x_label(self, x, argmax=True, test=False):
		batchsize = x.data.shape[0]
		y_distribution = self.encoder_x_y(x, test=test, softmax=True).data
		n_labels = y_distribution.shape[1]
		if self.gpu:
			y_distribution = cuda.to_cpu(y_distribution)
		if argmax:
			sampled_label = np.argmax(y_distribution, axis=1)
		else:
			sampled_label = np.zeros((batchsize,), dtype=np.int32)
			labels = np.arange(n_labels)
			for b in xrange(batchsize):
				label_id = np.random.choice(labels, p=y_distribution[b])
				sampled_label[b] = 1
			
		return sampled_label

	def decode_zy_x(self, z, y, test=False, output_pixel_value=False):
		return self.decoder(z, y, test=test, output_pixel_value=output_pixel_value)

	def bernoulli_nll_keepbatch(self, x, y):
		nll = F.softplus(y) - x * y
		return F.sum(nll, axis=1)

	def gaussian_kl_divergence_keepbatch(self, mean, ln_var):
		var = F.exp(ln_var)
		kld = (F.sum(mean * mean, axis=1) + F.sum(var, axis=1) - F.sum(ln_var, axis=1) - mean.data.shape[1]) * 0.5
		return kld

	def loss_unlabeled(self, unlabeled_x, L=1, test=False):
		# Math:
		# Loss = -E_{q(y|x)}[-loss_labeled(x, y)] - H(q(y|x))
		# where H(p) is the Entropy of the p
		loss_expectation = 0
		batchsize = unlabeled_x.data.shape[0]
		xp = self.xp
		y_expectation = self.encoder_x_y(unlabeled_x, test=test, softmax=True)
		num_types_of_label = y_expectation.data.shape[1]

		# Marginalize y
		loss_lower_bound = 0
		for n in xrange(num_types_of_label):
			index = xp.full((batchsize,), n, dtype=xp.int32)
			index = Variable(index)
			y = xp.zeros((batchsize, num_types_of_label), dtype=xp.float32)
			y[:, n] = 1
			y = Variable(y)
			loss_reconstruction, loss_kld_regularization = self.loss_labeled_keepbatch(unlabeled_x, y, L=1, test=test)
			loss_n = loss_reconstruction + loss_kld_regularization
			loss_lower_bound += F.select_item(y_expectation, index) * loss_n
		loss_lower_bound = F.sum(loss_lower_bound) / batchsize

		# -H(q(y|x))
		# Math:
		# -sum_{y}q(y|x)logq(y|x)
		loss_entropy = F.sum(y_expectation * F.log(y_expectation + 1e-6)) / batchsize

		return loss_lower_bound, loss_entropy

	def loss_unlabeled_fast(self, unlabeled_x, L=1, test=False):
		# Math:
		# Loss = -E_{q(y|x)}[-loss_labeled(x, y)] - H(q(y|x))
		# where H(p) is the Entropy of the p
		loss_expectation = 0
		batchsize = unlabeled_x.data.shape[0]
		xp = self.xp
		y_expectation = self.encoder_x_y(unlabeled_x, test=test, softmax=True)
		num_types_of_label = y_expectation.data.shape[1]

		unlabeled_x_ext = xp.zeros((batchsize * num_types_of_label, unlabeled_x.data.shape[1]), dtype=xp.float32)
		y_ext = xp.zeros((batchsize * num_types_of_label, num_types_of_label), dtype=xp.float32)
		for n in xrange(num_types_of_label):
			y_ext[n * batchsize:(n + 1) * batchsize,n] = 1
			unlabeled_x_ext[n * batchsize:(n + 1) * batchsize] = unlabeled_x.data

		y_ext = Variable(y_ext)
		unlabeled_x_ext = Variable(unlabeled_x_ext)

		loss_reconstruction, loss_kld_regularization = self.loss_labeled(unlabeled_x_ext, y_ext, L=1, test=test)
		loss_lower_bound = loss_reconstruction + loss_kld_regularization

		# -H(q(y|x))
		# Math:
		# -sum_{y}q(y|x)logq(y|x)
		loss_entropy = F.sum(y_expectation * F.log(y_expectation + 1e-6)) / batchsize

		return loss_lower_bound, loss_entropy

	def train(self, labeled_x, labeled_y, label_ids, unlabeled_x, labeled_L=1, unlabeled_L=1, test=False):

		def lower_bound(log_px_zy, log_py, log_pz, log_qz_xy):
			lb = log_px_zy + log_py + log_pz - log_qz_xy
			return lb

		batchsize = labeled_x.data.shape[0]
		num_types_of_label = labeled_y.data.shape[1]
		xp = self.xp

		# Lower bound for labeled data
		z_l = self.encoder_xy_z(labeled_x, labeled_y, test=test)
		log_px_zy_l = self.log_px_zy(labeled_x, z_l, labeled_y, test=test)
		log_py_l = self.log_py(labeled_y, test=test)
		log_pz_l  self.log_pz(z_l, test=test)
		log_qz_xy_l = self.log_qz_xy(labeled_x, labeled_y, z_l, test=test)
		lower_bound_l  lower_bound(log_px_zy_l, log_py_l, log_pz_l, log_qz_xy_l)

		# Lower bound for unlabeled data
		# Marginalize y
		unlabeled_x_ext = xp.zeros((batchsize * num_types_of_label, unlabeled_x.data.shape[1]), dtype=xp.float32)
		y_ext = xp.zeros((batchsize * num_types_of_label, num_types_of_label), dtype=xp.float32)
		for n in xrange(num_types_of_label):
			y_ext[n * batchsize:(n + 1) * batchsize,n] = 1
			unlabeled_x_ext[n * batchsize:(n + 1) * batchsize] = unlabeled_x.data
		y_ext = Variable(y_ext)
		unlabeled_x_ext = Variable(unlabeled_x_ext)

		z_u = self.encoder_xy_z(unlabeled_x_ext, y_ext, test=test)
		log_px_zy_u = self.log_px_zy(unlabeled_x_ext, z_u, y_ext, test=test)
		log_py_u = self.log_py(y_ext, test=test)
		log_pz_u  self.log_pz(z_u, test=test)
		log_qz_xy_u = self.log_qz_xy(unlabeled_x_ext, y_ext, z_u, test=test)
		lower_bound_u  lower_bound(log_px_zy_u, log_py_u, log_pz_u, log_qz_xy_u)

		loss_labeled = -F.sum(lower_bound_l)
		loss_unlabeled = -F.sum(lower_bound_u)
		loss = loss_labeled + loss_unlabeled

		self.zero_grads()
		loss.backward()
		self.update()

		if self.gpu:
			loss_labeled.to_cpu()
			loss_unlabeled.to_cpu()
		return loss_labeled.data, loss_unlabeled.data

	def train_classification(self, labeled_x, label_ids, alpha=1.0, test=False):
		y_distribution = self.encode_x_y(labeled_x, softmax=False, test=test)
		batchsize = labeled_x.data.shape[0]
		num_types_of_label = y_distribution.data.shape[1]

		loss_classifier = alpha * F.softmax_cross_entropy(y_distribution, label_ids)
		self.zero_grads()
		loss_classifier.backward()
		self.update()
		if self.gpu:
			loss_classifier.to_cpu()
		return loss_classifier.data

	def train_supervised(self, labeled_x, labeled_y, alpha, L=1, test=False):
		loss = self.loss_labeled(labeled_x, labeled_y, alpha, L=L, test=test)

		self.zero_grads()
		loss.backward()
		self.update()

		if self.gpu:
			loss.to_cpu()
		return loss.data

	def train_unsupervised(self, unlabeled_x, n_labels, L=1, test=False):
		loss = self.loss_unlabeled(unlabeled_x, n_labels, L=L, test=test)

		self.zero_grads()
		loss.backward()
		self.update()

		if self.gpu:
			loss.to_cpu()
		return loss.data

	def log_px_zy(self, x, z, y, test=False):
		# do not apply F.sigmoid to the output of the decoder
		x_expectation = self.decoder(z, y, test=test, output_pixel_value=False)
		negative_log_likelihood = F.bernoulli_nll(x, x_expectation)
		log_px_zy = -negative_log_likelihood
		return log_px_zy

	def log_py(self, y, test=False):
		# prior p(y) expecting that all classes are evenly distributed
		return math.log(1.0 / y.data.shape[1])

	def log_pz(self, z, test=False):
		constant = -0.5 * math.log(2.0 * math.pi)
		log_pz = constant - 0.5 * z ** 2
		return log_pz

	def log_qz_xy(self, x, y, z, test=False):
		z_mean, z_ln_var = self.encoder_xy_z(x, y, test=test, sample_output=False)
		negative_log_likelihood = F.gaussian_nll(z, z_mean, z_ln_var)
		log_qz_xy = -negative_log_likelihood
		return log_qz_xy

	def log_qy_x(self, x, y, test=False):
		y_expectation = self.encoder_x_y(x, test=False, softmax=True)
		log_qy_x = y * F.log(y_expectation + 1e-6)
		return log_qy_x

	def load(self, dir=None):
		if dir is None:
			raise Exception()
		for attr in vars(self):
			prop = getattr(self, attr)
			if isinstance(prop, chainer.Chain) or isinstance(prop, chainer.optimizer.GradientMethod):
				filename = dir + "/%s_%s.hdf5" % (self.name, attr)
				if os.path.isfile(filename):
					print "loading",  filename
					serializers.load_hdf5(filename, prop)
				else:
					print filename, "missing."
		print "model loaded."

	def save(self, dir=None):
		if dir is None:
			raise Exception()
		try:
			os.mkdir(dir)
		except:
			pass
		for attr in vars(self):
			prop = getattr(self, attr)
			if isinstance(prop, chainer.Chain) or isinstance(prop, chainer.optimizer.GradientMethod):
				serializers.save_hdf5(dir + "/%s_%s.hdf5" % (self.name, attr), prop)
		print "model saved."

	def __call__(self, x, test=False, output_pixel_value=True):
		return self.decoder(self.encoder(x, test=test), test=test, output_pixel_value=True)

class GaussianM2VAE(VAE):

	def build(self, conf):
		wscale = 1.0
		encoder_xy_z_attributes = {}
		encoder_xy_z_units = zip(conf.encoder_xy_z_hidden_units[:-1], conf.encoder_xy_z_hidden_units[1:])
		encoder_xy_z_units += [(conf.encoder_x_y_hidden_units[-1], conf.ndim_z)]
		for i, (n_in, n_out) in enumerate(encoder_xy_z_units):
			encoder_xy_z_attributes["layer_mean_%i" % i] = L.Linear(n_in, n_out, wscale=wscale)
			encoder_xy_z_attributes["batchnorm_mean_%i" % i] = L.BatchNormalization(n_in)
			encoder_xy_z_attributes["layer_var_%i" % i] = L.Linear(n_in, n_out, wscale=wscale)
			encoder_xy_z_attributes["batchnorm_var_%i" % i] = L.BatchNormalization(n_in)
		encoder_xy_z_attributes["layer_mean_merge_x"] = L.Linear(conf.ndim_x, conf.encoder_xy_z_hidden_units[0])
		encoder_xy_z_attributes["layer_mean_merge_y"] = L.Linear(conf.ndim_y, conf.encoder_xy_z_hidden_units[0])
		encoder_xy_z_attributes["batchnorm_mean_merge_x"] = L.BatchNormalization(conf.ndim_x)
		encoder_xy_z_attributes["layer_var_merge_x"] = L.Linear(conf.ndim_x, conf.encoder_xy_z_hidden_units[0])
		encoder_xy_z_attributes["layer_var_merge_y"] = L.Linear(conf.ndim_y, conf.encoder_xy_z_hidden_units[0])
		encoder_xy_z_attributes["batchnorm_var_merge_x"] = L.BatchNormalization(conf.ndim_x)
		encoder_xy_z = GaussianEncoder(**encoder_xy_z_attributes)
		encoder_xy_z.n_layers = len(encoder_xy_z_units)
		encoder_xy_z.activation_function = conf.encoder_xy_z_activation_function
		encoder_xy_z.output_activation_function = conf.encoder_xy_z_output_activation_function
		encoder_xy_z.apply_dropout = conf.encoder_xy_z_apply_dropout
		encoder_xy_z.apply_batchnorm = conf.encoder_xy_z_apply_batchnorm
		encoder_xy_z.apply_batchnorm_to_input = conf.encoder_xy_z_apply_batchnorm_to_input

		encoder_x_y_attributes = {}
		encoder_x_y_units = [(conf.ndim_x, conf.encoder_x_y_hidden_units[0])]
		encoder_x_y_units += zip(conf.encoder_x_y_hidden_units[:-1], conf.encoder_x_y_hidden_units[1:])
		encoder_x_y_units += [(conf.encoder_x_y_hidden_units[-1], conf.ndim_y)]
		for i, (n_in, n_out) in enumerate(encoder_x_y_units):
			encoder_x_y_attributes["layer_%i" % i] = L.Linear(n_in, n_out, wscale=wscale)
			encoder_x_y_attributes["batchnorm_%i" % i] = L.BatchNormalization(n_in)
		encoder_x_y = SoftmaxEncoder(**encoder_x_y_attributes)
		encoder_x_y.n_layers = len(encoder_x_y_units)
		encoder_x_y.activation_function = conf.encoder_x_y_activation_function
		encoder_x_y.output_activation_function = conf.encoder_x_y_output_activation_function
		encoder_x_y.apply_dropout = conf.encoder_x_y_apply_dropout
		encoder_x_y.apply_batchnorm = conf.encoder_x_y_apply_batchnorm
		encoder_x_y.apply_batchnorm_to_input = conf.encoder_x_y_apply_batchnorm_to_input

		decoder_attributes = {}
		decoder_units = zip(conf.decoder_hidden_units[:-1], conf.decoder_hidden_units[1:])
		decoder_units += [(conf.decoder_hidden_units[-1], conf.ndim_x)]
		for i, (n_in, n_out) in enumerate(decoder_units):
			decoder_attributes["layer_mean_%i" % i] = L.Linear(n_in, n_out, wscale=wscale)
			decoder_attributes["batchnorm_mean_%i" % i] = L.BatchNormalization(n_in)
			decoder_attributes["layer_var_%i" % i] = L.Linear(n_in, n_out, wscale=wscale)
			decoder_attributes["batchnorm_var_%i" % i] = L.BatchNormalization(n_in)

		# Note: GaussianDecoder is the same as GaussianEncoder (it takes x and y)
		decoder_attributes["layer_mean_merge_x"] = L.Linear(conf.ndim_z, conf.decoder_hidden_units[0], wscale=wscale)
		decoder_attributes["layer_mean_merge_y"] = L.Linear(conf.ndim_y, conf.decoder_hidden_units[0], wscale=wscale)
		decoder_attributes["batchnorm_mean_merge_x"] = L.BatchNormalization(conf.ndim_z)
		decoder_attributes["layer_var_merge_x"] = L.Linear(conf.ndim_z, conf.decoder_hidden_units[0], wscale=wscale)
		decoder_attributes["layer_var_merge_y"] = L.Linear(conf.ndim_y, conf.decoder_hidden_units[0], wscale=wscale)
		decoder_attributes["batchnorm_var_merge_x"] = L.BatchNormalization(conf.ndim_z)
		decoder = GaussianDecoder(**decoder_attributes)
		decoder.n_layers = len(decoder_units)
		decoder.activation_function = conf.decoder_activation_function
		decoder.output_activation_function = conf.decoder_output_activation_function
		decoder.apply_dropout = conf.decoder_apply_dropout
		decoder.apply_batchnorm = conf.decoder_apply_batchnorm
		decoder.apply_batchnorm_to_input = conf.decoder_apply_batchnorm_to_input

		if conf.use_gpu:
			encoder_xy_z.to_gpu()
			encoder_x_y.to_gpu()
			decoder.to_gpu()
		return encoder_xy_z, encoder_x_y, decoder

	def loss_labeled(self, x, y, L=1, test=False):
		# Math:
		# Loss = -E_{q(z|x,y)}[logp(x|y,z) + logp(y)] + KL(q(z|x,y)||p(z))
		loss_reconstruction = 0
		batchsize = x.data.shape[0]
		z_mean, z_ln_var = self.encoder_xy_z(x, y, test=test, sample_output=False)
		# -E_{q(z|x,y)}[logp(x|y,z) + logp(y)]
		for l in xrange(L):
			# Sample z
			z = F.gaussian(z_mean, z_ln_var)
			# Decode
			x_reconstruction_mean, x_reconstruction_ln_var = self.decode_zy_x(z, y, test=test, output_pixel_value=False)
			# Approximation of E_q(z|x)[log(p(x|z))]
			loss_reconstruction += F.gaussian_nll(x, x_reconstruction_mean, x_reconstruction_ln_var)
		loss_reconstruction /= L * batchsize
		# KL(q(z|x,y)||p(z))
		loss_kld_regularization = F.gaussian_kl_divergence(z_mean, z_ln_var) / batchsize

		return loss_reconstruction, loss_kld_regularization

class BernoulliM2VAE(VAE):

	def build(self, conf):
		wscale = 1.0
		encoder_xy_z_attributes = {}
		encoder_xy_z_units = zip(conf.encoder_xy_z_hidden_units[:-1], conf.encoder_xy_z_hidden_units[1:])
		encoder_xy_z_units += [(conf.encoder_xy_z_hidden_units[-1], conf.ndim_z)]
		for i, (n_in, n_out) in enumerate(encoder_xy_z_units):
			encoder_xy_z_attributes["layer_mean_%i" % i] = L.Linear(n_in, n_out, wscale=wscale)
			encoder_xy_z_attributes["batchnorm_mean_%i" % i] = L.BatchNormalization(n_in)
			encoder_xy_z_attributes["layer_var_%i" % i] = L.Linear(n_in, n_out, wscale=wscale)
			encoder_xy_z_attributes["batchnorm_var_%i" % i] = L.BatchNormalization(n_in)
		encoder_xy_z_attributes["layer_mean_merge_x"] = L.Linear(conf.ndim_x, conf.encoder_xy_z_hidden_units[0], wscale=wscale)
		encoder_xy_z_attributes["batchnorm_mean_merge_x"] = L.BatchNormalization(conf.ndim_x)
		encoder_xy_z_attributes["layer_var_merge_x"] = L.Linear(conf.ndim_x, conf.encoder_xy_z_hidden_units[0], wscale=wscale)
		encoder_xy_z_attributes["batchnorm_var_merge_x"] = L.BatchNormalization(conf.ndim_x)
		encoder_xy_z_attributes["layer_mean_merge_y"] = L.Linear(conf.ndim_y, conf.encoder_xy_z_hidden_units[0], wscale=wscale)
		encoder_xy_z_attributes["layer_var_merge_y"] = L.Linear(conf.ndim_y, conf.encoder_xy_z_hidden_units[0], wscale=wscale)
		encoder_xy_z = GaussianEncoder(**encoder_xy_z_attributes)
		encoder_xy_z.n_layers = len(encoder_xy_z_units)
		encoder_xy_z.activation_function = conf.encoder_xy_z_activation_function
		encoder_xy_z.output_activation_function = conf.encoder_xy_z_output_activation_function
		encoder_xy_z.apply_dropout = conf.encoder_xy_z_apply_dropout
		encoder_xy_z.apply_batchnorm = conf.encoder_xy_z_apply_batchnorm
		encoder_xy_z.apply_batchnorm_to_input = conf.encoder_xy_z_apply_batchnorm_to_input

		encoder_x_y_attributes = {}
		encoder_x_y_units = [(conf.ndim_x, conf.encoder_x_y_hidden_units[0])]
		encoder_x_y_units += zip(conf.encoder_x_y_hidden_units[:-1], conf.encoder_x_y_hidden_units[1:])
		encoder_x_y_units += [(conf.encoder_x_y_hidden_units[-1], conf.ndim_y)]
		for i, (n_in, n_out) in enumerate(encoder_x_y_units):
			encoder_x_y_attributes["layer_%i" % i] = L.Linear(n_in, n_out, wscale=wscale)
			encoder_x_y_attributes["batchnorm_%i" % i] = L.BatchNormalization(n_in)
		encoder_x_y = SoftmaxEncoder(**encoder_x_y_attributes)
		encoder_x_y.n_layers = len(encoder_x_y_units)
		encoder_x_y.activation_function = conf.encoder_x_y_activation_function
		encoder_x_y.output_activation_function = conf.encoder_x_y_output_activation_function
		encoder_x_y.apply_dropout = conf.encoder_x_y_apply_dropout
		encoder_x_y.apply_batchnorm = conf.encoder_x_y_apply_batchnorm
		encoder_x_y.apply_batchnorm_to_input = conf.encoder_x_y_apply_batchnorm_to_input

		decoder_attributes = {}
		decoder_units = zip(conf.decoder_hidden_units[:-1], conf.decoder_hidden_units[1:])
		decoder_units += [(conf.decoder_hidden_units[-1], conf.ndim_x)]
		for i, (n_in, n_out) in enumerate(decoder_units):
			decoder_attributes["layer_%i" % i] = L.Linear(n_in, n_out, wscale=wscale)
			decoder_attributes["batchnorm_%i" % i] = L.BatchNormalization(n_in)
		decoder_attributes["layer_merge_z"] = L.Linear(conf.ndim_z, conf.decoder_hidden_units[0], wscale=wscale)
		decoder_attributes["batchnorm_merge_z"] = L.BatchNormalization(conf.ndim_z)
		decoder_attributes["layer_merge_y"] = L.Linear(conf.ndim_y, conf.decoder_hidden_units[0], wscale=wscale)
		decoder = BernoulliDecoder(**decoder_attributes)
		decoder.n_layers = len(decoder_units)
		decoder.activation_function = conf.decoder_activation_function
		decoder.output_activation_function = conf.decoder_output_activation_function
		decoder.apply_dropout = conf.decoder_apply_dropout
		decoder.apply_batchnorm = conf.decoder_apply_batchnorm
		decoder.apply_batchnorm_to_input = conf.decoder_apply_batchnorm_to_input

		if conf.use_gpu:
			encoder_xy_z.to_gpu()
			encoder_x_y.to_gpu()
			decoder.to_gpu()
		return encoder_xy_z, encoder_x_y, decoder

	def loss_labeled(self, x, y, L=1, test=False):
		# Math:
		# Loss = -E_{q(z|x,y)}[logp(x|y,z) + logp(y)] + KL(q(z|x,y)||p(z))
		loss_reconstruction = 0
		batchsize = x.data.shape[0]
		z_mean, z_ln_var = self.encoder_xy_z(x, y, test=test, sample_output=False)
		# -E_{q(z|x,y)}[logp(x|y,z) + logp(y)]
		for l in xrange(L):
			# Sample z
			z = F.gaussian(z_mean, z_ln_var)
			# Decode
			x_expectation = self.decode_zy_x(z, y, test=test)
			# x is between -1 to 1 so we convert it to be between 0 to 1
			# logp(y) = log(1/num_labels)
			reconstuction_loss = F.bernoulli_nll(x, x_expectation) - math.log(1.0 / y.data.shape[1])
			loss_reconstruction += reconstuction_loss
		loss_reconstruction /= L * batchsize
		# KL(q(z|x,y)||p(z))
		loss_kld_regularization = F.gaussian_kl_divergence(z_mean, z_ln_var) / batchsize

		return loss_reconstruction, loss_kld_regularization

	def loss_labeled_keepbatch(self, x, y, L=1, test=False):
		# Math:
		# Loss = -E_{q(z|x,y)}[logp(x|y,z) + logp(y)] + KL(q(z|x,y)||p(z))
		loss_reconstruction = 0
		batchsize = x.data.shape[0]
		z_mean, z_ln_var = self.encoder_xy_z(x, y, test=test, sample_output=False)
		# -E_{q(z|x,y)}[logp(x|y,z) + logp(y)]
		for l in xrange(L):
			# Sample z
			z = F.gaussian(z_mean, z_ln_var)
			# Decode
			x_expectation = self.decode_zy_x(z, y, test=test)
			# logp(y) = log(1/num_labels)
			reconstuction_loss = self.bernoulli_nll_keepbatch(x, x_expectation) - math.log(1.0 / y.data.shape[1])
			loss_reconstruction += reconstuction_loss
		loss_reconstruction /= L
		# KL(q(z|x,y)||p(z))
		loss_kld_regularization = self.gaussian_kl_divergence_keepbatch(z_mean, z_ln_var)

		return loss_reconstruction, loss_kld_regularization


class SoftmaxEncoder(chainer.Chain):
	def __init__(self, **layers):
		super(SoftmaxEncoder, self).__init__(**layers)
		self.activation_function = "tanh"
		self.output_activation_function = None
		self.apply_batchnorm_to_input = True
		self.apply_batchnorm = True
		self.apply_dropout = True

	@property
	def xp(self):
		return np if self._cpu else cuda.cupy

	def forward_one_step(self, x, test):
		f = activations[self.activation_function]
		chain = [x]

		for i in range(self.n_layers):
			u = chain[-1]
			if i == 0:
				if self.apply_batchnorm_to_input:
					u = getattr(self, "batchnorm_%d" % i)(u, test=test)
			else:
				if self.apply_batchnorm:
					u = getattr(self, "batchnorm_%d" % i)(u, test=test)
			u = getattr(self, "layer_%i" % i)(u)
			if i == self.n_layers - 1:
				if self.output_activation_function is None:
					output = u
				else:
					output = activations[self.output_activation_function](u)
			else:
				output = f(u)
				if self.apply_dropout:
					output = F.dropout(output, train=not test)
			chain.append(output)

		return chain[-1]

	def __call__(self, x, test=False, softmax=True):
		output = self.forward_one_step(x, test=test)
		if softmax:
			return F.softmax(output)
		return output

class GaussianEncoder(chainer.Chain):
	def __init__(self, **layers):
		super(GaussianEncoder, self).__init__(**layers)
		self.activation_function = "tanh"
		self.output_activation_function = None
		self.apply_batchnorm_to_input = True
		self.apply_batchnorm = True
		self.apply_dropout = True

	@property
	def xp(self):
		return np if self._cpu else cuda.cupy

	def forward_one_step(self, x, y, test=False, sample_output=True):
		f = activations[self.activation_function]

		if self.apply_batchnorm_to_input:
			merged_input_mean = f(self.layer_mean_merge_x(self.batchnorm_mean_merge_x(x, test=test)) + self.layer_mean_merge_y(y))
			merged_input_var = f(self.layer_var_merge_x(self.batchnorm_var_merge_x(x, test=test)) + self.layer_var_merge_y(y))
		else:
			merged_input_mean = f(self.layer_mean_merge_x(x) + self.layer_mean_merge_y(y))
			merged_input_var = f(self.layer_var_merge_x(x) + self.layer_var_merge_y(y))

		chain_mean = [merged_input_mean]
		chain_variance = [merged_input_var]

		# Hidden
		for i in range(self.n_layers):
			u = chain_mean[-1]
			if self.apply_batchnorm:
				u = getattr(self, "batchnorm_mean_%d" % i)(u, test=test)
			u = getattr(self, "layer_mean_%i" % i)(u)
			if i == self.n_layers - 1:
				if self.output_activation_function is None:
					output = u
				else:
					output = activations[self.output_activation_function](u)
			else:
				output = f(u)
				if self.apply_dropout:
					output = F.dropout(output, train=not test)
			chain_mean.append(output)

			u = chain_variance[-1]
			if self.apply_batchnorm:
				u = getattr(self, "batchnorm_var_%i" % i)(u, test=test)
			u = getattr(self, "layer_var_%i" % i)(u)
			if i == self.n_layers - 1:
				if self.output_activation_function is None:
					output = u
				else:
					output = activations[self.output_activation_function](u)
			else:
				output = f(u)
				if self.apply_dropout:
					output = F.dropout(output, train=not test)
			chain_variance.append(output)

		mean = chain_mean[-1]
		# log(sigma^2)
		ln_var = chain_variance[-1]

		return mean, ln_var

	def __call__(self, x, y, test=False, sample_output=True):
		mean, ln_var = self.forward_one_step(x, y, test=test, sample_output=sample_output)
		if sample_output:
			return F.gaussian(mean, ln_var)
		return mean, ln_var

# Network structure is same as the GaussianEncoder
class GaussianDecoder(GaussianEncoder):

	def __call__(self, z, y, test=False, output_pixel_value=False):
		mean, ln_var = self.forward_one_step(z, y, test=test, sample_output=False)
		if output_pixel_value:
			return F.gaussian(mean, ln_var)
		return mean, ln_var

class BernoulliDecoder(SoftmaxEncoder):

	def forward_one_step(self, z, y, test):
		f = activations[self.activation_function]

		if self.apply_batchnorm_to_input:
			merged_input = f(self.layer_merge_z(self.batchnorm_merge_z(z, test=test)) + self.layer_merge_y(y))
		else:
			merged_input = f(self.layer_merge_z(z) + self.layer_merge_y(y))

		chain = [merged_input]

		for i in range(self.n_layers):
			u = chain[-1]
			if self.apply_batchnorm:
				u = getattr(self, "batchnorm_%d" % i)(u, test=test)
			u = getattr(self, "layer_%i" % i)(u)
			if i == self.n_layers - 1:
				if self.output_activation_function is None:
					output = u
				else:
					output = activations[self.output_activation_function](u)
			else:
				output = f(u)
				if self.apply_dropout:
					output = F.dropout(output, train=not test)
			chain.append(output)

		return chain[-1]

	def __call__(self, z, y, test=False, output_pixel_value=False):
		output = self.forward_one_step(z, y, test=test)
		if output_pixel_value:
			return F.sigmoid(output)
		return output


class Multiply(function.Function):
	def check_type_forward(self, in_types):
		n_in = in_types.size()
		type_check.expect(n_in == 2)
		matrix_type, vector_type = in_types

		type_check.expect(
			matrix_type.dtype == np.float32,
			vector_type.dtype == np.float32,
			matrix_type.ndim == 2,
			vector_type.ndim == 2,
		)

	def forward(self, inputs):
		xp = cuda.get_array_module(inputs[0])
		matrix, vector = inputs
		output = matrix * vector
		return output,

	def backward(self, inputs, grad_outputs):
		xp = cuda.get_array_module(inputs[0])
		matrix, vector = inputs
		return grad_outputs[0] * vector, xp.sum(grad_outputs[0] * matrix, axis=1).reshape(-1, 1)

def multiply(matrix, vector):
	return Multiply()(matrix, vector)