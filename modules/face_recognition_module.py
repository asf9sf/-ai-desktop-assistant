import os
import json
import numpy as np
import cv2
import threading
from typing import Optional, Tuple, List

try:
    import face_recognition
except ImportError:
    face_recognition = None


def _sanitize_frame(frame):
    """
    确保摄像头帧是 dlib/face_recognition 能接受的格式：uint8 RGB 连续数组。
    处理 numpy 2.x 和各种摄像头后端的兼容性问题。
    """
    if frame is None:
        return None
    # 强制转 uint8 + copy 保证连续
    img = np.array(frame, dtype=np.uint8).copy()
    # 如果是 3 通道 BGR -> 转 RGB
    if img.ndim == 3 and img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = np.array(img, dtype=np.uint8).copy()
    elif img.ndim == 3 and img.shape[2] == 4:
        # RGBA -> RGB
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2RGB)
        img = np.array(img, dtype=np.uint8).copy()
    elif img.ndim == 2:
        # 灰度 -> 3 通道
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
        img = np.array(img, dtype=np.uint8).copy()
    return img


class FaceRecognizer:
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "face_db", "faces.json"
            )
        self.db_path = db_path
        self.known_encodings = []
        self.known_names = []
        self._load_database()
        self._lock = threading.Lock()
        self._last_result = None
        self._frame_skip = 2
        self._frame_count = 0

    def _load_database(self):
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for name, enc_list in data.items():
                    for enc in enc_list:
                        self.known_encodings.append(np.array(enc))
                        self.known_names.append(name)
            except Exception:
                pass

    def _save_database(self):
        data = {}
        for name, enc in zip(self.known_names, self.known_encodings):
            if name not in data:
                data[name] = []
            data[name].append(enc.tolist())
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def register_face(self, name: str, image_path: str = None, frame=None) -> bool:
        """注册人脸到数据库。可以传图片路径或摄像头frame。"""
        if face_recognition is None:
            raise RuntimeError("face_recognition库未安装，无法使用人脸识别")

        try:
            if image_path:
                image = face_recognition.load_image_file(image_path)
                image = _sanitize_frame(image)
            else:
                image = _sanitize_frame(frame)
            if image is None:
                return False
            encodings = face_recognition.face_encodings(image)
            if not encodings:
                return False
            with self._lock:
                self.known_encodings.append(encodings[0])
                self.known_names.append(name)
                self._save_database()
            return True
        except Exception:
            return False

    def register_from_camera(self, name: str, camera_id: int = 0) -> Tuple[bool, str]:
        """从摄像头采集人脸并注册。"""
        if face_recognition is None:
            return False, "face_recognition库未安装"

        # 尝试多种方式打开摄像头
        cap = self._open_camera(camera_id)
        if cap is None:
            return False, "无法打开摄像头"

        success = False
        msg = "未检测到人脸"
        try:
            for _ in range(60):
                ret, frame = cap.read()
                if not ret or frame is None:
                    continue
                rgb = _sanitize_frame(frame)
                if rgb is None:
                    continue
                locs = face_recognition.face_locations(rgb, model="hog")
                if locs:
                    if self.register_face(name, frame=rgb):
                        success = True
                        msg = "注册成功"
                        break
                    else:
                        msg = "特征提取失败"
        finally:
            cap.release()
        return success, msg

    def recognize_frame(self, frame_bgr) -> Tuple[Optional[str], float, list]:
        """识别单帧，返回(name, confidence, face_locations)。"""
        if face_recognition is None:
            return None, 0.0, []

        # 先清洗帧
        rgb_full = _sanitize_frame(frame_bgr)
        if rgb_full is None:
            return None, 0.0, []

        # 降采样加速
        h, w = rgb_full.shape[:2]
        small = cv2.resize(rgb_full, (w // 2, h // 2))
        small = _sanitize_frame(small)

        face_locations = face_recognition.face_locations(small, model="hog")
        if not face_locations:
            return None, 0.0, []

        if not self.known_encodings:
            restored_locs = [(t * 2, r * 2, b * 2, l * 2) for (t, r, b, l) in face_locations]
            return None, 0.0, restored_locs

        encodings = face_recognition.face_encodings(small, face_locations)
        if not encodings:
            restored_locs = [(t * 2, r * 2, b * 2, l * 2) for (t, r, b, l) in face_locations]
            return None, 0.0, restored_locs

        # 只处理第一张人脸
        enc = encodings[0]
        distances = face_recognition.face_distance(self.known_encodings, enc)
        best_idx = int(np.argmin(distances))
        best_dist = float(distances[best_idx])
        threshold = 0.45

        restored_locs = [(t * 2, r * 2, b * 2, l * 2) for (t, r, b, l) in face_locations]

        if best_dist < threshold:
            name = self.known_names[best_idx]
            confidence = max(0.0, 1.0 - best_dist)
            return name, confidence, restored_locs

        return None, 0.0, restored_locs

    def recognize_from_camera(self, camera_id: int = 0, timeout_sec: int = 15) -> Tuple[Optional[str], np.ndarray]:
        """
        从摄像头实时识别，带帧跳过优化。
        返回 (识别到的名字 or None, 最后一帧BGR图像)。
        """
        if face_recognition is None:
            return None, None

        cap = self._open_camera(camera_id)
        if cap is None:
            return None, None

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        import time
        start = time.time()
        last_frame = None
        stable_frames = 0
        stable_name = None

        try:
            while time.time() - start < timeout_sec:
                ret, frame = cap.read()
                if not ret or frame is None:
                    continue
                last_frame = frame.copy()

                self._frame_count += 1
                if self._frame_count % self._frame_skip != 0:
                    time.sleep(0.01)
                    continue

                name, conf, locs = self.recognize_frame(frame)

                if name:
                    if name == stable_name:
                        stable_frames += 1
                    else:
                        stable_name = name
                        stable_frames = 1
                    if stable_frames >= 2:
                        return name, last_frame
                else:
                    stable_frames = 0
                    stable_name = None

                time.sleep(0.02)
        finally:
            cap.release()
            self._frame_count = 0
        return None, last_frame

    def _open_camera(self, camera_id: int):
        """尝试多种方式打开摄像头，返回 VideoCapture 或 None。"""
        # 方式1: CAP_DSHOW (Windows DirectShow)
        cap = cv2.VideoCapture(camera_id, cv2.CAP_DSHOW)
        if cap.isOpened():
            # 读取一帧验证
            ret, frame = cap.read()
            if ret and frame is not None and frame.ndim == 3 and frame.shape[2] == 3:
                # 检查是否是有效帧（非全黑/全白/条纹）
                if frame.std() > 5:
                    return cap
            cap.release()

        # 方式2: 默认后端
        cap = cv2.VideoCapture(camera_id)
        if cap.isOpened():
            ret, frame = cap.read()
            if ret and frame is not None and frame.ndim == 3 and frame.shape[2] == 3:
                if frame.std() > 5:
                    return cap
            cap.release()

        # 方式3: CAP_ANY + 高分辨率
        cap = cv2.VideoCapture(camera_id, cv2.CAP_ANY)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            ret, frame = cap.read()
            if ret and frame is not None and frame.ndim == 3 and frame.shape[2] == 3:
                if frame.std() > 5:
                    return cap
            cap.release()

        return None

    def has_registered_faces(self) -> bool:
        return len(self.known_encodings) > 0
