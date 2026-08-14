# 🤖 Smart Assistant - 本地智能助手

![License: Non-Commercial](https://img.shields.io/badge/License-Non--Commercial-orange.svg)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
![PyQt6](https://img.shields.io/badge/PyQt6-6.5+-green.svg)

> ⚠️ **本项目采用非商业开源许可，严禁任何商业用途**

一个功能完整的桌面智能助手，集成了人脸识别、LLM对话、情感TTS、声纹验证、桌面自动化等多种能力，支持离线运行。

---

## ✨ 功能特性

### 🔐 身份验证
- **人脸识别登录**：基于 face_recognition 库的人脸验证
- **声纹验证**：基于说话人嵌入的声纹识别

### 💬 智能对话
- **LLM 驱动**：支持 Ollama、LM Studio、自定义 API 等多种 LLM 提供商
- **多轮对话**：支持上下文理解和长期记忆
- **意图识别**：快速正则匹配 + ReAct 多步推理

### 🎙️ 语音系统
- **双 TTS 引擎**：
  - CosyVoice：支持情感控制的高质量语音合成
  - sherpa-onnx：高性能离线 TTS
- **离线 ASR**：基于 SenseVoice 的中文语音识别
- **情感引擎**：根据对话内容自动调整情感表达

### 🎮 桌面自动化
- **软件控制**：打开/关闭应用、窗口管理
- **浏览器自动化**：基于 Playwright 的网页操作
- **视觉自动化**：屏幕识别和操作

### 🎵 娱乐功能
- **音乐播放**：网易云音乐集成，支持搜索、播放、歌词
- **中国象棋**：内置 AI 对手，5级难度可调节

### 📚 学术能力
- **arXiv 搜索**：快速检索学术论文

### 🧠 长期记忆
- 对话历史存储
- 用户偏好学习

---

## 🛠️ 技术栈

| 类别 | 技术 | 说明 |
|------|------|------|
| GUI | PyQt6 | 现代化桌面界面 |
| LLM | Ollama/LM Studio/API | 多提供商支持 |
| TTS | CosyVoice + sherpa-onnx | 情感语音合成 |
| ASR | sherpa-onnx + SenseVoice | 离线语音识别 |
| 人脸识别 | face_recognition + dlib | 安全登录 |
| 浏览器 | Playwright | 网页自动化 |
| 数据库 | SQLite | 长期记忆存储 |

---

## 📦 安装指南

### 环境要求
- Python 3.10+
- Windows 10/11 (64-bit)
- 建议 8GB+ 内存
- GPU 可选（用于加速 LLM 推理）

### 1. 克隆仓库

```bash
git clone https://github.com/asf9sf/-ai-desktop-assistant.git
cd -ai-desktop-assistant
```

### 2. 创建虚拟环境

```bash
# 使用 conda (推荐)
conda create -n pythonproject2 python=3.10
conda activate pythonproject2

# 或使用 venv
python -m venv venv
venv\Scripts\activate  # Windows
```

### 3. 安装依赖

```bash
# 基础依赖
pip install PyQt6 face-recognition dlib opencv-python numpy requests pypinyin

# TTS 依赖
pip install modelscope transformers torchaudio sounddevice pyttsx3

# 浏览器自动化
pip install playwright
playwright install chromium

# 可选：学术搜索
pip install arxiv
```

---

## 🎯 模型下载说明

本项目需要以下模型文件，请按照说明下载并放置到对应目录。

### 📍 目录结构

```
data/
├── tts_models/          # TTS 模型
├── model/
│   ├── ASR/             # 语音识别模型
│   └── SpeakerID/       # 说话人识别模型
CosyVoice/
└── pretrained_models/   # CosyVoice 预训练模型
```

---

### 1️⃣ sherpa-onnx TTS 模型（必选，轻量）

**下载地址**：[https://github.com/k2-fsa/sherpa-onnx/releases](https://github.com/k2-fsa/sherpa-onnx/releases)

**所需模型**：`vits-zh-ll`（约 200MB）

```bash
# Windows PowerShell 下载示例
$url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-zh-ll.tar.bz2"
$output = "data/tts_models/vits-zh-ll.tar.bz2"
Invoke-WebRequest -Uri $url -OutFile $output

# 解压到 data/tts_models/vits-zh-ll/
tar -xjf $output -C data/tts_models/
```

**或手动下载**：
1. 访问 [sherpa-onnx releases](https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models)
2. 下载 `vits-zh-ll.tar.bz2`
3. 解压到 `data/tts_models/vits-zh-ll/`

---

### 2️⃣ CosyVoice 情感 TTS 模型（可选，高质量）

**下载地址**：[https://www.modelscope.cn/](https://www.modelscope.cn/) 或 [https://huggingface.co/](https://huggingface.co/)

**所需模型**：`CosyVoice-300M-Instruct`（约 1.5GB）

```bash
# 方式一：从 ModelScope 下载
pip install modelscope
python -c "
from modelscope import snapshot_download
snapshot_download('iic/CosyVoice-300M-Instruct', 
                 cache_dir='CosyVoice/pretrained_models')
"

# 方式二：从 HuggingFace 下载
pip install huggingface_hub
python -c "
from huggingface_hub import snapshot_download
snapshot_download('FunAudio-NLP/CosyVoice-300M-Instruct',
                 cache_dir='CosyVoice/pretrained_models')
"

# 方式三：手动下载
# 1. 克隆 CosyVoice 仓库
git clone https://github.com/FunAudio-NLP/CosyVoice.git .

# 2. 下载 Matcha-TTS 依赖
git clone https://github.com/shivammehta25/Matcha-TTS.git third_party/Matcha-TTS

# 3. 下载预训练模型到 CosyVoice/pretrained_models/CosyVoice-300M-Instruct/
#    从 ModelScope 或 HuggingFace 下载以下文件：
#    - llm.pt
#    - flow.pt  
#    - speech_tokenizer_v1.onnx
#    - flow.encoder/decoder 文件
#    - llm.text_encoder 文件
```

**目录结构**：
```
CosyVoice/pretrained_models/CosyVoice-300M-Instruct/
├── llm.pt
├── flow.pt
├── speech_tokenizer_v1.onnx
├── flow.encoder.fp16.zip
├── flow.encoder.fp32.zip
├── flow.decoder.estimator.fp32.onnx
├── llm.text_encoder.fp16.zip
├── llm.text_encoder.fp32.zip
├── llm.llm.fp16.zip
└── llm.llm.fp32.zip
```

---

### 3️⃣ ASR 语音识别模型（必选）

**下载地址**：[https://github.com/k2-fsa/sherpa-onnx/releases](https://github.com/k2-fsa/sherpa-onnx/releases)

**所需模型**：`sherpa-onnx-sense-voice-zh-en-ja-ko-yue`（约 100MB）

```bash
# Windows PowerShell 下载示例
$url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue.tar.bz2"
$output = "data/model/sherpa-onnx-sense-voice-zh-en-ja-ko-yue.tar.bz2"
Invoke-WebRequest -Uri $url -OutFile $output

# 解压到 data/model/ASR/
mkdir -p data/model/ASR
tar -xjf $output -C data/model/ASR/
```

**或手动下载**：
1. 访问 [sherpa-onnx ASR releases](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models)
2. 下载 `sherpa-onnx-sense-voice-zh-en-ja-ko-yue.tar.bz2`
3. 解压到 `data/model/ASR/`

---

### 4️⃣ 说话人识别模型（可选，声纹验证需要）

**下载地址**：[https://github.com/k2-fsa/sherpa-onnx/releases](https://github.com/k2-fsa/sherpa-onnx/releases)

**所需模型**：`3d-speechbrain-zh-cn`（约 10MB）

```bash
# Windows PowerShell 下载示例
$url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-id-models/3d-speechbrain-zh-cn.tar.bz2"
$output = "data/model/3d-speechbrain-zh-cn.tar.bz2"
Invoke-WebRequest -Uri $url -OutFile $output

# 解压到 data/model/SpeakerID/
mkdir -p data/model/SpeakerID
tar -xjf $output -C data/model/SpeakerID/
```

**或手动下载**：
1. 访问 [sherpa-onnx SpeakerID releases](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-id-models)
2. 下载 `3d-speechbrain-zh-cn.tar.bz2`
3. 解压到 `data/model/SpeakerID/`

---

### 📋 模型下载汇总

| 模型 | 大小 | 必选 | 下载地址 | 存储路径 |
|------|------|------|----------|----------|
| sherpa-onnx TTS | ~200MB | ✅ | [GitHub](https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models) | `data/tts_models/` |
| CosyVoice 模型 | ~1.5GB | ❌ | [ModelScope](https://www.modelscope.cn/) | `CosyVoice/pretrained_models/` |
| SenseVoice ASR | ~100MB | ✅ | [GitHub](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models) | `data/model/ASR/` |
| 声纹识别 | ~10MB | ❌ | [GitHub](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-id-models) | `data/model/SpeakerID/` |

---

### 🔧 快速安装脚本

创建 `download_models.py` 一键下载所有必选模型：

```python
import urllib.request
import tarfile
import os

def download_file(url, output_path):
    print(f"下载: {url}")
    urllib.request.urlretrieve(url, output_path)
    print(f"完成: {output_path}")

def extract_tar_bz2(file_path, extract_path):
    print(f"解压: {file_path}")
    with tarfile.open(file_path, 'r:bz2') as tar:
        tar.extractall(extract_path)
    os.remove(file_path)
    print(f"完成: {extract_path}")

# 创建目录
os.makedirs("data/tts_models", exist_ok=True)
os.makedirs("data/model/ASR", exist_ok=True)
os.makedirs("data/model/SpeakerID", exist_ok=True)

# 下载 TTS 模型
download_file(
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-zh-ll.tar.bz2",
    "data/tts_models/vits-zh-ll.tar.bz2"
)
extract_tar_bz2("data/tts_models/vits-zh-ll.tar.bz2", "data/tts_models/")

# 下载 ASR 模型
download_file(
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue.tar.bz2",
    "data/model/sense-voice.tar.bz2"
)
extract_tar_bz2("data/model/sense-voice.tar.bz2", "data/model/ASR/")

print("✅ 必选模型下载完成！")
print("可选模型请参考 README.md 手动下载")
```

---

## ⚙️ 配置

```bash
# 复制配置模板
copy config\settings.example.json config\settings.json

# 编辑 settings.json，设置：
# - user_name: 你的名字
# - llm_configs: LLM API 配置
# - tts_model_dir: TTS 模型路径
```

### 推荐 LLM 配置

使用 Ollama（本地运行）：
```bash
# 安装 Ollama: https://ollama.com/
ollama pull qwen2.5:7b
```

使用 LM Studio：
1. 下载 [LM Studio](https://lmstudio.ai/)
2. 加载模型并启动本地服务器

---

## 🚀 使用方法

### 启动程序

```bash
python main.py
```

### 基本操作

1. **首次启动**：系统会引导你完成人脸和声纹注册
2. **对话交互**：
   - 直接输入文本与 AI 对话
   - 或使用语音输入（点击麦克风按钮）
3. **快捷指令**：
   - `点歌晴天` - 播放音乐
   - `打开计算器` - 软件控制
   - `搜索论文 LLM` - 学术搜索
   - `下一首` - 音乐控制

### 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+Shift+S` | 打开设置 |
| `Ctrl+Shift+C` | 中国象棋 |
| `Ctrl+Shift+M` | 音乐模式 |
| `Esc` | 关闭当前窗口 |

---

## 📁 项目结构

```
-ai-desktop-assistant/
├── main.py                     # 主入口
├── requirements.txt            # 依赖列表
├── config/
│   ├── settings.example.json   # 配置模板
│   └── settings.json           # 用户配置（自行创建）
├── modules/
│   ├── agent_core.py           # 智能体核心（意图识别、工具调度）
│   ├── tts_module.py           # TTS 引擎封装
│   ├── speech_module.py        # 语音识别
│   ├── emotion_module.py       # 情感引擎
│   ├── face_recognition_module.py  # 人脸识别
│   ├── voiceprint_module.py    # 声纹验证
│   ├── memory_system.py        # 长期记忆
│   ├── browser_automation.py   # 浏览器自动化
│   ├── desktop_automation.py   # 桌面自动化
│   ├── app_controller.py       # 软件控制
│   ├── arxiv_searcher.py       # arXiv 搜索
│   ├── music/                  # 音乐模块
│   └── game/                   # 象棋游戏
├── ui/
│   ├── main_window.py          # 主窗口
│   ├── welcome_window.py       # 欢迎窗口
│   ├── settings_dialog.py      # 设置对话框
│   └── xiangqi_game_dialog.py  # 象棋游戏
├── data/
│   ├── tts_models/             # TTS 模型（需下载）
│   ├── model/                  # ASR/SpeakerID 模型（需下载）
│   └── cache/                  # 缓存文件
└── docs/                       # 文档
```

---

## 🎯 指令速查

### 音乐指令
```
点歌<歌名>          # 播放指定歌曲
搜索音乐<关键词>    # 搜索音乐
查看歌单            # 查看当前歌单
暂停/继续/下一首    # 播放控制
<歌名>的歌词        # 查询歌词
```

### 软件控制
```
打开<软件名>        # 启动应用
关闭<软件名>        # 关闭应用
最小化/最大化       # 窗口控制
```

### 学术搜索
```
搜索论文<关键词>    # arXiv 搜索
<论文ID>的详情      # 论文详情
```

---

## ❓ 常见问题

### Q: 启动时提示找不到模型？
A: 请确保已下载对应模型并放置在正确目录下。参考 [模型下载说明](#-模型下载说明)。

### Q: 人脸识别失败？
A: 首次使用需在设置中完成人脸注册，并确保光线充足、正面面对摄像头。

### Q: TTS 没有声音？
A: 检查系统音频输出设备，确认未被静音。尝试切换 TTS 引擎（CosyVoice/sherpa/system）。

### Q: LLM 连接失败？
A: 确认 LLM 服务已启动（如 Ollama、LM Studio），检查 settings.json 中的 API 地址。

### Q: 如何只使用轻量模式？
A: 只需要下载 sherpa-onnx TTS 和 SenseVoice ASR 模型，跳过 CosyVoice 模型即可。

---

## 🤝 贡献指南

欢迎贡献代码！请遵循以下步骤：

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'feat: add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

---

## 📄 许可证

本项目基于**非商业开源许可**发布，**严禁任何商业用途**。详见 [LICENSE](LICENSE) 文件。

- ✅ 允许个人学习、研究、教学使用
- ✅ 允许在非营利组织或教育机构中使用
- ❌ 禁止用于任何商业产品或服务
- ❌ 禁止用于盈利性 AI 服务或 SaaS 产品

---

## 🙏 致谢

感谢以下开源项目：
- [CosyVoice](https://github.com/FunAudio-NLP/CosyVoice) - 情感 TTS 引擎
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) - 高性能推理框架
- [Matcha-TTS](https://github.com/shivammehta25/Matcha-TTS) - TTS 模型
- [EmotiVoice](https://github.com/stepfun-ai/EmotiVoice) - 情感语音合成
- [PyQt6](https://www.riverbankcomputing.com/) - GUI 框架
- 以及所有本项目使用的开源库

---

⭐️ 如果这个项目对你有帮助，请给个 Star ！

📧 如有问题，欢迎提交 Issue 或联系维护者。
