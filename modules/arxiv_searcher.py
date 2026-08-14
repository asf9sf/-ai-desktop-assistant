"""arXiv 论文搜索模块 — 基于 arXiv.org API 的学术论文检索。

支持按关键词、分类、作者等维度搜索论文，并返回标题、作者、摘要、PDF链接等信息。
API 文档: https://info.arxiv.org/help/api/index.html
"""
import logging
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

ARXIV_API = "http://export.arxiv.org/api/query"

# arXiv 分类代码映射（常见领域）
CATEGORY_MAP = {
    "ai": "cs.AI",
    "人工智能": "cs.AI",
    "nlp": "cs.CL",
    "自然语言": "cs.CL",
    "cv": "cs.CV",
    "计算机视觉": "cs.CV",
    "ml": "cs.LG",
    "机器学习": "cs.LG",
    "stat_ml": "stat.ML",
    "统计机器学习": "stat.ML",
    "physics": "physics",
    "物理": "physics",
    "math": "math",
    "数学": "math",
    "qbio": "q-bio",
    "生物": "q-bio",
    "eess": "eess",
    "电气工程": "eess",
    "cs": "cs",
    "计算机科学": "cs",
}


class ArxivSearcher:
    """arXiv 论文搜索器。"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

    def search(
        self,
        keyword: str,
        max_results: int = 5,
        category: Optional[str] = None,
        sort_by: str = "submittedDate",
    ) -> Tuple[bool, List[Dict] | str]:
        """
        搜索 arXiv 论文。

        Args:
            keyword: 搜索关键词
            max_results: 最大返回数量（默认5）
            category: 分类代码或中文分类名（如 cs.AI、人工智能）
            sort_by: 排序方式（submittedDate/relevance/lastUpdatedDate）

        Returns:
            (成功标志, 论文列表或错误信息)
        """
        if not keyword.strip():
            return False, "搜索关键词不能为空"

        # 构造搜索查询
        search_query = f'ti:"{keyword}" OR abs:"{keyword}"'

        # 分类过滤
        cat_code = self._resolve_category(category)
        if cat_code:
            search_query += f" AND cat:{cat_code}"

        # 排序映射
        sort_map = {
            "submittedDate": "submittedDate",
            "relevance": "relevance",
            "lastUpdatedDate": "lastUpdatedDate",
        }

        params = {
            "search_query": search_query,
            "start": 0,
            "max_results": min(max_results, 20),
            "sortBy": sort_map.get(sort_by, "submittedDate"),
            "sortOrder": "descending",
        }

        logger.info(f"[arXiv] 搜索: keyword={keyword}, category={cat_code}, max={max_results}")

        try:
            resp = self.session.get(ARXIV_API, params=params, timeout=15)
            resp.raise_for_status()

            papers = self._parse_atom(resp.text)
            logger.info(f"[arXiv] 搜索完成: 找到 {len(papers)} 篇论文")

            if not papers:
                return True, []

            return True, papers

        except requests.exceptions.Timeout:
            logger.error("[arXiv] 请求超时")
            return False, "arXiv 搜索请求超时，请稍后重试"
        except requests.exceptions.ConnectionError:
            logger.error("[arXiv] 连接失败")
            return False, "无法连接到 arXiv API，请检查网络"
        except Exception as e:
            logger.error(f"[arXiv] 搜索异常: {e}", exc_info=True)
            return False, f"arXiv 搜索失败: {e}"

    def search_by_author(self, author: str, max_results: int = 5) -> Tuple[bool, List[Dict] | str]:
        """按作者搜索论文。"""
        if not author.strip():
            return False, "作者名不能为空"

        search_query = f'au:"{author}"'
        params = {
            "search_query": search_query,
            "start": 0,
            "max_results": min(max_results, 20),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }

        logger.info(f"[arXiv] 按作者搜索: author={author}")

        try:
            resp = self.session.get(ARXIV_API, params=params, timeout=15)
            resp.raise_for_status()
            papers = self._parse_atom(resp.text)
            return True, papers
        except Exception as e:
            logger.error(f"[arXiv] 作者搜索异常: {e}", exc_info=True)
            return False, f"作者搜索失败: {e}"

    def search_by_id(self, arxiv_id: str) -> Tuple[bool, Optional[Dict]]:
        """按 arXiv ID 获取单篇论文详情。"""
        search_query = f"id:{arxiv_id}"
        params = {
            "search_query": search_query,
            "start": 0,
            "max_results": 1,
        }

        logger.info(f"[arXiv] 按ID查询: id={arxiv_id}")

        try:
            resp = self.session.get(ARXIV_API, params=params, timeout=15)
            resp.raise_for_status()
            papers = self._parse_atom(resp.text)
            if papers:
                return True, papers[0]
            return False, "未找到该论文"
        except Exception as e:
            logger.error(f"[arXiv] ID 查询异常: {e}", exc_info=True)
            return False, f"查询失败: {e}"

    def format_papers(self, papers: List[Dict], max_abstract_length: int = 300) -> str:
        """格式化论文列表为可读文本。"""
        if not papers:
            return "未找到相关论文"

        lines = [f"📚 arXiv 搜索结果（共 {len(papers)} 篇）", "=" * 50]

        for i, paper in enumerate(papers, 1):
            lines.append(f"\n{i}. {paper['title']}")
            lines.append(f"   作者: {', '.join(paper['authors'][:5])}")
            if len(paper['authors']) > 5:
                lines.append(f"          等 {len(paper['authors'])} 位作者")
            lines.append(f"   发布: {paper['published']}")

            abstract = paper.get("summary", "")
            if len(abstract) > max_abstract_length:
                abstract = abstract[:max_abstract_length] + "..."
            lines.append(f"   摘要: {abstract}")

            if paper.get("pdf_url"):
                lines.append(f"   📄 PDF: {paper['pdf_url']}")
            if paper.get("abs_url"):
                lines.append(f"   🔗 详情: {paper['abs_url']}")

            if paper.get("categories"):
                lines.append(f"   🏷️ 分类: {', '.join(paper['categories'][:3])}")

        return "\n".join(lines)

    def format_single_paper(self, paper: Dict) -> str:
        """格式化单篇论文详情。"""
        lines = [
            f"📄 {paper['title']}",
            f"{'=' * 60}",
            f"作者: {', '.join(paper['authors'])}",
            f"发布日期: {paper['published']}",
            f"arXiv ID: {paper.get('id', 'N/A')}",
        ]

        if paper.get("categories"):
            lines.append(f"分类: {', '.join(paper['categories'])}")

        lines.append(f"\n📝 摘要:\n{paper.get('summary', 'N/A')}")

        if paper.get("pdf_url"):
            lines.append(f"\n📥 PDF 下载: {paper['pdf_url']}")
        if paper.get("abs_url"):
            lines.append(f"🔗 arXiv 页面: {paper['abs_url']}")

        return "\n".join(lines)

    def _parse_atom(self, xml_text: str) -> List[Dict]:
        """解析 arXiv Atom XML 响应。"""
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(xml_text)

        papers = []
        for entry in root.findall("atom:entry", ns):
            paper = self._parse_entry(entry, ns)
            if paper:
                papers.append(paper)

        return papers

    def _parse_entry(self, entry: ET.Element, ns: Dict) -> Optional[Dict]:
        """解析单个论文条目。"""
        try:
            title_el = entry.find("atom:title", ns)
            if title_el is None or title_el.text is None:
                return None

            title = " ".join(title_el.text.strip().split())

            # 摘要
            summary_el = entry.find("atom:summary", ns)
            summary = " ".join(summary_el.text.strip().split()) if summary_el is not None and summary_el.text else ""

            # 作者
            authors = []
            for author_el in entry.findall("atom:author", ns):
                name_el = author_el.find("atom:name", ns)
                if name_el is not None and name_el.text:
                    authors.append(name_el.text.strip())

            # 发布日期
            published_el = entry.find("atom:published", ns)
            published = published_el.text[:10] if published_el is not None and published_el.text else ""

            # arXiv ID
            id_el = entry.find("atom:id", ns)
            arxiv_id = id_el.text.strip() if id_el is not None and id_el.text else ""

            # 链接
            pdf_url = ""
            abs_url = ""
            for link_el in entry.findall("atom:link", ns):
                title_attr = link_el.get("title", "")
                rel_attr = link_el.get("rel", "")
                href = link_el.get("href", "")
                if title_attr == "pdf":
                    pdf_url = href
                elif rel_attr == "alternate":
                    abs_url = href

            # 分类
            categories = []
            for cat_el in entry.findall("atom:category", ns):
                term = cat_el.get("term", "")
                if term:
                    categories.append(term)

            return {
                "id": arxiv_id,
                "title": title,
                "summary": summary,
                "authors": authors,
                "published": published,
                "pdf_url": pdf_url,
                "abs_url": abs_url,
                "categories": categories,
            }
        except Exception as e:
            logger.warning(f"[arXiv] 解析条目失败: {e}")
            return None

    @staticmethod
    def _resolve_category(category: Optional[str]) -> Optional[str]:
        """将中文分类名或简写映射为 arXiv 分类代码。"""
        if not category:
            return None

        category = category.strip().lower()

        # 直接匹配
        if category in CATEGORY_MAP:
            return CATEGORY_MAP[category]

        # 反向匹配（值→键）
        for key, val in CATEGORY_MAP.items():
            if category == val.lower():
                return val

        # 如果已经是合法的 arXiv 分类代码，直接返回
        if "." in category or category.startswith("cs") or category.startswith("astro"):
            return category

        return None


def search_arxiv(
    keyword: str,
    max_results: int = 5,
    category: Optional[str] = None,
) -> Tuple[bool, str]:
    """便捷函数：搜索 arXiv 并返回格式化文本。"""
    searcher = ArxivSearcher()
    ok, result = searcher.search(keyword, max_results, category)

    if not ok:
        return False, str(result)

    papers = result
    formatted = searcher.format_papers(papers)
    return True, formatted
