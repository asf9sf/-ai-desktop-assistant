# TTS 语音合成模块设计文档

> 离线、CPU 友好的语音合成方案。Sherpa-ONNX 为主引擎，pyttsx3 兜底，支持流式并行朗读、进度回调、音量增益。

---

## 1. 设计目标

| 目标 | 说明 |
|------|------|
| 离线运行 | 不依赖云服务，全部本地推理 |
| CPU 友好 | GPU 已被大模型占用，TTS 必须在 CPU 上跑 |
| 不阻塞 UI | 合成与播放放在独立线程，主线程只接收回调 |
| 流式并行 | AI 一边输出文本，TTS 一边分句朗读，不等待整段完成 |
| 进度反馈 | 实时报告朗读进度（句子级 + 采样级），驱动 UI 进度条 |
| 可中断 | 任何时候能立即停止，清空队列，不卡死 |
| 自动降级 | Sherpa 加载失败时自动切换到系统 TTS |

---

## 2. 模块结构

```
modules/tts_module.py
├── _try_import_*()          # 延迟导入（sherpa / pyttsx3 / sounddevice / numpy）
├── split_sentences(text)    # 按中文标点切句
├── is_sentence_end(text)    # 判断文本是否以句末标点结尾
├── class SherpaTTS          # Sherpa-ONNX 引擎封装
│   ├── __init__             # 自动探测模型文件、规则 FST、构建 OfflineTts
│   ├── set_speaker_id       # 带越界校验
│   ├── set_volume           # 0.1~5.0
│   └── synthesize(text)     # 返回 PCM 16-bit 字节流（已应用音量增益）
├── class SystemTTS          # pyttsx3 兜底
│   ├── _set_chinese_voice   # 优先选择中文语音包
│   ├── speak_blocking(text) # 同步阻塞朗读
│   └── stop()
├── class TTSManager         # 统一管理器（核心）
│   ├── speak(text)               # 异步整段朗读
│   ├── speak_immediately(text)   # 立即朗读（停当前 + 清队列）
│   ├── speak_streaming_start()   # 开启流式模式
│   ├── speak_streaming_append()  # 追加 LLM 增量
│   ├── speak_streaming_flush()   # flush 剩余文本
│   ├── stop()                    # 立即中断
│   └── shutdown()                # 释放线程
├── check_dependencies()     # 依赖检查（给设置页用）
└── find_sherpa_models(dir)  # 扫描模型目录
```

---

## 3. 核心类：TTSManager

### 3.1 构造参数

```python
TTSManager(
    engine: str = "sherpa",        # "sherpa" | "system" | "off"
    model_dir: str = "",           # Sherpa 模型目录
    speaker_id: int = 0,           # 说话人 ID（多说话人模型）
    auto_play: bool = False,       # AI 回复后是否自动朗读
    volume: float = 2.5,           # 音量增益 0.1~5.0
    on_state_change: Callable,     # 播放状态变化回调
    on_progress: Callable,         # 朗读进度回调
)
```

### 3.2 回调签名

```python
# 播放状态变化（开始/结束）
on_state_change(playing: bool)

# 朗读进度（仅 Sherpa 有采样级进度）
on_progress(
    sentence_idx: int,      # 已完成句子数
    total_sentences: int,   # 总句子数
    sample_pos: int,        # 当前句已播放采样数
    total_samples: int,     # 当前句总采样数
)
```

### 3.3 三种朗读模式

| 模式 | API | 适用场景 |
|------|-----|---------|
| 整段朗读 | `speak(text)` | 用户点击「朗读」按钮 |
| 立即朗读 | `speak_immediately(text)` | 试听、欢迎语 |
| 流式朗读 | `speak_streaming_start/append/flush` | AI 自动朗读（与 LLM 并行） |

---

## 4. 关键设计点

### 4.1 回调模式播放（避免 sd.wait 卡死）

**问题**：早期版本用 `sd.play()/sd.wait()`，被 `sd.stop()` 中断后 `sd.wait()` 会永久阻塞，导致 worker 线程死锁，后续所有朗读失效。

**解决**：用 `sd.OutputStream` 的回调模式 + `threading.Event` 轮询。

