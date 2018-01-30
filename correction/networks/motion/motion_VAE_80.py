import numpy as np
import matplotlib.pyplot as plt

from keras.layers import Input, Lambda, Layer, concatenate, LeakyReLU, Dense, Reshape, Flatten, BatchNormalization
from keras.layers import Conv2D, Conv2DTranspose
from keras.models import Model
from keras.callbacks import EarlyStopping, ReduceLROnPlateau, ModelCheckpoint
from keras import backend as K
from keras import metrics
import os

def LeakyReluConv2D(filters, kernel_size, strides, padding):
    def f(inputs):
        conv2d = Conv2D(filters,
                        kernel_size=kernel_size,
                        strides=strides,
                        padding=padding)(inputs)
        return LeakyReLU()(conv2d)
    return f

def LeakyReluConv2DTranspose(filters, kernel_size, strides, padding):
    def f(inputs):
        conv2d = Conv2DTranspose(filters=filters,
                                 kernel_size=kernel_size,
                                 strides=strides,
                                 padding=padding)(inputs)
        return LeakyReLU()(conv2d)
    return f

def sliceRef(input):
    return input[:input.shape[0]//2, :, :, :]

def sliceArt(input):
    return input[input.shape[0]//2:, :, :, :]

def sampling(args):
    z_mean, z_log_var = args
    epsilon = K.random_normal(shape=(K.shape(z_mean)[0], 512), mean=0.,
                              stddev=1.0)
    return z_mean + K.exp(z_log_var) * epsilon

def encode(input):
    conv_1 = LeakyReluConv2D(filters=32, kernel_size=3, strides=1, padding='same')(input)
    conv_2 = LeakyReluConv2D(filters=64, kernel_size=3, strides=2, padding='same')(conv_1)
    conv_3 = LeakyReluConv2D(filters=128, kernel_size=3, strides=1, padding='same')(conv_2)
    return conv_3

def encode_shared(input):
    conv_1 = LeakyReluConv2D(filters=256, kernel_size=3, strides=1, padding='same')(input)
    conv_2 = LeakyReluConv2D(filters=256, kernel_size=3, strides=2, padding='same')(conv_1)
    conv_3 = LeakyReluConv2D(filters=256, kernel_size=3, strides=1, padding='same')(conv_2)
    conv_4 = LeakyReluConv2D(filters=256, kernel_size=3, strides=2, padding='same')(conv_3)
    flat = Flatten()(conv_4)

    mu = Dense(512)(flat)
    sd = Dense(512)(flat)

    z = Lambda(sampling, output_shape=(500,))([mu, sd])

    return z, mu, sd

def decode(input):
    dense = Dense(25600)(input)
    reshape = Reshape((256, 10, 10))(dense)
    output = LeakyReluConv2DTranspose(filters=256, kernel_size=3, strides=2, padding='same')(reshape)
    output = LeakyReluConv2DTranspose(filters=256, kernel_size=3, strides=1, padding='same')(output)
    output = LeakyReluConv2DTranspose(filters=256, kernel_size=3, strides=2, padding='same')(output)
    output = LeakyReluConv2DTranspose(filters=128, kernel_size=3, strides=1, padding='same')(output)
    output = LeakyReluConv2DTranspose(filters=64, kernel_size=3, strides=2, padding='same')(output)
    output = Conv2DTranspose(filters=1, kernel_size=1, strides=1, padding='same', activation='tanh')(output)
    return output

def createModel(patchSize):
    # input corrupted and non-corrupted image
    x_ref = Input(shape=(1, patchSize[0], patchSize[1]))
    x_art = Input(shape=(1, patchSize[0], patchSize[1]))

    # create respective encoders
    encoded_ref = encode(x_ref)
    encoded_art = encode(x_art)

    # concatenate the encoded features together
    combined = concatenate([encoded_ref, encoded_art], axis=0)

    # create the shared encoder
    z, mu, sd = encode_shared(combined)

    # create the decoder
    decoded = decode(z)

    # separate the concatenated images
    decoded_ref2ref = Lambda(sliceRef)(decoded)
    decoded_art2ref = Lambda(sliceArt)(decoded)

    loss_ref2ref = patchSize[0] * patchSize[1] * metrics.binary_crossentropy(K.flatten(x_ref), K.flatten(decoded_ref2ref))
    loss_art2ref = patchSize[0] * patchSize[1] * metrics.binary_crossentropy(K.flatten(x_ref), K.flatten(decoded_art2ref))
    loss_kl = - 0.5 * K.sum(1 + mu - K.square(mu) - K.exp(sd), axis=-1)

    # generate the VAE and encoder model
    vae = Model([x_ref, x_art], [decoded_ref2ref, decoded_art2ref])

    # add loss
    vae.add_loss(K.mean(loss_ref2ref + loss_art2ref + loss_kl))
    return vae

def fTrain(dData, sOutPath, patchSize, dHyper):
    # parse inputs
    batchSize = [128] if dHyper['batchSize'] is None else dHyper['batchSize']
    learningRate = [0.001] if dHyper['learningRate'] is None else dHyper['learningRate']
    epochs = 300 if dHyper['epochs'] is None else dHyper['epochs']

    for iBatch in batchSize:
        for iLearn in learningRate:
            fTrainInner(dData, sOutPath, patchSize, epochs, iBatch, iLearn)

def fTrainInner(dData, sOutPath, patchSize, epochs, batchSize, lr):
    train_ref = dData['train_ref']
    train_art = dData['train_art']
    test_ref = dData['test_ref']
    test_art = dData['test_art']

    train_ref = np.expand_dims(train_ref, axis=1)
    train_art = np.expand_dims(train_art, axis=1)
    test_ref = np.expand_dims(test_ref, axis=1)
    test_art = np.expand_dims(test_art, axis=1)

    vae = createModel(patchSize)
    vae.compile(optimizer='adam', loss=None)
    vae.summary()

    print('Training with epochs {} batch size {} learning rate {}'.format(epochs, batchSize, lr))

    weights_file = sOutPath + os.sep + 'vae_weight_ps_{}_bs_{}.h5'.format(patchSize[0], batchSize)

    callback_list = [EarlyStopping(monitor='val_loss', patience=5, verbose=1)]
    callback_list.append(ModelCheckpoint(weights_file, monitor='val_loss', verbose=1, period=1, save_best_only=True, save_weights_only=True))
    callback_list.append(ReduceLROnPlateau(monitor='val_loss', factor=0.5, patience=3, min_lr=1e-4, verbose=1))

    vae.fit([train_ref, train_art],
            shuffle=True,
            epochs=epochs,
            batch_size=batchSize,
            validation_data=([test_ref, test_art], None),
            verbose=1,
            callbacks=callback_list)

def fPredict(dData, sOutPath, patchSize, dHyper):
    weights_file = sOutPath + os.sep + '{}.h5'.format(dHyper['bestModel'])

    vae = createModel(patchSize)
    vae.compile(optimizer='adam', loss=None)

    vae.load_weights(weights_file)

    # TODO: adapt the embedded batch size
    predict_ref, predict_art = vae.predict([dData['test_ref'][:128], dData['test_art'][:128]], 128, verbose=1)

    test_ref = np.squeeze(dData['test_ref'][:128], axis=1)
    test_art = np.squeeze(dData['test_art'][:128], axis=1)
    predict_ref = np.squeeze(predict_ref, axis=1)
    predict_art = np.squeeze(predict_art, axis=1)

    nPatch = predict_ref.shape[0]

    for i in range(nPatch//6):
        fig, axes = plt.subplots(nrows=5, ncols=4)
        plt.gray()

        cols_title = ['test_ref', 'predict_ref', 'test_art', 'predict_art']

        for ax, col in zip(axes[0], cols_title):
            ax.set_title(col)

        for j in range(5):
            axes[j, 0].imshow(test_ref[6*i+j])
            axes[j, 1].imshow(predict_ref[6*i+j])
            axes[j, 2].imshow(test_art[6*i+j])
            axes[j, 3].imshow(predict_art[6*i+j])

        figManager = plt.get_current_fig_manager()
        figManager.resize(*figManager.window.maxsize())

        plt.show()

