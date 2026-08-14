"""
浏览器自动化模块 - 基于 Playwright (CDP) 的浏览器操作
第3层（浏览器专属方案）：通过 CDP 直接操作 DOM，比 VLM 更精准
支持：打开URL、搜索、点击元素、输入文字、获取页面内容

优势：像素级精准、毫秒级响应、零成本、能获取 DOM 结构

重要：所有 Playwright 操作通过专用工作线程执行，避免跨线程问题
"""

import os
import time
import logging
import threading
import queue
import uuid
from typing import Tuple, Optional, List, Dict, Any

# 设置 Playwright 浏览器路径（项目本地存储）
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", 
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".playwright-browsers"))

logger = logging.getLogger(__name__)


class _BrowserWorker:
    """专用浏览器工作线程 - 所有 Playwright 操作在此线程中执行。

    Playwright sync API 不是线程安全的，必须在创建它的同一线程中使用。
    这个类创建一个常驻线程，通过任务队列分发操作。
    """

    def __init__(self):
        self._queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True, name="BrowserWorker")
        self._thread.start()
        self._started = threading.Event()

        # 浏览器状态（仅在工作线程中读写）
        self._browser = None
        self._page = None
        self._playwright = None
        self._inited = False

        # 等待初始化完成
        self._started.wait(timeout=30)

    def _run(self):
        """工作线程主循环。"""
        # 初始化浏览器
        self._init_browser()
        self._started.set()

        # 处理任务
        while True:
            try:
                task = self._queue.get()
                if task is None:
                    break
                func, args, kwargs, result_event, result_container = task
                try:
                    result = func(*args, **kwargs)
                    result_container.append(result)
                except Exception as e:
                    result_container.append(("error", str(e)))
                finally:
                    result_event.set()
            except Exception as e:
                logger.error(f"浏览器工作线程异常: {e}")

    def _init_browser(self):
        """在工作线程中初始化浏览器（Microsoft Edge）。"""
        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()

            # 尝试连接已有的 Edge 实例
            connected_existing = False
            try:
                self._browser = self._playwright.chromium.connect_over_cdp("http://localhost:9222")
                logger.info("已连接到现有 Edge 实例")
                connected_existing = True
            except Exception:
                pass

            if not connected_existing:
                logger.info("启动新的 Microsoft Edge 浏览器实例")
                # 优先尝试 Playwright 内置的 Edge channel
                edge_launched = False
                try:
                    self._browser = self._playwright.chromium.launch(
                        channel="msedge",
                        headless=False,
                        args=[
                            '--no-sandbox',
                            '--disable-dev-shm-usage',
                            '--disable-gpu',
                        ],
                    )
                    edge_launched = True
                    logger.info("已通过 Playwright channel 启动 Edge")
                except Exception as e1:
                    logger.info(f"channel 方式启动 Edge 失败: {e1}")

                # 回退：查找系统 Edge 可执行文件路径
                if not edge_launched:
                    edge_paths = [
                        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
                        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
                    ]
                    for ep in edge_paths:
                        if os.path.exists(ep):
                            logger.info(f"通过可执行文件路径启动 Edge: {ep}")
                            self._browser = self._playwright.chromium.launch(
                                executable_path=ep,
                                headless=False,
                                args=[
                                    '--no-sandbox',
                                    '--disable-dev-shm-usage',
                                    '--disable-gpu',
                                ],
                            )
                            edge_launched = True
                            break

                # 如果都失败了，回退到 Chromium
                if not edge_launched:
                    logger.warning("未找到 Edge，回退到 Chromium")
                    self._browser = self._playwright.chromium.launch(
                        headless=False,
                        args=[
                            '--no-sandbox',
                            '--disable-dev-shm-usage',
                            '--disable-gpu',
                        ],
                    )

            self._page = self._browser.new_page()
            self._inited = True
            logger.info("🌐 浏览器已启动")
        except Exception as e:
            logger.error(f"初始化浏览器失败: {e}")
            self._close_browser()

    def _close_browser(self):
        """安全关闭浏览器。"""
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._browser = None
        self._page = None
        self._playwright = None
        self._inited = False

    def _call(self, func, *args, **kwargs):
        """在工作线程中调用函数并等待结果。"""
        result_event = threading.Event()
        result_container = []
        task = (func, args, kwargs, result_event, result_container)
        self._queue.put(task)
        result_event.wait(timeout=60)
        if not result_container:
            return ("error", "操作超时")
        return result_container[0]

    # ---- 供 BrowserAutomator 调用的公开方法 ----

    def _ensure_page(self):
        """确保浏览器页面可用。"""
        if not self._inited or self._page is None:
            self._init_browser()
            return self._page is not None
        try:
            _ = self._page.url
            return True
        except Exception:
            logger.info("页面已失效，重新初始化浏览器...")
            self._close_browser()
            self._init_browser()
            return self._page is not None

    def get_page(self):
        """获取当前页面。"""
        self._ensure_page()
        return self._page

    def get_all_pages(self):
        """获取所有页面。"""
        self._ensure_page()
        if not self._browser or not self._browser.contexts:
            return []
        return self._browser.contexts[0].pages

    def get_current_page(self):
        """获取当前活动页面（最新的）。"""
        pages = self.get_all_pages()
        return pages[-1] if pages else self._page

    def set_current_page(self, page):
        """设置当前活动页面。"""
        self._page = page

    def close(self):
        """关闭浏览器。"""
        self._close_browser()


