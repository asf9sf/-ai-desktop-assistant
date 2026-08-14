import os
import re
import json
import time
import webbrowser
import urllib.parse
import requests
from typing import List, Dict, Optional, Tuple
from pypinyin import lazy_pinyin


SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 常见网站搜索 URL 模板（站内搜索）
# key: 可触发的网站名称；value: (搜索URL模板, 是否需要URL编码)
SITE_SEARCH_TEMPLATES: Dict[str, Tuple[str, bool]] = {
    "bilibili": ("https://search.bilibili.com/all?keyword={}", True),
    "b站": ("https://search.bilibili.com/all?keyword={}", True),
    "哔哩哔哩": ("https://search.bilibili.com/all?keyword={}", True),
    "youtube": ("https://www.youtube.com/results?search_query={}", True),
    "油管": ("https://www.youtube.com/results?search_query={}", True),
    "知乎": ("https://www.zhihu.com/search?type=content&q={}", True),
    "zhihu": ("https://www.zhihu.com/search?type=content&q={}", True),
    "csdn": ("https://so.csdn.net/so/search?q={}", True),
    "github": ("https://github.com/search?q={}", True),
    "淘宝": ("https://s.taobao.com/search?q={}", True),
    "京东": ("https://search.jd.com/Search?keyword={}", True),
    "百度": ("https://www.baidu.com/s?wd={}", True),
    "谷歌": ("https://www.google.com/search?q={}", True),
    "google": ("https://www.google.com/search?q={}", True),
    "小红书": ("https://www.xiaohongshu.com/search_result?keyword={}", True),
    "微博": ("https://s.weibo.com/weibo?q={}", True),
    "豆瓣": ("https://search.douban.com/movie/subject_search?search_text={}", True),
    "stackoverflow": ("https://stackoverflow.com/search?q={}", True),
    "51job": ("https://we.51job.com/pc/search?keyword={}", True),
    "猎聘": ("https://www.liepin.com/zhaopin/?key={}", True),
    "boss直聘": ("https://www.zhipin.com/web/geek/job?query={}", True),
    "抖音": ("https://www.douyin.com/search/{}", False),
    "tiktok": ("https://www.tiktok.com/search?q={}", True),
}

# 通用搜索引擎的 site: 限定模板（当无专属站内搜索时使用）
SITE_SEARCH_ENGINE = "https://www.bing.com/search?q={site}%20{keyword}"


