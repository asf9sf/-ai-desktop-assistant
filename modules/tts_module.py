"""TTS 模块：CosyVoice（情感） + Sherpa-ONNX（主） + pyttsx3（兜底）。
纯 CPU 离线语音合成，不占用 GPU。

特性：
- CosyVoice：支持情感控制、多种说话风格
- 回调模式播放，彻底避免 sd.play/sd.wait 卡死
- 流式朗读：AI 输出时实时分句入队，与大模型线程并行
- 进度回调：报告当前句子索引和采样播放进度
"""
import os
import sys
import re
import tempfile
import threading
import queue
from typing import Optional, Callable

# 延迟导入
_sherpa = None
_sherpa_imported = False
_pyttsx3 = None
_pyttsx3_imported = False
_sounddevice = None
_sd_imported = False
_numpy = None
_np_imported = None
_cosyvoice = None
_cosyvoice_imported = False
_torchaudio = None
_torchaudio_imported = False


def _try_import_sherpa():
    global _sherpa, _sherpa_imported
    if _sherpa_imported:
        return _sherpa
    _sherpa_imported = True
    try:
        import sherpa_onnx
        _sherpa = sherpa_onnx
    except Exception:
        _sherpa = None
    return _sherpa


def _try_import_pyttsx3():
    global _pyttsx3, _pyttsx3_imported
    if _pyttsx3_imported:
        return _pyttsx3
    _pyttsx3_imported = True
    try:
        import pyttsx3
        _pyttsx3 = pyttsx3
    except Exception:
        _pyttsx3 = None
    return _pyttsx3


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


def _try_import_cosyvoice():
    """尝试导入 CosyVoice 模块（含 Matcha-TTS 子模块路径）。"""
    global _cosyvoice, _cosyvoice_imported
    if _cosyvoice_imported:
        return _cosyvoice
    _cosyvoice_imported = True
    try:
        base_path = os.path.dirname(os.path.dirname(__file__))
        cosyvoice_path = os.path.join(base_path, 'CosyVoice')
        matcha_path = os.path.join(cosyvoice_path, 'third_party', 'Matcha-TTS')
        if os.path.isdir(matcha_path) and matcha_path not in sys.path:
            sys.path.insert(0, matcha_path)
        if cosyvoice_path not in sys.path:
            sys.path.insert(0, cosyvoice_path)
        from cosyvoice.cli.cosyvoice import AutoModel
        _cosyvoice = AutoModel
    except Exception:
        _cosyvoice = None
    return _cosyvoice


def _try_import_torchaudio():
    """尝试导入 torchaudio。"""
    global _torchaudio, _torchaudio_imported
    if _torchaudio_imported:
        return _torchaudio
    _torchaudio_imported = True
    try:
        import torchaudio
        _torchaudio = torchaudio
    except Exception:
        _torchaudio = None
    return _torchaudio


# ---------------- 句子切分 ----------------
_SENT_SPLIT = re.compile(r'(?<=[。！？.!?\n…])\s*')
_SENT_END = re.compile(r'[。！？.!?\n…]$')


def split_sentences(text: str) -> list:
    """按标点切分句子。"""
    if not text:
        return []
    parts = _SENT_SPLIT.split(text)
    return [p.strip() for p in parts if p.strip()]


def is_sentence_end(text: str) -> bool:
    """判断文本是否以句子结束标点结尾。"""
    return bool(text and _SENT_END.search(text.strip()[-1]))


