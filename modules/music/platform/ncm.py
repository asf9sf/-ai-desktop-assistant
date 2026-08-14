"""网易云音乐搜索（第三方 NodeJS API）"""
import logging
from typing import List, Optional
from .base import BaseMusicPlayer
from ..model import Song, Platform

logger = logging.getLogger(__name__)

DEFAULT_API_BASES = [
    "http://45.152.64.114:3005",
    "http://42.193.244.179:3000",
    "https://163api.qijieya.cn",
    "https://zm.armoe.cn",
    "https://music-api.focalors.ltd",
    "https://wyy.xhily.com",
]


class NCMNodeJSPlayer(BaseMusicPlayer):
    platform = Platform(
        name="ncm_nodejs",
        display_name="网易云",
        keywords=["网易", "网易云", "nj", "ncm"],
    )

    _active_base: Optional[str] = None

    def __init__(self, proxy: str = ""):
        super().__init__(proxy)
        self._ensure_api_base()

    def _ensure_api_base(self):
        if self._active_base:
            return
        for base in DEFAULT_API_BASES:
            try:
                resp = self.session.get(
                    f"{base}/search?keywords=test&limit=1",
                    headers=self.HEADERS, timeout=8,
                )
                if resp.status_code == 200 and "result" in resp.json():
                    self._active_base = base
                    logger.info(f"网易云 API 使用: {base}")
                    return
            except Exception:
                continue
        logger.warning("所有网易云 API 节点均不可用")

    def fetch_songs(self, keyword: str, limit: int = 10) -> List[Song]:
        if not self._active_base:
            self._ensure_api_base()
        if not self._active_base:
            return []

        url = f"{self._active_base}/search"
        params = {"keywords": keyword, "limit": limit}

        for attempt in range(2):
            try:
                resp = self.session.get(
                    url, params=params, headers=self.HEADERS, timeout=15,
                )
                if resp.status_code != 200:
                    if attempt == 0:
                        self._active_base = None
                        self._ensure_api_base()
                        continue
                    return []

                data = resp.json()
                result = data.get("result", {})
                songs_data = result.get("songs", [])
                if not songs_data:
                    return []

                songs = []
                for item in songs_data:
                    song = self._parse_song(item)
                    if song:
                        songs.append(song)
                return songs
            except Exception as e:
                logger.warning(f"网易云搜索异常: {e}")
                if attempt == 0:
                    self._active_base = None
                    self._ensure_api_base()
                    continue
        return []

    def _parse_song(self, item: dict) -> Optional[Song]:
        try:
            song_id = str(item.get("id", ""))
            if not song_id:
                return None

            name = item.get("name", "")

            artists = []
            ar_list = item.get("ar") or item.get("artists") or []
            for ar in ar_list:
                if isinstance(ar, dict):
                    n = ar.get("name", "")
                    if n:
                        artists.append(n)
                elif isinstance(ar, str):
                    artists.append(ar)
            artist_str = "/".join(artists) if artists else ""

            duration = item.get("duration") or item.get("dt") or 0
            if duration and duration < 1000:
                duration = duration * 1000

            pic_url = ""
            al = item.get("al") or item.get("album") or {}
            if al:
                if isinstance(al, dict):
                    pic_url = al.get("picUrl", "") or al.get("pic_url", "") or al.get("pic", "")

            return Song(
                id=song_id,
                name=name,
                artists=artist_str,
                duration=duration,
                title=name,
                author=artist_str,
                cover_url=pic_url,
                source="网易云",
            )
        except Exception:
            return None

    def fetch_song_audio(self, song: Song) -> Optional[Song]:
        if song.audio_url:
            return song
        if not self._active_base:
            self._ensure_api_base()
        if not self._active_base:
            return song

        url = f"{self._active_base}/song/url"
        params = {"id": song.id, "br": 320000}
        try:
            resp = self.session.get(
                url, params=params, headers=self.HEADERS, timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                br_data = data.get("data", [])
                if br_data:
                    song.audio_url = br_data[0].get("url", "")
                    if song.audio_url:
                        song.duration = br_data[0].get("time", 0)
                        logger.info(
                            f"获取音频链接成功: id={song.id}, "
                            f"br={br_data[0].get('br')}, "
                            f"time={br_data[0].get('time')}, "
                            f"url={song.audio_url[:80]}..."
                        )
                    else:
                        logger.warning(f"歌曲 {song.id} 无可用播放URL (可能版权限制)")
                else:
                    logger.warning(f"歌曲 {song.id} 无返回音频数据")
            else:
                logger.warning(f"获取音频链接 HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"获取音频链接失败: {e}")
        return song

    def fetch_lyrics(self, song: Song) -> Optional[str]:
        if song.lyrics:
            return song.lyrics
        if not self._active_base:
            self._ensure_api_base()
        if not self._active_base:
            return None

        url = f"{self._active_base}/lyric"
        params = {"id": song.id}
        try:
            resp = self.session.get(
                url, params=params, headers=self.HEADERS, timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                song.lyrics = data.get("lrc", {}).get("lyric", "")
                return song.lyrics
        except Exception as e:
            logger.warning(f"获取歌词失败: {e}")
        return None
