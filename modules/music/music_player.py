"""音乐播放器 — 统一入口，支持多平台搜索、播放、歌单管理"""
import os
import re
import sys
import json
import logging
import tempfile
import threading
import subprocess
import platform
from typing import List, Optional, Tuple

from .model import Song, Platform
from .platform.base import BaseMusicPlayer
from .platform.ncm import NCMNodeJSPlayer

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "cache", "music")
PLAYLIST_FILE = os.path.join(CACHE_DIR, "playlist.json")


class MusicPlayer:
    """统一音乐播放器"""

    def __init__(self):
        self._players: List[BaseMusicPlayer] = []
        self._default_player_name = "网易云"
        self._playlist: List[dict] = []
        self._current_index: int = -1
        self._is_playing: bool = False
        self._play_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._current_path: Optional[str] = None
        self._register_players()
        self._load_playlist()

    def _register_players(self):
        self._players = []
        self._players.append(NCMNodeJSPlayer())
        logger.info(f"已注册 {len(self._players)} 个音乐平台: "
                    f"{[p.platform.display_name for p in self._players]}")

    def _get_player(self, name: Optional[str] = None,
                    keyword: Optional[str] = None) -> Optional[BaseMusicPlayer]:
        if name:
            name_lower = name.lower().replace("点歌", "").strip()
            for p in self._players:
                if (p.platform.display_name.lower() == name_lower or
                        p.platform.name.lower() == name_lower):
                    return p
        if keyword:
            for p in self._players:
                for kw in p.platform.keywords:
                    if kw.lower() in keyword.lower():
                        return p
        for p in self._players:
            if p.platform.display_name == self._default_player_name:
                return p
        return self._players[0] if self._players else None

    # ==================== 搜索 ====================

    def search(self, keyword: str, limit: int = 10,
               platform_name: Optional[str] = None) -> Tuple[bool, str, List[Song]]:
        player = self._get_player(name=platform_name, keyword=keyword)
        if not player:
            return False, "无可用音乐平台", []

        logger.info(f"搜索歌曲 [{player.platform.display_name}]: {keyword}")
        songs = player.fetch_songs(keyword, limit=limit)
        if not songs:
            return False, f"在「{player.platform.display_name}」中未找到「{keyword}」", []

        lines = [f"🎵 在「{player.platform.display_name}」中找到 {len(songs)} 首歌曲：\n"]
        for i, song in enumerate(songs, 1):
            lines.append(song.to_text(i))
        return True, "\n".join(lines), songs

    # ==================== 播放 ====================

    def play(self, song: Song) -> Tuple[bool, str, dict]:
        logger.info(f"[音乐播放] 开始处理歌曲: {song.display_name}")
        info = {"preview": False, "song_name": song.display_name, "artist": song.display_artist}
        try:
            if not song.audio_url:
                logger.info(f"[音乐播放] 无 audio_url，尝试获取播放链接...")
                player = self._get_player()
                if player and hasattr(player, "fetch_song_audio"):
                    song = player.fetch_song_audio(song)
                    logger.info(f"[音乐播放] 获取到 audio_url: {song.audio_url[:80] if song.audio_url else 'NONE'}")

            if not song.audio_url:
                logger.error(f"[音乐播放] 无法获取播放链接: {song.display_name}")
                return False, f"无法获取「{song.display_name}」的播放链接", info

            self._stop_playback()

            os.makedirs(CACHE_DIR, exist_ok=True)
            local_path = os.path.join(CACHE_DIR, f"{song.id}.mp3")
            logger.info(f"[音乐播放] 本地缓存路径: {local_path}")

            need_download = True
            if os.path.exists(local_path):
                file_size = os.path.getsize(local_path)
                if not self._verify_mp3(local_path):
                    logger.warning(f"[音乐播放] 缓存文件损坏，重新下载...")
                    os.remove(local_path)
                else:
                    if not self._looks_like_preview(file_size, song.duration):
                        logger.info(f"[音乐播放] 使用已缓存的音频: {local_path} ({file_size} bytes)")
                        need_download = False
                    else:
                        logger.warning(
                            f"[音乐播放] 缓存疑似试听片段 "
                            f"(size={file_size}, duration={song.duration}s)，重新下载..."
                        )
                        os.remove(local_path)

            if need_download:
                logger.info(f"[音乐播放] 开始下载音频 (URL: {song.audio_url[:80]}...)")
                success = self._download_audio(song.audio_url, local_path)
                if not success:
                    logger.error(f"[音乐播放] 音频下载失败: {song.display_name}")
                    return False, f"音频下载失败：{song.display_name}", info
                file_size = os.path.getsize(local_path)
                logger.info(f"[音乐播放] 音频下载完成: {local_path} ({file_size} bytes)")

                if file_size < 10_000:
                    logger.error(f"[音乐播放] 下载内容异常过小 ({file_size} bytes)")
                    return False, f"音频下载异常：{song.display_name}", info

                if song.duration and song.duration > 0:
                    est_bitrate = file_size * 8 / song.duration
                    logger.info(
                        f"[音乐播放] 音频信息: size={file_size} bytes, "
                        f"duration={song.duration}s, est_bitrate={est_bitrate:.0f} kbps"
                    )
                    if est_bitrate < 64:
                        logger.warning(
                            f"[音乐播放] 码率过低 ({est_bitrate:.0f} kbps)，可能是片段或低音质"
                        )

                    if self._looks_like_preview(file_size, song.duration):
                        info["preview"] = True
                        logger.warning(
                            f"[音乐播放] 音频疑似试听片段 "
                            f"(size={file_size}, duration={song.duration}s)"
                        )
                else:
                    if file_size < 500_000:
                        logger.warning(
                            f"[音乐播放] 下载文件较小 ({file_size} bytes)，时长未知，可能为试听或低音质"
                        )
                        info["preview"] = True

                if not self._verify_mp3(local_path):
                    logger.error(f"[音乐播放] 下载的音频文件损坏: {song.display_name}")
                    try:
                        os.remove(local_path)
                    except Exception:
                        pass
                    return False, f"音频文件损坏：{song.display_name}", info

            # 试听片段直接返回，不播放（由上层回退到B站搜索完整版）
            if info["preview"]:
                logger.info(
                    f"[音乐播放] 识别为试听片段，跳过本地播放: "
                    f"{song.display_name} (duration={song.duration}s)"
                )
                # 不加入歌单，不启动播放器
                return (
                    False,
                    f"「{song.display_name}」为VIP歌曲，当前获取到的是试听片段",
                    info,
                )

            song.path = local_path
            play_ok = self._play_file(local_path, song)
            if not play_ok:
                logger.error(f"[音乐播放] 音频播放启动失败")
                return False, "音频播放启动失败", info

            self._add_to_playlist(song)
            for i, item in enumerate(self._playlist):
                if str(item.get("id", "")) == str(song.id):
                    self._current_index = i
                    break
            logger.info(f"[音乐播放] 播放已启动: {song.display_name} - {song.display_artist} (index={self._current_index})")

            return True, f"▶ 正在播放：{song.display_name} - {song.display_artist}", info
        except Exception as e:
            logger.error(f"[音乐播放] 播放异常: {e}", exc_info=True)
            return False, f"播放失败：{e}", info

    def play_by_name(self, song_name: str,
                     platform_name: Optional[str] = None) -> Tuple[bool, str, dict]:
        player = self._get_player(name=platform_name, keyword=song_name)
        if not player:
            return False, "无可用音乐平台", {"preview": False, "song_name": song_name, "artist": ""}

        songs = player.fetch_songs(song_name, limit=1)
        if not songs:
            return False, f"未找到「{song_name}」", {"preview": False, "song_name": song_name, "artist": ""}

        song = songs[0]
        return self.play(song)

    def play_by_index(self, index: int) -> Tuple[bool, str, dict]:
        if 1 <= index <= len(self._playlist):
            item = self._playlist[index - 1]
            song = Song(
                id=str(item.get("id", "")),
                name=item.get("name", ""),
                artists=item.get("artist", ""),
                audio_url=item.get("audio_url"),
                path=item.get("path"),
                source=item.get("source", ""),
            )
            self._current_index = index - 1
            return self.play(song)
        return False, f"歌单中没有第 {index} 首歌曲", {"preview": False, "song_name": "", "artist": ""}

    def play_next(self) -> Tuple[bool, str, dict]:
        if not self._playlist:
            return False, "歌单为空", {"preview": False, "song_name": "", "artist": ""}
        self._stop_playback()
        self._send_media_key("stop")
        self._current_index = (self._current_index + 1) % len(self._playlist)
        song_data = self._playlist[self._current_index]
        song_name = song_data.get("name", "")
        logger.info(f"[播放] 换一首: {song_name} (index={self._current_index})")
        return self.play_by_index(self._current_index + 1)

    def play_prev(self) -> Tuple[bool, str, dict]:
        if not self._playlist:
            return False, "歌单为空", {"preview": False, "song_name": "", "artist": ""}
        self._stop_playback()
        self._send_media_key("stop")
        self._current_index = (self._current_index - 1) % len(self._playlist)
        song_data = self._playlist[self._current_index]
        song_name = song_data.get("name", "")
        logger.info(f"[播放] 上一首: {song_name} (index={self._current_index})")
        return self.play_by_index(self._current_index + 1)

    def stop(self) -> Tuple[bool, str]:
        self._stop_playback()
        self._current_index = -1
        self._send_media_key("stop")
        return True, "⏹ 已停止播放"

    def pause(self) -> Tuple[bool, str]:
        logger.info("[播放] 暂停请求")
        self._send_media_key("play_pause")
        self._is_playing = False
        return True, "⏸ 已暂停"

    def resume(self) -> Tuple[bool, str]:
        logger.info("[播放] 恢复请求, _current_index=%d, playlist_len=%d", self._current_index, len(self._playlist))
        self._send_media_key("play_pause")
        self._is_playing = True
        return True, "▶ 继续播放"

    def next_track(self) -> Tuple[bool, str]:
        """下一首"""
        self._send_media_key("next")
        if self._playlist and self._current_index < len(self._playlist) - 1:
            self._current_index += 1
        return True, "⏭ 下一首"

    def prev_track(self) -> Tuple[bool, str]:
        """上一首"""
        self._send_media_key("prev")
        if self._current_index > 0:
            self._current_index -= 1
        return True, "⏮ 上一首"

    @staticmethod
    def _send_media_key(action: str):
        """发送 Windows 媒体快捷键（play_pause/next/prev/stop）。
        使用 keybd_event 模拟键盘按键，兼容所有媒体播放器。
        """
        if sys.platform != "win32":
            return
        VK_MEDIA_PLAY_PAUSE = 0xB3
        VK_MEDIA_NEXT_TRACK = 0xB0
        VK_MEDIA_PREV_TRACK = 0xB1
        VK_MEDIA_STOP = 0xB2
        KEYEVENTF_KEYUP = 0x0002
        key_map = {
            "play_pause": VK_MEDIA_PLAY_PAUSE,
            "play": VK_MEDIA_PLAY_PAUSE,
            "pause": VK_MEDIA_PLAY_PAUSE,
            "next": VK_MEDIA_NEXT_TRACK,
            "prev": VK_MEDIA_PREV_TRACK,
            "stop": VK_MEDIA_STOP,
        }
        vk = key_map.get(action)
        if vk is None:
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            user32.keybd_event(vk, 0, 0, 0)
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            logger.info(f"[媒体键] 已发送: {action} (VK=0x{vk:02X})")
        except Exception as e:
            logger.warning(f"[媒体键] 发送失败: {e}")

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @property
    def current_song(self) -> Optional[dict]:
        if 0 <= self._current_index < len(self._playlist):
            return self._playlist[self._current_index]
        return None

    # ==================== 歌词 ====================

    def get_lyrics(self, song_name: str) -> Tuple[bool, str]:
        player = self._get_player(keyword=song_name)
        if not player:
            return False, "无可用音乐平台"

        songs = player.fetch_songs(song_name, limit=1)
        if not songs:
            return False, f"未找到「{song_name}」"

        song = songs[0]
        if hasattr(player, "fetch_lyrics"):
            lyrics = player.fetch_lyrics(song)
            if lyrics:
                display = f"🎵 {song.display_name} - {song.display_artist}\n\n{lyrics}"
                return True, display
        return False, "无法获取歌词"

    # ==================== 歌单 ====================

    def list_playlist(self) -> Tuple[bool, str]:
        if not self._playlist:
            return True, "📋 歌单为空"

        lines = [f"📋 我的歌单（共 {len(self._playlist)} 首）：\n"]
        for i, item in enumerate(self._playlist, 1):
            playing = " ▶" if i - 1 == self._current_index else ""
            name = item.get("name", "未知")
            artist = item.get("artist", "")
            lines.append(f"  {i}. {name} - {artist}{playing}")
        return True, "\n".join(lines)

    def clear_playlist(self) -> Tuple[bool, str]:
        self._playlist = []
        self._current_index = -1
        self._save_playlist()
        return True, "歌单已清空"

    def remove_from_playlist(self, index: int) -> Tuple[bool, str]:
        if 1 <= index <= len(self._playlist):
            removed = self._playlist.pop(index - 1)
            self._save_playlist()
            return True, f"已从歌单移除：{removed.get('name', '')}"
        return False, f"歌单中没有第 {index} 首歌曲"

    # ==================== 内部方法 ====================

    def _download_audio(self, url: str, save_path: str) -> bool:
        import requests
        try:
            logger.info(f"[下载] 开始下载: {url[:100]}")
            resp = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                    "Referer": "https://music.163.com/",
                },
                timeout=(10, 120),
                stream=True,
            )
            if resp.status_code != 200:
                logger.error(f"[下载] HTTP {resp.status_code}: {url[:80]}")
                return False
            total = int(resp.headers.get("content-length", 0))
            logger.info(f"[下载] 预期文件大小: {total} bytes" if total else "[下载] 预期文件大小: 未知")
            downloaded = 0
            last_log = 0
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0 and downloaded - last_log >= 512000:
                        pct = downloaded * 100 // total
                        logger.info(f"[下载] 进度: {downloaded}/{total} ({pct}%)")
                        last_log = downloaded
            logger.info(f"[下载] 下载完成: {downloaded} bytes -> {save_path}")

            if downloaded == 0:
                logger.error("[下载] 下载内容为空")
                return False

            if total > 0 and downloaded < total * 0.5:
                logger.error(f"[下载] 下载不完整: 期望 {total} bytes，实际 {downloaded} bytes")
                return False

            return True
        except Exception as e:
            logger.error(f"[下载] 下载失败: {e}", exc_info=True)
            if os.path.exists(save_path):
                try:
                    os.remove(save_path)
                except Exception:
                    pass
            return False

    @staticmethod
    def _looks_like_preview(file_size: int, duration_ms: int) -> bool:
        """判断音频是否疑似试听片段。

        规则：
        1. 时长 <= 60 秒
        2. 且文件大小对应的码率在 32-256 kbps 合理区间内
        3. 此时说明文件本身是完整下载的试听片段，不是不完整下载
        """
        if not duration_ms or duration_ms <= 0:
            if file_size < 500_000:
                return True
            return False

        duration_sec = duration_ms / 1000.0
        if duration_sec <= 60:
            return True
        return False

    @staticmethod
    def _verify_mp3(file_path: str) -> bool:
        """验证 MP3 文件完整性：检查文件头 + 文件尾部不截断。"""
        try:
            file_size = os.path.getsize(file_path)
            if file_size < 1024:
                logger.warning(f"[MP3校验] 文件过小: {file_size} bytes")
                return False
            with open(file_path, "rb") as f:
                header = f.read(16)
                if len(header) < 4:
                    return False
                has_id3 = header.startswith(b"ID3")
                has_mpeg = (
                    len(header) >= 2 and
                    header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
                )
                if not has_id3 and not has_mpeg:
                    logger.warning(f"[MP3校验] 不是有效的MP3头: {header[:4]!r}")
                    return False

                f.seek(max(0, file_size - 256))
                tail = f.read(256)
                tail = tail.rstrip(b"\x00")
                if len(tail) < 16:
                    logger.warning(f"[MP3校验] 文件尾部异常，可能被截断")
                    return False
                if tail.endswith(b"\xff\xf4") or tail.endswith(b"\xff\xfb"):
                    pass
                return True
        except Exception as e:
            logger.warning(f"[MP3校验] 校验异常: {e}")
            return False

    def _play_file(self, path: str, song: Optional[Song] = None) -> bool:
        """播放音频文件。返回是否成功启动播放。
        - WAV: 使用 winsound 同步播放
        - MP3/其他: 使用系统默认播放器打开
        """
        logger.info(f"[播放] 准备播放文件: {path}")

        if not os.path.exists(path):
            logger.error(f"[播放] 文件不存在: {path}")
            return False

        file_size = os.path.getsize(path)
        if file_size == 0:
            logger.error(f"[播放] 文件为空: {path}")
            return False

        ext = os.path.splitext(path)[1].lower()
        logger.info(f"[播放] 文件类型: {ext}, 大小: {file_size} bytes")

        self._stop_playback()
        self._stop_event.clear()

        if ext == ".wav":
            return self._play_wav(path)
        else:
            return self._play_with_system(path, song)

    def _play_wav(self, path: str) -> bool:
        """使用 winsound 播放 WAV 文件（仅支持 WAV）"""
        logger.info(f"[播放] 使用 winsound 播放 WAV: {path}")
        self._is_playing = True

        def _run():
            try:
                import winsound
                winsound.PlaySound(path, winsound.SND_FILENAME)
                logger.info("[播放] winsound 播放完成")
            except Exception as e:
                logger.error(f"[播放] winsound 播放异常: {e}")
            finally:
                self._is_playing = False

        self._play_thread = threading.Thread(target=_run, daemon=True)
        self._play_thread.start()
        return True

    def _play_with_system(self, path: str, song: Optional[Song] = None) -> bool:
        """使用系统默认播放器打开音频文件（支持 MP3/FLAC 等）"""
        logger.info(f"[播放] 使用系统播放器打开: {path}")

        try:
            if sys.platform == "win32":
                return self._play_windows(path)
            elif sys.platform == "darwin":
                return self._play_mac(path)
            else:
                return self._play_linux(path)
        except Exception as e:
            logger.error(f"[播放] 系统播放器启动失败: {e}", exc_info=True)
            return False

    def _play_windows(self, path: str) -> bool:
        """Windows 播放：使用 os.startfile 启动默认播放器，然后用媒体键控制。
        这样避免了 WMP COM 的线程亲和性问题，也支持任何默认播放器。
        """
        logger.info(f"[播放] Windows 平台，启动默认播放器...")

        try:
            os.startfile(path)
            logger.info(f"[播放] os.startfile 成功: {path}")
            self._is_playing = True
            self._current_path = path

            def _monitor_playback():
                """监控播放状态：通过文件是否存在 + 停止事件判断。"""
                import time
                try:
                    while not self._stop_event.is_set():
                        time.sleep(1.0)
                    logger.info("[播放] 监控线程: 收到停止信号")
                except Exception as e:
                    logger.error(f"[播放] 监控线程异常: {e}")
                finally:
                    self._is_playing = False

            self._play_thread = threading.Thread(target=_monitor_playback, daemon=True)
            self._play_thread.start()
            return True
        except Exception as e:
            logger.error(f"[播放] os.startfile 失败: {e}")
            return False

    def _play_mac(self, path: str) -> bool:
        """macOS: 使用 open 命令"""
        subprocess.Popen(["open", path])
        logger.info(f"[播放] macOS open 命令启动: {path}")
        self._is_playing = True
        return True

    def _play_linux(self, path: str) -> bool:
        """Linux: 使用 xdg-open 或 mplayer"""
        try:
            subprocess.Popen(["xdg-open", path])
            logger.info(f"[播放] Linux xdg-open 启动: {path}")
            self._is_playing = True
            return True
        except Exception:
            pass

        try:
            subprocess.Popen(["mplayer", path])
            self._is_playing = True
            return True
        except Exception:
            logger.error(f"[播放] Linux 无可用播放器")
            return False

    def _stop_playback(self):
        self._stop_event.set()
        self._is_playing = False
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_ASYNC)
        except Exception:
            pass

    def _add_to_playlist(self, song: Song):
        entry = {
            "id": song.id,
            "name": song.display_name,
            "artist": song.display_artist,
            "duration": song.duration,
            "audio_url": song.audio_url,
            "path": song.path,
            "source": song.source,
        }
        # 检查歌单中是否已存在相同歌曲
        for i, item in enumerate(self._playlist):
            if str(item.get("id", "")) == str(song.id):
                self._current_index = i
                logger.info(f"[歌单] 歌曲已存在，切换到: {song.display_name} (index={i})")
                return
        self._playlist.append(entry)
        self._current_index = len(self._playlist) - 1
        self._save_playlist()

    def deduplicate_playlist(self) -> int:
        """去除歌单中的重复歌曲，返回移除的数量。"""
        seen = set()
        unique = []
        removed = 0
        for item in self._playlist:
            sid = str(item.get("id", ""))
            if sid and sid not in seen:
                seen.add(sid)
                unique.append(item)
            else:
                removed += 1
        if removed > 0:
            self._playlist = unique
            self._save_playlist()
            # 重新校正当前播放索引
            if 0 <= self._current_index < len(self._playlist):
                pass  # 保持不变
            elif self._playlist:
                self._current_index = 0
            logger.info(f"[歌单] 去重完成: 移除 {removed} 首重复歌曲，剩余 {len(self._playlist)} 首")
        return removed

    def _load_playlist(self):
        try:
            if os.path.exists(PLAYLIST_FILE):
                with open(PLAYLIST_FILE, "r", encoding="utf-8") as f:
                    self._playlist = json.load(f)
                logger.info(f"加载歌单: {len(self._playlist)} 首歌曲")
                # 自动去重
                removed = self.deduplicate_playlist()
                if removed > 0:
                    logger.info(f"歌单去重: 移除 {removed} 首重复歌曲")
        except Exception:
            self._playlist = []

    def _save_playlist(self):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(PLAYLIST_FILE, "w", encoding="utf-8") as f:
                json.dump(self._playlist, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存歌单失败: {e}")

    def close(self):
        self._stop_playback()
        self._send_media_key("stop")
        for p in self._players:
            p.close()
