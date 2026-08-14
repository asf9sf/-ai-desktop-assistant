"""
MemSkill —— 基于 Agent Skill 格式的长期记忆系统

设计理念：
- 记忆即 Skill：每条记忆是一个结构化对象（name/keywords/summary/triggers/raw_content/importance）
- 分层漏斗检索：语义匹配 → LLM 摘要筛选 → 原始内容展开
- 自我维护：定期合并同类记忆、依重要性与时间衰减遗忘
- 成本最优：日常检索只动轻量索引，只在必要时加载原始全文

存储：SQLite（桌面应用无需外部数据库）
向量：优先用 OpenAI 兼容 /v1/embeddings，失败回退到字符 bigram TF-IDF 余弦相似度
"""

import os
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple

from .llm_module import LLMClient


# ---------------- 数据结构 ----------------

class MemorySkill:
    """一条记忆技能对象。"""
    def __init__(self, data: Dict[str, Any]):
        self.skill_id: str = data.get("skill_id", "")
        self.name: str = data.get("name", "")
        self.keywords: List[str] = data.get("keywords", [])
        self.summary: str = data.get("summary", "")
        self.triggers: List[str] = data.get("triggers", [])
        self.raw_content: str = data.get("raw_content", "")
        self.importance: int = int(data.get("importance", 3))
        self.created_at: str = data.get("created_at", "")
        self.last_accessed_at: str = data.get("last_accessed_at", "")
        self.access_count: int = int(data.get("access_count", 0))
        self.source_session_ids: List[str] = data.get("source_session_ids", [])
        self.is_merged: bool = bool(data.get("is_merged", False))
        self.merged_from: List[str] = data.get("merged_from", [])
        self.embedding: List[float] = data.get("embedding", [])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "keywords": self.keywords,
            "summary": self.summary,
            "triggers": self.triggers,
            "raw_content": self.raw_content,
            "importance": self.importance,
            "created_at": self.created_at,
            "last_accessed_at": self.last_accessed_at,
            "access_count": self.access_count,
            "source_session_ids": self.source_session_ids,
            "is_merged": self.is_merged,
            "merged_from": self.merged_from,
        }

    def brief(self) -> str:
        """用于 UI 展示的简短信息。"""
        stars = "★" * self.importance + "☆" * (5 - self.importance)
        return f"[{stars}] {self.name}\n   {self.summary}\n   关键词: {', '.join(self.keywords)}"


# ---------------- TF-IDF 回退相似度 ----------------

def _char_bigrams(text: str) -> Dict[str, int]:
    """对中文友好的字符 bigram 频次表。"""
    text = re.sub(r"\s+", "", text)
    freq: Dict[str, int] = {}
    for i in range(len(text) - 1):
        bg = text[i:i + 2]
        freq[bg] = freq.get(bg, 0) + 1
    # 单字也作为一元特征
    for ch in text:
        freq[ch] = freq.get(ch, 0) + 1
    return freq


def _cosine_sparse(a: Dict[str, float], b: Dict[str, float]) -> float:
    """稀疏向量的余弦相似度。"""
    if not a or not b:
        return 0.0
    dot = sum(a[k] * b.get(k, 0.0) for k in a)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def tfidf_similarity(text_a: str, text_b: str) -> float:
    """基于字符 bigram 的 TF-IDF 风格余弦相似度（回退方案）。"""
    return _cosine_sparse(_char_bigrams(text_a), _char_bigrams(text_b))