# ---------------- CosyVoice 引擎（情感TTS） ----------------
class CosyVoiceTTS:
    """CosyVoice 情感TTS引擎封装。支持情感控制、多种说话风格。"""

    def __init__(self, model_dir: str = "",
                 speaker: str = "中文女",
                 emotion: str = "",
                 volume: float = 1.5):
        cosyvoice_cls = _try_import_cosyvoice()
        if cosyvoice_cls is None:
            raise RuntimeError("CosyVoice 未安装，请执行: pip install modelscope transformers")

        # 默认模型路径
        if not model_dir:
            model_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                     'CosyVoice', 'pretrained_models', 'CosyVoice-300M-Instruct')

        if not os.path.isdir(model_dir):
            raise RuntimeError(f"CosyVoice 模型目录不存在: {model_dir}")

        self.model_dir = model_dir
        self.speaker = speaker
        self.emotion = emotion
        self.volume = max(0.1, min(5.0, float(volume)))
        self.sample_rate = 22050
        self._model = None
        self._temp_dir = tempfile.mkdtemp(prefix="cosyvoice_tts_")

    def load_model(self):
        """延迟加载模型（首次调用时加载）。"""
        if self._model is not None:
            return True
        try:
            AutoModel = _try_import_cosyvoice()
            if AutoModel is None:
                return False
            self._model = AutoModel(model_dir=self.model_dir)
            self.sample_rate = self._model.sample_rate
            return True
        except Exception as e:
            self._model = None
            return False

    def set_emotion(self, emotion: str):
        """设置情感指令。"""
        self.emotion = emotion

    def set_speaker(self, speaker: str):
        """设置说话人。"""
        self.speaker = speaker

    def set_volume(self, volume: float):
        """设置音量增益。"""
        self.volume = max(0.1, min(5.0, float(volume)))

    def synthesize(self, text: str) -> Optional['bytes']:
        """合成文本为 PCM 16-bit 小端字节流。失败返回 None。"""
        if not text or not text.strip():
            return None

        if not self.load_model():
            return None

        try:
            import torchaudio
            np = _try_import_numpy()
            if np is None:
                return None

            # 构建情感指令
            instruct = self.emotion if self.emotion else ""

            # 使用推理模式
            wav_file = os.path.join(self._temp_dir, f"tts_{hash(text)}.wav")
            
            for i, j in enumerate(self._model.inference_instruct(
                text, self.speaker, instruct
            )):
                torchaudio.save(wav_file, j['tts_speech'], self.sample_rate)
                break

            # 读取音频文件
            if not os.path.exists(wav_file):
                return None

            soundfile = __import__('soundfile')
            data, sr = soundfile.read(wav_file)
            self.sample_rate = sr

            # 转换为 PCM 16-bit
            arr = np.array(data, dtype=np.float32)
            if self.volume != 1.0:
                arr *= self.volume
            arr = np.clip(arr, -1.0, 1.0)
            arr = (arr * 32767).astype(np.int16)
            
            # 清理临时文件
            try:
                os.remove(wav_file)
            except Exception:
                pass

            return arr.tobytes()

        except Exception:
            return None

    def cleanup(self):
        """清理临时文件。"""
        import shutil
        try:
            if os.path.exists(self._temp_dir):
                shutil.rmtree(self._temp_dir, ignore_errors=True)
        except Exception:
            pass


