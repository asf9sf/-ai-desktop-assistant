"""
声纹识别模块 - 基于 sherpa_onnx 说话人嵌入的声纹验证

技术方案（参考 aidesktopweb 项目）：
- 使用 sherpa_onnx.SpeakerEmbeddingExtractor 提取说话人嵌入向量
- 直接比较参考音频和待验证音频的 embedding 余弦相似度
- 单参考音频方案：简单、高效、准确率高
- 默认阈值 0.4（3D-Speaker CAM++ 模型的合理范围）
"""

import os
import json
import time
import logging
import threading
import queue
import wave
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

# 延迟导入
_numpy = None
_np_imported = False
_sounddevice = None
_sd_imported = False
_sherpa_onnx = None
_so_imported = False
_soundfile = None
_sf_imported = False


def _try_import_numpy():
    global _numpy, _np_imported
    if _np_imported:
        return _numpy
    _np_imported = True
    try:
        import numpy as np
        _numpy = np
    except Exception:
        _numpy = None
    return _numpy


def _try_import_sounddevice():
    global _sounddevice, _sd_imported
    if _sd_imported:
        return _sounddevice
    _sd_imported = True
    try:
        import sounddevice as sd
        _sounddevice = sd
    except Exception:
        _sounddevice = None
    return _sounddevice


def _try_import_sherpa_onnx():
    global _sherpa_onnx, _so_imported
    if _so_imported:
        return _sherpa_onnx
    _so_imported = True
    try:
        import sherpa_onnx
        _sherpa_onnx = sherpa_onnx
    except Exception as e:
        logger.warning(f"sherpa_onnx 导入失败: {e}")
        _sherpa_onnx = None
    return _sherpa_onnx


def _try_import_soundfile():
    global _soundfile, _sf_imported
    if _sf_imported:
        return _soundfile
    _sf_imported = True
    try:
        import soundfile as sf
        _soundfile = sf
    except Exception:
        _soundfile = None
    return _soundfile


# 音频参数
SAMPLE_RATE = 16000
CHANNELS = 1
RECORD_DURATION = 5  # 声纹录入录音时长（秒）
DEFAULT_THRESHOLD = 0.4  # 默认声纹识别阈值

# 声纹录入录音模板
ENROLLMENT_TEMPLATES = [
    "你好，很高兴遇见你，这是我的声音",
    "我是这个智能助手的主人，请记住我的声音特征",
    "人工智能正在改变世界，语音识别技术让人机交互更加自然",
    "请帮我记住这个声音，当我说话时你能第一时间识别出是我",
    "你好小智，我是你的主人，今天天气真不错",
]

# 项目路径
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VP_MODEL_PATH = os.path.join(
    _PROJECT_ROOT, "data", "model", "SpeakerID",
    "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
)
_VOICEPRINT_DIR = os.path.join(_PROJECT_ROOT, "data", "cache", "voiceprint")
_REFERENCE_FILE = os.path.join(_VOICEPRINT_DIR, "myvoice.wav")