def cosine_vec(a: List[float], b: List[float]) -> float:
    """稠密向量的余弦相似度。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------------- 存储层 ----------------

class MemoryStore:
    """SQLite 存储层。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self):
        with self._lock, self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    skill_id          TEXT PRIMARY KEY,
                    name              TEXT NOT NULL,
                    keywords          TEXT NOT NULL DEFAULT '[]',
                    summary           TEXT NOT NULL DEFAULT '',
                    triggers          TEXT NOT NULL DEFAULT '[]',
                    raw_content       TEXT NOT NULL DEFAULT '',
                    importance        INTEGER NOT NULL DEFAULT 3,
                    created_at        TEXT NOT NULL,
                    last_accessed_at  TEXT NOT NULL,
                    access_count      INTEGER NOT NULL DEFAULT 0,
                    source_session_ids TEXT NOT NULL DEFAULT '[]',
                    is_merged         INTEGER NOT NULL DEFAULT 0,
                    merged_from       TEXT NOT NULL DEFAULT '[]',
                    embedding         TEXT NOT NULL DEFAULT '[]',
                    status            TEXT NOT NULL DEFAULT 'active'
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_status ON memories(status)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_importance ON memories(importance)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_last_access ON memories(last_accessed_at)")

    def _row_to_skill(self, row: sqlite3.Row) -> MemorySkill:
        return MemorySkill({
            "skill_id": row["skill_id"],
            "name": row["name"],
            "keywords": json.loads(row["keywords"]),
            "summary": row["summary"],
            "triggers": json.loads(row["triggers"]),
            "raw_content": row["raw_content"],
            "importance": row["importance"],
            "created_at": row["created_at"],
            "last_accessed_at": row["last_accessed_at"],
            "access_count": row["access_count"],
            "source_session_ids": json.loads(row["source_session_ids"]),
            "is_merged": bool(row["is_merged"]),
            "merged_from": json.loads(row["merged_from"]),
            "embedding": json.loads(row["embedding"]) if row["embedding"] else [],
        })

    def insert(self, skill: MemorySkill):
        with self._lock, self._conn() as c:
            c.execute("""
                INSERT OR REPLACE INTO memories
                (skill_id, name, keywords, summary, triggers, raw_content, importance,
                 created_at, last_accessed_at, access_count, source_session_ids,
                 is_merged, merged_from, embedding, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active')
            """, (
                skill.skill_id, skill.name,
                json.dumps(skill.keywords, ensure_ascii=False),
                skill.summary,
                json.dumps(skill.triggers, ensure_ascii=False),
                skill.raw_content, skill.importance,
                skill.created_at, skill.last_accessed_at, skill.access_count,
                json.dumps(skill.source_session_ids, ensure_ascii=False),
                1 if skill.is_merged else 0,
                json.dumps(skill.merged_from, ensure_ascii=False),
                json.dumps(skill.embedding) if skill.embedding else "[]",
            ))

    def get(self, skill_id: str) -> Optional[MemorySkill]:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT * FROM memories WHERE skill_id=? AND status='active'", (skill_id,)).fetchone()
            return self._row_to_skill(row) if row else None

    def list_active(self, limit: int = 500) -> List[MemorySkill]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT * FROM memories WHERE status='active' ORDER BY importance DESC, created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [self._row_to_skill(r) for r in rows]

    def update_access(self, skill_id: str, accessed_at: str):
        with self._lock, self._conn() as c:
            c.execute(
                "UPDATE memories SET last_accessed_at=?, access_count=access_count+1 WHERE skill_id=?",
                (accessed_at, skill_id)
            )

    def set_status(self, skill_id: str, status: str):
        with self._lock, self._conn() as c:
            c.execute("UPDATE memories SET status=? WHERE skill_id=?", (status, skill_id))

    def delete(self, skill_id: str):
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM memories WHERE skill_id=?", (skill_id,))

    def count(self) -> int:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT COUNT(*) AS n FROM memories WHERE status='active'").fetchone()
            return row["n"] if row else 0


# ---------------- 记忆管理器 ----------------