# ---------------- Sherpa-ONNX 引擎 ----------------
class SherpaTTS:
    """Sherpa-ONNX TTS 引擎封装。支持情感标签（通过 GenerationConfig.extra 传递）。"""

    # 支持的情感标签（中文 VITS 模型）
    EMOTION_TAGS = ["", "happy", "sad", "angry", "fearful", "surprised", "disgusted"]
    EMOTION_LABELS = {
        "": "默认", "happy": "开心", "sad": "悲伤", "angry": "愤怒",
        "fearful": "恐惧", "surprised": "惊讶", "disgusted": "厌恶",
    }

    def __init__(self, model_dir: str, speaker_id: int = 0,
                 num_speakers: int = 1, sample_rate: int = 22050,
                 volume: float = 2.5, emotion: str = ""):
        sherpa = _try_import_sherpa()
        if sherpa is None:
            raise RuntimeError("sherpa-onnx 未安装，请执行: pip install sherpa-onnx")
        if not os.path.isdir(model_dir):
            raise RuntimeError(f"模型目录不存在: {model_dir}")

        self.model_dir = model_dir
        self.sample_rate = sample_rate
        self.speaker_id = speaker_id
        self.volume = max(0.1, min(5.0, float(volume)))
        self.emotion = emotion.strip() if emotion else ""

        model_path = None
        lexicon_path = None
        tokens_path = None

        candidates = [model_dir]
        for sub in ("vits", "VITS"):
            p = os.path.join(model_dir, sub)
            if os.path.isdir(p):
                candidates.append(p)

        for d in candidates:
            if not os.path.isdir(d):
                continue
            try:
                for f in os.listdir(d):
                    fl = f.lower()
                    if fl.endswith(".onnx"):
                        if model_path is None:
                            model_path = os.path.join(d, f)
                    if fl in ("lexicon.txt", "lexicon"):
                        lexicon_path = os.path.join(d, f)
                    if fl in ("tokens.txt", "tokens"):
                        tokens_path = os.path.join(d, f)
            except (PermissionError, OSError):
                continue

        if not model_path:
            raise RuntimeError(f"未在 {model_dir} 找到 .onnx 模型文件")

        if tokens_path is None:
            for d in candidates:
                p = os.path.join(d, "tokens.txt")
                if os.path.exists(p):
                    tokens_path = p
                    break
        if lexicon_path is None:
            for d in candidates:
                p = os.path.join(d, "lexicon.txt")
                if os.path.exists(p):
                    lexicon_path = p
                    break

        rule_fsts = []
        for d in candidates:
            if not os.path.isdir(d):
                continue
            try:
                for f in os.listdir(d):
                    if f.lower().endswith(".fst"):
                        p = os.path.join(d, f)
                        if p not in rule_fsts:
                            rule_fsts.append(p)
            except (PermissionError, OSError):
                continue

        is_matcha = "matcha" in os.path.basename(model_path).lower()
        if is_matcha:
            acoustic = model_path
            vocoder = None
            for d in candidates:
                try:
                    for f in os.listdir(d):
                        if ("hifigan" in f.lower() or "vocoder" in f.lower()) and f.endswith(".onnx"):
                            vocoder = os.path.join(d, f)
                            break
                except (PermissionError, OSError):
                    continue
                if vocoder:
                    break
            if vocoder is None:
                raise RuntimeError("Matcha-TTS 需要 vocoder (.onnx)")
            cfg = sherpa.OfflineTtsMatchaModelConfig(
                acoustic_model=acoustic,
                vocoder=vocoder,
                lexicon=lexicon_path or "",
                tokens=tokens_path or "",
            )
        else:
            cfg = sherpa.OfflineTtsVitsModelConfig(
                model=model_path,
                lexicon=lexicon_path or "",
                tokens=tokens_path or "",
            )

        model_cfg = sherpa.OfflineTtsModelConfig(
            num_threads=2,
            debug=False,
            provider="cpu",
            **({"vits": cfg} if not is_matcha else {"matcha": cfg}),
        )
        tts_cfg = sherpa.OfflineTtsConfig(
            model=model_cfg,
            rule_fsts=",".join(rule_fsts) if rule_fsts else "",
            max_num_sentences=2,
        )
        self.tts = sherpa.OfflineTts(tts_cfg)

        try:
            actual = self.tts.num_speakers
            self.num_speakers = max(1, int(actual))
        except Exception:
            self.num_speakers = max(1, num_speakers)

        if self.speaker_id >= self.num_speakers:
            self.speaker_id = 0

    def set_speaker_id(self, speaker_id: int):
        if speaker_id < 0:
            speaker_id = 0
        if speaker_id >= self.num_speakers:
            speaker_id = 0
        self.speaker_id = speaker_id

    def set_volume(self, volume: float):
        self.volume = max(0.1, min(5.0, float(volume)))

    def set_emotion(self, emotion: str):
        """设置情感标签。"""
        self.emotion = emotion.strip() if emotion else ""

    def synthesize(self, text: str) -> Optional['bytes']:
        """合成文本为 PCM 16-bit 小端字节流（已应用音量增益和情感标签）。失败返回 None。"""
        try:
            sherpa = _try_import_sherpa()
            if sherpa is None:
                return None

            # 构建生成配置（支持情感标签）
            gc = sherpa.GenerationConfig()
            gc.sid = self.speaker_id
            gc.speed = 1.0
            if self.emotion:
                gc.extra = {"emotion": self.emotion}

            audio = self.tts.generate(text, gc)
            samples = audio.samples
            sr = audio.sample_rate
            self.sample_rate = sr
            np = _try_import_numpy()
            if np is None:
                return None
            arr = np.array(samples, dtype=np.float32)
            # 音量增益（默认 2.5x 放大，VITS 模型输出振幅约 0.25，偏低）
            if self.volume != 1.0:
                arr *= self.volume
            arr = np.clip(arr, -1.0, 1.0)
            arr = (arr * 32767).astype(np.int16)
            return arr.tobytes()
        except Exception:
            return None