```python
def _play_with_sherpa(self, text: str):
    pcm = self._sherpa.synthesize(text)
    arr = np.frombuffer(pcm, dtype=np.int16)
    arr_f = arr.astype(np.float32) / 32768.0
    total = len(arr_f)
    idx = [0]
    self._stream_done = threading.Event()

    def callback(outdata, frames, time_info, status):
        if self._stop_flag.is_set():
            raise sd.CallbackAbort        # 立即中断
        cur = idx[0]
        if cur >= total:
            raise sd.CallbackStop         # 正常结束
        n = min(frames, total - cur)
        outdata[:n, 0] = arr_f[cur:cur + n]
        if n < frames:
            outdata[n:, 0] = 0
        idx[0] += n
        self._report_progress(idx[0], total)   # 实时进度

    def finished_cb():
        self._stream_done.set()

    self._sd_stream = sd.OutputStream(
        samplerate=self._sherpa.sample_rate,
        channels=1, dtype='float32',
        callback=callback,
        finished_callback=finished_cb,
    )
    self._sd_stream.start()

    # 轮询等待：每 100ms 检查 stop_flag，不永久阻塞
    while not self._stream_done.is_set():
        if self._stop_flag.is_set():
            try: self._sd_stream.stop()
            except: pass
            break
        self._stream_done.wait(timeout=0.1)
```

### 4.2 工作线程的 stop_flag 陷阱

**问题**：`_start_worker()` 在线程已存在时不清 `_stop_flag`，导致 `stop()` 后新句子被 worker 跳过。

**解决**：无论线程是否已存在，**始终清除** `_stop_flag`。

```python
def _start_worker(self):
    self._running = True
    self._stop_flag.clear()   # ← 关键：始终清除
    if self._worker_thread and self._worker_thread.is_alive():
        return
    self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
    self._worker_thread.start()
```

### 4.3 流式并行朗读

TTS 与 LLM 输出**真正并行**：LLM 每产生一段增量文本，TTS 立即检测句子边界并入队朗读，不必等整段回复完成。

```python
# main_window.py 中的集成
def _send_message(self, text):
    # ... 启动 LLM worker ...
    if self.tts and self.tts.auto_play:
        self.tts.speak_streaming_start()      # ① 开启流式模式

def _on_llm_partial(self, delta):
    # ... 更新气泡 ...
    if self.tts and self.tts.auto_play:
        self.tts.speak_streaming_append(delta)  # ② 实时分句入队

def _on_llm_done(self, full_text):
    # ... 完成气泡 ...
    if self.tts and self.tts.auto_play:
        self.tts.speak_streaming_flush()        # ③ flush 剩余
```

`speak_streaming_append` 内部用正则检测句子边界：

```python
_SENT_SPLIT = re.compile(r'(?<=[。！？.!?\n…])\s*')

def speak_streaming_append(self, delta: str):
    self._streaming_buf += delta
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
```

### 4.4 音量增益

**问题**：VITS 模型输出振幅只有满幅的 ~19%，声音偏小。

**解决**：在 `SherpaTTS.synthesize()` 中直接乘以增益，PCM 里就包含放大结果（避免播放环节重复处理）。

```python
class SherpaTTS:
    def __init__(self, ..., volume: float = 2.5):
        self.volume = max(0.1, min(5.0, float(volume)))

    def synthesize(self, text):
        audio = self.tts.generate(text, sid=self.speaker_id, speed=1.0)
        arr = np.array(audio.samples, dtype=np.float32)
        if self.volume != 1.0:
            arr *= self.volume          # 增益
        arr = np.clip(arr, -1.0, 1.0)   # 防削波
        arr = (arr * 32767).astype(np.int16)
        return arr.tobytes()
```

**默认 2.5x**：输出从满幅 19% → 49%，感知音量明显提升，留足 headroom 不爆音。

### 4.5 Sherpa-ONNX API 正确调用

sherpa-onnx 1.13.x 的 API 容易踩坑：

```python
# ❌ 错误写法（会失败）
cfg = sherpa.OfflineTtsVitsModelConfig(model=path, lexicon=lex, tokens=tok)
cfg.rule_fsts = "..."                       # rule_fsts 不在 vits config 上！
mc = sherpa.OfflineTtsModelConfig(
    matcha=None, vits=cfg,                  # 传 None 会覆盖默认值导致类型错误
    num_threads=2, provider="cpu",
)
tc = sherpa.OfflineTtsConfig(model_cfg=mc)  # 参数名应是 model，不是 model_cfg！

# ✅ 正确写法
vits = sherpa.OfflineTtsVitsModelConfig(model=path, lexicon=lex, tokens=tok)
model_cfg = sherpa.OfflineTtsModelConfig(
    vits=vits,                              # 只传需要的字段，不传 None
    num_threads=2, debug=False, provider="cpu",
)
tts_cfg = sherpa.OfflineTtsConfig(
    model=model_cfg,                        # 参数名是 model
    rule_fsts=",".join(rule_fsts),          # rule_fsts 在这里
    max_num_sentences=2,
)
tts = sherpa.OfflineTts(tts_cfg)
```