class MemSkillManager:
    """
    长期记忆系统核心管理器。
    对外接口：
      - add_memory(conversation_text, session_id)         写入
      - retrieve_relevant_memories(current_query)          三层漏斗检索
      - schedule_maintenance()                             合并 + 衰减
      - list_all() / delete_memory() / update_importance() UI 管理
    """

    # 超参数（可外部修改）
    TOP_K_FIRST_LAYER = 5
    SIMILARITY_THRESHOLD_MERGE = 0.9
    FORGET_DAYS_LOW_IMPORTANCE = 30
    LOW_IMPORTANCE_THRESHOLD = 3

    # 写入提示词
    EXTRACT_PROMPT = (
        "你是一个记忆提取器。分析以下对话，提取关于用户的重要事实、偏好、经历或决定，"
        "并严格按JSON格式输出。如果没有任何值得长期记忆的信息，则返回 {\"skip\": true}。\n"
        "要求：\n"
        "- name: 简短标题（≤15字）\n"
        "- keywords: 3-5个核心关键词\n"
        "- summary: 一句话总结核心事实（≤60字）\n"
        "- triggers: 2-3个能触发该记忆的对话场景\n"
        "- importance: 1-5整数评分（1=琐碎闲聊，5=关键健康/安全/身份信息）\n\n"
        "只输出JSON，不要输出任何其他文字。格式：\n"
        "{\"skip\": false, \"name\": \"...\", \"keywords\": [\"...\"], "
        "\"summary\": \"...\", \"triggers\": [\"...\"], \"importance\": 3}\n\n"
        "对话内容：\n{conversation_text}"
    )

    # 检索第二层：LLM 筛选提示
    FILTER_PROMPT = (
        "你是一个记忆相关性判断助手。\n"
        "用户当前消息：「{query}」\n"
        "以下是候选记忆的摘要列表（带编号）：\n{candidates}\n\n"
        "请选出与用户当前情境真正相关的记忆编号（可以是0~3条）。\n"
        "只输出JSON：{\"indices\": [0, 2], \"reason\": \"简短原因\"}\n"
        "如果都不相关，返回 {\"indices\": [], \"reason\": \"无\"}。"
    )

    # 合并提示词
    MERGE_PROMPT = (
        "你是一个记忆合并器。下面是多条相似的记忆，请将它们合并为一条更完整的记忆，"
        "提炼精华，避免冗余。严格输出JSON。\n"
        "格式：{\"name\": \"...\", \"keywords\": [\"...\"], \"summary\": \"...\", "
        "\"triggers\": [\"...\"], \"importance\": 4}\n\n"
        "待合并记忆：\n{memories_text}"
    )

    def __init__(self, llm: LLMClient, db_path: str = None):
        if db_path is None:
            db_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "memory.db"
            )
        self.llm = llm
        self.store = MemoryStore(db_path)
        self._embedding_ok = True  # 是否尝试使用 embedding API
        self._embedding_checked = False

    # ---------- 写入模块 ----------

    def add_memory(self, conversation_text: str, session_id: str = "") -> Optional[MemorySkill]:
        """
        解析对话文本，提取记忆并存储。
        若对话不值得记忆（LLM 判定 skip），返回 None。
        """
        conversation_text = conversation_text.strip()
        if not conversation_text or len(conversation_text) < 10:
            return None

        extracted = self._llm_extract(conversation_text)
        if not extracted or extracted.get("skip"):
            return None

        now = self._now_iso()
        skill_id = f"mem_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        summary = extracted.get("summary", "").strip()
        embedding = self._get_embedding(summary)

        skill = MemorySkill({
            "skill_id": skill_id,
            "name": extracted.get("name", "未命名记忆")[:15],
            "keywords": extracted.get("keywords", [])[:5],
            "summary": summary[:60],
            "triggers": extracted.get("triggers", [])[:3],
            "raw_content": conversation_text[:5000],  # 截断防止过大
            "importance": max(1, min(5, int(extracted.get("importance", 3)))),
            "created_at": now,
            "last_accessed_at": now,
            "access_count": 0,
            "source_session_ids": [session_id] if session_id else [],
            "is_merged": False,
            "merged_from": [],
            "embedding": embedding,
        })
        self.store.insert(skill)
        return skill

    def _llm_extract(self, conversation_text: str) -> Dict[str, Any]:
        """调用 LLM 提取记忆结构。"""
        prompt = self.EXTRACT_PROMPT.replace("{conversation_text}", conversation_text[:4000])
        messages = [
            {"role": "system", "content": "你是一个记忆提取器，只输出JSON。"},
            {"role": "user", "content": prompt},
        ]
        try:
            raw = self.llm.chat(messages, stream_callback=None, timeout=30)
            raw = raw.strip()
            m = re.search(r"\{[\s\S]*\}", raw)
            if m:
                obj = json.loads(m.group(0))
                return obj
        except Exception:
            pass
        return {"skip": True}

    def _get_embedding(self, text: str) -> List[float]:
        """获取文本向量。失败返回空列表（检索时回退到 TF-IDF）。"""
        if not text.strip():
            return []
        try:
            vecs = self.llm.embed([text], timeout=15)
            if vecs and len(vecs) > 0 and len(vecs[0]) > 0:
                self._embedding_ok = True
                return vecs[0]
        except Exception:
            pass
        self._embedding_ok = False
        return []

    # ---------- 检索模块（三层漏斗）----------

    def retrieve_relevant_memories(self, current_query: str) -> List[MemorySkill]:
        """
        三层漏斗检索：
        1. 语义匹配 Top-K（embedding 或 TF-IDF 回退）
        2. LLM 筛选真正相关的记忆
        3. 返回选中记忆（含 raw_content），并更新访问记录
        """
        current_query = current_query.strip()
        if not current_query:
            return []

        all_memories = self.store.list_active(limit=500)
        if not all_memories:
            return []

        # ---- 第一层：快速语义匹配 ----
        top_k = self._first_layer_match(current_query, all_memories, k=self.TOP_K_FIRST_LAYER)
        if not top_k:
            return []

        # ---- 第二层：LLM 细粒度筛选 ----
        selected = self._second_layer_filter(current_query, top_k)
        if not selected:
            return []

        # ---- 第三层：返回含 raw_content 的完整记忆，更新访问记录 ----
        now = self._now_iso()
        for mem in selected:
            self.store.update_access(mem.skill_id, now)
        return selected

    def _first_layer_match(self, query: str, memories: List[MemorySkill], k: int = 5) -> List[MemorySkill]:
        """第一层：语义匹配。优先 embedding，回退 TF-IDF。"""
        query_emb = self._get_embedding(query)
        use_embedding = bool(query_emb) and self._embedding_ok

        scored: List[Tuple[float, MemorySkill]] = []
        for mem in memories:
            if use_embedding and mem.embedding:
                sim = cosine_vec(query_emb, mem.embedding)
            else:
                # 回退：对 query 与 summary+keywords 做 TF-IDF
                text_b = mem.summary + " " + " ".join(mem.keywords) + " " + mem.name
                sim = tfidf_similarity(query, text_b)
            scored.append((sim, mem))

        scored.sort(key=lambda x: x[0], reverse=True)
        # 取 Top-K，且相似度需 > 0.1（过滤明显无关）
        return [mem for sim, mem in scored[:k] if sim > 0.1]

    def _second_layer_filter(self, query: str, candidates: List[MemorySkill]) -> List[MemorySkill]:
        """第二层：LLM 从候选摘要中选出真正相关的记忆。"""
        if not candidates:
            return []

        # 构建候选列表文本
        cand_lines = []
        for i, mem in enumerate(candidates):
            cand_lines.append(
                f"[{i}] 标题:{mem.name} | 摘要:{mem.summary} | 关键词:{','.join(mem.keywords)}"
            )
        candidates_text = "\n".join(cand_lines)

        prompt = self.FILTER_PROMPT.replace("{query}", query[:200]).replace("{candidates}", candidates_text)
        messages = [
            {"role": "system", "content": "你是一个记忆相关性判断助手，只输出JSON。"},
            {"role": "user", "content": prompt},
        ]
        try:
            raw = self.llm.chat(messages, stream_callback=None, timeout=15)
            raw = raw.strip()
            m = re.search(r"\{[\s\S]*\}", raw)
            if m:
                obj = json.loads(m.group(0))
                indices = obj.get("indices", [])
                selected = []
                for idx in indices:
                    if isinstance(idx, int) and 0 <= idx < len(candidates):
                        selected.append(candidates[idx])
                return selected
        except Exception:
            pass

        # LLM 筛选失败时，回退到第一层结果中 importance 最高的 1~2 条
        candidates.sort(key=lambda m: m.importance, reverse=True)
        return candidates[:2] if candidates else []

    # ---------- 维护模块 ----------

    def schedule_maintenance(self) -> Dict[str, int]:
        """
        执行维护任务：合并 + 衰减删除。
        返回统计信息 {"merged": n, "forgotten": n}。
        """
        stats = {"merged": 0, "forgotten": 0}
        try:
            stats["merged"] = self._merge_similar()
        except Exception:
            pass
        try:
            stats["forgotten"] = self._decay_forget()
        except Exception:
            pass
        return stats

    def _merge_similar(self) -> int:
        """合并高相似度记忆。返回合并数。"""
        memories = self.store.list_active(limit=500)
        if len(memories) < 2:
            return 0

        merged_count = 0
        used_ids: set = set()

        for i, mem_a in enumerate(memories):
            if mem_a.skill_id in used_ids:
                continue
            group = [mem_a]
            for mem_b in memories[i + 1:]:
                if mem_b.skill_id in used_ids:
                    continue
                sim = self._similarity(mem_a, mem_b)
                if sim >= self.SIMILARITY_THRESHOLD_MERGE:
                    group.append(mem_b)

            if len(group) > 1:
                merged = self._merge_group(group)
                if merged:
                    for m in group:
                        self.store.set_status(m.skill_id, "archived")
                        used_ids.add(m.skill_id)
                    self.store.insert(merged)
                    merged_count += len(group) - 1

        return merged_count

    def _similarity(self, a: MemorySkill, b: MemorySkill) -> float:
        """计算两条记忆的相似度。优先 embedding，回退 TF-IDF。"""
        if a.embedding and b.embedding and self._embedding_ok:
            return cosine_vec(a.embedding, b.embedding)
        text_a = a.summary + " " + " ".join(a.keywords)
        text_b = b.summary + " " + " ".join(b.keywords)
        return tfidf_similarity(text_a, text_b)

    def _merge_group(self, group: List[MemorySkill]) -> Optional[MemorySkill]:
        """用 LLM 合并一组相似记忆为一条新记忆。"""
        memories_text = "\n\n".join([
            f"记忆{i+1}:\n  标题: {m.name}\n  摘要: {m.summary}\n  关键词: {', '.join(m.keywords)}\n  "
            f"触发场景: {', '.join(m.triggers)}\n  重要性: {m.importance}\n  原始内容: {m.raw_content[:500]}"
            for i, m in enumerate(group)
        ])
        prompt = self.MERGE_PROMPT.replace("{memories_text}", memories_text[:4000])
        messages = [
            {"role": "system", "content": "你是一个记忆合并器，只输出JSON。"},
            {"role": "user", "content": prompt},
        ]
        try:
            raw = self.llm.chat(messages, stream_callback=None, timeout=30)
            m = re.search(r"\{[\s\S]*\}", raw.strip())
            if not m:
                return None
            obj = json.loads(m.group(0))
        except Exception:
            return None

        now = self._now_iso()
        merged_ids = [m.skill_id for m in group]
        all_sessions = []
        for m in group:
            all_sessions.extend(m.source_session_ids)

        raw_combined = "\n---\n".join([m.raw_content for m in group])[:5000]
        summary = obj.get("summary", group[0].summary)[:60]
        embedding = self._get_embedding(summary)

        return MemorySkill({
            "skill_id": f"mem_{datetime.now().strftime('%Y%m%d_%H%M%S')}_m{uuid.uuid4().hex[:6]}",
            "name": obj.get("name", group[0].name)[:15],
            "keywords": obj.get("keywords", [])[:5],
            "summary": summary,
            "triggers": obj.get("triggers", [])[:3],
            "raw_content": raw_combined,
            "importance": max(1, min(5, int(obj.get("importance", max(m.importance for m in group))))),
            "created_at": group[0].created_at,
            "last_accessed_at": now,
            "access_count": sum(m.access_count for m in group),
            "source_session_ids": list(set(all_sessions)),
            "is_merged": True,
            "merged_from": merged_ids,
            "embedding": embedding,
        })

    def _decay_forget(self) -> int:
        """衰减遗忘：低重要度 + 长期未访问的记忆归档。"""
        memories = self.store.list_active(limit=1000)
        threshold_date = datetime.now(timezone.utc) - timedelta(days=self.FORGET_DAYS_LOW_IMPORTANCE)
        threshold_str = threshold_date.strftime("%Y-%m-%dT%H:%M:%S")

        forgotten = 0
        for mem in memories:
            if mem.importance < self.LOW_IMPORTANCE_THRESHOLD:
                if mem.last_accessed_at < threshold_str:
                    self.store.set_status(mem.skill_id, "archived")
                    forgotten += 1
        return forgotten

    # ---------- UI 管理接口 ----------

    def list_all(self) -> List[MemorySkill]:
        return self.store.list_active(limit=1000)

    def delete_memory(self, skill_id: str):
        self.store.set_status(skill_id, "deleted")

    def update_importance(self, skill_id: str, importance: int):
        importance = max(1, min(5, int(importance)))
        with self.store._lock, self.store._conn() as c:
            c.execute("UPDATE memories SET importance=? WHERE skill_id=?", (importance, skill_id))

    def count(self) -> int:
        return self.store.count()

    # ---------- 工具 ----------

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def build_context_text(self, memories: List[MemorySkill]) -> str:
        """将检索到的记忆格式化为可注入 LLM 上下文的文本。"""
        if not memories:
            return ""
        lines = ["以下是你对用户的长期记忆（请自然地参考这些信息来回复，不要生硬罗列）："]
        for m in memories:
            lines.append(
                f"• [{m.name}] {m.summary}\n"
                f"  详情: {m.raw_content[:300]}"
            )
        return "\n".join(lines)
