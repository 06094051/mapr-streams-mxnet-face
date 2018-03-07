from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from symbol.resnet import *
from symbol.config import config
from symbol.processing import bbox_pred, clip_boxes, nms
import face_embedding
from mapr_streams_python import Consumer, KafkaError, Producer
import numpy as np
import cv2, os, json, time
import mxnet as mx
from scipy import misc
import sys
import os
import argparse
import tensorflow as tf
import random
import sklearn
from sklearn.decomposition import PCA
from time import sleep
from easydict import EasyDict as edict
from mtcnn_detector import MtcnnDetector
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src', 'common'))
import face_image
import face_preprocess

def ch_dev(arg_params, aux_params, ctx):
    new_args = dict()
    new_auxs = dict()
    for k, v in arg_params.items():
        new_args[k] = v.as_in_context(ctx)
    for k, v in aux_params.items():
        new_auxs[k] = v.as_in_context(ctx)
    return new_args, new_auxs

def resize(im, target_size, max_size):
    """
    only resize input image to target size and return scale
    :param im: BGR image input by opencv
    :param target_size: one dimensional size (the short side)
    :param max_size: one dimensional max size (the long side)
    :return:
    """
    im_shape = im.shape
    im_size_min = np.min(im_shape[0:2])
    im_size_max = np.max(im_shape[0:2])
    im_scale = float(target_size) / float(im_size_min)
    if np.round(im_scale * im_size_max) > max_size:
        im_scale = float(max_size) / float(im_size_max)
    im = cv2.resize(im, None, None, fx=im_scale, fy=im_scale, interpolation=cv2.INTER_LINEAR)
    return im, im_scale

