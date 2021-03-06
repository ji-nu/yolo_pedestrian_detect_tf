import tensorflow as tf
import cv2
import numpy as np
import config as cfg
import sys
import threading

import RPi.GPIO as GPIO
import time

person_num = 0

class Segment(object):
    def __init__(self):
        GPIO.setmode(GPIO.BCM)
        self.segments = (11, 4, 23, 8, 7, 10, 18, 25)
        self.digits = (22, 27, 17, 24)
        
        for seg in self.segments:
            GPIO.setup(seg, GPIO.OUT)
            GPIO.output(seg, 1)

        for dig in self.digits:
            GPIO.setup(dig, GPIO.OUT)
            GPIO.output(dig, 0)
        
        self.num = {' ':(1,1,1,1,1,1,1),
                '0':(0,0,0,0,0,0,1),
                '1':(1,0,0,1,1,1,1),
                '2':(0,0,1,0,0,1,0),
                '3':(0,0,0,0,1,1,0),
                '4':(1,0,0,1,1,0,0),
                '5':(0,1,0,0,1,0,0),
                '6':(0,1,0,0,0,0,0),
                '7':(0,0,0,1,1,1,1),
                '8':(0,0,0,0,0,0,0),
                '9':(0,0,0,0,1,0,0)}

    def __del__(self):
        GPIO.cleanup()

    def set_num(self):
        if not (0 <= person_num < 10000):
            return

        while True:
            s = str(person_num).rjust(4, '0')
            for digit in range(4):
                for loop in range(0,7):
                    GPIO.output(self.segments[loop], self.num[s[digit]][loop])
                GPIO.output(self.digits[digit], 1)
                time.sleep(0.001)
                GPIO.output(self.digits[digit], 0)