class VoiceprintManager:
    """声纹识别管理器 - 基于单参考音频的简洁实现。"""

    def __init__(self):
        os.makedirs(_VOICEPRINT_DIR, exist_ok=True)

        logger.info("=" * 50)
        logger.info("🔊 声纹识别模块初始化")
        logger.info(f"  声纹存储目录: {_VOICEPRINT_DIR}")
        logger.info(f"  参考音频路径: {_REFERENCE_FILE}")

        # sherpa_onnx 说话人嵌入提取器
        self._extractor = None
        self._model_loaded = False
        self._use_onnx = False

        # 预加载参考音频的 embedding（可选，用于加速验证）
        self._ref_embedding = None
        self._ref_embedding_cache_time = 0

        # 录音相关
        self._recording = False
        self._record_thread = None
        self._audio_queue = queue.Queue()

        # 加载模型
        self._try_load_extractor()

        # 检查是否已有声纹
        has_vp = self.has_voiceprint()
        logger.info(f"  已有声纹: {'是' if has_vp else '否'}")
        logger.info(f"  默认阈值: {DEFAULT_THRESHOLD}")
        logger.info("=" * 50)

    # ---------- 模型加载 ----------

    def _try_load_extractor(self) -> bool:
        """加载 sherpa_onnx 说话人嵌入模型。"""
        so = _try_import_sherpa_onnx()
        if so is None:
            logger.warning("sherpa_onnx 不可用")
            return False

        if not os.path.exists(_VP_MODEL_PATH):
            logger.warning(f"声纹模型文件不存在: {_VP_MODEL_PATH}")
            return False

        try:
            config = so.SpeakerEmbeddingExtractorConfig(
                model=_VP_MODEL_PATH,
                debug=False,
                provider="cpu",
                num_threads=max(1, os.cpu_count() - 1),
            )
            self._extractor = so.SpeakerEmbeddingExtractor(config)
            self._model_loaded = True
            self._use_onnx = True
            logger.info(f"说话人嵌入模型加载成功: {_VP_MODEL_PATH}")
            return True
        except Exception as e:
            logger.error(f"说话人嵌入模型加载失败: {e}")
            self._extractor = None
            return False

    def is_using_onnx(self) -> bool:
        return self._use_onnx and self._model_loaded

    # ---------- 核心功能 ----------

    def has_voiceprint(self) -> bool:
        """检查是否已录入声纹。"""
        return os.path.exists(_REFERENCE_FILE)

    def enroll_voiceprint(self, name: str = "myvoice", duration: float = RECORD_DURATION,
                          on_progress=None, on_complete=None, on_error=None) -> bool:
        """
        录入声纹：录制一段音频作为参考声纹。

        Args:
            name: 声纹名称（目前固定保存为 myvoice.wav）
            duration: 录音时长（秒）
            on_progress: 进度回调 (percent, remaining)
            on_complete: 完成回调 (success, message)
            on_error: 错误回调 (error_msg)
        """
        logger.info(f"🎙️ 开始声纹录入 (时长={duration}秒)")
        
        if not self._model_loaded:
            logger.error("声纹录入失败: 模型未加载")
            if on_error:
                on_error("声纹模型未加载")
            return False

        if self._recording:
            logger.warning("声纹录入失败: 正在录音中")
            if on_error:
                on_error("正在录音中，请稍候再试")
            return False

        self._recording = True
        record_start = time.time()

        def on_record_done(audio_data, sample_rate):
            try:
                logger.info(f"录音完成: 音频长度={len(audio_data)/sample_rate:.2f}秒, 采样率={sample_rate}Hz")
                
                # 保存参考音频
                self._save_reference_audio(audio_data, sample_rate)
                
                # 验证：尝试提取嵌入
                logger.info("🔍 验证声纹嵌入提取...")
                embedding = self._extract_embedding(audio_data, sample_rate)
                if embedding is not None:
                    logger.info(f"✅ 声纹嵌入提取成功: shape={embedding.shape}")
                else:
                    logger.warning("⚠️ 声纹嵌入提取失败，但音频已保存")
                
                # 清除缓存
                self._ref_embedding = None
                logger.info(f"✅ 声纹录入成功，已保存到: {_REFERENCE_FILE}")
                
                if on_complete:
                    on_complete(True, "声纹录入成功")
            except Exception as e:
                logger.error(f"声纹录入失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
                if on_error:
                    on_error(str(e))

        def progress_monitor():
            remaining = duration
            while self._recording and remaining > 0:
                time.sleep(0.1)
                elapsed = time.time() - record_start
                remaining = max(0, duration - elapsed)
                percent = min(100, int(elapsed / duration * 100))
                if on_progress:
                    on_progress(percent, remaining)

        threading.Thread(target=progress_monitor, daemon=True).start()
        return self._start_recording(duration, on_record_done)

    def verify_voiceprint_from_file(self, audio_file_path: str,
                                    threshold: float = DEFAULT_THRESHOLD) -> Tuple[bool, float, str]:
        """
        从音频文件验证声纹。

        Args:
            audio_file_path: 待验证的音频文件路径
            threshold: 相似度阈值（默认 0.4）

        Returns:
            (是否匹配, 相似度, 匹配名称)
        """
        if not self.has_voiceprint():
            logger.warning("没有录入声纹")
            return False, 0.0, ""

        if not self._model_loaded:
            return False, 0.0, ""

        try:
            # 加载参考音频 embedding
            ref_embedding = self._get_reference_embedding()
            if ref_embedding is None:
                return False, 0.0, ""

            # 加载待验证音频
            query_audio, query_sr = self._load_audio(audio_file_path)
            if query_audio is None:
                return False, 0.0, ""

            # 提取待验证音频的 embedding
            query_embedding = self._extract_embedding(query_audio, query_sr)
            if query_embedding is None:
                return False, 0.0, ""

            # 计算相似度
            similarity = self._cosine_similarity(ref_embedding, query_embedding)
            is_match = similarity >= threshold

            logger.info(
                f"声纹验证: 文件={audio_file_path}, "
                f"相似度={similarity:.4f}, 阈值={threshold}, 匹配={is_match}"
            )

            return is_match, similarity, "myvoice" if is_match else ""

        except Exception as e:
            logger.error(f"声纹验证异常: {e}")
            return False, 0.0, ""

    def verify_voiceprint(self, audio_data, sample_rate: int = SAMPLE_RATE,
                          threshold: float = DEFAULT_THRESHOLD) -> Tuple[bool, float]:
        """
        直接验证音频数据是否匹配参考声纹。

        Args:
            audio_data: 音频数据（numpy float32）
            sample_rate: 采样率
            threshold: 相似度阈值

        Returns:
            (是否匹配, 相似度)
        """
        if not self.has_voiceprint():
            return False, 0.0

        if not self._model_loaded:
            return False, 0.0

        try:
            ref_embedding = self._get_reference_embedding()
            if ref_embedding is None:
                return False, 0.0

            query_embedding = self._extract_embedding(audio_data, sample_rate)
            if query_embedding is None:
                return False, 0.0

            similarity = self._cosine_similarity(ref_embedding, query_embedding)
            is_match = similarity >= threshold

            logger.debug(f"声纹验证: 相似度={similarity:.4f}, 阈值={threshold}, 匹配={is_match}")
            return is_match, similarity

        except Exception as e:
            logger.error(f"声纹验证异常: {e}")
            return False, 0.0

    def verify_voiceprint_files(self, ref_file_path: str, query_file_path: str,
                                threshold: float = DEFAULT_THRESHOLD) -> Tuple[bool, float]:
        """比较两个音频文件。"""
        try:
            ref_audio, ref_sr = self._load_audio(ref_file_path)
            query_audio, query_sr = self._load_audio(query_file_path)

            if ref_audio is None or query_audio is None:
                return False, 0.0

            ref_emb = self._extract_embedding(ref_audio, ref_sr)
            query_emb = self._extract_embedding(query_audio, query_sr)

            if ref_emb is None or query_emb is None:
                return False, 0.0

            similarity = self._cosine_similarity(ref_emb, query_emb)
            is_match = similarity >= threshold
            return is_match, similarity

        except Exception as e:
            logger.error(f"文件对比异常: {e}")
            return False, 0.0

    # ---------- 录音采集 ----------

    def _start_recording(self, duration: float, on_complete) -> bool:
        """开始录音。"""
        sd = _try_import_sounddevice()
        if sd is None:
            logger.error("sounddevice 未安装")
            self._recording = False
            return False

        self._audio_queue = queue.Queue()

        try:
            stream = sd.InputStream(
                channels=CHANNELS,
                samplerate=SAMPLE_RATE,
                blocksize=1024,
                callback=self._audio_callback,
            )
            stream.start()

            def record_loop():
                start_time = time.time()
                chunks = []
                try:
                    while self._recording and (time.time() - start_time) < duration:
                        try:
                            chunk = self._audio_queue.get(timeout=0.1)
                            chunks.append(chunk)
                        except queue.Empty:
                            continue

                    if chunks:
                        np = _try_import_numpy()
                        audio_data = np.concatenate(chunks, axis=0).flatten()
                        on_complete(audio_data, SAMPLE_RATE)
                finally:
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
                    self._recording = False

            self._record_thread = threading.Thread(target=record_loop, daemon=True)
            self._record_thread.start()
            logger.info(f"开始录音，时长 {duration} 秒")
            return True

        except Exception as e:
            logger.error(f"启动录音失败: {e}")
            self._recording = False
            return False

    def stop_recording(self):
        """停止录音。"""
        self._recording = False

    def _audio_callback(self, indata, frames, time_info, status):
        if self._recording:
            self._audio_queue.put(indata.copy())

    # ---------- 音频处理 ----------

    def _save_reference_audio(self, audio_data, sample_rate: int):
        """保存参考音频为 WAV 文件。"""
        np = _try_import_numpy()
        if np is None:
            return

        if audio_data.dtype != np.int16:
            if audio_data.dtype == np.float32 or audio_data.dtype == np.float64:
                audio_data = (audio_data * 32767).astype(np.int16)
            else:
                audio_data = audio_data.astype(np.int16)

        os.makedirs(_VOICEPRINT_DIR, exist_ok=True)
        with wave.open(_REFERENCE_FILE, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio_data.tobytes())

        logger.info(f"参考音频已保存: {_REFERENCE_FILE}")

    def _load_audio(self, filepath: str) -> Optional[Tuple]:
        """从文件加载音频。"""
        sf = _try_import_soundfile()
        if sf is None:
            return None

        if not os.path.exists(filepath):
            logger.warning(f"音频文件不存在: {filepath}")
            return None

        try:
            audio, sample_rate = sf.read(filepath, dtype="float32", always_2d=True)
            audio = audio[:, 0]
            return audio, sample_rate
        except Exception as e:
            logger.error(f"加载音频失败: {e}")
            return None

    def _extract_embedding(self, audio_data, sample_rate: int) -> Optional['np.ndarray']:
        """提取说话人嵌入向量。"""
        np = _try_import_numpy()
        if np is None or self._extractor is None:
            return None

        try:
            # 确保音频是 float32
            if audio_data.dtype != np.float32:
                if audio_data.dtype == np.int16:
                    audio_data = audio_data.astype(np.float32) / 32768.0
                else:
                    audio_data = audio_data.astype(np.float32)

            # 归一化
            max_val = np.max(np.abs(audio_data))
            if max_val > 1e-6:
                audio_data = audio_data / max_val

            # 提取 embedding
            vp_stream = self._extractor.create_stream()
            vp_stream.accept_waveform(sample_rate=sample_rate, waveform=audio_data)
            vp_stream.input_finished()

            embedding = self._extractor.compute(vp_stream)
            result = np.array(embedding, dtype=np.float32)

            # L2 归一化
            norm = np.linalg.norm(result)
            if norm > 1e-10:
                result = result / norm

            return result

        except Exception as e:
            logger.error(f"嵌入提取失败: {e}")
            return None

    def _get_reference_embedding(self) -> Optional['np.ndarray']:
        """获取参考音频的 embedding（带缓存）。"""
        np = _try_import_numpy()
        if np is None:
            return None

        current_time = time.time()
        # 缓存 10 分钟
        if self._ref_embedding is not None and (current_time - self._ref_embedding_cache_time) < 600:
            return self._ref_embedding

        result = self._load_audio(_REFERENCE_FILE)
        if result is None:
            return None

        audio, sr = result
        embedding = self._extract_embedding(audio, sr)

        if embedding is not None:
            self._ref_embedding = embedding
            self._ref_embedding_cache_time = current_time

        return embedding

    @staticmethod
    def _cosine_similarity(a: 'np.ndarray', b: 'np.ndarray') -> float:
        """计算余弦相似度。"""
        np = _try_import_numpy()
        if np is None:
            return 0.0

        try:
            # 处理维度
            if a.ndim > 1:
                a = np.mean(a, axis=0)
            if b.ndim > 1:
                b = np.mean(b, axis=0)

            if a.shape != b.shape:
                logger.debug(f"维度不匹配: a={a.shape}, b={b.shape}")
                return 0.0

            dot_product = np.dot(a, b)
            norm_a = np.linalg.norm(a)
            norm_b = np.linalg.norm(b)

            if norm_a < 1e-10 or norm_b < 1e-10:
                return 0.0

            return float(dot_product / (norm_a * norm_b))

        except Exception as e:
            logger.warning(f"相似度计算异常: {e}")
            return 0.0

    # ---------- 管理功能 ----------

    def list_voiceprints(self) -> List[dict]:
        """列出已注册的声纹。"""
        result = []
        if self.has_voiceprint():
            result.append({
                'name': 'myvoice',
                'template_count': 1,
                'model_type': 'ONNX',
            })
        return result

    def delete_voiceprint(self, name: str = "myvoice") -> bool:
        """删除声纹。"""
        logger.info(f"🗑️  删除声纹: name={name}")
        if os.path.exists(_REFERENCE_FILE):
            try:
                os.remove(_REFERENCE_FILE)
                self._ref_embedding = None
                logger.info(f"✅ 声纹已删除: {_REFERENCE_FILE}")
                return True
            except Exception as e:
                logger.error(f"❌ 删除声纹失败: {e}")
                return False
        else:
            logger.warning("⚠️  声纹文件不存在，无需删除")
            return False

    def delete_all_voiceprints(self):
        """删除所有声纹。"""
        logger.info("🗑️  删除所有声纹")
        self.delete_voiceprint()

    def get_voiceprint_count(self) -> int:
        return 1 if self.has_voiceprint() else 0

    def set_verification_enabled(self, enabled: bool):
        self._verification_enabled = enabled

    def is_verification_enabled(self) -> bool:
        return getattr(self, '_verification_enabled', False) and self.has_voiceprint()

    def get_model_info(self) -> dict:
        return {
            'model_type': 'sherpa_onnx_speaker_embedding',
            'model_loaded': self._model_loaded,
            'model_path': _VP_MODEL_PATH,
            'use_onnx': self._use_onnx,
            'voiceprint_count': self.get_voiceprint_count(),
            'has_reference': self.has_voiceprint(),
        }
