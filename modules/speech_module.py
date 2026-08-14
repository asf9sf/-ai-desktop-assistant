"""
语音识别模块 - 基于 sherpa-onnx-sense-voice 的离线语音输入

支持三种模式：
  1. off - 关闭语音识别
  2. push_to_talk - 实时对话（按住/点击说话，松开发送）
  3. wake_word - 唤醒词对话（持续监听，听到唤醒词后自动发送）

使用 sherpa-onnx + SenseVoice 模型，纯 CPU 推理，不占用 GPU。
支持情感识别和事件检测。
"""

import os
import time
import json
import logging
import threading
import queue
import wave
import re
from typing import Optional, Callable, List, Tuple

logger = logging.getLogger(__name__)

# 延迟导入
_sherpa_onnx = None
_so_imported = False
_sounddevice = None
_sd_imported = False
_numpy = None
_np_imported = False
_soundfile = None
_sf_imported = False


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


def _try_import_sounddevice():
    global _sounddevice, _sd_imported
    if _sd_imported:
        return _sounddevice
    _sd_imported = True
    try:
        import sounddevice as sd
        _sounddevice = sd
    except Exception as e:
        logger.warning(f"sounddevice 导入失败: {e}")
        _sounddevice = None
    return _sounddevice


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


# 语音识别模式枚举
MODE_OFF = "off"
MODE_PUSH_TO_TALK = "push_to_talk"
MODE_WAKE_WORD = "wake_word"

# 默认唤醒词
DEFAULT_WAKE_WORDS = ["小智", "你好小智", "小智小智"]

# 音频参数
SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_SIZE = 512  # ~32ms at 16kHz

# 情感映射
EMOTION_MAP = {
    "HAPPY": "[开心]",
    "SAD": "[伤心]",
    "ANGRY": "[愤怒]",
    "DISGUSTED": "[厌恶]",
    "SURPRISED": "[惊讶]",
    "NEUTRAL": "",
    "EMO_UNKNOWN": "",
}

# 事件映射
EVENT_MAP = {
    "BGM": "",
    "Applause": "[鼓掌]",
    "Laughter": "[大笑]",
    "Cry": "[哭]",
    "Sneeze": "[打喷嚏]",
    "Cough": "[咳嗽]",
    "Breath": "[深呼吸]",
    "Speech": "",
    "Event_UNK": "",
}