class Detector(object):

    def __init__(self):
        self.sess = tf.Session()
        self.saver = tf.train.import_meta_graph('./model/model.ckpt.meta')
        self.saver.restore(self.sess, tf.train.latest_checkpoint('./model/'))
        self.graph = tf.get_default_graph()
        self.input_node = self.graph.get_tensor_by_name('images:0')
        self.output_node = self.graph.get_tensor_by_name('yolo/fc_36/BiasAdd:0')

        self.classes = cfg.CLASSES
        self.num_class = len(self.classes)
        self.image_size = cfg.IMAGE_SIZE
        self.cell_size = cfg.CELL_SIZE
        self.boxes_per_cell = cfg.BOXES_PER_CELL
        self.threshold = cfg.THRESHOLD
        self.iou_threshold = cfg.IOU_THRESHOLD
        self.boundary1 = self.cell_size * self.cell_size * self.num_class
        self.boundary2 = self.boundary1 + \
                         self.cell_size * self.cell_size * self.boxes_per_cell

    def draw_result(self, img, result):
        for i in range(len(result)):
            x = int(result[i][1])
            y = int(result[i][2])
            w = int(result[i][3] / 2)
            h = int(result[i][4] / 2)
            cv2.rectangle(img, (x - w, y - h), (x + w, y + h), (0, 255, 0), 2)
            cv2.rectangle(img, (x - w, y - h - 20),
                          (x + w, y - h), (125, 125, 125), -1)
            lineType = cv2.LINE_AA if cv2.__version__ > '3' else cv2.CV_AA
            cv2.putText(
                img, result[i][0] + ' : %.2f' % result[i][5],
                (x - w + 5, y - h - 7), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (0, 0, 0), 1, lineType)

    def detect(self, img):
        img_h, img_w, _ = img.shape
        inputs = cv2.resize(img, (self.image_size, self.image_size))
        inputs = cv2.cvtColor(inputs, cv2.COLOR_BGR2RGB).astype(np.float32)
        inputs = (inputs / 255.0) * 2.0 - 1.0
        inputs = np.reshape(inputs, (1, self.image_size, self.image_size, 3))

        result = self.detect_from_cvmat(inputs)[0]

        for i in range(len(result)):
            result[i][1] *= (1.0 * img_w / self.image_size)
            result[i][2] *= (1.0 * img_h / self.image_size)
            result[i][3] *= (1.0 * img_w / self.image_size)
            result[i][4] *= (1.0 * img_h / self.image_size)

        return result

    def detect_from_cvmat(self, inputs):
        net_output = self.sess.run(self.output_node,
                                   feed_dict={self.input_node: inputs})
        results = []
        for i in range(net_output.shape[0]):
            results.append(self.interpret_output(net_output[i]))
        return results

    def interpret_output(self, output):
        probs = np.zeros((self.cell_size, self.cell_size,
                          self.boxes_per_cell, self.num_class))
        class_probs = np.reshape(
            output[0:self.boundary1],
            (self.cell_size, self.cell_size, self.num_class))
        scales = np.reshape(
            output[self.boundary1:self.boundary2],
            (self.cell_size, self.cell_size, self.boxes_per_cell))
        boxes = np.reshape(
            output[self.boundary2:],
            (self.cell_size, self.cell_size, self.boxes_per_cell, 4))


        offset = np.array(
            [np.arange(self.cell_size)] * self.cell_size * self.boxes_per_cell)
        offset = np.transpose(
            np.reshape(
                offset,
                [self.boxes_per_cell, self.cell_size, self.cell_size]),
            (1, 2, 0))

        boxes[:, :, :, 0] += offset
        boxes[:, :, :, 1] += np.transpose(offset, (1, 0, 2))
        boxes[:, :, :, :2] = 1.0 * boxes[:, :, :, 0:2] / self.cell_size
        boxes[:, :, :, 2:] = np.square(boxes[:, :, :, 2:])

        boxes *= self.image_size

        for i in range(self.boxes_per_cell):
            for j in range(self.num_class):
                probs[:, :, i, j] = np.multiply(
                    class_probs[:, :, j], scales[:, :, i])

        filter_mat_probs = np.array(probs >= self.threshold, dtype='bool')
        filter_mat_boxes = np.nonzero(filter_mat_probs)
        boxes_filtered = boxes[filter_mat_boxes[0],
                               filter_mat_boxes[1], filter_mat_boxes[2]]

        probs_filtered = probs[filter_mat_probs]


        classes_num_filtered = np.argmax(
            filter_mat_probs, axis=3)[
            filter_mat_boxes[0], filter_mat_boxes[1], filter_mat_boxes[2]]

        argsort = np.array(np.argsort(probs_filtered))[::-1]

        boxes_filtered = boxes_filtered[argsort]
        probs_filtered = probs_filtered[argsort]
        classes_num_filtered = classes_num_filtered[argsort]


        for i in range(len(boxes_filtered)):
            if probs_filtered[i] == 0:
                continue
            for j in range(i + 1, len(boxes_filtered)):
                if self.iou(boxes_filtered[i], boxes_filtered[j]) > self.iou_threshold:
                    probs_filtered[j] = 0.0

        filter_iou = np.array(probs_filtered > 0.0, dtype='bool')
        boxes_filtered = boxes_filtered[filter_iou]
        probs_filtered = probs_filtered[filter_iou]
        classes_num_filtered = classes_num_filtered[filter_iou]


        result = []
        for i in range(len(boxes_filtered)):
            result.append(
                [self.classes[classes_num_filtered[i]],
                 boxes_filtered[i][0],
                 boxes_filtered[i][1],
                 boxes_filtered[i][2],
                 boxes_filtered[i][3],
                 probs_filtered[i]])

        return result

    def iou(self, box1, box2):
        tb = min(box1[0] + 0.5 * box1[2], box2[0] + 0.5 * box2[2]) - \
            max(box1[0] - 0.5 * box1[2], box2[0] - 0.5 * box2[2])
        lr = min(box1[1] + 0.5 * box1[3], box2[1] + 0.5 * box2[3]) - \
            max(box1[1] - 0.5 * box1[3], box2[1] - 0.5 * box2[3])
        inter = 0 if tb < 0 or lr < 0 else tb * lr
        return inter / (box1[2] * box1[3] + box2[2] * box2[3] - inter)

    def camera_detector(self, cap, wait=10):
        ret, _ = cap.read()
        global person_num

        while ret:
            ret, frame = cap.read()
            result = self.detect(frame)
            person = list(filter(lambda r: r[0] == 'person', result))
            person_num = len(person)
            print('person num %d' % person_num)
            self.draw_result(frame, person)
            cv2.imshow('Camera', frame)
            cv2.waitKey(wait)

            ret, frame = cap.read()

    def image_detector(self, imname, wait=0):
        image = cv2.imread(imname)
        result = self.detect(image)
        self.draw_result(image, result)
        cv2.imshow('Image', image)
        cv2.waitKey(wait)



if __name__ == '__main__':

    # detect from camera
    # detector = Detector(load_graph(model_path))
    detector = Detector()
    segment = Segment()

    cap = cv2.VideoCapture(0)
    t_yolo = threading.Thread(target=detector.camera_detector, args=(cap, ))
    t_segment = threading.Thread(target=segment.set_num)
    t_yolo.start()
    t_segment.start()
    t_yolo.join()
    t_segment.join()
    #detector.camera_detector(cap)

    # detect from image file
    # imname = 'test/person.jpg'
    # detector.image_detector(imname)