class BrowserSearcher:
    """网页搜索 + 结果筛选 + 打开链接。"""

    def __init__(self, llm_client=None):
        self.llm = llm_client
        self._cache = {}  # query -> list[result]

    # ---------- 站内搜索 ----------

    def search_on_site(self, site: str, keyword: str) -> Tuple[bool, str, str]:
        """
        在指定网站站内搜索关键词，并打开搜索结果页。
        返回 (是否成功, 描述信息, 构造的URL)
        """
        url = self.build_site_search_url(site, keyword)
        if not url:
            # 未知网站，退回到通用搜索引擎的 site: 语法
            url = self.build_site_search_url("__general__", f"site:{site} {keyword}")
        ok = self.open_url(url)
        site_label = site
        msg = f"已在「{site_label}」中搜索「{keyword}」" if ok else f"打开浏览器失败"
        return ok, msg, url

    @staticmethod
    def build_site_search_url(site: str, keyword: str) -> str:
        """构造指定网站的搜索 URL。未知网站返回空字符串。"""
        site_lower = site.lower().strip()
        keyword_stripped = keyword.strip()
        encoded = urllib.parse.quote(keyword_stripped)

        # 精确匹配
        for key, (template, needs_encode) in SITE_SEARCH_TEMPLATES.items():
            if key.lower() == site_lower:
                return template.format(encoded if needs_encode else keyword_stripped)

        # 包含匹配（例如 "bilibili.com" 包含 "bilibili"）
        for key, (template, needs_encode) in SITE_SEARCH_TEMPLATES.items():
            if key.lower() in site_lower or site_lower in key.lower():
                return template.format(encoded if needs_encode else keyword_stripped)

        # 通用 fallback：Bing 的 site: 语法
        encoded_full = urllib.parse.quote_plus(f"site:{site} {keyword_stripped}")
        return f"https://www.bing.com/search?q={encoded_full}"

    def search(self, query: str, num_results: int = 10) -> List[Dict]:
        """
        搜索并返回结果列表。每个元素：{title, url, snippet}
        优先使用Bing（中文友好、无严格反爬），失败时用百度。
        """
        cache_key = query.strip().lower()
        if cache_key in self._cache:
            return self._cache[cache_key]

        results = self._search_bing(query, num_results)
        if not results:
            results = self._search_baidu(query, num_results)

        self._cache[cache_key] = results
        return results

    def open_url(self, url: str) -> bool:
        """用系统默认浏览器打开URL。多策略保证成功。"""
        try:
            # 方法1: webbrowser模块
            result = webbrowser.open(url, new=2)
            if result:
                return True
        except Exception:
            pass

        try:
            # 方法2: Windows os.startfile（最可靠）
            if os.name == 'nt':
                os.startfile(url)
                return True
        except Exception:
            pass

        try:
            # 方法3: subprocess 调用系统命令
            import subprocess
            if os.name == 'nt':
                subprocess.Popen(['cmd', '/c', 'start', '', url], shell=False)
            else:
                subprocess.Popen(['xdg-open', url])
            return True
        except Exception:
            pass

        return False

    def search_and_open_best(self, query: str) -> Tuple[bool, str, Dict]:
        """
        执行搜索，用LLM筛选最匹配的一条结果，并用浏览器打开。
        返回 (是否成功, 描述信息, 选中的result字典)
        """
        results = self.search(query, num_results=8)
        if not results:
            return False, f"搜索「{query}」没有找到结果", {}

        best_idx = 0
        if self.llm is not None:
            try:
                best_idx = self._pick_best_with_llm(query, results)
            except Exception:
                best_idx = 0
        if best_idx < 0 or best_idx >= len(results):
            best_idx = 0

        chosen = results[best_idx]
        ok = self.open_url(chosen["url"])
        msg = f"已为你打开：{chosen.get('title', '搜索结果')}" if ok else f"找到结果但浏览器打开失败：{chosen.get('title')}"
        return ok, msg, chosen

    # ---------- 搜索引擎实现 ----------

    def _search_bing(self, query: str, num: int) -> List[Dict]:
        url = "https://www.bing.com/search?" + urllib.parse.urlencode({
            "q": query,
            "count": num,
            "setlang": "zh-CN",
            "cc": "CN",
        })
        try:
            r = requests.get(url, headers=SEARCH_HEADERS, timeout=10)
            r.raise_for_status()
            html = r.text
        except Exception:
            return []

        results = []
        li_blocks = re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', html, re.S)

        for block in li_blocks[:num]:
            title = ""
            link_url = ""
            snippet = ""

            # 优先从 <h2> 中提取标题和链接
            h2_m = re.search(r'<h2[^>]*>(.*?)</h2>', block, re.S)
            if h2_m:
                h2_content = h2_m.group(1)
                h2_link = re.search(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', h2_content, re.S)
                if h2_link:
                    link_url = h2_link.group(1)
                    # 清理标题：去掉所有 HTML 标签和多余空白
                    title = re.sub(r"<[^>]*>", "", h2_link.group(2)).strip()
                    title = re.sub(r"\s+", " ", title)

            # 如果 h2 没找到，找所有非 bing/microsoft 的链接
            if not link_url:
                all_links = re.findall(
                    r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.S
                )
                for href, text in all_links:
                    if 'bing.com' not in href and 'microsoft.com' not in href:
                        link_url = href
                        title = re.sub(r"<[^>]*>", "", text).strip()
                        title = re.sub(r"\s+", " ", title)
                        break

            # 取摘要
            p_m = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
            if p_m:
                snippet = re.sub(r"<[^>]*>", "", p_m.group(1)).strip()
                snippet = re.sub(r"\s+", " ", snippet)[:200]

            if title and link_url:
                results.append({
                    "title": title,
                    "url": link_url,
                    "snippet": snippet,
                })

        return results

    def _search_baidu(self, query: str, num: int) -> List[Dict]:
        url = "https://www.baidu.com/s?" + urllib.parse.urlencode({
            "wd": query,
            "rn": num,
            "ie": "utf-8",
        })
        try:
            r = requests.get(url, headers=SEARCH_HEADERS, timeout=10)
            r.raise_for_status()
            html = r.text
        except Exception:
            return []

        results = []
        # 抽取结果块
        pattern = re.compile(
            r'<div class="(?:result|c-container).*?data-click=.*?>(.*?)</div>\s*(?:</div>)*',
            re.S,
        )
        # 提取标题和链接
        for m in re.finditer(r'<h3[^>]*class="t"[^>]*>(.*?)</h3>', html, re.S):
            h3 = m.group(1)
            link_m = re.search(r'<a[^>]*href="(http[^"]+)"[^>]*>(.*?)</a>', h3, re.S)
            if not link_m:
                continue
            raw_url = link_m.group(1)
            title = re.sub(r"<.*?>", "", link_m.group(2)).strip()
            snippet = ""
            # 简单找前后文本作为摘要
            pos = m.start()
            nearby = html[max(0, pos - 400): pos + 1200]
            snip_m = re.search(
                r'<span[^>]*class="[^"]*content-right_8Zs40[^"]*"[^>]*>(.*?)</span>',
                nearby, re.S,
            )
            if snip_m:
                snippet = re.sub(r"<.*?>", "", snip_m.group(1)).strip()
            else:
                snip_m = re.search(
                    r'<div[^>]*class="c-abstract[^"]*"[^>]*>(.*?)</div>', nearby, re.S
                )
                if snip_m:
                    snippet = re.sub(r"<.*?>", "", snip_m.group(1)).strip()
            if title and raw_url:
                results.append({"title": title, "url": raw_url, "snippet": snippet})
            if len(results) >= num:
                break
        return results

    def _pick_best_with_llm(self, query: str, results: List[Dict]) -> int:
        """让LLM根据标题和摘要选择最符合用户意图的结果索引。"""
        items_text = []
        for i, r in enumerate(results):
            items_text.append(
                f"[{i}] 标题:{r.get('title','')}\n    摘要:{r.get('snippet','')[:120]}"
            )
        prompt = (
            f"用户搜索词：「{query}」\n"
            f"下面是搜索引擎返回的候选结果，每条带编号[i]。\n"
            f"请仔细阅读每条结果的标题和摘要，选出与用户意图最接近、最能满足用户需求的那一条。\n"
            f"请只输出一个数字编号（0到{len(results)-1}），不要输出任何其他文字。\n\n"
            + "\n\n".join(items_text)
        )
        messages = [
            {"role": "system", "content": "你是一个搜索结果筛选助手，只输出数字索引。"},
            {"role": "user", "content": prompt},
        ]
        # 不流式，短超时
        text = self.llm.chat(messages, stream_callback=None, timeout=20)
        text = text.strip()
        m = re.search(r"\d+", text)
        if m:
            return int(m.group(0))
        return 0