class BrowserAutomator:
    """浏览器自动化控制器 - CDP 优先方案（单线程工作模式）。

    所有 Playwright 操作都在专用工作线程中执行，彻底解决跨线程问题。
    外部调用通过 _run() 方法将任务分派到工作线程执行。
    """

    def __init__(self):
        self._worker = None
        self._init_lock = threading.Lock()

    @property
    def available(self) -> bool:
        try:
            from playwright.sync_api import sync_playwright
            return True
        except ImportError:
            return False

    def _ensure_worker(self):
        """确保工作线程已启动。"""
        if self._worker is not None:
            return self._worker
        with self._init_lock:
            if self._worker is None:
                self._worker = _BrowserWorker()
                logger.info("浏览器工作线程已启动")
        return self._worker

    def _run(self, func, *args, **kwargs):
        """在工作线程中执行函数。"""
        worker = self._ensure_worker()
        return worker._call(func, *args, **kwargs)

    def _check_worker_result(self, result):
        """检查工作线程返回结果。"""
        if isinstance(result, tuple) and len(result) >= 2 and result[0] == "error":
            return False, str(result[1])
        return True, result

    # ---------------- 内部实现（在工作线程中执行） ----------------

    def _impl_open_url(self, url: str) -> Tuple[bool, str]:
        """打开 URL。"""
        page = self._worker.get_page()
        if page is None:
            return False, "浏览器未就绪"

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(1.0)
            title = page.title()
            current_url = page.url
            logger.info(f"📺 已打开: {title} ({current_url[:80]})")
            return True, f"已打开: {title}"
        except Exception as e:
            logger.error(f"打开 URL 失败: {e}")
            # 尝试恢复
            self._worker._close_browser()
            self._worker._init_browser()
            page = self._worker.get_page()
            if page is None:
                return False, "浏览器恢复失败"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(1.0)
                return True, f"已打开（恢复后）: {page.title()}"
            except Exception as e2:
                return False, f"打开失败: {e2}"

    def _impl_new_page(self, url: Optional[str] = None) -> Tuple[bool, str]:
        """打开新标签页。"""
        page = self._worker.get_page()
        if page is None:
            return False, "浏览器未就绪"
        try:
            context = page.context
            new_tab = context.new_page()
            if url:
                new_tab.goto(url, wait_until="domcontentloaded", timeout=30000)
            self._worker.set_current_page(new_tab)
            return True, "新标签页已打开"
        except Exception as e:
            logger.error(f"新标签页失败: {e}")
            return False, f"失败: {e}"

    def _impl_get_page_info(self) -> Dict[str, Any]:
        """获取当前页面信息。"""
        page = self._worker.get_page()
        if page is None:
            return {"error": "浏览器未就绪"}
        try:
            return {
                "url": page.url,
                "title": page.title(),
                "has_search": bool(page.query_selector('input[type="search"], input[name*="search"], input[name*="kw"]')),
            }
        except Exception as e:
            return {"error": str(e)}

    def _impl_get_page_text(self, max_length: int = 2000) -> Optional[str]:
        """获取页面主要文本内容。"""
        page = self._worker.get_page()
        if page is None:
            return None
        try:
            return page.evaluate("""(maxLen) => {
                document.querySelectorAll('script, style, nav, footer, header, aside').forEach(el => el.remove());
                return document.body.innerText.substring(0, maxLen);
            }""", max_length)
        except Exception as e:
            logger.error(f"获取页面文本失败: {e}")
            return None

    # ---------------- 网站搜索 ----------------

    SITE_SEARCH_URLS = {
        "bilibili": "https://search.bilibili.com/all?keyword={}",
        "知乎": "https://www.zhihu.com/search?type=content&q={}",
        "zhihu": "https://www.zhihu.com/search?type=content&q={}",
        "csdn": "https://so.csdn.net/so/search?q={}",
        "github": "https://github.com/search?q={}",
        "youtube": "https://www.youtube.com/results?search_query={}",
        "百度": "https://www.baidu.com/s?wd={}",
        "google": "https://www.google.com/search?q={}",
        "taobao": "https://s.taobao.com/search?q={}",
        "京东": "https://search.jd.com/Search?keyword={}",
    }

    SITE_DOMAINS = {
        "bilibili": "bilibili",
        "zhihu": "知乎",
        "csdn": "csdn",
        "github": "github",
        "youtube": "youtube",
        "baidu": "百度",
        "google": "google",
        "taobao": "taobao",
        "jd": "京东",
    }

    def _impl_detect_current_site(self) -> Optional[str]:
        """检测当前网站。"""
        page = self._worker.get_page()
        if page is None:
            return None
        try:
            url = page.url.lower()
            for domain, site_name in self.SITE_DOMAINS.items():
                if domain in url:
                    return site_name
        except Exception:
            pass
        return None

    def _impl_search_on_page(self, keyword: str) -> Tuple[bool, str]:
        """智能搜索：检测当前网站并跳转。"""
        page = self._worker.get_page()
        if page is None:
            return False, "浏览器未就绪"

        try:
            site = self._impl_detect_current_site()
            if site and site in self.SITE_SEARCH_URLS:
                import urllib.parse
                search_url = self.SITE_SEARCH_URLS[site].format(
                    urllib.parse.quote(keyword))
                logger.info(f"🔍 检测到网站 [{site}]，跳转: {search_url[:80]}")
                page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(2.0)
                return True, f"已在「{site}」搜索「{keyword}」"

            # 通用搜索
            selectors = [
                'input[type="search"]', 'input[name*="search"]',
                'input[name*="kw"]', 'input[placeholder*="搜索"]',
                'input[placeholder*="Search"]', 'input[type="text"]',
            ]
            for sel in selectors:
                try:
                    search_box = page.query_selector(sel)
                    if search_box:
                        search_box.click()
                        search_box.fill("")
                        search_box.fill(keyword)
                        search_box.press("Enter")
                        time.sleep(2.0)
                        return True, f"已搜索「{keyword}」"
                except Exception:
                    continue
            return False, "未找到搜索框，且无法识别当前网站"
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            return False, f"搜索失败: {e}"

    # ---------------- 元素操作 ----------------

    def _impl_click_element(self, description: str) -> Tuple[bool, str]:
        """通过描述点击页面元素。"""
        page = self._worker.get_page()
        if page is None:
            return False, "浏览器未就绪"

        desc = description.strip()
        page_count_before = len(self._worker.get_all_pages())

        clicked = False
        click_msg = ""

        # 1. 精确文本匹配
        try:
            locator = page.get_by_text(desc, exact=True)
            if locator.count() > 0:
                locator.first.click(timeout=2000)
                clicked = True
                click_msg = f"[CDP] 已点击「{desc}」"
        except Exception:
            pass

        # 2. 模糊文本匹配
        if not clicked:
            try:
                locator = page.get_by_text(desc, exact=False)
                if locator.count() > 0:
                    locator.first.click(timeout=2000)
                    clicked = True
                    click_msg = f"[CDP] 已模糊点击「{desc}」"
            except Exception:
                pass

        # 3. 按角色
        if not clicked:
            for role in ["button", "link", "menuitem", "tab"]:
                try:
                    locator = page.get_by_role(role, name=desc)
                    if locator.count() > 0:
                        locator.first.click(timeout=2000)
                        clicked = True
                        click_msg = f"[CDP] 已通过角色点击「{desc}」"
                        break
                except Exception:
                    continue

        # 4. 链接文本
        if not clicked:
            try:
                locator = page.get_by_role("link", name=desc)
                if locator.count() > 0:
                    locator.first.click(timeout=2000)
                    clicked = True
                    click_msg = f"[CDP] 已点击链接「{desc}」"
            except Exception:
                pass

        # 5. 第一个搜索结果
        if not clicked and ("第一个" in desc or "首个" in desc or "first" in desc.lower()):
            ok, msg = self._impl_click_first_search_result()
            if ok:
                return True, "[CDP] 已点击第一个搜索结果"
            clicked = True
            click_msg = msg

        if not clicked:
            return False, f"CDP 未找到「{desc}」元素"

        # 等待新标签页
        for _ in range(10):
            time.sleep(0.5)
            pages = self._worker.get_all_pages()
            if len(pages) > page_count_before:
                self._worker.set_current_page(pages[-1])
                return True, f"{click_msg}，已切换到新标签页"

        return True, click_msg

    def _impl_type_in_field(self, field_description: str, text: str) -> Tuple[bool, str]:
        """在输入框输入文本。"""
        page = self._worker.get_page()
        if page is None:
            return False, "浏览器未就绪"

        strategies = [
            lambda: page.get_by_label(field_description),
            lambda: page.get_by_placeholder(field_description),
            lambda: page.get_by_role("textbox", name=field_description),
        ]
        for strategy in strategies:
            try:
                locator = strategy()
                if locator.count() > 0:
                    locator.first.click()
                    locator.first.fill(text)
                    return True, f"[CDP] 已在「{field_description}」输入"
            except Exception:
                continue

        # 回退
        try:
            inputs = page.query_selector_all("input[type='text'], input[type='search'], input:not([type])")
            for inp in inputs:
                try:
                    placeholder = inp.get_attribute("placeholder") or ""
                    name = inp.get_attribute("name") or ""
                    if field_description.lower() in placeholder.lower() or field_description.lower() in name.lower():
                        inp.click()
                        inp.fill(text)
                        return True, "[CDP] 已输入"
                except Exception:
                    continue
        except Exception:
            pass
        return False, f"CDP 未找到「{field_description}」输入框"

    def _impl_click_first_search_result(self) -> Tuple[bool, str]:
        """点击第一个搜索结果。使用卡片级选择器确保点击的是搜索结果而非页面其他区域。"""
        page = self._worker.get_page()
        if page is None:
            return False, "浏览器未就绪"

        site = self._impl_detect_current_site()

        # B站专用：使用与 click_nth_result 相同的卡片定位逻辑
        if site == "bilibili":
            try:
                result = page.evaluate("""() => {
                    const cardSelectors = [
                        '.bili-video-card', '.video-list-item', '.search-video-card',
                        '[class*="video-card"]', '[class*="search-card"]',
                        '.card-box', '.video-item',
                    ];
                    let cards = [];
                    for (const sel of cardSelectors) {
                        const found = document.querySelectorAll(sel);
                        if (found.length > 0 && found.length < 200) {
                            cards = Array.from(found);
                            break;
                        }
                    }
                    if (cards.length === 0) {
                        return {success: false, reason: 'no_cards'};
                    }
                    // 从第一张卡片中提取第一个视频链接
                    const firstCard = cards[0];
                    const videoLinks = firstCard.querySelectorAll(
                        'a[href*="/video/BV"], a[href*="b23.tv"], a[href*="/video/"]'
                    );
                    for (const a of videoLinks) {
                        const href = a.href || '';
                        if (href && (href.includes('/video/BV') || href.includes('b23.tv'))) {
                            let title = (a.getAttribute('title') || a.getAttribute('aria-label') || '').trim();
                            if (!title) {
                                const ts = a.querySelector('[class*="title"], [class*="Title"], h3, h4');
                                if (ts) title = ts.innerText.trim();
                            }
                            a.click();
                            return {success: true, url: href, title: title};
                        }
                    }
                    // 回退：在第一张卡片中找任意视频相关链接
                    const allLinks = firstCard.querySelectorAll('a[href]');
                    for (const a of allLinks) {
                        const href = a.href || '';
                        if (href && (href.includes('bilibili.com') || href.includes('b23.tv'))) {
                            a.click();
                            return {success: true, url: href, title: ''};
                        }
                    }
                    return {success: false, reason: 'no_video_link_in_card'};
                }""")
                if result and result.get("success"):
                    title = result.get("title", "")
                    logger.info(f"🔍 B站点击第一个结果: url={result.get('url', '')[:80]} title={title[:30]}")
                    return True, f"已点击第一个视频「{title}」：{result.get('url', '')[:60]}"
                logger.info(f"B站点击第一个结果失败: {result}")
            except Exception as e:
                logger.error(f"B站点击第一个结果异常: {e}")

        # 通用选择器
        site_selectors = {
            "知乎": [".SearchResult-Card a", ".List-item a", "h2 a"],
            "csdn": [".result-item a", ".BlogItem-title a"],
            "github": [".repo-list-item a", ".search-result a"],
            "youtube": ["#video-title", "a[href*='/watch']"],
            "百度": [".result h3 a", ".c-container h3 a"],
            "google": ["#search-results a", ".g a[href]", "h3 a"],
        }
        if site and site in site_selectors:
            for sel in site_selectors[site]:
                try:
                    elem = page.query_selector(sel)
                    if elem:
                        elem.click()
                        return True, "已点击第一个结果"
                except Exception:
                    continue

        # 通用 JS 兜底
        try:
            result = page.evaluate("""() => {
                const selectors = [
                    '.search-result-item a', '.result-item a',
                    '.search-item a', 'h2 a', 'h3 a', '.card a[href]',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el) { el.click(); return true; }
                }
                const main = document.querySelector('main, article, [class*="content"], [class*="result"]');
                if (main) {
                    const link = main.querySelector('a[href]');
                    if (link) { link.click(); return true; }
                }
                return false;
            }""")
            if result:
                return True, "已点击第一个结果"
        except Exception:
            pass
        return False, "未找到搜索结果"

    def _impl_list_search_results(self, max_results: int = 20) -> Tuple[bool, str]:
        """列出搜索结果。"""
        page = self._worker.get_page()
        if page is None:
            return False, "浏览器未就绪"

        try:
            current_url = page.url
            if current_url == "about:blank" or not current_url:
                return False, "当前页面为空白页，请先使用 search_on_site 打开搜索结果页"
        except Exception as e:
            return False, f"获取页面信息失败: {e}"

        site = self._impl_detect_current_site()

        try:
            if site == "bilibili":
                results = self._impl_extract_bilibili_results(page, max_results)
            else:
                results = self._impl_extract_generic_results(page, max_results)

            if results and results.get("count", 0) > 0:
                lines = [f"【搜索结果列表】共 {results['count']} 条，显示前 {min(max_results, results['count'])} 条：\n"]
                for r in results.get("results", []):
                    idx = r["index"]
                    title = r.get("title", "未知标题")
                    author = r.get("author", "")
                    duration = r.get("duration", "")
                    meta_parts = [f"#{idx}"]
                    if author:
                        meta_parts.append(author)
                    if duration:
                        meta_parts.append(duration)
                    meta = " | ".join(meta_parts)
                    lines.append(f"  {meta}")
                    lines.append(f"    标题: {title}")
                    lines.append(f"    链接: {r.get('url', '')[:80]}")
                    lines.append("")
                return True, "\n".join(lines)
            else:
                return False, "未找到搜索结果，请确保当前页面是搜索结果页"
        except Exception as e:
            logger.error(f"列出搜索结果失败: {e}")
            return False, f"列出搜索结果失败: {e}"

    def _impl_extract_bilibili_results(self, page, max_results: int) -> Optional[Dict]:
        """从B站提取视频列表。"""
        return page.evaluate("""(maxN) => {
            const cardSelectors = [
                '.bili-video-card', '.video-list-item', '.search-video-card',
                '[class*="video-card"]', '[class*="search-card"]',
                '.card-box', '.video-item',
            ];
            const results = [];
            const seen = new Set();
            let cards = [];
            for (const sel of cardSelectors) {
                const found = document.querySelectorAll(sel);
                if (found.length > 0 && found.length < 200) {
                    cards = Array.from(found);
                    break;
                }
            }
            if (cards.length === 0) {
                const allLinks = document.querySelectorAll('a[href*="/video/BV"], a[href*="b23.tv"]');
                cards = Array.from(allLinks).map(a => a.closest('[class*="card"], [class*="item"], li, div') || a);
            }
            for (const card of cards) {
                if (results.length >= maxN) break;
                const videoLinks = card.querySelectorAll('a[href*="/video/BV"], a[href*="b23.tv"]');
                for (const a of videoLinks) {
                    if (results.length >= maxN) break;
                    const href = a.href || '';
                    if (!href || seen.has(href)) continue;
                    seen.add(href);
                    let title = (a.getAttribute('title') || a.getAttribute('aria-label') || '').trim();
                    if (!title) {
                        for (const sel of ['[class*="title"]', '[class*="Title"]', '[class*="name"]', 'h3', 'h4']) {
                            const el = a.querySelector(sel);
                            if (el && el.innerText && el.innerText.trim().length >= 2) {
                                title = el.innerText.trim();
                                break;
                            }
                        }
                    }
                    if (!title) title = (a.innerText || '').trim().split(/\\s+/)[0];
                    if (!title || title.length < 2) continue;
                    results.push({
                        index: results.length + 1,
                        title: title.substring(0, 100),
                        duration: '',
                        url: href.substring(0, 150),
                    });
                }
            }
            return {count: results.length, results: results};
        }""", max_results)

    def _impl_extract_generic_results(self, page, max_results: int) -> Optional[Dict]:
        """从通用页面提取结果。"""
        return page.evaluate("""(maxN) => {
            const selectors = [
                '.search-result-item a', '.result-item a',
                '.search-item a', '.video-card a', 'h2 a', 'h3 a'
            ];
            const seen = new Set();
            const results = [];
            for (const sel of selectors) {
                const elems = document.querySelectorAll(sel);
                for (const el of elems) {
                    const href = el.href || '';
                    if (!href || seen.has(href) || href.startsWith('#') || href.startsWith('javascript:')) continue;
                    const title = (el.innerText || el.getAttribute('title') || '').trim();
                    if (!title || title.length < 3) continue;
                    seen.add(href);
                    results.push({
                        index: results.length + 1,
                        title: title.substring(0, 80),
                        url: href.substring(0, 120),
                    });
                    if (results.length >= maxN) break;
                }
                if (results.length >= maxN) break;
            }
            return {count: results.length, results: results};
        }""", max_results)

    def _impl_click_nth_result(self, n: int) -> Tuple[bool, str]:
        """点击第 n 个搜索结果。"""
        page = self._worker.get_page()
        if page is None:
            return False, "浏览器未就绪"

        site = self._impl_detect_current_site()
        page_count_before = len(self._worker.get_all_pages())
        nth = max(1, n)

        try:
            if site == "bilibili":
                result = page.evaluate("""(idx) => {
                    const cardSelectors = [
                        '.bili-video-card', '.video-list-item', '.search-video-card',
                        '[class*="video-card"]', '[class*="search-card"]',
                        '.card-box', '.video-item',
                    ];
                    let cards = [];
                    for (const sel of cardSelectors) {
                        const found = document.querySelectorAll(sel);
                        if (found.length > 0 && found.length < 200) {
                            cards = Array.from(found);
                            break;
                        }
                    }
                    const results = [];
                    const seen = new Set();
                    for (const card of cards) {
                        if (results.length >= idx) break;
                        const videoLinks = card.querySelectorAll('a[href*="/video/BV"], a[href*="b23.tv"]');
                        for (const a of videoLinks) {
                            if (results.length >= idx) break;
                            const href = a.href || '';
                            if (!href || seen.has(href)) continue;
                            seen.add(href);
                            let title = (a.getAttribute('title') || a.getAttribute('aria-label') || '').trim();
                            if (!title) {
                                const ts = a.querySelector('[class*="title"], [class*="Title"], h3, h4');
                                if (ts) title = ts.innerText.trim();
                            }
                            if (!title) title = (a.innerText || '').trim().split(/\\s+/)[0];
                            results.push({title: title.substring(0, 100), url: href, a: a});
                        }
                    }
                    if (results.length >= idx) {
                        results[idx - 1].a.click();
                        return {success: true, title: results[idx - 1].title};
                    }
                    return {success: false, total: results.length};
                }""", nth)
            else:
                result = page.evaluate("""(idx) => {
                    const selectors = [
                        '.search-result-item a', '.result-item a',
                        '.search-item a', '.video-card a', 'h2 a', 'h3 a',
                    ];
                    let count = 0;
                    for (const sel of selectors) {
                        const elems = document.querySelectorAll(sel);
                        for (const el of elems) {
                            if (!el.href || el.href.startsWith('#') || el.href.startsWith('javascript:')) continue;
                            const title = (el.innerText || '').trim();
                            if (!title || title.length < 3) continue;
                            count++;
                            if (count === idx) {
                                el.click();
                                return {success: true, title: title.substring(0, 80)};
                            }
                        }
                    }
                    return {success: false, total: count};
                }""", nth)

            if result and result.get("success"):
                for _ in range(10):
                    time.sleep(0.5)
                    pages = self._worker.get_all_pages()
                    if len(pages) > page_count_before:
                        self._worker.set_current_page(pages[-1])
                        return True, f"[CDP] 已点击第{n}个结果「{result.get('title', '')}」，已切换到新标签页"
                return True, f"[CDP] 已点击第{n}个结果「{result.get('title', '')}」"
            else:
                total = result.get("total", 0) if result else 0
                return False, f"未找到第{n}个结果（当前共{total}个）"
        except Exception as e:
            logger.error(f"点击第{n}个结果失败: {e}")
            return False, f"点击第{n}个结果失败: {e}"

    def _impl_click_result_by_keyword(self, keyword: str) -> Tuple[bool, str]:
        """按关键词点击搜索结果。"""
        page = self._worker.get_page()
        if page is None:
            return False, "浏览器未就绪"

        site = self._impl_detect_current_site()
        page_count_before = len(self._worker.get_all_pages())
        kw = keyword.strip().lower()

        try:
            if site == "bilibili":
                result = page.evaluate("""(kw) => {
                    const cardSelectors = [
                        '.bili-video-card', '.video-list-item', '.search-video-card',
                        '[class*="video-card"]', '[class*="search-card"]',
                        '.card-box', '.video-item',
                    ];
                    let cards = [];
                    for (const sel of cardSelectors) {
                        const found = document.querySelectorAll(sel);
                        if (found.length > 0 && found.length < 200) {
                            cards = Array.from(found);
                            break;
                        }
                    }
                    const seen = new Set();
                    for (const card of cards) {
                        const videoLinks = card.querySelectorAll('a[href*="/video/BV"], a[href*="b23.tv"]');
                        for (const a of videoLinks) {
                            const href = a.href || '';
                            if (!href || seen.has(href)) continue;
                            seen.add(href);
                            let title = (a.getAttribute('title') || a.getAttribute('aria-label') || '').trim();
                            if (!title) {
                                const ts = a.querySelector('[class*="title"], [class*="Title"], h3, h4');
                                if (ts) title = ts.innerText.trim();
                            }
                            if (!title) title = (a.innerText || '').trim().split(/\\s+/)[0];
                            if (title && kw && title.toLowerCase().includes(kw)) {
                                a.click();
                                return {success: true, title: title.substring(0, 100)};
                            }
                        }
                    }
                    return {success: false};
                }""", kw)
            else:
                result = page.evaluate("""(kw) => {
                    const selectors = [
                        '.search-result-item a', '.result-item a',
                        '.search-item a', '.video-card a', 'h2 a', 'h3 a',
                    ];
                    for (const sel of selectors) {
                        const elems = document.querySelectorAll(sel);
                        for (const el of elems) {
                            const title = (el.innerText || '').trim().toLowerCase();
                            if (title && kw && title.includes(kw) && el.href) {
                                el.click();
                                return {success: true, title: title.substring(0, 80)};
                            }
                        }
                    }
                    return {success: false};
                }""", kw)

            if result and result.get("success"):
                for _ in range(10):
                    time.sleep(0.5)
                    pages = self._worker.get_all_pages()
                    if len(pages) > page_count_before:
                        self._worker.set_current_page(pages[-1])
                        return True, f"[CDP] 已点击包含「{keyword}」的结果，已切换到新标签页"
                return True, f"[CDP] 已点击包含「{keyword}」的结果"
            else:
                return False, f"未找到标题包含「{keyword}」的结果"
        except Exception as e:
            logger.error(f"按关键词点击失败: {e}")
            return False, f"按关键词点击失败: {e}"

    def _impl_take_screenshot(self, save_path: str) -> Optional[str]:
        """截图。"""
        page = self._worker.get_page()
        if page is None:
            return None
        try:
            page.screenshot(path=save_path, full_page=False)
            return save_path
        except Exception as e:
            logger.error(f"截图失败: {e}")
            return None

    def _impl_get_browser_context(self) -> Dict[str, Any]:
        """获取浏览器上下文。"""
        page = self._worker.get_page()
        if page is None:
            return {"available": False, "reason": "浏览器未启动"}
        try:
            info = self._impl_get_page_info()
            elements_info = page.evaluate("""() => {
                const results = [];
                const selectors = 'button, a, input, select, [role="button"], [role="link"]';
                document.querySelectorAll(selectors).forEach(el => {
                    const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
                    if (text && text.length < 50) {
                        results.push({tag: el.tagName.toLowerCase(), text: text});
                    }
                });
                return results.slice(0, 30);
            }""")
            return {
                "available": True,
                "url": info.get("url", ""),
                "title": info.get("title", ""),
                "elements": elements_info,
            }
        except Exception as e:
            return {"available": False, "reason": str(e)}

    # ---------------- 公开 API（线程安全入口） ----------------

    def close(self):
        """关闭浏览器。"""
        if self._worker:
            self._run(self._worker.close)
            self._worker = None

    def get_page(self):
        """获取当前页面对象。"""
        return self._run(lambda: self._worker.get_page())

    def open_url(self, url: str) -> Tuple[bool, str]:
        return self._run(self._impl_open_url, url)

    def new_page(self, url: Optional[str] = None) -> Tuple[bool, str]:
        return self._run(self._impl_new_page, url)

    def get_page_info(self) -> Dict[str, Any]:
        return self._run(self._impl_get_page_info)

    def get_page_text(self, max_length: int = 2000) -> Optional[str]:
        return self._run(self._impl_get_page_text, max_length)

    def search_on_page(self, keyword: str, search_selector: str = None) -> Tuple[bool, str]:
        return self._run(self._impl_search_on_page, keyword)

    def bilibili_search(self, keyword: str, order: str = "") -> Tuple[bool, str]:
        url = f"https://search.bilibili.com/all?keyword={keyword}"
        if order:
            url += f"&order={order}"
        return self.open_url(url)

    def zhihu_search(self, keyword: str) -> Tuple[bool, str]:
        url = f"https://www.zhihu.com/search?type=content&q={keyword}"
        return self.open_url(url)

    def csdn_search(self, keyword: str) -> Tuple[bool, str]:
        url = f"https://so.csdn.net/so/search?q={keyword}"
        return self.open_url(url)

    def click_element(self, description: str) -> Tuple[bool, str]:
        return self._run(self._impl_click_element, description)

    def type_in_field(self, field_description: str, text: str) -> Tuple[bool, str]:
        return self._run(self._impl_type_in_field, field_description, text)

    def find_and_click_first_result(self) -> Tuple[bool, str]:
        return self._run(self._impl_click_first_search_result)

    def list_search_results(self, max_results: int = 20) -> Tuple[bool, str]:
        return self._run(self._impl_list_search_results, max_results)

    def click_nth_result(self, n: int) -> Tuple[bool, str]:
        return self._run(self._impl_click_nth_result, n)

    def click_result_by_keyword(self, keyword: str) -> Tuple[bool, str]:
        return self._run(self._impl_click_result_by_keyword, keyword)

    def take_screenshot(self, save_path: str = "temp/browser_screenshot.png") -> Optional[str]:
        return self._run(self._impl_take_screenshot, save_path)

    def smart_click(self, description: str) -> Tuple[bool, str]:
        return self.click_element(description)

    def smart_type(self, field_name: str, text: str) -> Tuple[bool, str]:
        return self.type_in_field(field_name, text)

    def get_browser_context(self) -> Dict[str, Any]:
        return self._run(self._impl_get_browser_context)

    def is_browser_url(self, url: str) -> bool:
        url_lower = url.lower()
        return any(kw in url_lower for kw in [
            "http://", "https://", "www.", ".com", ".cn", ".net",
            "bilibili", "知乎", "csdn", "github", "taobao", "jd.com",
        ])