# ---------------- 系统 TTS（pyttsx3）兜底 ----------------
class SystemTTS:
    """Windows SAPI5 系统 TTS，作为兜底。"""

    def __init__(self):
        pyttsx3 = _try_import_pyttsx3()
        if pyttsx3 is None:
            raise RuntimeError("pyttsx3 未安装，请执行: pip install pyttsx3")
        self.engine = pyttsx3.init()
        self._set_chinese_voice()

    def _set_chinese_voice(self):
        try:
            voices = self.engine.getProperty('voices')
            for v in voices:
                vid = (v.id or "").lower()
                name = (v.name or "").lower()
                if "chinese" in vid or "zh" in vid or "huihui" in name or "hanhan" in name or "yaoyao" in name:
                    self.engine.setProperty('voice', v.id)
                    return
            if voices:
                self.engine.setProperty('voice', voices[0].id)
        except Exception:
            pass

    def speak_blocking(self, text: str):
        try:
            self.engine.say(text)
            self.engine.runAndWait()
        except Exception:
            pass

    def stop(self):
        try:
            self.engine.stop()
        except Exception:
            pass


# ---------------- TTS 管理器 ----------------
class TTSManager:
    """TTS 管理器：回调模式播放 + 流式朗读 + 进度回调。

    engine: "cosyvoice" | "sherpa" | "system" | "off"

    回调：
    - on_state_change(playing: bool)：播放状态变化
    - on_progress(sentence_idx: int, total_sentences: int, sample_pos: int, total_samples: int)：
      朗读进度（仅 Sherpa 模式有采样级进度）
    """

    def __init__(self, engine: str = "sherpa", model_dir: str = "",
                 speaker_id: int = 0, auto_play: bool = False,
                 volume: float = 2.5,
                 cosyvoice_speaker: str = "中文女",
                 cosyvoice_emotion: str = "",
                 sherpa_emotion: str = "",
                 on_state_change: Optional[Callable[[bool], None]] = None,
                 on_progress: Optional[Callable[[int, int, int, int], None]] = None):
        self.engine_name = engine
        self.model_dir = model_dir
        self.speaker_id = speaker_id
        self.auto_play = auto_play
        self.volume = max(0.1, float(volume))
        self.on_state_change = on_state_change
        self.on_progress = on_progress

        # CosyVoice 配置
        self.cosyvoice_speaker = cosyvoice_speaker
        self.cosyvoice_emotion = cosyvoice_emotion

        # Sherpa 情感标签
        self.sherpa_emotion = sherpa_emotion.strip() if sherpa_emotion else ""

        self._sherpa: Optional[SherpaTTS] = None
        self._system: Optional[SystemTTS] = None
        self._cosyvoice_tts: Optional[CosyVoiceTTS] = None
        self._available = False
        self._lock = threading.Lock()

        # 播放队列与线程
        self._queue: queue.Queue = queue.Queue()
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._playing = False

        # sounddevice 流（回调模式）
        self._sd_stream = None
        self._stream_done: Optional[threading.Event] = None

        # 流式朗读缓冲
        self._streaming_buf = ""
        self._streaming_total = 0  # 流式模式已入队句子数

        # 进度跟踪
        self._total_sentences = 0
        self._played_sentences = 0

    # ---------- 配置 ----------
    def set_on_state_change(self, cb: Callable[[bool], None]):
        self.on_state_change = cb

    def set_on_progress(self, cb: Callable[[int, int, int, int], None]):
        self.on_progress = cb

    def set_engine(self, engine: str):
        if engine != self.engine_name:
            self.engine_name = engine
            self._sherpa = None
            self._system = None
            self._cosyvoice_tts = None
            self._available = False

    def set_model_dir(self, model_dir: str):
        if model_dir != self.model_dir:
            self.model_dir = model_dir
            self._sherpa = None
            self._cosyvoice_tts = None
            self._available = False

    def set_speaker_id(self, speaker_id: int):
        self.speaker_id = speaker_id
        if self._sherpa is not None:
            self._sherpa.set_speaker_id(speaker_id)

    def set_cosyvoice_speaker(self, speaker: str):
        """设置 CosyVoice 说话人。"""
        self.cosyvoice_speaker = speaker
        if self._cosyvoice_tts is not None:
            self._cosyvoice_tts.set_speaker(speaker)

    def set_cosyvoice_emotion(self, emotion: str):
        """设置 CosyVoice 情感指令。"""
        self.cosyvoice_emotion = emotion
        if self._cosyvoice_tts is not None:
            self._cosyvoice_tts.set_emotion(emotion)

    def set_auto_play(self, auto: bool):
        self.auto_play = auto

    def set_volume(self, volume: float):
        """设置音量增益（0.1~5.0）。1.0 = 原始音量，2.0 = 放大2倍。"""
        self.volume = max(0.1, min(5.0, float(volume)))
        if self._sherpa is not None:
            self._sherpa.set_volume(self.volume)
        if self._cosyvoice_tts is not None:
            self._cosyvoice_tts.set_volume(self.volume)

    def set_sherpa_emotion(self, emotion: str):
        """设置 Sherpa 情感标签。支持: happy, sad, angry, fearful, surprised, disgusted"""
        self.sherpa_emotion = emotion.strip() if emotion else ""
        if self._sherpa is not None:
            self._sherpa.set_emotion(self.sherpa_emotion)

    def is_available(self) -> bool:
        return self._available

    def is_playing(self) -> bool:
        return self._playing

    def _ensure_engine(self) -> bool:
        with self._lock:
            if self._available:
                return True
            if self.engine_name == "off":
                return False
            if self.engine_name == "cosyvoice":
                try:
                    self._cosyvoice_tts = CosyVoiceTTS(
                        model_dir=self.model_dir,
                        speaker=self.cosyvoice_speaker,
                        emotion=self.cosyvoice_emotion,
                        volume=self.volume
                    )
                    self._available = True
                except Exception:
                    try:
                        self._system = SystemTTS()
                        self._available = True
                        self.engine_name = "system"
                    except Exception:
                        self._available = False
            elif self.engine_name == "sherpa":
                try:
                    self._sherpa = SherpaTTS(self.model_dir, speaker_id=self.speaker_id,
                                             volume=self.volume, emotion=self.sherpa_emotion)
                    self._available = True
                except Exception:
                    try:
                        self._system = SystemTTS()
                        self._available = True
                        self.engine_name = "system"
                    except Exception:
                        self._available = False
            elif self.engine_name == "system":
                try:
                    self._system = SystemTTS()
                    self._available = True
                except Exception:
                    self._available = False
            return self._available

    # ---------- 常规朗读 ----------
    def speak(self, text: str):
        """异步朗读整段文本（自动分句排队）。"""
        if not text or not text.strip():
            return
        if self.engine_name == "off":
            return
        sentences = split_sentences(text)
        self._total_sentences = len(sentences)
        self._played_sentences = 0
        self._start_worker()
        for s in sentences:
            if s.strip():
                self._queue.put(s)

    def speak_immediately(self, text: str):
        """立即朗读（停止当前 + 清空队列 + 朗读新文本）。"""
        self._do_stop()
        self.speak(text)

    # ---------- 流式朗读（与 LLM 并行） ----------
    def speak_streaming_start(self):
        """开始流式朗读模式。清空缓冲和队列。"""
        self._do_stop()
        self._streaming_buf = ""
        self._streaming_total = 0
        self._total_sentences = 0
        self._played_sentences = 0
        self._start_worker()

    def speak_streaming_append(self, delta: str):
        """追加 LLM 增量文本。自动检测句子边界并入队朗读。"""
        if not delta or self.engine_name == "off":
            return
        self._streaming_buf += delta
        # 检测是否有完整句子
        while True:
            m = _SENT_SPLIT.search(self._streaming_buf)
            if m:
                sentence = self._streaming_buf[:m.end()].strip()
                self._streaming_buf = self._streaming_buf[m.end():]
                if sentence:
                    self._total_sentences += 1
                    self._queue.put(sentence)
            else:
                break

    def speak_streaming_flush(self):
        """流式结束：把缓冲区剩余文本入队。"""
        remaining = self._streaming_buf.strip()
        self._streaming_buf = ""
        if remaining:
            self._total_sentences += 1
            self._queue.put(remaining)

    # ---------- 停止 ----------
    def stop(self):
        self._do_stop()

    def _do_stop(self):
        self._stop_flag.set()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        if self._system:
            self._system.stop()
        # 停止 sounddevice 流
        if self._sd_stream is not None:
            try:
                self._sd_stream.stop()
                self._sd_stream.close()
            except Exception:
                pass
            self._sd_stream = None
        if self._stream_done is not None:
            self._stream_done.set()
        self._set_playing(False)

    def _set_playing(self, playing: bool):
        self._playing = playing
        if self.on_state_change:
            try:
                self.on_state_change(playing)
            except Exception:
                pass

    def _report_progress(self, sample_pos: int, total_samples: int):
        if self.on_progress:
            try:
                self.on_progress(
                    self._played_sentences,
                    self._total_sentences,
                    sample_pos,
                    total_samples,
                )
            except Exception:
                pass

    # ---------- 工作线程 ----------
    def _start_worker(self):
        """启动或唤醒工作线程。始终清除 _stop_flag。"""
        self._running = True
        self._stop_flag.clear()
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def _worker_loop(self):
        while self._running:
            try:
                sentence = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if self._stop_flag.is_set():
                continue
            self._play_sentence(sentence)
            self._played_sentences += 1

    def _play_sentence(self, text: str):
        if not self._ensure_engine():
            return
        self._set_playing(True)
        try:
            if self._cosyvoice_tts:
                self._play_with_cosyvoice(text)
            elif self._sherpa:
                self._play_with_sherpa(text)
            elif self._system:
                self._system.speak_blocking(text)
        except Exception:
            pass
        finally:
            if self._queue.empty():
                self._set_playing(False)

    def _play_with_cosyvoice(self, text: str):
        """用 CosyVoice 合成并用回调模式播放。"""
        pcm = self._cosyvoice_tts.synthesize(text)
        if pcm is None:
            if self._system is None:
                try:
                    self._system = SystemTTS()
                except Exception:
                    self._system = None
            if self._system:
                self._system.speak_blocking(text)
            return

        sd = _try_import_sounddevice()
        np = _try_import_numpy()
        if sd is None or np is None:
            return

        try:
            sample_rate = self._cosyvoice_tts.sample_rate
            arr = np.frombuffer(pcm, dtype=np.int16)
            arr_f = arr.astype(np.float32) / 32768.0
            total = len(arr_f)
            if total == 0:
                return

            idx = [0]
            self._stream_done = threading.Event()

            def callback(outdata, frames, time_info, status):
                if self._stop_flag.is_set():
                    raise sd.CallbackAbort
                cur = idx[0]
                if cur >= total:
                    raise sd.CallbackStop
                n = min(frames, total - cur)
                outdata[:n, 0] = arr_f[cur:cur + n]
                if n < frames:
                    outdata[n:, 0] = 0
                idx[0] += n
                self._report_progress(idx[0], total)

            def finished_cb():
                self._stream_done.set()

            if self._sd_stream is not None:
                try:
                    self._sd_stream.stop()
                    self._sd_stream.close()
                except Exception:
                    pass

            self._sd_stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype='float32',
                callback=callback,
                finished_callback=finished_cb,
            )
            self._sd_stream.start()

            while not self._stream_done.is_set():
                if self._stop_flag.is_set():
                    try:
                        self._sd_stream.stop()
                    except Exception:
                        pass
                    break
                self._stream_done.wait(timeout=0.1)

            try:
                self._sd_stream.stop()
                self._sd_stream.close()
            except Exception:
                pass
            self._sd_stream = None
            self._stream_done = None
        except Exception:
            if self._sd_stream is not None:
                try:
                    self._sd_stream.stop()
                    self._sd_stream.close()
                except Exception:
                    pass
                self._sd_stream = None
            self._stream_done = None

    def _play_with_sherpa(self, text: str):
        """用 Sherpa 合成并用回调模式播放（彻底避免 sd.wait 卡死）。"""
        pcm = self._sherpa.synthesize(text)
        if pcm is None:
            if self._system is None:
                try:
                    self._system = SystemTTS()
                except Exception:
                    self._system = None
            if self._system:
                self._system.speak_blocking(text)
            return

        sd = _try_import_sounddevice()
        np = _try_import_numpy()
        if sd is None or np is None:
            return

        try:
            arr = np.frombuffer(pcm, dtype=np.int16)
            arr_f = arr.astype(np.float32) / 32768.0
            # 注意：音量增益已在 SherpaTTS.synthesize() 中应用，这里不再重复
            total = len(arr_f)
            if total == 0:
                return

            idx = [0]
            self._stream_done = threading.Event()

            def callback(outdata, frames, time_info, status):
                if self._stop_flag.is_set():
                    raise sd.CallbackAbort
                cur = idx[0]
                if cur >= total:
                    raise sd.CallbackStop
                n = min(frames, total - cur)
                outdata[:n, 0] = arr_f[cur:cur + n]
                if n < frames:
                    outdata[n:, 0] = 0
                idx[0] += n
                # 报告进度
                self._report_progress(idx[0], total)

            def finished_cb():
                self._stream_done.set()

            # 关闭旧流
            if self._sd_stream is not None:
                try:
                    self._sd_stream.stop()
                    self._sd_stream.close()
                except Exception:
                    pass

            self._sd_stream = sd.OutputStream(
                samplerate=self._sherpa.sample_rate,
                channels=1,
                dtype='float32',
                callback=callback,
                finished_callback=finished_cb,
            )
            self._sd_stream.start()

            # 轮询等待：不会永久阻塞，每 100ms 检查 stop_flag
            while not self._stream_done.is_set():
                if self._stop_flag.is_set():
                    try:
                        self._sd_stream.stop()
                    except Exception:
                        pass
                    break
                self._stream_done.wait(timeout=0.1)

            # 清理流
            try:
                self._sd_stream.stop()
                self._sd_stream.close()
            except Exception:
                pass
            self._sd_stream = None
            self._stream_done = None
        except Exception:
            if self._sd_stream is not None:
                try:
                    self._sd_stream.stop()
                    self._sd_stream.close()
                except Exception:
                    pass
                self._sd_stream = None
            self._stream_done = None

    def shutdown(self):
        self._running = False
        self._do_stop()
        if self._worker_thread:
            try:
                self._worker_thread.join(timeout=1.0)
            except Exception:
                pass
        # 清理 CosyVoice 资源
        if self._cosyvoice_tts is not None:
            try:
                self._cosyvoice_tts.cleanup()
            except Exception:
                pass
            self._cosyvoice_tts = None