class SpeechManager:
    """
    基于 sherpa-onnx-sense-voice 的语音识别管理器。

    功能：
    - push_to_talk: 点击开始录音，再次点击停止并识别
    - wake_word: 持续监听，检测到唤醒词后自动识别并发送
    - off: 关闭所有语音活动

    识别结果包含：文本内容、情感标签、事件标签
    所有识别结果通过回调返回给 UI 层。
    """

    def __init__(self):
        # sherpa-onnx 识别器
        self._recognizer = None
        self._model_loaded = False
        self._model_path = self._get_model_path()

        # 模式
        self._mode = MODE_OFF
        self._wake_words = list(DEFAULT_WAKE_WORDS)
        self._wake_word_confidence = 0.5

        # 录音相关
        self._recording = False
        self._stream = None
        self._audio_queue = queue.Queue()
        self._record_thread: Optional[threading.Thread] = None
        self._wake_thread: Optional[threading.Thread] = None
        self._listen_thread: Optional[threading.Thread] = None

        # 回调
        self._on_partial: Optional[Callable[[str], None]] = None
        self._on_final: Optional[Callable[[str], None]] = None
        self._on_state_change: Optional[Callable[[str], None]] = None
        self._on_error: Optional[Callable[[str], None]] = None
        self._on_voiceprint_fail: Optional[Callable[[str], None]] = None

        # VAD 参数
        self._silence_threshold = 0.02
        self._silence_timeout = 1.5
        self._min_speech_duration = 0.3

        # 临时文件目录
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._temp_dir = os.path.join(project_root, "temp")
        os.makedirs(self._temp_dir, exist_ok=True)

        # 声纹验证用的缓存音频文件路径
        self._voiceprint_cache_path = os.path.join(project_root, "data", "cache", "cache_record.wav")
        os.makedirs(os.path.dirname(self._voiceprint_cache_path), exist_ok=True)

        # 声纹验证
        self._voiceprint_manager = None
        self._voiceprint_enabled = False
        self._voiceprint_threshold = 0.4  # 3D-Speaker CAM++ 模型推荐阈值

    def _get_model_path(self) -> str:
        """获取 SenseVoice 模型路径。"""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(
            project_root, "data", "model", "ASR",
            "sherpa-onnx-sense-voice-zh-en-ja-ko-yue"
        )

    # ---------- 配置 ----------

    def set_mode(self, mode: str):
        """设置语音识别模式。"""
        if mode not in (MODE_OFF, MODE_PUSH_TO_TALK, MODE_WAKE_WORD):
            logger.warning(f"未知模式: {mode}")
            return
        old_mode = self._mode
        self._mode = mode
        logger.info(f"语音模式: {old_mode} → {mode}")

        if old_mode != mode:
            self._stop_all()

        if mode == MODE_WAKE_WORD:
            self._start_wake_word_listening()
        elif mode == MODE_PUSH_TO_TALK:
            self._start_continuous_listening()
        elif mode == MODE_OFF:
            self._stop_all()

        self._notify_state(f"mode:{mode}")

    def get_mode(self) -> str:
        return self._mode

    def set_wake_words(self, words: List[str]):
        """设置唤醒词列表。"""
        self._wake_words = [w for w in words if w.strip()] or list(DEFAULT_WAKE_WORDS)

    def set_on_partial(self, callback: Callable[[str], None]):
        self._on_partial = callback

    def set_on_final(self, callback: Callable[[str], None]):
        self._on_final = callback

    def set_on_state_change(self, callback: Callable[[str], None]):
        self._on_state_change = callback

    def set_on_error(self, callback: Callable[[str], None]):
        self._on_error = callback

    def set_on_voiceprint_fail(self, callback: Callable[[str], None]):
        self._on_voiceprint_fail = callback

    # ---------- 声纹验证配置 ----------

    def set_voiceprint_manager(self, manager):
        self._voiceprint_manager = manager
        if manager:
            logger.info("✅ 声纹管理器已设置")
            self._log_voiceprint_status()

    def set_voiceprint_enabled(self, enabled: bool):
        self._voiceprint_enabled = enabled
        if self._voiceprint_manager:
            self._voiceprint_manager.set_verification_enabled(enabled)
        
        status = "启用" if enabled else "禁用"
        has_vp = self._voiceprint_manager and self._voiceprint_manager.has_voiceprint()
        logger.info(
            f"🔐 声纹验证{status} | "
            f"已录入声纹={'是' if has_vp else '否'} | "
            f"阈值={self._voiceprint_threshold:.2f}"
        )
        
        if enabled and not has_vp:
            logger.warning("⚠️ 声纹验证已启用，但尚未录入声纹，请先在设置中录入声纹")

    def is_voiceprint_enabled(self) -> bool:
        return self._voiceprint_enabled and self._voiceprint_manager is not None

    def set_voiceprint_threshold(self, threshold: float):
        old_threshold = self._voiceprint_threshold
        self._voiceprint_threshold = max(0.25, min(0.60, threshold))
        if old_threshold != self._voiceprint_threshold:
            logger.info(f"🎚️ 声纹识别阈值: {old_threshold:.2f} → {self._voiceprint_threshold:.2f}")

    def _log_voiceprint_status(self):
        """输出当前声纹验证状态。"""
        if not self._voiceprint_manager:
            logger.info("声纹状态: 声纹管理器未初始化")
            return
        
        model_info = self._voiceprint_manager.get_model_info()
        logger.info(
            f"📋 声纹详情: "
            f"模型类型={model_info.get('model_type', 'N/A')}, "
            f"模型加载={model_info.get('model_loaded', False)}, "
            f"声纹数量={model_info.get('voiceprint_count', 0)}, "
            f"参考音频={'存在' if model_info.get('has_reference') else '不存在'}"
        )

    def _verify_voiceprint(self, audio_data) -> bool:
        """验证音频是否通过声纹验证（直接验证，无需保存文件）。"""
        # 检查是否启用声纹验证
        if not self._voiceprint_enabled:
            logger.info("🔐 声纹验证: 未启用，跳过验证")
            return True

        if not self._voiceprint_manager:
            logger.info("🔐 声纹验证: 声纹管理器未初始化，跳过验证")
            return True

        # 检查是否已录入声纹
        if not self._voiceprint_manager.has_voiceprint():
            logger.info("🔐 声纹验证: 尚未录入声纹，跳过验证")
            return True

        logger.info(f"🔍 声纹验证开始 (阈值={self._voiceprint_threshold:.2f})...")

        try:
            # 转换音频格式
            np = _try_import_numpy()
            if np and audio_data.dtype != np.float32:
                if audio_data.dtype == np.int16:
                    audio_float = audio_data.astype(np.float32) / 32768.0
                else:
                    audio_float = audio_data.astype(np.float32)
            else:
                audio_float = audio_data

            logger.debug(f"音频预处理: shape={audio_float.shape}, dtype={audio_float.dtype}")

            # 执行验证
            is_match, similarity = self._voiceprint_manager.verify_voiceprint(
                audio_float, SAMPLE_RATE, self._voiceprint_threshold
            )

            if is_match:
                logger.info(f"✅ 声纹验证通过: 相似度={similarity:.4f} >= 阈值={self._voiceprint_threshold:.2f}")
                return True
            else:
                logger.warning(f"❌ 声纹验证失败: 相似度={similarity:.4f} < 阈值={self._voiceprint_threshold:.2f}")
                if self._on_voiceprint_fail:
                    self._on_voiceprint_fail(
                        f"声纹验证失败（相似度 {similarity:.2f}，阈值 {self._voiceprint_threshold:.2f}）"
                    )
                return False

        except Exception as e:
            logger.error(f"声纹验证异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return True  # 异常时放行，避免阻塞

    # ---------- 模型加载 ----------

    def _ensure_model(self) -> bool:
        """确保 sherpa-onnx SenseVoice 模型已加载。"""
        if self._model_loaded and self._recognizer is not None:
            return True

        so = _try_import_sherpa_onnx()
        if so is None:
            self._notify_error("sherpa_onnx 未安装，语音识别不可用")
            return False

        model_file = os.path.join(self._model_path, "model.int8.onnx")
        tokens_file = os.path.join(self._model_path, "tokens.txt")

        if not os.path.exists(model_file):
            self._notify_error(f"ASR 模型文件不存在: {model_file}")
            return False

        if not os.path.exists(tokens_file):
            self._notify_error(f"ASR tokens 文件不存在: {tokens_file}")
            return False

        try:
            self._notify_state("loading_model")
            logger.info("正在加载 sherpa-onnx SenseVoice 模型...")
            self._recognizer = so.OfflineRecognizer.from_sense_voice(
                model=model_file,
                tokens=tokens_file,
                use_itn=True,
                num_threads=max(1, os.cpu_count() - 1),
            )
            self._model_loaded = True
            logger.info("sherpa-onnx SenseVoice 模型加载完成")
            return True
        except Exception as e:
            logger.error(f"SenseVoice 模型加载失败: {e}")
            self._notify_error(f"语音模型加载失败: {e}")
            self._recognizer = None
            self._model_loaded = False
            return False

    # ---------- 录音控制 ----------

    def start_recording(self):
        """开始录音（push-to-talk 模式）。"""
        if self._mode != MODE_PUSH_TO_TALK:
            logger.warning("当前不是 push-to-talk 模式")
            return
        if self._recording:
            return

        sd = _try_import_sounddevice()
        if sd is None:
            self._notify_error("sounddevice 不可用")
            return

        self._audio_queue = queue.Queue()
        self._recording = True
        self._notify_state("recording")
        logger.info("开始录音")

        try:
            self._stream = sd.InputStream(
                channels=CHANNELS,
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                dtype='int16',
                callback=self._audio_callback,
            )
            self._stream.start()
        except Exception as e:
            logger.error(f"录音启动失败: {e}")
            self._recording = False
            self._notify_error(f"录音启动失败: {e}")

    def stop_recording_and_recognize(self):
        """停止录音并进行识别（push-to-talk 模式）。"""
        if not self._recording:
            return

        self._recording = False
        self._notify_state("processing")
        logger.info("停止录音，开始识别")

        try:
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None
        except Exception:
            pass

        audio_data = self._collect_audio_from_queue()
        if audio_data is None or len(audio_data) == 0:
            self._notify_error("未检测到语音")
            self._notify_state("idle")
            return

        if not self._verify_voiceprint(audio_data):
            self._notify_state("idle")
            return

        text = self._recognize_audio(audio_data)
        if text:
            self._notify_final(text)
        else:
            self._notify_error("未识别到语音内容")

        self._notify_state("idle")

    def _audio_callback(self, indata, frames, time_info, status):
        if self._recording:
            self._audio_queue.put(indata.copy())

    def _collect_audio_from_queue(self) -> Optional['np.ndarray']:
        np = _try_import_numpy()
        if np is None:
            return None

        chunks = []
        while not self._audio_queue.empty():
            try:
                chunks.append(self._audio_queue.get_nowait())
            except queue.Empty:
                break

        if not chunks:
            return None

        return np.concatenate(chunks, axis=0)

    # ---------- 唤醒词模式 ----------

    def _start_wake_word_listening(self):
        if not self._ensure_model():
            return

        if self._wake_thread and self._wake_thread.is_alive():
            return

        self._wake_thread = threading.Thread(
            target=self._wake_word_loop, daemon=True, name="wake-word-loop"
        )
        self._wake_thread.start()
        logger.info("唤醒词监听已启动")

    # ---------- 实时对话（持续监听）模式 ----------

    def _start_continuous_listening(self):
        if not self._ensure_model():
            return

        if self._listen_thread and self._listen_thread.is_alive():
            return

        self._listen_thread = threading.Thread(
            target=self._continuous_listening_loop, daemon=True, name="continuous-listening"
        )
        self._listen_thread.start()
        logger.info("实时对话持续监听已启动")

    def _continuous_listening_loop(self):
        sd = _try_import_sounddevice()
        np = _try_import_numpy()
        if sd is None or np is None:
            self._notify_error("音频设备不可用")
            return

        self._notify_state("listening")
        logger.info("实时对话监听中...")

        chunk_duration = 0.4
        block_size = int(SAMPLE_RATE * chunk_duration)

        try:
            stream = sd.InputStream(
                channels=CHANNELS,
                samplerate=SAMPLE_RATE,
                blocksize=block_size,
                dtype='int16',
            )
            stream.start()
        except Exception as e:
            logger.error(f"麦克风启动失败: {e}")
            self._notify_error(f"麦克风启动失败: {e}")
            return

        audio_buffer = []
        buffer_duration = 0
        in_speech = False
        silence_count = 0
        max_silence_blocks = 3

        try:
            while self._mode == MODE_PUSH_TO_TALK:
                try:
                    indata, overflowed = stream.read(block_size)
                    if overflowed:
                        continue

                    chunk_float = indata.astype(np.float32) / 32768.0
                    energy = np.sqrt(np.mean(chunk_float ** 2))

                    if energy > self._silence_threshold:
                        in_speech = True
                        silence_count = 0
                        audio_buffer.append(indata)
                        buffer_duration += chunk_duration

                        if buffer_duration >= 10.0:
                            self._process_listen_segment(audio_buffer, np)
                            audio_buffer = []
                            buffer_duration = 0
                            in_speech = False
                    else:
                        if in_speech:
                            audio_buffer.append(indata)
                            buffer_duration += chunk_duration
                            silence_count += 1

                            if silence_count >= max_silence_blocks or buffer_duration >= 10.0:
                                if buffer_duration >= self._min_speech_duration:
                                    self._process_listen_segment(audio_buffer, np)
                                audio_buffer = []
                                buffer_duration = 0
                                in_speech = False
                                silence_count = 0

                except Exception as e:
                    logger.error(f"持续监听循环异常: {e}")
                    break
        finally:
            if audio_buffer and buffer_duration >= self._min_speech_duration:
                try:
                    self._process_listen_segment(audio_buffer, np)
                except Exception:
                    pass
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            self._notify_state("idle")

    def _process_listen_segment(self, audio_chunks: list, np_module):
        if not audio_chunks:
            return

        self._notify_state("processing")
        audio_data = np_module.concatenate(audio_chunks, axis=0)

        if not self._verify_voiceprint(audio_data):
            self._notify_state("listening")
            return

        result = self._recognize_audio(audio_data)

        if result:
            text = result.get("text", "")
            logger.info(f"实时对话识别结果: {text}")
            self._notify_final(result)
        else:
            logger.debug("实时对话：未识别到有效内容")

        self._notify_state("listening")

    def _wake_word_loop(self):
        sd = _try_import_sounddevice()
        np = _try_import_numpy()
        if sd is None or np is None:
            self._notify_error("音频设备不可用")
            return

        self._notify_state("listening")
        logger.info(f"唤醒词监听中... (唤醒词: {', '.join(self._wake_words)})")

        chunk_duration = 0.5
        block_size = int(SAMPLE_RATE * chunk_duration)

        try:
            stream = sd.InputStream(
                channels=CHANNELS,
                samplerate=SAMPLE_RATE,
                blocksize=block_size,
                dtype='int16',
            )
            stream.start()
        except Exception as e:
            logger.error(f"唤醒词流启动失败: {e}")
            self._notify_error(f"麦克风启动失败: {e}")
            return

        audio_buffer = []
        buffer_duration = 0
        max_buffer_duration = 5.0

        try:
            while self._mode == MODE_WAKE_WORD:
                try:
                    indata, overflowed = stream.read(block_size)
                    if overflowed:
                        continue

                    audio_buffer.append(indata)
                    buffer_duration += chunk_duration

                    chunk = indata.astype(np.float32) / 32768.0
                    energy = np.sqrt(np.mean(chunk ** 2))

                    if energy > self._silence_threshold:
                        if buffer_duration > max_buffer_duration:
                            self._process_wake_word_audio(audio_buffer, np)
                            audio_buffer = []
                            buffer_duration = 0
                    else:
                        if buffer_duration > self._min_speech_duration:
                            self._process_wake_word_audio(audio_buffer, np)
                            audio_buffer = []
                            buffer_duration = 0

                    if buffer_duration > max_buffer_duration * 1.5:
                        audio_buffer = audio_buffer[-int(max_buffer_duration / chunk_duration):]
                        buffer_duration = len(audio_buffer) * chunk_duration

                except Exception as e:
                    logger.error(f"唤醒词循环异常: {e}")
                    break
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            self._notify_state("idle")

    def _process_wake_word_audio(self, audio_chunks: list, np_module):
        if not audio_chunks:
            return

        audio_data = np_module.concatenate(audio_chunks, axis=0)

        if not self._verify_voiceprint(audio_data):
            return

        result = self._recognize_audio(audio_data)
        if not result:
            return

        text = result.get("text", "")

        for wake_word in self._wake_words:
            if wake_word in text:
                idx = text.find(wake_word)
                remaining = text[idx + len(wake_word):].strip()

                self._notify_state("wake_detected")
                logger.info(f"检测到唤醒词「{wake_word}」，后续内容: {remaining}")

                if remaining:
                    new_result = dict(result)
                    new_result["text"] = remaining
                    self._notify_final(new_result)
                else:
                    new_result = dict(result)
                    new_result["text"] = "我在，请讲"
                    self._notify_final(new_result)
                break

        if self._on_partial and text:
            self._on_partial(f"[监听中] {text}")

    # ---------- 核心识别 ----------

    def _recognize_audio(self, audio_data) -> Optional[dict]:
        """
        识别音频数据，返回包含 text/emotion/event 的字典。

        Returns:
            dict: {"text": str, "emotion": str, "event": str} 或 None
        """
        if self._recognizer is None:
            if not self._ensure_model():
                return None

        np = _try_import_numpy()
        sf = _try_import_soundfile()
        if np is None or sf is None:
            logger.error("numpy 或 soundfile 不可用")
            return None

        try:
            # 确保音频是 int16 格式并保存为 WAV
            if audio_data.dtype != np.int16:
                if audio_data.dtype == np.float32 or audio_data.dtype == np.float64:
                    audio_data = (audio_data * 32767).astype(np.int16)
                else:
                    audio_data = audio_data.astype(np.int16)

            wav_path = os.path.join(self._temp_dir, f"speech_{int(time.time()*1000)}.wav")
            with wave.open(wav_path, 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio_data.tobytes())

            # 使用 soundfile 加载音频
            audio, sample_rate = sf.read(wav_path, dtype="float32", always_2d=True)
            audio = audio[:, 0]

            # sherpa-onnx 识别
            asr_stream = self._recognizer.create_stream()
            asr_stream.accept_waveform(sample_rate, audio)
            self._recognizer.decode_stream(asr_stream)

            # 解析结果
            result_str = str(asr_stream.result)
            result = json.loads(result_str)

            # 提取各字段
            raw_text = result.get('text', '')
            emotion_key = result.get('emotion', '').strip('<|>')
            event_key = result.get('event', '').strip('<|>')

            # 映射情感和事件
            emotion = EMOTION_MAP.get(emotion_key, f"[{emotion_key}]" if emotion_key else "")
            event = EVENT_MAP.get(event_key, f"[{event_key}]" if event_key else "")

            # 清理文本
            text = self._clean_output_text(raw_text)

            # 组合结果
            final_text = f"{event}{text}{emotion}"

            # 清理临时文件
            try:
                os.unlink(wav_path)
            except Exception:
                pass

            if final_text and final_text != "The.":
                audio_duration = len(audio_data) / SAMPLE_RATE
                logger.info(
                    f"📝 识别结果: text='{text}', emotion={emotion_key}, "
                    f"event={event_key}, audio时长={audio_duration:.2f}s"
                )
                return {
                    "text": final_text,
                    "raw_text": text,
                    "emotion": emotion_key,
                    "event": event_key,
                    "audio_duration": audio_duration,
                }

            logger.debug("识别结果为空或无效")
            return None

        except Exception as e:
            logger.error(f"语音识别失败: {e}")
            return None

    @staticmethod
    def _clean_output_text(text: str) -> str:
        """清理 SenseVoice 输出中的特殊 token。"""
        text = re.sub(r'<\|[^|]*\|>', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) < 2:
            return ""
        return text

    # ---------- 停止与清理 ----------

    def stop_all(self):
        self._stop_all()
        self._mode = MODE_OFF
        self._notify_state("idle")

    def _stop_all(self):
        self._recording = False

        try:
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None
        except Exception:
            pass

        if self._wake_thread and self._wake_thread.is_alive():
            self._wake_thread.join(timeout=2)
        if self._listen_thread and self._listen_thread.is_alive():
            self._listen_thread.join(timeout=2)
        self._wake_thread = None
        self._listen_thread = None

        logger.info("已停止所有语音活动")

    def cleanup(self):
        self._stop_all()
        self._recognizer = None
        self._model_loaded = False
        logger.info("SpeechManager 已清理")

    # ---------- 通知 ----------

    def _notify_state(self, state: str):
        if self._on_state_change:
            try:
                self._on_state_change(state)
            except Exception:
                pass

    def _notify_final(self, result):
        """通知最终结果，支持字符串和字典两种格式。"""
        if self._on_final:
            try:
                if isinstance(result, dict):
                    # 旧接口可能期望字符串，这里提取 text 字段
                    self._on_final(result.get("text", ""))
                else:
                    self._on_final(result)
            except Exception:
                pass

    def _notify_partial(self, text: str):
        if self._on_partial:
            try:
                self._on_partial(text)
            except Exception:
                pass

    def _notify_error(self, error: str):
        logger.error(f"语音错误: {error}")
        if self._on_error:
            try:
                self._on_error(error)
            except Exception:
                pass

    def is_recording(self) -> bool:
        return self._recording

    def is_listening(self) -> bool:
        return self._mode in (MODE_WAKE_WORD, MODE_PUSH_TO_TALK) and (
            (self._wake_thread is not None and self._wake_thread.is_alive()) or
            (self._listen_thread is not None and self._listen_thread.is_alive())
        )

    def get_model_info(self) -> dict:
        """获取模型信息。"""
        return {
            "model_type": "sherpa-onnx-sense-voice",
            "model_loaded": self._model_loaded,
            "model_path": self._model_path,
            "recognizer_ready": self._recognizer is not None,
        }