if __name__ == '__main__':
    ctx = mx.gpu(0)
    _, arg_params, aux_params = mx.model.load_checkpoint('mxnet-face-fr50', 0)
    arg_params, aux_params = ch_dev(arg_params, aux_params, ctx)
    sym = resnet_50(num_class=2)
    model = face_embedding.FaceModel()
    
    img_orig = cv2.imread('sam.jpg')
    img, scale = resize(img_orig.copy(), 600, 1000)
    im_info = np.array([[img.shape[0], img.shape[1], scale]], dtype=np.float32)  # (h, w, scale)
    img = np.swapaxes(img, 0, 2)
    img = np.swapaxes(img, 1, 2)  # change to (c, h, w) order
    img = img[np.newaxis, :]  # extend to (n, c, h, w)
    arg_params["data"] = mx.nd.array(img, ctx)
    arg_params["im_info"] = mx.nd.array(im_info, ctx)
    exe = sym.bind(ctx, arg_params, args_grad=None, grad_req="null", aux_states=aux_params)

    exe.forward(is_train=False)
    output_dict = {name: nd for name, nd in zip(sym.list_outputs(), exe.outputs)}
    rois = output_dict['rpn_rois_output'].asnumpy()[:, 1:]  # first column is index
    scores = output_dict['cls_prob_reshape_output'].asnumpy()[0]
    bbox_deltas = output_dict['bbox_pred_reshape_output'].asnumpy()[0]
    pred_boxes = bbox_pred(rois, bbox_deltas)
    pred_boxes = clip_boxes(pred_boxes, (im_info[0][0], im_info[0][1]))
    cls_boxes = pred_boxes[:, 4:8]
    cls_scores = scores[:, 1]
    keep = np.where(cls_scores >0.6)[0]
    cls_boxes = cls_boxes[keep, :]
    cls_scores = cls_scores[keep]
    dets = np.hstack((cls_boxes, cls_scores[:, np.newaxis])).astype(np.float32)
    keep = nms(dets.astype(np.float32), 0.3)
    dets = dets[keep, :]
    bbox = dets[0, :4]
    roundfunc = lambda t: int(round(t/scale))
    vfunc = np.vectorize(roundfunc)
    bbox = vfunc(bbox)
    f1, jpeg = model.get_feature(img_orig, bbox, None)
    f1T = f1.T

    img_orig = cv2.imread('frances.jpg')
    img, scale = resize(img_orig.copy(), 600, 1000)
    im_info = np.array([[img.shape[0], img.shape[1], scale]], dtype=np.float32)  # (h, w, scale)
    img = np.swapaxes(img, 0, 2)
    img = np.swapaxes(img, 1, 2)  # change to (c, h, w) order
    img = img[np.newaxis, :]  # extend to (n, c, h, w)
    arg_params["data"] = mx.nd.array(img, ctx)
    arg_params["im_info"] = mx.nd.array(im_info, ctx)
    exe = sym.bind(ctx, arg_params, args_grad=None, grad_req="null", aux_states=aux_params)

    exe.forward(is_train=False)
    output_dict = {name: nd for name, nd in zip(sym.list_outputs(), exe.outputs)}
    rois = output_dict['rpn_rois_output'].asnumpy()[:, 1:]  # first column is index
    scores = output_dict['cls_prob_reshape_output'].asnumpy()[0]
    bbox_deltas = output_dict['bbox_pred_reshape_output'].asnumpy()[0]
    pred_boxes = bbox_pred(rois, bbox_deltas)
    pred_boxes = clip_boxes(pred_boxes, (im_info[0][0], im_info[0][1]))
    cls_boxes = pred_boxes[:, 4:8]
    cls_scores = scores[:, 1]
    keep = np.where(cls_scores > 0.6 )[0]
    cls_boxes = cls_boxes[keep, :]
    cls_scores = cls_scores[keep]
    dets = np.hstack((cls_boxes, cls_scores[:, np.newaxis])).astype(np.float32)
    keep = nms(dets.astype(np.float32), 0.3)
    dets = dets[keep, :]
    bbox = dets[0, :4]
    roundfunc = lambda t: int(round(t/scale))
    vfunc = np.vectorize(roundfunc)
    bbox = vfunc(bbox)
    f2, jpeg = model.get_feature(img_orig, bbox, None)
    f2T = f2.T

    c = Consumer({'group.id': 'consumer12',
              'default.topic.config': {'auto.offset.reset': 'earliest', 'enable.auto.commit': 'false'}})
    # c.subscribe(['/user/mapr/nextgenDLapp/rawvideostream:topic1'])
    c.subscribe(['/tmp/rawvideostream:topic1'])
    running = True
    p = Producer({'streams.producer.default.stream': '/mapr/DLcluster/tmp/personalstream'})

    while running:
        msg = c.poll(timeout=0)
        if msg is None: continue
        if not msg.error():
            nparr = np.fromstring(msg.value(), np.uint8)
            img_orig = cv2.imdecode(nparr, 1)
            img, scale = resize(img_orig.copy(), 600, 1000)
            im_info = np.array([[img.shape[0], img.shape[1], scale]], dtype=np.float32)  # (h, w, scale)
            img = np.swapaxes(img, 0, 2)
            img = np.swapaxes(img, 1, 2)  # change to (c, h, w) order
            img = img[np.newaxis, :]  # extend to (n, c, h, w)

            arg_params["data"] = mx.nd.array(img, ctx)
            arg_params["im_info"] = mx.nd.array(im_info, ctx)
            exe = sym.bind(ctx, arg_params, args_grad=None, grad_req="null", aux_states=aux_params)

            tic = time.time()
            exe.forward(is_train=False)
            output_dict = {name: nd for name, nd in zip(sym.list_outputs(), exe.outputs)}
            rois = output_dict['rpn_rois_output'].asnumpy()[:, 1:]  # first column is index
            scores = output_dict['cls_prob_reshape_output'].asnumpy()[0]
            bbox_deltas = output_dict['bbox_pred_reshape_output'].asnumpy()[0]
            pred_boxes = bbox_pred(rois, bbox_deltas)
            pred_boxes = clip_boxes(pred_boxes, (im_info[0][0], im_info[0][1]))
            cls_boxes = pred_boxes[:, 4:8]
            cls_scores = scores[:, 1]
            keep = np.where(cls_scores >= 0.6)[0]
            cls_boxes = cls_boxes[keep, :]
            cls_scores = cls_scores[keep]
            dets = np.hstack((cls_boxes, cls_scores[:, np.newaxis])).astype(np.float32)
            keep = nms(dets.astype(np.float32), 0.3)
            dets = dets[keep, :]
            print(dets.shape[0])
            toc = time.time()
#            color = cv2.cvtColor(img_orig, cv2.COLOR_RGB2BGR)

            print("time cost is:{}s".format(toc-tic))
            for i in range(dets.shape[0]):
                bbox = dets[i, :4]
                roundfunc = lambda t: int(round(t/scale))
                vfunc = np.vectorize(roundfunc) 
                bbox = vfunc(bbox)
#                cv2.rectangle(color, (int(round(bbox[0]/scale)), int(round(bbox[1]/scale))),
                f_temp, img_orig = model.get_feature(img_orig, bbox, None) 
                sim1 = np.dot(f_temp, f1T)
                sim2 = np.dot(f_temp, f2T)
                ret, jpeg = cv2.imencode('.png', img_orig)
                if sim1 > 0.27:
                    p.produce('sam', jpeg.tostring())
                    print("sam")
                if sim2 > 0.23:
                    p.produce('frances', jpeg.tostring())
                    print("frances") 
        elif msg.error().code() != KafkaError._PARTITION_EOF:
            print(msg.error())
            running = False
    c.close()
    p.flush()