### 4.6 懒加载 + 自动降级

- **懒加载**：TTSManager 创建时不加载引擎，首次 `speak` 时才加载，避免启动卡顿
- **自动降级**：Sherpa 加载失败 → 自动切换到 SystemTTS（pyttsx3）

```python
def _ensure_engine(self) -> bool:
    with self._lock:
        if self._available:
            return True
        if self.engine_name == "sherpa":
            try:
                self._sherpa = SherpaTTS(self.model_dir, ...)
                self._available = True
            except Exception:
                try:
                    self._system = SystemTTS()       # 降级
                    self._available = True
                    self.engine_name = "system"
                except Exception:
                    self._available = False
        return self._available
```

### 4.7 越界 speaker_id 静默吞异常

**问题**：vits-zh-ll 有 5 个说话人，传 `sid=5` 时 Sherpa 不报错也不出声。

**解决**：初始化和 `set_speaker_id` 都做越界校验。

```python
def set_speaker_id(self, speaker_id: int):
    if speaker_id < 0:
        speaker_id = 0
    if speaker_id >= self.num_speakers:
        speaker_id = 0       # 越界回退到 0
    self.speaker_id = speaker_id
```

---

## 5. UI 集成

### 5.1 主窗口（main_window.py）

| 组件 | 说明 |
|------|------|
| `🔊 朗读` 按钮 | 点击朗读最后一条 AI 回复；播放中变 `⏸ 停止朗读` |
| 蓝色进度条 | 输入区上方 6px，播放时显示，句子进度(70%)+采样进度(30%) |
| 自动朗读 | `auto_play=True` 时，AI 流式输出实时分句朗读 |
| 状态回调 | `QTimer.singleShot(0, ...)` 切回主线程更新 UI |

```python
def _init_tts(self):
    s = self.llm.settings
    self.tts = TTSManager(
        engine=s.get("tts_engine", "off"),
        model_dir=s.get("tts_model_dir", ""),
        speaker_id=s.get("tts_speaker_id", 0),
        auto_play=s.get("tts_auto_play", False),
        volume=s.get("tts_volume", 2.5),
    )
    self.tts.set_on_state_change(self._on_tts_state_change)
    self.tts.set_on_progress(self._on_tts_progress)

def _on_tts_progress(self, sidx, total, pos, samples):
    # 工作线程回调 → 切主线程
    QTimer.singleShot(0, lambda: self._update_tts_progress(sidx, total, pos, samples))

def _update_tts_progress(self, sidx, total, pos, samples):
    sentence_part = (sidx / total) * 70 if total > 0 else 0
    sample_part = (pos / samples) * 30 if samples > 0 else 0
    self.tts_progress.setValue(int(min(100, sentence_part + sample_part)))
```

### 5.2 设置页（settings_dialog.py）

「⚙ 设置 → 语音朗读」标签页：

| 控件 | 说明 |
|------|------|
| 依赖状态面板 | 显示 sherpa-onnx / sounddevice / numpy / pyttsx3 是否就绪 |
| 一键安装按钮 | 缺依赖时显示，调用 pip 安装 |
| 引擎选择 | 关闭 / 系统 TTS / Sherpa-ONNX |
| 自动朗读复选框 | AI 回复后是否自动朗读 |
| 模型目录 | 浏览 / 自动扫描 |
| 说话人 ID | SpinBox，试听后自动检测范围 |
| 音量增益滑块 | 0.5x ~ 5.0x，默认 2.5x |
| 试听按钮 | 复用同一 TTSManager 实例，避免音频流冲突 |

**试听复用机制**（避免重复创建导致音频流冲突）：

