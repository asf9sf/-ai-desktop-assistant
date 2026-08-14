"""音乐平台抽象基类（同步版，使用 requests 替代 aiohttp）"""
import json
import logging
import re
from abc import ABC, abstractmethod
from typing import ClassVar, List, Optional
import requests
from ..model import Song, Platform

logger = logging.getLogger(__name__)


class BaseMusicPlayer(ABC):
    _registry: ClassVar[List[type["BaseMusicPlayer"]]] = []
    platform: ClassVar[Platform]

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; WOW64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://music.163.com/",
    }

    def __init__(self, proxy: str = ""):
        self.proxy = proxy
        self.session = requests.Session()
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if ABC not in cls.__bases__:
            BaseMusicPlayer._registry.append(cls)

    @classmethod
    def get_all_subclass(cls) -> List[type["BaseMusicPlayer"]]:
        return cls._registry

    @classmethod
    def get_player_by_name(cls, name: str) -> Optional["BaseMusicPlayer"]:
        name_lower = name.strip().lower()
        for pcls in cls._registry:
            p = pcls()
            if p.platform.display_name.lower() == name_lower:
                return p
        return None

    @classmethod
    def get_player_by_keyword(cls, text: str) -> Optional["BaseMusicPlayer"]:
        text_lower = text.strip().lower()
        for pcls in cls._registry:
            p = pcls()
            for kw in p.platform.keywords:
                if kw.lower() in text_lower:
                    return p
        return None

    @classmethod
    def get_default_player(cls) -> Optional["BaseMusicPlayer"]:
        if cls._registry:
            return cls._registry[0]()
        return None

    @abstractmethod
    def fetch_songs(self, keyword: str, limit: int = 10) -> List[Song]:
        raise NotImplementedError

    def fetch_song_by_id(self, song_id: str) -> Optional[Song]:
        songs = self.fetch_songs(song_id, limit=1)
        return songs[0] if songs else None

    def _request(self, url: str, method: str = "GET",
                 data: Optional[dict] = None,
                 headers: Optional[dict] = None,
                 timeout: int = 15) -> Optional[dict]:
        try:
            hdrs = headers or self.HEADERS
            if method.upper() == "POST":
                resp = self.session.post(url, data=data, headers=hdrs, timeout=timeout)
            else:
                resp = self.session.get(url, headers=hdrs, timeout=timeout)
            if resp.status_code != 200:
                logger.warning(f"HTTP {resp.status_code}: {url[:80]}")
                return None
            text = resp.text.strip()
            if not text:
                return None
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        except requests.exceptions.Timeout:
            logger.warning(f"请求超时: {url[:80]}")
            return None
        except requests.exceptions.ConnectionError:
            logger.warning(f"连接失败: {url[:80]}")
            return None
        except Exception as e:
            logger.warning(f"请求异常: {e}")
            return None

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass
