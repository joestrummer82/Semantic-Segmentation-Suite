import tensorflow as tf
from tensorflow.contrib import slim
import numpy as np
import resnet_v1
import os, sys

def Upsampling(inputs,feature_map_shape):
    return tf.image.resize_bilinear(inputs, size=feature_map_shape)

def ConvUpscaleBlock(inputs, n_filters, kernel_size=[3, 3], scale=2):
    """
    Basic conv transpose block for Encoder-Decoder upsampling
    Apply successivly Transposed Convolution, BatchNormalization, ReLU nonlinearity
    """
    net = slim.conv2d_transpose(inputs, n_filters, kernel_size=[3, 3], stride=[2, 2], activation_fn=None)
    net = tf.nn.relu(slim.batch_norm(net))
    return net

def ConvBlock(inputs, n_filters, kernel_size=[3, 3]):
    """
    Basic conv block for Encoder-Decoder
    Apply successivly Convolution, BatchNormalization, ReLU nonlinearity
    """
    net = slim.conv2d(inputs, n_filters, kernel_size, activation_fn=None, normalizer_fn=None)
    net = tf.nn.relu(slim.batch_norm(net))
    return net

def InterpBlock(net, level, feature_map_shape, pooling_type):
    
    # Compute the kernel and stride sizes according to how large the final feature map will be
    # When the kernel size and strides are equal, then we can compute the final feature map size
    # by simply dividing the current size by the kernel or stride size
    # The final feature map sizes are 1x1, 2x2, 3x3, and 6x6. We round to the closest integer
    kernel_size = [int(np.round(float(feature_map_shape[0]) / float(level))), int(np.round(float(feature_map_shape[1]) / float(level)))]
    stride_size = kernel_size

    net = slim.pool(net, kernel_size, stride=stride_size, pooling_type='MAX')
    net = slim.conv2d(net, 512, [1, 1], activation_fn=None)
    net = slim.batch_norm(net)
    net = tf.nn.relu(net)
    net = Upsampling(net, feature_map_shape)
    return net

def PyramidPoolingModule(inputs, feature_map_shape, pooling_type):
    """
    Build the Pyramid Pooling Module.
    """

    interp_block1 = InterpBlock(inputs, 1, feature_map_shape, pooling_type)
    interp_block2 = InterpBlock(inputs, 2, feature_map_shape, pooling_type)
    interp_block3 = InterpBlock(inputs, 3, feature_map_shape, pooling_type)
    interp_block6 = InterpBlock(inputs, 6, feature_map_shape, pooling_type)

    res = tf.concat([inputs, interp_block6, interp_block3, interp_block2, interp_block1], axis=-1)
    return res



def build_pspnet(inputs, label_size, preset_model='PSPNet-Res50', pooling_type = "MAX", num_classes=12, 
    weight_decay=1e-5, upscaling_method="bilinear", is_training=True, pretrained_dir="models"):
    """
    Builds the PSPNet model. 

    Arguments:
      inputs: The input tensor
      label_size: Size of the final label tensor. We need to know this for proper upscaling 
      preset_model: Which model you want to use. Select which ResNet model to use for feature extraction 
      num_classes: Number of classes
      pooling_type: Max or Average pooling

    Returns:
      PSPNet model
    """

    inputs = mean_image_subtraction(inputs)

    if preset_model == 'PSPNet-Res50':
        with slim.arg_scope(resnet_v1.resnet_arg_scope(weight_decay=weight_decay)):
            logits, end_points = resnet_v1.resnet_v1_50(inputs, is_training=is_training, scope='resnet_v1_50')
            # PSPNet requires pre-trained ResNet weights
            init_fn = slim.assign_from_checkpoint_fn(os.path.join(pretrained_dir, 'resnet_v1_50.ckpt'), slim.get_model_variables('resnet_v1_50'))
    elif preset_model == 'PSPNet-Res101':
        with slim.arg_scope(resnet_v1.resnet_arg_scope(weight_decay=weight_decay)):
            logits, end_points = resnet_v1.resnet_v1_101(inputs, is_training=is_training, scope='resnet_v1_101')
            # PSPNet requires pre-trained ResNet weights
            init_fn = slim.assign_from_checkpoint_fn(os.path.join(pretrained_dir, 'resnet_v1_101.ckpt'), slim.get_model_variables('resnet_v1_101'))
    elif preset_model == 'PSPNet-Res152':
        with slim.arg_scope(resnet_v1.resnet_arg_scope(weight_decay=weight_decay)):
            logits, end_points = resnet_v1.resnet_v1_152(inputs, is_training=is_training, scope='resnet_v1_152')
            # PSPNet requires pre-trained ResNet weights
            init_fn = slim.assign_from_checkpoint_fn(os.path.join(pretrained_dir, 'resnet_v1_152.ckpt'), slim.get_model_variables('resnet_v1_152'))
    else:
        raise ValueError("Unsupported ResNet model '%s'. This function only supports ResNet 50, ResNet 101, and ResNet 152" % (preset_model))

    


    f = [end_points['pool5'], end_points['pool4'],
         end_points['pool3'], end_points['pool2']]


    feature_map_shape = [int(x / 8.0) for x in label_size]
    psp = PyramidPoolingModule(f[2], feature_map_shape=feature_map_shape, pooling_type=pooling_type)

    net = slim.conv2d(psp, 512, [3, 3], activation_fn=None)
    net = slim.batch_norm(net)
    net = tf.nn.relu(net)

    if upscaling_method.lower() == "conv":
        net = ConvUpscaleBlock(net, 256, kernel_size=[3, 3], scale=2)
        net = ConvBlock(net, 256)
        net = ConvUpscaleBlock(net, 128, kernel_size=[3, 3], scale=2)
        net = ConvBlock(net, 128)
        net = ConvUpscaleBlock(net, 64, kernel_size=[3, 3], scale=2)
        net = ConvBlock(net, 64)
    elif upscaling_method.lower() == "bilinear":
        net = Upsampling(net, label_size)

    
    net = slim.dropout(net, keep_prob=(0.9))
    
    net = slim.conv2d(net, num_classes, [1, 1], activation_fn=None, scope='logits')

    return net, init_fn


def mean_image_subtraction(inputs, means=[123.68, 116.78, 103.94]):
    inputs=tf.to_float(inputs)
    num_channels = inputs.get_shape().as_list()[-1]
    if len(means) != num_channels:
      raise ValueError('len(means) must match the number of channels')
    channels = tf.split(axis=3, num_or_size_splits=num_channels, value=inputs)
    for i in range(num_channels):
        channels[i] -= means[i]
    return tf.concat(axis=3, values=channels)