```python
def _test_tts(self):
    if not hasattr(self, "_test_mgr") or self._test_mgr is None:
        self._test_mgr = TTSManager(engine=engine, model_dir=model_dir,
                                    speaker_id=sid, volume=vol)
    else:
        self._test_mgr.set_engine(engine)
        self._test_mgr.set_model_dir(model_dir)
        self._test_mgr.set_speaker_id(sid)
        self._test_mgr.set_volume(vol)
    self._test_mgr.speak_immediately("你好，这是语音朗读测试。")

def closeEvent(self, event):
    if hasattr(self, "_test_mgr") and self._test_mgr is not None:
        self._test_mgr.shutdown()       # 关闭时清理，避免音频流残留
        self._test_mgr = None
```

---

## 6. 配置项（settings.json）

```json
{
  "tts_engine": "sherpa",           // "off" | "system" | "sherpa"
  "tts_model_dir": "F:/jubensha2/game5/data/tts_models/vits-zh-ll",
  "tts_auto_play": true,            // AI 回复后自动朗读
  "tts_speaker_id": 0,              // 说话人 ID
  "tts_volume": 2.5                 // 音量增益 0.1~5.0
}
```

默认值在 `llm_module.py` 的 `load_settings()` 中设置：

```python
self.settings.setdefault("tts_engine", "system")
self.settings.setdefault("tts_model_dir", "")
self.settings.setdefault("tts_auto_play", False)
self.settings.setdefault("tts_speaker_id", 0)
self.settings.setdefault("tts_volume", 2.5)
```

---

## 7. 模型

### 7.1 当前使用的模型

**vits-zh-ll**（轻量中文 VITS）

- 来源：`https://huggingface.co/csukuangfj/sherpa-onnx-vits-zh-ll`
- 镜像下载：`HF_ENDPOINT=https://hf-mirror.com` + `huggingface_hub.snapshot_download`
- 大小：~115 MB
- 说话人：5 个
- 采样率：16 kHz
- CPU 加载时间：~0.6 秒

### 7.2 模型文件结构

```
data/tts_models/vits-zh-ll/
├── model.onnx              # VITS 神经网络
├── lexicon.txt             # 词典
├── tokens.txt              # 音素表
├── date.fst                # 日期归一化规则
├── number.fst              # 数字归一化规则
├── phone.fst               # 电话号码规则
├── new_heteronym.fst       # 多音字规则
├── G_multisperaker_latest.json
└── dict/                   # jieba 分词词典
    ├── jieba.dict.utf8
    ├── hmm_model.utf8
    └── ...
```

### 7.3 自动探测逻辑

`SherpaTTS.__init__` 会自动在模型目录及其 `vits/` 子目录下查找：
- `*.onnx` → 模型文件
- `lexicon.txt` → 词典
- `tokens.txt` → 音素表
- `*.fst` → 规则文件（全部收集，逗号拼接传给 `rule_fsts`）

---

## 8. 依赖

| 包 | 版本 | 用途 |
|----|------|------|
| sherpa-onnx | 1.13.4 | 高质量离线 TTS |
| sounddevice | 0.5.5 | 音频播放 |
| numpy | 1.26.4 | 数值计算（兼容 dlib 19.24.0） |
| pyttsx3 | 2.99 | 系统 TTS 兜底 |
| huggingface_hub | latest | 模型下载 |

安装命令：

```bash
pip install sherpa-onnx sounddevice numpy pyttsx3 huggingface_hub
```

---

## 9. 踩坑记录

| 问题 | 根因 | 解决 |
|------|------|------|
| 试听只能播一次 | `sd.play/sd.wait` 被中断后永久阻塞 | 改用 `sd.OutputStream` 回调模式 + Event 轮询 |
| 第二次点击朗读无反应 | `_start_worker` 不清 `_stop_flag`，worker 跳过新句子 | 始终清除 `_stop_flag` |
| speaker_id 非 0 无声音 | Sherpa 对越界 sid 静默吞异常 | 越界校验回退到 0 |
| 自动朗读不触发 | 同 stop_flag 问题 | 同上 |
| Sherpa 引擎创建失败 | `OfflineTtsModelConfig(matcha=None, vits=cfg)` 传 None 导致类型错误 | 用 `**{"vits": cfg}` 动态传参 |
| `rule_fsts` 不生效 | 错误地设在 vits config 上 | 移到 `OfflineTtsConfig` 上 |
| 声音偏小 | VITS 输出振幅仅满幅 19% | synthesize 时乘以 2.5x 增益 |
| 欢迎语被淡出打断 | 淡出定时器固定 1.8s，语音可能更长 | 轮询 `is_playing()`，等播完再淡出 |
| 设置页试听冲突 | 每次创建新 TTSManager | 复用同一实例，关闭时 shutdown |