# ---------------- 依赖检查工具 ----------------
def check_dependencies() -> dict:
    """检查 TTS 依赖状态（首次调用可能较慢，建议在后台线程中执行）。"""
    sherpa = _try_import_sherpa()
    pyttsx3 = _try_import_pyttsx3()
    sd = _try_import_sounddevice()
    np = _try_import_numpy()
    cosyvoice = _try_import_cosyvoice()
    torchaudio = _try_import_torchaudio()
    return {
        "sherpa_onnx": sherpa is not None,
        "pyttsx3": pyttsx3 is not None,
        "sounddevice": sd is not None,
        "numpy": np is not None,
        "cosyvoice": cosyvoice is not None,
        "torchaudio": torchaudio is not None,
        "sherpa_ready": sherpa is not None and sd is not None and np is not None,
        "cosyvoice_ready": cosyvoice is not None and torchaudio is not None and sd is not None,
    }


def check_dependencies_fast() -> dict:
    """快速检查 TTS 依赖状态（不触发重型导入，仅检查模块是否已缓存）。"""
    return {
        "sherpa_onnx": bool(_sherpa_imported and _sherpa is not None),
        "pyttsx3": bool(_pyttsx3_imported and _pyttsx3 is not None),
        "sounddevice": bool(_sd_imported and _sounddevice is not None),
        "numpy": bool(_np_imported and _numpy is not None),
        "cosyvoice": bool(_cosyvoice_imported and _cosyvoice is not None),
        "torchaudio": bool(_torchaudio_imported and _torchaudio is not None),
        "sherpa_ready": bool(_sherpa_imported and _sherpa is not None and
                             _sd_imported and _sounddevice is not None and
                             _np_imported and _numpy is not None),
        "cosyvoice_ready": bool(_cosyvoice_imported and _cosyvoice is not None and
                                 _torchaudio_imported and _torchaudio is not None and
                                 _sd_imported and _sounddevice is not None),
    }


def find_sherpa_models(search_dir: str) -> list:
    models = []
    if not os.path.isdir(search_dir):
        return models
    try:
        for entry in os.scandir(search_dir):
            if not entry.is_dir():
                continue
            has_onnx = False
            try:
                for f in os.listdir(entry.path):
                    if f.lower().endswith(".onnx"):
                        has_onnx = True
                        break
            except (PermissionError, OSError):
                continue
            if has_onnx:
                models.append(entry.path)
            try:
                for sub in os.scandir(entry.path):
                    if sub.is_dir():
                        try:
                            for f in os.listdir(sub.path):
                                if f.lower().endswith(".onnx"):
                                    models.append(sub.path)
                                    break
                        except (PermissionError, OSError):
                            continue
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError):
        pass
    return models
