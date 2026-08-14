import os
import json
import base64
import requests
from typing import Optional, List, Dict, Any, Callable
from io import BytesIO


class LLMClient:
    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config", "settings.json"
            )
        self.config_path = config_path
        self.settings = {}
        self.load_settings()

    def load_settings(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.settings = json.load(f)
            except Exception:
                self.settings = {}
        # 默认值
        self.settings.setdefault("llm_provider", "ollama")
        self.settings.setdefault("llm_configs", {
            "lmstudio": {"base_url": "http://localhost:1234/v1", "api_key": "lmstudio", "model": "local-model"},
            "ollama": {"base_url": "http://localhost:11434/v1", "api_key": "ollama", "model": "qwen2.5:7b"},
            "custom": {"base_url": "https://api.openai.com/v1", "api_key": "", "model": "gpt-4o-mini"},
        })
        self.settings.setdefault("ai_persona", "你是一个贴心的智能助手。")
        self.settings.setdefault("user_name", "用户")
        self.settings.setdefault("ai_name", "小智")
        # TTS 配置默认值
        self.settings.setdefault("tts_engine", "system")  # off / system / sherpa
        self.settings.setdefault("tts_model_dir", "")
        self.settings.setdefault("tts_auto_play", False)
        self.settings.setdefault("tts_speaker_id", 0)
        self.settings.setdefault("tts_volume", 2.5)
        # 声纹配置默认值
        self.settings.setdefault("voiceprint_enabled", False)
        self.settings.setdefault("voiceprint_threshold", 0.4)
        # 情绪引擎配置默认值
        self.settings.setdefault("emotion_enabled", True)
        self.settings.setdefault("personality_openness", 0.5)
        self.settings.setdefault("personality_conscientiousness", 0.5)
        self.settings.setdefault("personality_extraversion", 0.5)
        self.settings.setdefault("personality_agreeableness", 0.5)
        self.settings.setdefault("personality_neuroticism", 0.5)

    def save_settings(self):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.settings, f, ensure_ascii=False, indent=2)

    def get_provider(self) -> str:
        return self.settings.get("llm_provider", "ollama")

    def set_provider(self, name: str):
        self.settings["llm_provider"] = name
        self.save_settings()

    def get_config(self, provider: str = None) -> Dict[str, Any]:
        if provider is None:
            provider = self.get_provider()
        return self.settings.get("llm_configs", {}).get(provider, {})

    def update_config(self, provider: str, config: Dict[str, Any]):
        if "llm_configs" not in self.settings:
            self.settings["llm_configs"] = {}
        self.settings["llm_configs"][provider] = config
        self.save_settings()

    def chat(
        self,
        messages: List[Dict[str, str]],
        stream_callback: Optional[Callable[[str], None]] = None,
        provider: str = None,
        timeout: int = 120,
    ) -> str:
        """
        调用大模型聊天接口。支持流式。
        返回完整回答文本。
        """
        cfg = self.get_config(provider)
        base_url = cfg.get("base_url", "").rstrip("/")
        api_key = cfg.get("api_key", "no-key")
        model = cfg.get("model", "")
        if not base_url or not model:
            raise ValueError("大模型配置不完整，请检查设置")

        url = f"{base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {
            "model": model,
            "messages": messages,
            "stream": stream_callback is not None,
            "temperature": 0.7,
        }

        if stream_callback:
            full = []
            with requests.post(url, headers=headers, json=payload, stream=True, timeout=timeout) as r:
                r.raise_for_status()
                # 使用原始字节流手动解码，彻底避免 requests 编码问题
                buffer = b""
                for chunk in r.iter_content(chunk_size=1):
                    if not chunk:
                        continue
                    buffer += chunk
                    # 按行分割处理
                    while b"\n" in buffer:
                        raw_line, buffer = buffer.split(b"\n", 1)
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        if line.startswith("data: "):
                            line = line[6:]
                        if line == "[DONE]":
                            break
                        try:
                            data = json.loads(line)
                            delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if delta:
                                full.append(delta)
                                stream_callback(delta)
                        except Exception:
                            continue
            return "".join(full)
        else:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            r.raise_for_status()
            # 强制 UTF-8 解码
            r.encoding = 'utf-8'
            data = r.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")

    def vision_chat(
        self,
        prompt: str,
        image_base64: str,
        provider: str = None,
        timeout: int = 60,
    ) -> str:
        """
        调用视觉语言模型（VLM）接口。
        prompt: 文本提示
        image_base64: 图片的 base64 编码（不含前缀）
        返回模型回答文本。
        """
        cfg = self.get_config(provider)
        base_url = cfg.get("base_url", "").rstrip("/")
        api_key = cfg.get("api_key", "no-key")
        model = cfg.get("vision_model") or cfg.get("model", "")
        if not base_url or not model:
            raise ValueError("大模型配置不完整，请检查设置")

        # === 彻底打破 LM Studio KV cache ===
        import random
        import time as time_mod
        import hashlib
        ts = time_mod.time()
        rand_num = random.randint(10000, 99999)
        # 用图片 base64 的哈希作为指纹，让 prompt 随图片变化
        img_hash = hashlib.md5(image_base64[:1000].encode()).hexdigest()[:8]
        cache_buster = f"[会话ID: {ts:.3f}-{rand_num} | 图片指纹: {img_hash}]\n"
        prompt_with_buster = cache_buster + prompt

        url = f"{base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        # 构造多模态消息：system 消息带时间戳防止前缀缓存
        system_msg = {
            "role": "system",
            "content": f"当前时间: {time_mod.strftime('%Y-%m-%d %H:%M:%S', time_mod.localtime(ts))}.{rand_num}"
        }
        user_content = [
            {"type": "text", "text": prompt_with_buster},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{image_base64}"
                },
            },
        ]
        payload = {
            "model": model,
            "messages": [
                system_msg,
                {"role": "user", "content": user_content}
            ],
            "temperature": 0.1,  # 视觉任务用低温
            "seed": rand_num,  # 随机种子，防止 LM Studio 缓存
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            r.raise_for_status()
            r.encoding = 'utf-8'
            data = r.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            return f"VLM 调用失败: {e}"

    def embed(self, texts, provider: str = None, timeout: int = 30) -> List[List[float]]:
        """
        调用 OpenAI 兼容的 /v1/embeddings 接口生成文本向量。
        texts 可以是单条字符串或字符串列表，统一返回 List[List[float]]。
        失败时返回空列表（调用方应做回退处理）。
        """
        if isinstance(texts, str):
            texts = [texts]
        cfg = self.get_config(provider)
        base_url = cfg.get("base_url", "").rstrip("/")
        api_key = cfg.get("api_key", "no-key")
        model = cfg.get("embedding_model") or cfg.get("model", "")
        if not base_url or not model:
            return []
        url = f"{base_url}/embeddings"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {"model": model, "input": texts}
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            r.raise_for_status()
            r.encoding = 'utf-8'
            data = r.json()
            # OpenAI 格式: {"data": [{"embedding": [...], "index": 0}, ...]}
            items = data.get("data", [])
            items.sort(key=lambda x: x.get("index", 0))
            return [it.get("embedding", []) for it in items]
        except Exception:
            return []

    def build_messages(self, user_prompt: str, history: List[Dict[str, str]] = None) -> List[Dict[str, str]]:
        system_msg = self.settings.get("ai_persona", "你是一个贴心的智能助手。")
        messages = [{"role": "system", "content": system_msg}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_prompt})
        return messages

    def simple_chat(self, user_prompt: str, history: List[Dict[str, str]] = None,
                    stream_callback=None) -> str:
        msgs = self.build_messages(user_prompt, history)
        return self.chat(msgs, stream_callback=stream_callback)
