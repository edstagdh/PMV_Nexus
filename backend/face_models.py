"""The two face models (3.12.0, backend/faces.py): file names, sizes, SHA-256 and where they come
from. Standard library only, so Tools/get_face_models.py and the installers can use it before the
app's packages are installed.

Both are OpenCV's own (https://github.com/opencv/opencv_zoo):
- YuNet face detector, MIT license;
- SFace face recognizer, Apache-2.0 license.
"""
from __future__ import annotations

from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "faces"
_ZOO = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
MODELS = {
    "detector": {"file": "face_detection_yunet_2023mar.onnx", "size": 232589,
                 "sha256": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
                 "url": _ZOO + "face_detection_yunet/face_detection_yunet_2023mar.onnx",
                 "license": "MIT"},
    "recognizer": {"file": "face_recognition_sface_2021dec.onnx", "size": 38696353,
                   "sha256": "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
                   "url": _ZOO + "face_recognition_sface/face_recognition_sface_2021dec.onnx",
                   "license": "Apache-2.0"},
}
