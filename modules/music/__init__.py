"""音乐模块 — 多平台点歌、搜索、播放"""
from .model import Song, Platform
from .music_player import MusicPlayer

__all__ = ["Song", "Platform", "MusicPlayer"]
