"""音乐数据模型"""
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class Song:
    id: str
    name: Optional[str] = None
    artists: Optional[str] = None
    duration: Optional[int] = None
    title: Optional[str] = None
    author: Optional[str] = None
    cover_url: Optional[str] = None
    audio_url: Optional[str] = None
    path: Optional[str] = None
    lyrics: Optional[str] = None
    comments: Optional[list] = None
    source: Optional[str] = None
    note: Optional[str] = None

    @property
    def display_name(self) -> str:
        return self.title or self.name or "未知歌曲"

    @property
    def display_artist(self) -> str:
        return self.artists or self.author or "未知歌手"

    @property
    def duration_text(self) -> str:
        if self.duration and self.duration > 0:
            mins, secs = divmod(self.duration // 1000, 60)
            return f"{mins}:{secs:02d}"
        return ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.display_name,
            "artist": self.display_artist,
            "duration": self.duration_text,
            "audio_url": self.audio_url,
            "cover_url": self.cover_url,
            "lyrics": self.lyrics,
            "source": self.source,
        }

    def to_text(self, index: int = 0) -> str:
        lines = [f"{index}. {self.display_name}"]
        lines[0] += f" - {self.display_artist}" if self.display_artist else ""
        if self.duration_text:
            lines[0] += f" ({self.duration_text})"
        return lines[0]


@dataclass
class Platform:
    name: str
    display_name: str
    keywords: List[str] = field(default_factory=list)
