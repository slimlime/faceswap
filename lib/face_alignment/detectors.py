#!/usr/bin python3
""" DLIB Detector for face alignment
    Code adapted and modified from:
    https://github.com/1adrianb/face-alignment """

import os
import numpy as np

from tensorflow import Graph, Session

import dlib

from .mtcnn import create_mtcnn, detect_face


CACHE_PATH = os.path.join(os.path.dirname(__file__), ".cache")


class Detector(object):
    """ Detector object """
    def __init__(self):
        self.initialized = False
        self.verbose = False
        self.data_path = self.set_data_path()
        self.detected_faces = None

    @staticmethod
    def set_data_path():
        """ path to data file/models
            override for specific detector """
        pass

    def set_predetected(self, width, height):
        """ Set a dlib rectangle for predetected faces """
        # Predetected_face is used for sort tool.
        # Landmarks should not be extracted again from predetected faces,
        # because face data is lost, resulting in a large variance
        # against extract from original image
        self.detected_faces = [dlib.rectangle(0, 0, width, height)]

    @staticmethod
    def is_mmod_rectangle(d_rectangle):
        """ Return whether the passed in object is
            a dlib.mmod_rectangle """
        return isinstance(d_rectangle, dlib.mmod_rectangle)


class ManualDetector(Detector):
    """ Manual Detector """
    def set_data_path(self):
        return None

    def create_detector(self, verbose):
        """ Create the mtcnn detector """
        self.verbose = verbose

        if self.verbose:
            print("Adding Manual detector")

    def detect_faces(self, bounding_box):
        """ Return the given bounding box in a dlib rectangle """
        face = bounding_box
        self.detected_faces = [dlib.rectangle(int(face[0]), int(face[1]),
                                              int(face[2]), int(face[3]))]


class DLibDetector(Detector):
    """ Dlib detector for face recognition """
    def __init__(self):
        Detector.__init__(self)
        self.detectors = list()

    @staticmethod
    def compiled_for_cuda():
        """ Return a message on DLIB Cuda Compilation status """
        msg = "DLib IS "
        if not dlib.DLIB_USE_CUDA:
            msg += "NOT "
        msg += "compiled to use CUDA"
        return msg

    @staticmethod
    def set_data_path():
        """ Load the face detector data """
        data_path = os.path.join(CACHE_PATH,
                                 "mmod_human_face_detector.dat")
        if not os.path.exists(data_path):
            raise Exception("Error: Unable to find {}, reinstall "
                            "the lib!".format(data_path))
        return data_path

    def create_detector(self, verbose, detector, placeholder):
        """ Add the requested detectors """
        self.verbose = verbose

        if detector == "dlib-cnn" or detector == "dlib-all":
            if self.verbose:
                print("Adding DLib - CNN detector")
            self.detectors.append(dlib.cnn_face_detection_model_v1(
                self.data_path))

        if detector == "dlib-hog" or detector == "dlib-all":
            if self.verbose:
                print("Adding DLib - HOG detector")
            self.detectors.append(dlib.get_frontal_face_detector())

        for current_detector in self.detectors:
            current_detector(placeholder, 0)

        self.initialized = True

    def detect_faces(self, image):
        """ Detect faces in rgb image """
        self.detected_faces = None
        for current_detector in self.detectors:
            self.detected_faces = current_detector(image, 0)

            if self.detected_faces:
                break


class MTCNNDetector(Detector):
    """ MTCNN detector for face recognition """
    def __init__(self):
        Detector.__init__(self)
        self.kwargs = None

    @staticmethod
    def validate_kwargs(kwargs):
        """ Validate that cli kwargs are correct. If not reset to default """
        valid = True
        if kwargs['minsize'] < 10:
            valid = False
        elif len(kwargs['threshold']) != 3:
            valid = False
        elif not all(0.0 < threshold < 1.0
                     for threshold in kwargs['threshold']):
            valid = False
        elif not 0.0 < kwargs['factor'] < 1.0:
            valid = False

        if not valid:
            print("Invalid MTCNN arguments received. Running with defaults")
            return {"minsize": 20,                 # minimum size of face
                    "threshold": [0.6, 0.7, 0.7],  # three steps threshold
                    "factor": 0.709}               # scale factor
        return kwargs

    @staticmethod
    def set_data_path():
        """ Load the mtcnn models """
        for model in ("det1.npy", "det2.npy", "det3.npy"):
            model_path = os.path.join(CACHE_PATH, model)
            if not os.path.exists(model_path):
                raise Exception("Error: Unable to find {}, reinstall "
                                "the lib!".format(model_path))
        return CACHE_PATH

    def create_detector(self, verbose, mtcnn_kwargs):
        """ Create the mtcnn detector """
        self.verbose = verbose

        if self.verbose:
            print("Adding MTCNN detector")

        self.kwargs = mtcnn_kwargs

        mtcnn_graph = Graph()
        with mtcnn_graph.as_default():
            mtcnn_session = Session()
            with mtcnn_session.as_default():
                pnet, rnet, onet = create_mtcnn(mtcnn_session, self.data_path)
        mtcnn_graph.finalize()

        self.kwargs["pnet"] = pnet
        self.kwargs["rnet"] = rnet
        self.kwargs["onet"] = onet
        self.initialized = True

    def detect_faces(self, image):
        """ Detect faces in rgb image """
        self.detected_faces = None
        detected_faces, points = detect_face(image, **self.kwargs)
        detected_faces = self.tighten_bounding_box(detected_faces, points)
        self.detected_faces = [dlib.rectangle(int(face[0]), int(face[1]),
                                              int(face[2]), int(face[3]))
                               for face in detected_faces]

    @staticmethod
    def tighten_bounding_box(faces, landmarks):
        """ Tighten the bounding box for Face Alignment.

            Face Alignment was built to expect a DLIB bounding
            box and sometimes can't calculate landmarks on edge
            cases. Tightening up the bounding box along the
            longest edge improves it's chances and helps remove
            'jitter' """
        retval = list()
        if len(faces) == 0:
            return retval
        face_landmarks = np.hsplit(landmarks, len(faces))
        for idx, face in enumerate(faces):
            pts = np.vsplit(face_landmarks[idx], 2)
            boundary = (np.amin(pts[0]), np.amin(pts[1]),
                        np.amax(pts[0]), np.amax(pts[1]))

            len_x, len_y = face[2] - face[0], face[3] - face[1]
            raw = 1 if len_x > len_y else 0 if len_x < len_y else -1
            bounding = list()

            for i in range(4):
                if i % 2 == raw:
                    bounding.append(int(face[i]))
                else:
                    # Reduce bounding box by 75% around landmarks
                    remove = (boundary[i] - face[i]) / 4
                    bounding.append(int(boundary[i] - remove))

            retval.append(bounding)
        return retval
