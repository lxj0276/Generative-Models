from __future__ import division
import os
import time
from glob import glob
import tensorflow as tf
import numpy as np
from six.moves import xrange

from ops import *
from utils import *
from tensorflow.contrib import losses


class VAE(object):
    def __init__(self, sess, image_size=108,sample_size=64,
                 batch_size=64, output_size=64, z_dim=100, ef_dim=64, df_dim=64,
                 efc_dim=2048, dfc_dim=256,sigma=1.0, dataset_name='default',
                 checkpoint_dir=None, sample_dir=None):
        """

        Args:
            sess: TensorFlow session
            batch_size: The size of batch. Should be specified before training.
            output_size: (optional) The resolution in pixels of the images. [64]
            z_dim: (optional) Dimension of dim for Z. [100]
            df_dim: (optional) Dimension of decoder filters in last conv layer. [64]
            ef_dim: (optional) Dimension of encoder filters in first conv layer. [64]
            efc_dim: (optional) Dimension of encoder units for for fully connected layer. [1024]
            dfc_dim: (optional) Dimension of decoder units for fully connected layer. [1024]
        """
        self.sess = sess
        self.batch_size = batch_size
        self.image_size = image_size
        self.output_size = output_size
        self.sample_size=sample_size

        self.z_dim = z_dim  # 100

        self.ef_dim = ef_dim
        self.df_dim = df_dim

        self.efc_dim = efc_dim
        self.dfc_dim = dfc_dim

        # batch normalization : deals with poor initialization helps gradient flow
        self.d_bn0 = batch_norm(name='d_bn0')
        self.d_bn1 = batch_norm(name='d_bn1')
        self.d_bn2 = batch_norm(name='d_bn2')
        self.d_bn3 = batch_norm(name='d_bn3')

        self.e_bn0 = batch_norm(name='e_bn0')
        self.e_bn1 = batch_norm(name='e_bn1')
        self.e_bn2 = batch_norm(name='e_bn2')
        self.e_bn3 = batch_norm(name='e_bn3')

        #Weight of the reconstruction error
        self.sigma=sigma

        self.dataset_name = dataset_name
        self.checkpoint_dir = checkpoint_dir
        self.sample_dir=sample_dir
        self.build_model()


    def build_model(self):
        self.images = tf.placeholder(tf.float32, [self.batch_size] + [self.output_size, self.output_size, 3],
                                     name='real_images')
        self.codes_mean,self.codes_sigma = self.encoder(self.images)
        self.codes_sigma=tf.sqrt(tf.exp(self.codes_sigma))
        self.codes=self.codes_mean+self.codes_sigma*tf.random_normal([self.batch_size, self.z_dim])
        self.results = self.decoder(self.codes)

        self.sample_codes=tf.placeholder(tf.float32, [None,self.z_dim],
                                     name='sample_codes')
        self.sampler = self.sampler(self.sample_codes)

        regularization_loss_one_dimension=-1.0+tf.square(self.codes_mean)+\
            tf.square(self.codes_sigma)-2*tf.log(self.codes_sigma+1e-8)
        self.regularization_loss=0.5*tf.reduce_mean(tf.reduce_sum(regularization_loss_one_dimension,1))
        self.reconstruction_loss=0.5/tf.square(self.sigma)*tf.reduce_mean\
            (tf.reduce_sum(tf.square(self.images-self.results),[1,2,3]),0)
        self.total_loss=self.regularization_loss+self.reconstruction_loss

        self.regular_sum = scalar_summary("regularization_loss", self.regularization_loss)
        self.recon_sum = scalar_summary("reconstruction_loss", self.reconstruction_loss)
        self.total_sum=scalar_summary("total_loss", self.total_loss)
        self.saver = tf.train.Saver()

    def train(self, config):
        optimizer=tf.train.AdamOptimizer(config.learning_rate, beta1=config.beta1) \
            .minimize(self.total_loss)

        try:
            tf.initialize_all_variables().run()
        except:
            init_op = tf.global_variables_initializer()
            self.sess.run(init_op)

        merged_summary_op = tf.merge_all_summaries()
        self.writer = SummaryWriter("./logs", self.sess.graph)

        counter = 1
        start_time = time.time()

        if self.load(self.checkpoint_dir):
            print(" [*] Load SUCCESS")
        else:
            print(" [!] Load failed...")

        for epoch in xrange(config.epoch):
            data = glob(os.path.join("./data", config.dataset, "*.jpg"))
            batch_idxs = min(len(data), config.train_size) // config.batch_size

            for idx in xrange(0, batch_idxs):
                batch_files = data[idx * config.batch_size:(idx + 1) * config.batch_size]
                batch = [get_image(batch_file) for batch_file in batch_files]
                batch_images = np.array(batch).astype(np.float32)

                sample_codes = np.random.normal(0, 1.0, [self.sample_size, self.z_dim]) \
                    .astype(np.float32)

                # Update the autoencoder
                _, summary_str = self.sess.run([optimizer,merged_summary_op],
                                               feed_dict={self.images: batch_images})
                self.writer.add_summary(summary_str, counter)

                total_loss = self.total_loss.eval({self.images: batch_images})

                counter += 1
                print("Epoch: [%2d] [%4d/%4d] time: %4.4f, loss: %.8f" \
                      % (epoch, idx, batch_idxs,
                         time.time() - start_time,total_loss))

                if np.mod(counter, 100) == 1:
                    samples = self.sess.run(
                        self.sampler,
                        feed_dict={self.sample_codes: sample_codes}
                    )
                    save_images(samples, [8, 8],
                                './{}/train_{:02d}_{:04d}.png'.format(config.sample_dir, epoch, idx))

                if np.mod(counter, 500) == 2:
                    self.save(config.checkpoint_dir, counter)




    def encoder(self, image, reuse=False):
        with tf.variable_scope("encoder") as scope:
            if reuse:
                scope.reuse_variables()
            h0 = lrelu(self.e_bn0(conv2d(image, self.ef_dim, name='e_h0_conv')))
            h1 = lrelu(self.e_bn1(conv2d(h0, self.ef_dim * 2, name='e_h1_conv')))
            h2 = lrelu(self.e_bn2(conv2d(h1, self.ef_dim * 4, name='e_h2_conv')))
            h3 = lrelu(self.e_bn3(linear(tf.reshape(h2, [self.batch_size, -1]),self.efc_dim, 'e_h3_lin')))
            h4 = lrelu(linear(h3, self.z_dim*2, 'e_h4_lin'))
            h5=tf.nn.tanh(h4)#unnecessary
            return h5[:,0:self.z_dim],h5[:,self.z_dim:] #mu,sigma


    def decoder(self, z):
        with tf.variable_scope("decoder") as scope:
            s = self.output_size
            s2, s4, s8 = int(s / 2), int(s / 4), int(s / 8)

            # project `z` and reshape
            self.z_, self.h0_w, self.h0_b = linear(z, self.dfc_dim * s8 * s8, 'd_h0_lin', with_w=True)

            self.h0 = tf.reshape(self.z_, [-1, s8, s8, self.dfc_dim ])
            h0 = lrelu(self.d_bn0(self.h0))

            self.h1, self.h1_w, self.h1_b = deconv2d(h0,
                                                     [self.batch_size, s4, s4, self.df_dim*4], name='d_h1',
                                                     with_w=True)
            h1 = lrelu(self.d_bn1(self.h1))

            h2, self.h2_w, self.h2_b = deconv2d(h1,
                                                [self.batch_size, s2, s2, self.df_dim *2], name='d_h2',
                                                with_w=True)
            h2 = lrelu(self.d_bn2(h2))

            h3, self.h3_w, self.h3_b = deconv2d(h2,
                                                [self.batch_size, s, s, self.df_dim], name='d_h3',
                                                with_w=True)

            h3 = lrelu(self.d_bn3(h3))
            h4, self.h4_w, self.h4_b = deconv2d(h3,
                                                [self.batch_size, s, s, 3],d_h=1,d_w=1, name='d_h4',
                                                with_w=True)

            return tf.nn.tanh(h4)


    def sampler(self, z):
        with tf.variable_scope("decoder") as scope:
            scope.reuse_variables()

            s = self.output_size
            s2, s4, s8 = int(s / 2), int(s / 4), int(s / 8)

            # project `z` and reshape
            self.z_, self.h0_w, self.h0_b = linear(z, self.dfc_dim * s8 * s8, 'd_h0_lin', with_w=True)

            self.h0 = tf.reshape(self.z_, [-1, s8, s8, self.dfc_dim])
            h0 = lrelu(self.d_bn0(self.h0))

            self.h1, self.h1_w, self.h1_b = deconv2d(h0,
                                                     [self.batch_size, s4, s4, self.df_dim * 4], name='d_h1',
                                                     with_w=True)
            h1 = lrelu(self.d_bn1(self.h1))

            h2, self.h2_w, self.h2_b = deconv2d(h1,
                                                [self.batch_size, s2, s2, self.df_dim * 2], name='d_h2',
                                                with_w=True)
            h2 = lrelu(self.d_bn2(h2))

            h3, self.h3_w, self.h3_b = deconv2d(h2,
                                                [self.batch_size, s, s, self.df_dim], name='d_h3',
                                                with_w=True)

            h3 = lrelu(self.d_bn3(h3))
            h4, self.h4_w, self.h4_b = deconv2d(h3,
                                                [self.batch_size, s, s, 3], d_h=1, d_w=1, name='d_h4',
                                                with_w=True)

            return tf.nn.tanh(h4)



    def save(self, checkpoint_dir, step):
        model_name = "VAE.model"
        model_dir = "%s_%s_%s" % (self.dataset_name, self.batch_size, self.output_size)
        checkpoint_dir = os.path.join(checkpoint_dir, model_dir)

        if not os.path.exists(checkpoint_dir):
            os.makedirs(checkpoint_dir)

        self.saver.save(self.sess,
                        os.path.join(checkpoint_dir, model_name),
                        global_step=step)

    def load(self, checkpoint_dir):
        print(" [*] Reading checkpoints...")

        model_dir = "%s_%s_%s" % (self.dataset_name, self.batch_size, self.output_size)
        checkpoint_dir = os.path.join(checkpoint_dir, model_dir)

        ckpt = tf.train.get_checkpoint_state(checkpoint_dir)
        if ckpt and ckpt.model_checkpoint_path:
            ckpt_name = os.path.basename(ckpt.model_checkpoint_path)
            self.saver.restore(self.sess, os.path.join(checkpoint_dir, ckpt_name))
            print(" [*] Success to read {}".format(ckpt_name))
            return True
        else:
            print(" [*] Failed to find a checkpoint")
            return False