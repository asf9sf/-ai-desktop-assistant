import re
import json
import threading
import time
import logging
from typing import Dict, Any, Tuple, Optional, List

from .llm_module import LLMClient
from .app_controller import AppController
from .browser_search import BrowserSearcher
from .memory_system import MemSkillManager
from .desktop_automation import DesktopAutomator
from .browser_automation import BrowserAutomator
from .vision_automation import VisionAutomator
from .file_operation_module import FileOperator
from .scheduler_module import Scheduler
from .emotion_module import EmotionEngine, Personality, Appraisal, analyze_user_input, build_emotion_prompt
from .music import MusicPlayer
from .arxiv_searcher import ArxivSearcher

logger = logging.getLogger(__name__)


class Agent:
    """智能体核心：ReAct 多步推理 + 工具调度 + 长期记忆。

    架构：
      1. 快速正则意图识别（单工具指令直接执行，绕过 LLM）
      2. 复杂/多步任务进入 ReAct 循环：思考 → 行动 → 观察 → 再思考
         - 工具执行结果（observation）回喂 LLM，决定下一步
         - 支持最多 MAX_REACT_ITERATIONS 轮多工具串行调度
         - LLM 判定 finish 时给出最终回复（基于全部观察）
    """

    # ReAct 最大迭代轮数（防止死循环）
    MAX_REACT_ITERATIONS = 5

    # 需要自动前置 screen_context 的桌面视觉工具
    DESKTOP_VISION_TOOLS = {
        "vision_click", "vision_type", "vision_send_message",
        "vision_hover", "vision_drag", "vision_scroll",
        "vision_click_first_result", "vision_find_and_open",
    }

    # 需要自动后置 verify_action 的关键操作工具
    CRITICAL_OPERATION_TOOLS = {
        "vision_click", "vision_type", "vision_send_message",
        "open_app", "open_url", "vision_find_and_open",
    }

    # 工具注册表（自描述，供 LLM 选择）
    TOOL_DEFINITIONS = [
        {
            "name": "open_app",
            "description": "打开用户电脑上的软件/程序/文件",
            "params": {"target": "软件名、程序名或文件名（去掉「打开」等动词）"},
        },
        {
            "name": "close_app",
            "description": "关闭正在运行的软件/进程",
            "params": {"target": "进程名或软件名"},
        },
        {
            "name": "search_and_open",
            "description": "搜索网页并自动打开最匹配的一条结果（通用搜索引擎搜索）",
            "params": {"target": "搜索关键词"},
        },
        {
            "name": "search",
            "description": "只搜索不打开，返回搜索结果列表",
            "params": {"target": "搜索关键词"},
        },
        {
            "name": "search_on_site",
            "description": "【站内搜索首选】在指定网站（B站、知乎、CSDN、GitHub、百度、YouTube等）直接搜索关键词并跳转到搜索结果页。一步完成，比打开网站再手动输入搜索框更可靠更快。",
            "params": {"site": "网站名（B站/知乎/CSDN/github/百度等）", "keyword": "搜索关键词"},
        },
        {
            "name": "open_url",
            "description": "在浏览器中打开指定URL链接。CDP 可用时自动走 CDP 路径（精准 DOM 操作），不可用时回退到系统浏览器。",
            "params": {"target": "完整URL链接（以http://或https://开头）"},
        },
        # ---------- CDP 浏览器自动化（第3层） ----------
        {
            "name": "cdp_open_url",
            "description": "使用 CDP (Chrome DevTools Protocol) 在受控浏览器中打开URL。比系统浏览器+视觉定位方案更精准、更快。",
            "params": {"target": "完整URL链接"},
        },
        {
            "name": "cdp_click",
            "description": "使用 CDP 在当前浏览器页面中点击元素。直接操作 DOM，比 VLM 视觉定位精准100倍。优先于 vision_click 使用。",
            "params": {"target": "要点击的元素描述（如 搜索按钮、第一个结果、登录按钮）"},
        },
        {
            "name": "cdp_type",
            "description": "使用 CDP 在浏览器页面的输入框中输入文字。直接操作 DOM，精准可靠。",
            "params": {"target": "输入框描述||要输入的文字", "format": "搜索框||多模态大模型"},
        },
        {
            "name": "cdp_search_page",
            "description": "使用 CDP 在当前浏览器页面的搜索框中输入关键词并搜索。",
            "params": {"target": "搜索关键词"},
        },
        {
            "name": "cdp_page_content",
            "description": "使用 CDP 获取当前浏览器页面的主要文本内容。",
            "params": {"target": "可选，聚焦描述"},
        },
        {
            "name": "cdp_click_first_result",
            "description": "使用 CDP 在当前浏览器搜索结果页中点击第一个结果。比 vision_click_first_result 更精准。",
            "params": {"target": "可选，结果描述"},
        },
        {
            "name": "cdp_list_results",
            "description": "使用 CDP 列出当前浏览器搜索结果页的所有结果（编号、标题、作者、时长等），方便选择要点击的结果。",
            "params": {"target": "可选，最大结果数，默认20"},
        },
        {
            "name": "cdp_click_nth_result",
            "description": "使用 CDP 点击搜索结果页中第 N 个结果（从1开始计数）。例如点击第3个视频。",
            "params": {"target": "结果编号（数字，从1开始）"},
        },
        {
            "name": "cdp_click_by_keyword",
            "description": "使用 CDP 点击标题包含指定关键词的搜索结果。例如点击标题包含'教程'的视频。",
            "params": {"target": "要匹配的关键词（如'教程'、'讲解'等）"},
        },
        # ---------- UIA 桌面自动化（第1层） ----------
        {
            "name": "uia_screen_context",
            "description": "使用 Windows UIA 获取当前前台窗口的结构化信息：窗口标题、控件列表（按钮、输入框、菜单等）。比 vision_screenshot 更快更准。",
            "params": {"target": "可选，聚焦描述"},
        },
        {
            "name": "uia_click",
            "description": "使用 Windows UIA 在当前桌面窗口中点击控件。直接操作控件树，毫秒级响应、100%准确，远优于视觉定位。仅适用于支持 UIA 的应用（Office、记事本、设置等标准应用）。",
            "params": {"target": "控件名称（如 保存、关闭、搜索、确定）"},
        },
        {
            "name": "uia_type",
            "description": "使用 Windows UIA 在指定输入框中输入文字。直接操作控件，精准可靠。",
            "params": {"target": "输入框名称||要输入的文字", "format": "搜索框||你好"},
        },
        {
            "name": "desktop_click",
            "description": "在桌面应用中点击按钮或控件（搜索框、联系人、发送按钮等）",
            "params": {"window": "窗口标题关键字（如QQ、微信）", "control": "要点击的控件文本（如搜索、发送、联系人名）"},
        },
        {
            "name": "desktop_type",
            "description": "向当前焦点窗口输入文字（支持中文，通过剪贴板粘贴）",
            "params": {"text": "要输入的文字内容"},
        },
        {
            "name": "desktop_send_message",
            "description": "在指定桌面聊天应用中搜索联系人并发送消息",
            "params": {"app": "聊天软件名称（如QQ、微信、钉钉）", "contact": "联系人姓名/代号", "message": "要发送的消息内容"},
        },
        {
            "name": "desktop_hotkey",
            "description": "向当前窗口发送键盘组合键",
            "params": {"key1": "第一个键（如ctrl、shift、alt）", "key2": "第二个键（如v、c、a、s）"},
        },
        {
            "name": "vision_click",
            "description": "使用视觉模型截屏识别并点击屏幕上的元素（如搜索框、按钮、联系人等）。适用于 pywinauto 无法识别控件的复杂应用（如QQ）。",
            "params": {"target": "要点击的元素描述（如搜索框、发送按钮、联系人清风）"},
        },
        {
            "name": "vision_type",
            "description": "截屏识别输入框并输入文字。先定位到输入框，再输入文字。",
            "params": {"target": "输入框描述||要输入的文字", "format": "搜索框||你好"},
        },
        {
            "name": "vision_send_message",
            "description": "使用视觉识别在桌面聊天软件中发送消息。截屏识别搜索框和发送按钮，自动完成搜索联系人→输入消息→点击发送的全流程。",
            "params": {"target": "软件名||联系人||消息", "format": "QQ||0110||你好"},
        },
        {
            "name": "vision_screenshot",
            "description": "截取当前屏幕并让视觉模型分析界面状态，返回识别到的元素列表",
            "params": {"target": "要分析的内容描述（可留空，返回界面概览）"},
        },
        {
            "name": "vision_analyze",
            "description": "截屏并让视觉模型回答关于屏幕内容的问题。适用于用户问'你看到了什么'、'这是什么'等视觉询问",
            "params": {"target": "用户的视觉问题（如：这是什么软件、屏幕上有什么）"},
        },
        {
            "name": "vision_click_first_result",
            "description": "在浏览器搜索结果页面中，用 VLM 找到并点击第一个搜索结果的标题链接。适用于'在XX搜索YY并打开第一篇/第一个'等场景，必须先用 search_on_site 或 search 打开了搜索结果页后再调用",
            "params": {"target": "结果描述（可选，可留空或填'第一个搜索结果'）"},
        },
        {
            "name": "vision_find_and_open",
            "description": "看看屏幕后用 VLM 定位并双击打开屏幕上的某个元素（图标、按钮、文件等）。适用于'看看屏幕，然后打开XX'、'帮我打开屏幕上的XX'等需要先看屏幕再操作的场景。会先截屏，再用 VLM 找到目标位置，最后双击打开。",
            "params": {"target": "要打开的元素描述（如 微信图标、记事本、回收站、文件管理器）"},
        },
        {
            "name": "screen_context",
            "description": "快速获取当前屏幕的结构化概览：活动窗口、应用类型、关键UI元素、当前状态。在执行操作前先了解屏幕上有什么。",
            "params": {"target": "可选，聚焦描述（如：浏览器地址栏、微信对话、文件管理器）"},
        },
        {
            "name": "verify_action",
            "description": "操作后验证：截屏检查上一步操作是否成功执行，返回成功/失败判断和详情。在执行关键操作（如点击、输入、打开页面）后使用。",
            "params": {"target": "上一步操作描述||期望结果，格式如：点击了搜索按钮||搜索结果应出现"},
        },
        {
            "name": "watch_screen",
            "description": "持续监控屏幕，等待指定条件出现后返回。适用于等待下载完成、等待页面加载、等待邮件到达等场景。最多等待30秒。",
            "params": {"target": "等待的条件描述，如：下载完成、页面加载完毕、有新消息"},
        },
        # ---------- 文件操作 ----------
        {
            "name": "read_file",
            "description": "读取文本文件内容（支持 txt、md、log、json 等纯文本文件）",
            "params": {"target": "文件的完整路径或含驱动器号的路径，如 C:\\Users\\xxx\\Documents\\report.txt"},
        },
        {
            "name": "write_file",
            "description": "将文本内容写入文件（覆盖写入）。如果文件不存在会自动创建",
            "params": {"target": "文件路径||要写入的文本内容", "format": "C:\\Users\\out.txt||Hello World"},
        },
        {
            "name": "read_excel",
            "description": "读取 Excel 文件（.xlsx）并返回表格内容，支持指定工作表名",
            "params": {"target": "Excel 文件路径，如 C:\\data\\report.xlsx"},
        },
        {
            "name": "excel_stats",
            "description": "对 Excel 文件进行统计分析：计算每列的数值统计（总和、平均、最大、最小等）",
            "params": {"target": "Excel 文件路径"},
        },
        {
            "name": "read_csv",
            "description": "读取 CSV 文件内容",
            "params": {"target": "CSV 文件路径"},
        },
        {
            "name": "create_word",
            "description": "生成 Word 文档（.docx），支持标题、多段落文本和表格数据",
            "params": {"target": "保存路径||标题||段落文本（用 ||| 分隔）|||表格行（用 ; 分隔每行，| 分隔单元格）", "format": "C:\\report.docx||周报||第一段内容|||第二段内容|||姓名|年龄\\n张三|25"},
        },
        {
            "name": "list_files",
            "description": "列出指定文件夹中的文件，支持按扩展名过滤和递归搜索",
            "params": {"target": "文件夹路径||过滤模式（可选，如 *.jpg）", "format": "C:\\Users\\Downloads||*.pdf"},
        },
        {
            "name": "search_files",
            "description": "在文件夹中按关键字搜索文件名",
            "params": {"target": "文件夹路径||关键字", "format": "C:\\Users\\Documents||合同"},
        },
        {
            "name": "organize_by_date",
            "description": "将文件夹中的文件按修改日期整理到 YYYY-MM 子文件夹中",
            "params": {"target": "要整理的文件夹路径"},
        },
        {
            "name": "organize_by_type",
            "description": "将文件夹中的文件按类型（图片、文档、表格、视频等）分类整理",
            "params": {"target": "要整理的文件夹路径"},
        },
        {
            "name": "file_info",
            "description": "获取文件的详细信息（大小、创建时间、修改时间等）",
            "params": {"target": "文件路径"},
        },
        # ---------- 定时任务 ----------
        {
            "name": "add_schedule",
            "description": "创建定时任务。支持三种方式：1) cron表达式(分 时 日 月 周) 如 '0 8 * * *' 表示每天早上8点；2) 间隔分钟数 如 '30' 表示每30分钟；3) 一次性执行时间 如 '2026-08-07T14:00:00'",
            "params": {"target": "任务名称||Cron表达式或分钟数或ISO时间||要执行的指令（给智能体的prompt）", "format": "早间播报||0 8 * * *||播报今天的天气和日程"},
        },
        {
            "name": "list_schedules",
            "description": "列出所有定时任务及其状态",
            "params": {"target": "无参数，填空字符串即可"},
        },
        {
            "name": "remove_schedule",
            "description": "删除定时任务",
            "params": {"target": "要删除的任务ID"},
        },
        {
            "name": "toggle_schedule",
            "description": "启用或禁用定时任务",
            "params": {"target": "任务ID||enable 或 disable", "format": "abc123||disable"},
        },
        {
            "name": "run_schedule_now",
            "description": "立即手动触发执行某个定时任务",
            "params": {"target": "任务ID"},
        },
        # ---------- 音乐 ----------
        {
            "name": "play_music",
            "description": "点歌并播放。支持多平台搜索，自动下载并本地播放。",
            "params": {"target": "歌曲名或歌手名，可带平台名（如：周杰伦、网易云||晴天）"},
        },
        {
            "name": "search_music",
            "description": "搜索音乐，返回搜索结果列表，不自动播放",
            "params": {"target": "歌曲名或歌手名，可带平台（如：网易云||晴天）"},
        },
        {
            "name": "list_music",
            "description": "列出当前歌单中的所有歌曲",
            "params": {"target": "填空字符串"},
        },
        {
            "name": "music_control",
            "description": "音乐播放控制：暂停、继续、上一首、下一首、停止",
            "params": {"target": "pause/resume/next/prev/stop"},
        },
        {
            "name": "music_lyrics",
            "description": "查询歌曲歌词",
            "params": {"target": "歌曲名"},
        },
        # ---------- arXiv 论文搜索 ----------
        {
            "name": "search_arxiv",
            "description": "在 arXiv 学术论文库中搜索最新论文，返回标题、作者、摘要和PDF链接",
            "params": {"target": "搜索关键词，可带分类（如：cs.AI||大语言模型推理 或 人工智能||扩散模型）"},
        },
        {
            "name": "arxiv_paper_detail",
            "description": "查询指定 arXiv 论文的详细信息",
            "params": {"target": "arXiv ID"},
        },
        {
            "name": "arxiv_author_search",
            "description": "按作者名搜索该作者的最新论文",
            "params": {"target": "作者姓名"},
        },
        {
            "name": "finish",
            "description": "任务完成或无需调用工具，给出最终回复",
            "params": {"answer": "给用户的最终回复（基于历史和观察结果）"},
        },
    ]

    # 复杂动作指示词——出现这些词时跳过快速路径，进入 ReAct 多步循环
    COMPLEX_ACTION_PATTERN = re.compile(
        r"(然后|接着|再|之后|并|并且|同时|以及|"
        r"发|发送|写|编辑|分享|上传|下载|整理|分析|总结|告诉|提醒|通知|"
        r"查找|找出|筛选|排序|合并|对比|计算|翻译)"
    )

    # 视觉询问检测——触发截屏分析
    VISION_QUERY_PATTERN = re.compile(
        r"(你.*?看到|看见|看看|看一下|看.*?什么|"
        r"帮我看|给我看|让我看|看看这|这是什么|这是什么东西|"
        r"分析.*?屏幕|屏幕.*?分析|识别.*?图片|图片.*?识别|"
        r"当前.*?画面|画面.*?什么|显示.*?什么|"
        r"截个图|截图看看|看屏幕|看一下屏幕|"
        r"帮我看看|你能看到|你看到了|你看见了|"
        r"屏幕.*?有什么|屏幕.*?是什么|显示.*?什么)"
    )

    # 看看屏幕然后打开——复合指令，走 ReAct 多步循环
    VISION_FIND_AND_OPEN_PATTERN = re.compile(
        r"(看看|看一下|看|瞧瞧).*?(屏幕|桌面|界面).*?"
        r"(?:(?:然后|之后|接着|再)\s*)?"
        r"(?:帮我|请)?\s*"
        r"(打开|开启|启动|运行|点开|进去)"
    )

    def __init__(self, llm: LLMClient, app_ctrl: AppController, searcher: BrowserSearcher,
                 memory: MemSkillManager = None, automator: DesktopAutomator = None,
                 vision_automator: VisionAutomator = None,
                 browser_automator: BrowserAutomator = None,
                 file_operator: FileOperator = None, scheduler: Scheduler = None,
                 emotion_engine: EmotionEngine = None):
        self.llm = llm
        self.app_ctrl = app_ctrl
        self.searcher = searcher
        self.memory = memory
        self.automator = automator or DesktopAutomator()
        self.browser = browser_automator or BrowserAutomator()
        self.vision = vision_automator or VisionAutomator(llm)
        self.file_op = file_operator or FileOperator()
        self.scheduler = scheduler
        self.emotion = emotion_engine or EmotionEngine()
        self.music = MusicPlayer()
        self.arxiv = ArxivSearcher()
        self._history: List[Dict[str, str]] = []
        self._session_id = f"sess_{threading.get_ident()}"
        self.screen_perception_enabled = True
        self.emotion_enabled = True
        self._last_observations: List[Dict[str, Any]] = []  # 跨对话的操作历史
        self._last_action_info: Dict[str, Any] = {}  # 上次操作的结果信息

    def clear_history(self):
        self._history.clear()
        self._last_observations = []
        self._last_action_info = {}

    def _build_cross_dialogue_context(self) -> str:
        """构建跨对话上下文摘要，让 LLM 知道之前做了什么。"""
        if not self._last_observations:
            return ""

        lines = ["【上一轮操作摘要】（供参考，延续之前的任务上下文）"]
        for obs in self._last_observations:
            tool_name = obs.get("tool", "")
            target = obs.get("target", "")
            result = obs.get("observation", "")[:200]
            if tool_name and result:
                lines.append(f"  - 执行了 {tool_name}({target}): {result}")

        # 检查是否有 CDP 浏览器操作
        cdp_actions = [o for o in self._last_observations
                      if "cdp" in o.get("tool", "").lower() or "CDP" in o.get("observation", "")]
        if cdp_actions:
            lines.append("\n  💡 CDP 浏览器在之前的操作中被使用过，可能仍然活跃。如果需要在浏览器中操作，优先使用 CDP 工具。")

        # 检查是否有搜索结果列表
        list_results = [o for o in self._last_observations if "cdp_list_results" in o.get("tool", "")]
        if list_results:
            lines.append("\n  💡 之前列出过搜索结果，用户可能要选择其中某个结果。请使用 cdp_click_nth_result(编号) 或 cdp_click_by_keyword(关键词)。")

        return "\n".join(lines)

    def history(self) -> List[Dict[str, str]]:
        return list(self._history)

    # ---------------- 情绪引擎接口 ----------------

    def get_emotion_state(self) -> Dict[str, Any]:
        """获取当前情绪状态摘要（供 UI 显示）。"""
        if not self.emotion or not self.emotion_enabled:
            return {"disabled": True}
        try:
            return self.emotion.get_state_summary()
        except Exception:
            return {"error": "情绪引擎异常"}

    def set_emotion_enabled(self, enabled: bool):
        """启用/禁用情绪引擎。"""
        self.emotion_enabled = enabled

    def reset_emotion(self):
        """重置情绪状态。"""
        if self.emotion:
            self.emotion.reset()

    # ---------------- 主入口 ----------------

    def process(self, text: str, stream_callback=None) -> Tuple[str, Dict[str, Any]]:
        """
        处理用户输入，返回 (最终回复文本, 动作详情字典)。
        动作详情可能包含：action=chat/open_app/close_app/search/best_open

        路由策略：
          - 视觉询问（截屏分析）→ 快速截屏 + VLM 分析
          - 单工具指令（打开/关闭/搜索）→ 快速执行，绕过 LLM
          - 多步/复合任务（含连接词或复杂动作词）→ ReAct 循环
          - 纯聊天 → ReAct 循环（首轮即 finish）
        """
        text = text.strip()
        if not text:
            return "请输入内容", {"action": "none"}

        # ===== 情绪引擎：分析用户输入并更新情绪状态 =====
        if self.emotion_enabled and self.emotion:
            try:
                appraisal = analyze_user_input(text)
                self.emotion.tick(appraisal)
                logger.debug(f"情绪引擎更新: {self.emotion.get_state_summary()}")
            except Exception as e:
                logger.warning(f"情绪引擎处理异常: {e}")

        # 0.1 看看屏幕然后打开——复合指令，走 ReAct 多步循环
        if self.VISION_FIND_AND_OPEN_PATTERN.search(text):
            logger.info(f"🔍 检测到「看看屏幕+打开」复合指令，走 ReAct 循环")
            # 提取打开目标：去掉"看看屏幕然后打开"/"看一下屏幕然后打开"等前缀
            target = self._extract_open_target(text)
            if target:
                # 直接构造一个简化的请求给 ReAct
                enhanced = f"请看看屏幕，然后打开「{target}」。先截屏了解屏幕内容，再用视觉定位找到并双击打开它。"
                return self._react_loop(enhanced, stream_callback)

        # 0.1b 直接打开屏幕上的XX（没有"看看"前缀）
        SCREEN_OPEN_PATTERN = re.compile(
            r"(?:帮我|请)?(?:打开|开启|启动|运行|点开)\s*(?:一下|下)?\s*"
            r"(?:屏幕上的|桌面上的|界面上的|屏幕里的)"
        )
        if SCREEN_OPEN_PATTERN.search(text):
            logger.info(f"🔍 检测到「打开屏幕上的XX」指令，走视觉 ReAct")
            target = self._extract_open_target(text)
            if target:
                enhanced = f"请看看屏幕，然后打开「{target}」。先截屏了解屏幕内容，再用视觉定位找到并双击打开它。"
                return self._react_loop(enhanced, stream_callback)

        # 0.2 视觉询问检测 → 截屏分析快速路径（纯看屏幕不操作）
        if self._is_visual_query(text):
            return self._handle_visual_query(text, stream_callback)

        # 1. 快速正则意图判断
        intent, target = self._fast_intent(text)

        # 音乐快速路径
        if intent == "play_music":
            if stream_callback:
                stream_callback(f"正在为你播放「{target}」...\n")
            ok, msg, _info = self._execute_tool("play_music", target)
            reply = msg

            # 试听片段回退：play() 已跳过本地播放，直接在B站搜索歌曲+歌手
            if _info and _info.get("preview"):
                song_name = _info.get("song_name", target)
                artist = _info.get("artist", "")
                if stream_callback:
                    stream_callback(f"\n🎵 检测到试听片段，正在为你从B站搜索完整版...\n")
                fallback_msg = self._fallback_bilibili_search(song_name, artist, stream_callback)
                if fallback_msg:
                    reply = f"⚠️ 此歌为VIP歌曲，已为你转至B站搜索完整版。\n\n🔁 B站：{fallback_msg}"

            if stream_callback:
                self._stream_emit(reply, stream_callback)
            self._push_history(text, reply)
            return reply, {"action": "play_music", "success": ok}

        if intent == "music_control":
            ok, msg, _info = self._execute_tool("music_control", target)
            if stream_callback:
                self._stream_emit(msg, stream_callback)
            self._push_history(text, msg)
            return msg, {"action": "music_control", "target": target, "success": ok}

        if intent == "list_music":
            ok, msg, _info = self._execute_tool("list_music", "")
            if stream_callback:
                self._stream_emit(msg, stream_callback)
            self._push_history(text, msg)
            return msg, {"action": "list_music", "success": ok}

        if intent == "music_lyrics":
            ok, msg, _info = self._execute_tool("music_lyrics", target)
            if stream_callback:
                self._stream_emit(msg, stream_callback)
            self._push_history(text, msg)
            return msg, {"action": "music_lyrics", "target": target, "success": ok}

        # arXiv 论文搜索快速路径
        if intent == "search_arxiv":
            if stream_callback:
                stream_callback(f"🔍 正在 arXiv 搜索「{target}」...\n")
            ok, msg, _info = self._execute_tool("search_arxiv", target)
            if ok and stream_callback:
                self._stream_emit(msg, stream_callback)
            elif stream_callback:
                stream_callback(f"❌ {msg}\n")
            self._push_history(text, msg)
            return msg, {"action": "search_arxiv", "target": target, "success": ok}

        if intent == "arxiv_author_search":
            if stream_callback:
                stream_callback(f"🔍 正在 arXiv 搜索作者「{target}」的论文...\n")
            ok, msg, _info = self._execute_tool("arxiv_author_search", target)
            if ok and stream_callback:
                self._stream_emit(msg, stream_callback)
            elif stream_callback:
                stream_callback(f"❌ {msg}\n")
            self._push_history(text, msg)
            return msg, {"action": "arxiv_author_search", "target": target, "success": ok}

        # 检测"打开屏幕上的XX"类指令 → 走视觉 ReAct
        if intent == "open_app" and target and self._is_screen_reference(target):
            logger.info(f"🔍 检测到屏幕引用打开指令，走视觉 ReAct: target={target}")
            enhanced = f"请看看屏幕，然后打开「{target}」。先截屏了解屏幕内容，再用视觉定位找到并双击打开它。"
            return self._react_loop(enhanced, stream_callback)

        # 检测复杂/多步指令 → 走 ReAct
        if intent in ("open_app", "close_app", "search", "search_only"):
            if self._is_complex_command(target, text):
                return self._react_loop(text, stream_callback)
            # 简单单工具指令，直接执行
            return self._execute_single_tool(intent, target, text)

        # 2. 纯聊天 / 复杂任务 → ReAct 多步循环
        return self._react_loop(text, stream_callback)

    # ---------------- 快速路径 ----------------

    def _is_visual_query(self, text: str) -> bool:
        """检测用户是否在询问屏幕内容（视觉询问）。"""
        return bool(self.VISION_QUERY_PATTERN.search(text))

    def _handle_visual_query(self, text: str, stream_callback=None) -> Tuple[str, Dict[str, Any]]:
        """处理视觉询问：截屏 + VLM 分析 + 流式回复。"""
        # 1. 通知用户正在截屏
        if stream_callback:
            stream_callback("好的，让我看看屏幕...\n")
        
        # 2. 截屏
        img_b64, width, height = self.vision.capture_screen()
        if not img_b64:
            reply = "抱歉，截屏失败了，请检查屏幕截图权限。"
            self._push_history(text, reply)
            return reply, {"action": "visual_query", "success": False, "error": "截屏失败"}
        
        if stream_callback:
            stream_callback(f"（已截取 {width}x{height} 屏幕，正在分析...）\n\n")
        
        # 3. 构造 VLM 分析 prompt
        analysis_prompt = self._build_visual_analysis_prompt(text)
        
        # 4. 调用 VLM 分析
        try:
            analysis = self.vision.llm.vision_chat(analysis_prompt, img_b64)
        except Exception as e:
            analysis = f"抱歉，视觉分析出错了：{e}"
        
        # 5. 流式输出分析结果
        if stream_callback and analysis:
            self._stream_emit(analysis, stream_callback)
        
        # 6. 保存历史
        self._push_history(text, analysis)
        
        return analysis, {
            "action": "visual_query",
            "success": True,
            "screenshot_size": f"{width}x{height}",
            "analysis": analysis,
        }

    def _build_visual_analysis_prompt(self, user_query: str) -> str:
        """构造视觉分析 prompt。"""
        return (
            f"你是一个视觉助手，下面是用户当前的屏幕截图。\n"
            f"用户问：「{user_query}」\n\n"
            f"请仔细观察截图内容，回答用户的问题。要求：\n"
            f"1. 准确描述屏幕上的主要内容\n"
            f"2. 如果用户问的是具体问题，给出直接的回答\n"
            f"3. 如果看到了有趣的内容，可以简单评论一下\n"
            f"4. 回答要简洁自然，像和朋友聊天一样\n"
            f"5. 使用简体中文回答"
        )

    def _is_complex_command(self, target: str, full_text: str) -> bool:
        """检测是否为多步或复合任务，需要 ReAct 循环。"""
        # 整句中包含序列连接词
        if self.COMPLEX_ACTION_PATTERN.search(full_text):
            return True
        
        # 检测 target 是否包含了通讯/发送类动作（正则贪婪匹配导致的误捕获）
        # 比如 "QQ并给代号0110发送你好" -> target 被贪婪匹配了整句
        COMM_PATTERN = re.compile(r"(发送|发消息|告诉|通知|给.*发送|帮.*发)")
        if target and COMM_PATTERN.search(target):
            return True
            
        # target 中残留其他动作动词
        if target and self.COMPLEX_ACTION_PATTERN.search(target):
            return True
        return False

    def _execute_single_tool(self, intent: str, target: str, text: str) -> Tuple[str, Dict[str, Any]]:
        """执行单个快速工具指令并返回简短回复（不进入 ReAct 循环）。"""
        if intent == "open_app":
            ok, msg = self.app_ctrl.open_app(target)
            reply = f"好的，{msg}。还有什么可以帮你的吗？" if ok else f"抱歉，{msg}。你可以尝试告诉我更具体的名称。"
            self._push_history(text, reply)
            return reply, {"action": "open_app", "target": target, "success": ok}

        if intent == "close_app":
            ok, msg = self.app_ctrl.close_app(target)
            reply = msg if ok else f"抱歉，{msg}"
            self._push_history(text, reply)
            return reply, {"action": "close_app", "target": target, "success": ok}

        if intent == "search":
            ok, msg, chosen = self.searcher.search_and_open_best(target)
            self._push_history(text, msg)
            return msg, {
                "action": "best_open" if ok else "search",
                "target": target, "success": ok, "chosen": chosen,
            }

        if intent == "search_only":
            results = self.searcher.search(target, num_results=5)
            if results:
                lines = [f"「{target}」的搜索结果："]
                for i, r in enumerate(results[:5], 1):
                    lines.append(f"{i}. {r.get('title','')}\n   {r.get('url','')}")
                reply = "\n".join(lines)
            else:
                reply = f"搜索「{target}」没有找到结果"
            self._push_history(text, reply)
            return reply, {"action": "search", "target": target, "results": results}

        # 兜底
        return self._react_loop(text, None)

    # ---------------- ReAct 多步循环 ----------------

    def _react_loop(self, text: str, stream_callback) -> Tuple[str, Dict[str, Any]]:
        """
        ReAct 循环：思考 → 行动 → 观察 → 再思考。
        每轮让 LLM 决定下一步动作；工具执行结果回喂 LLM 用于下一步决策。
        最多 MAX_REACT_ITERATIONS 轮，LLM 判定 finish 时给出最终回复。
        注入长期记忆；循环结束后异步写入记忆。
        """
        # 注入跨对话上下文：智能判断是延续任务还是新任务
        observations: List[Dict[str, Any]] = []
        if self._last_observations:
            # 判断当前用户请求是否包含"新搜索"意图
            _new_search_patterns = re.compile(
                r'(搜索|搜|查找|找|打开.*浏览器|打开.*B站|打开.*b站|打开.*网站|navigate|goto|open.*site)',
                re.IGNORECASE
            )
            _continuation_patterns = re.compile(
                r'(第\s*\d+\s*[个条项只]|第一个|首个|第二个|第三个|上一个|下一个|'
                r'那个|这个|它|继续|接着|然后|之后|打开(这个|那个|它)|'
                r'点击(这个|那个|它)|选择(这个|那个|它)|'
                r'讲解|详细|最|排行|热门|推荐)',
                re.IGNORECASE
            )

            is_new_search = bool(_new_search_patterns.search(text))
            is_continuation = bool(_continuation_patterns.search(text))

            if is_new_search:
                # 新搜索：总是清除旧上下文，即使同时包含延续词（如"搜索XXX，打开第一个"）
                # 因为搜索需要获取全新结果，旧上下文只会干扰
                logger.info(f"🔍 检测到新搜索意图，跳过旧上下文注入: '{text[:50]}'")
                self._last_observations = []
                self._last_action_info = {}
            elif is_continuation or (self._last_action_info.get("action", "").startswith("cdp_") or
                                     self._last_action_info.get("action", "").startswith("search_on_site")):
                # 延续任务：注入完整上下文
                last_obs_summary = self._build_cross_dialogue_context()
                if last_obs_summary:
                    observations.append({
                        "thought": "（延续上一次对话的上下文）",
                        "tool": "",
                        "target": "",
                        "observation": last_obs_summary,
                        "success": True,
                    })
                    logger.info(f"📎 注入跨对话上下文（延续任务）: {last_obs_summary[:100]}")
            else:
                # 其他情况：注入一个简短提醒，但不让旧结果干扰
                logger.info(f"💡 检测到新任务，不注入旧搜索结果上下文")

        action_info: Dict[str, Any] = {"action": "chat"}

        # 检索长期记忆（一次性，整轮复用）
        memory_context = ""
        if self.memory:
            try:
                relevant = self.memory.retrieve_relevant_memories(text)
                if relevant:
                    memory_context = self.memory.build_context_text(relevant)
            except Exception:
                pass

        final_answer = ""
        break_loop = False

        for i in range(self.MAX_REACT_ITERATIONS):
            decision = self._ask_react_action(text, observations, memory_context)

            tool = str(decision.get("tool", "finish")).lower()
            thought = decision.get("thought", "")
            answer = decision.get("answer", "")

            # 终止条件：LLM 判定完成或无需工具
            if tool in ("finish", "none", ""):
                if answer:
                    final_answer = answer
                    # 模拟流式输出，保留 UI 流式体验
                    if stream_callback:
                        self._stream_emit(answer, stream_callback)
                else:
                    # answer 为空，单独触发流式生成
                    final_answer = self._generate_final_streaming(
                        text, observations, memory_context, stream_callback
                    )
                break_loop = True
                break

            target = str(decision.get("target", "")).strip()
            if not target:
                observations.append({
                    "thought": thought, "tool": tool, "target": "",
                    "observation": "[错误] 缺少 target 参数，无法执行",
                    "success": False,
                })
                continue

            # ---------- 自动屏幕感知前置 ----------
            # 桌面视觉工具前自动插入 screen_context（不消耗迭代次数）
            if self.screen_perception_enabled and tool in self.DESKTOP_VISION_TOOLS:
                auto_ctx = self._auto_screen_context(observations, target)
                if auto_ctx:
                    observations.append(auto_ctx)

            # 执行工具
            ok, obs, info = self._execute_tool(tool, target)
            action_info = info
            observations.append({
                "thought": thought, "tool": tool, "target": target,
                "observation": obs, "success": ok,
            })

            # ---------- 自动操作验证后置 ----------
            # 关键操作后自动插入 verify_action（不消耗迭代次数）
            if self.screen_perception_enabled and ok and tool in self.CRITICAL_OPERATION_TOOLS:
                auto_verify = self._auto_verify_action(tool, target, observations)
                if auto_verify:
                    observations.append(auto_verify)

        if not break_loop:
            # 超过最大迭代仍未 finish，强制流式总结
            final_answer = self._generate_final_streaming(
                text, observations, memory_context, stream_callback
            )

        # 推入对话历史
        self._push_history(text, final_answer)

        # 保存跨对话上下文（供下一次对话使用）
        self._last_observations = list(observations)
        self._last_action_info = dict(action_info)

        # 异步写入记忆（不阻塞回复）
        if self.memory:
            conv_text = f"用户：{text}\n助手：{final_answer}"
            t = threading.Thread(
                target=self._async_add_memory,
                args=(conv_text, self._session_id),
                daemon=True,
            )
            t.start()

        return final_answer, action_info

    def _ask_react_action(self, text: str, observations: List[Dict[str, Any]],
                          memory_context: str) -> Dict[str, Any]:
        """
        让 LLM 基于 ReAct 协议决定下一步动作。
        返回 {"thought": "...", "tool": "...", "target": "...", "answer": "..."}
        - tool != finish：填 tool/target，answer 留空
        - tool == finish：填 answer（最终回复）
        """
        tools_desc = "\n".join(
            f"- {t['name']}: {t['description']}。参数：{t['params']}"
            for t in self.TOOL_DEFINITIONS
        )

        sys_prompt = (
            "你是一个使用 ReAct（思考-行动-观察）协议的智能助手。"
            "你需要根据用户请求和已执行的操作历史，决定下一步动作。\n\n"
            f"可用工具：\n{tools_desc}\n\n"
            "输出规则：\n"
            "1. 每次只输出一个 JSON，不能输出任何其他文字。\n"
            "2. JSON 格式：{\"thought\": \"简短思考\", \"tool\": \"...\", \"target\": \"...\", \"answer\": \"...\"}\n"
            "3. 如果需要调用工具：填写 tool 和 target，answer 留空字符串。\n"
            "4. 如果任务完成或无需工具：tool=\"finish\"，answer 填写给用户的最终回复（基于已有观察）。\n"
            "5. 不要重复调用已经失败的工具；如果工具失败，考虑替代方案或用 finish 解释原因。\n"
            "6. 如果用户请求是多步骤任务，按顺序逐步执行，每轮一个工具。\n"
            "7. 如果用户是纯聊天/问候/知识问答，直接 tool=\"finish\"，answer 填写回复。\n"
            "8. 当用户要求在特定网站（如B站、知乎、CSDN、GitHub、淘宝等）搜索时，使用 search_on_site 工具，target 格式为 \"网站名||关键词\"，例如 \"B站||python\"。该工具在 CDP 可用时会自动走 CDP 精准路径，无需手动选择 cdp_* 工具。\n"
            "9. 当用户要求打开特定URL链接时，使用 open_url 工具。该工具在 CDP 可用时会自动走 CDP 精准路径。\n"
            "10. 当用户要求在桌面聊天软件（QQ、微信、钉钉）中发送消息时，优先使用 vision_send_message 工具（基于视觉识别，更可靠），target 格式为 \"软件名||联系人||消息内容\"，例如 \"QQ||0110||你好\"。\n"
            "11. 如果需要在桌面应用中点击某个控件但 desktop_click 失败（复杂应用如QQ控件难识别），改用 vision_click 工具，target 为元素描述（如\"搜索框\"、\"发送按钮\"、\"联系人清风\"）。\n"
            "12. 当需要向输入框输入文字时，使用 vision_type 工具，target 格式为 \"输入框描述||要输入的文字\"，例如 \"搜索框||张三\"。\n"
            "13. 当需要了解当前屏幕上有什么元素时，使用 vision_screenshot 工具进行截屏分析。\n"
            "14. 当用户问「你看到了什么」、「这是什么」、「帮我看看」等视觉问题时，使用 vision_analyze 工具，target 填写用户的问题。\n"
            "15. 当用户要求「在XX搜索YY**并打开第一篇/第一个/第一条**」时，分两步：先调用 search_on_site（target=\"网站名||关键词\"，如\"CSDN||多模态大模型\"）打开搜索结果页，再调用 vision_click_first_result 工具用视觉模型识别并点击第一个搜索结果。注意：只有用户**明确要求打开第一篇/第一个**时才需要这两步！如果用户只是说「在XX搜索YY」而没要求打开第一篇，那么搜索完成后就直接 finish，不要自动点击！\n"
            "16. 当用户要求读取、查看、编辑、整理文件或文件夹时，使用文件操作工具：read_file（读文本）、read_excel（读Excel）、excel_stats（Excel统计）、read_csv（读CSV）、write_file（写文件）、create_word（生成Word）、list_files（列文件）、search_files（搜索文件）、organize_by_date（按日期整理）、organize_by_type（按类型整理）、file_info（文件信息）。\n"
            "17. 当用户要求创建定时任务、提醒、计划时，使用 add_schedule 工具。支持三种时间格式：cron表达式（如 '0 8 * * *' 每天8点）、间隔分钟数（如 '30' 每30分钟）、ISO时间（如 '2026-08-07T14:00:00' 一次性）。\n"
            "18. 使用 list_schedules 查看所有定时任务，remove_schedule 删除任务，toggle_schedule 启用/禁用任务，run_schedule_now 立即执行任务。\n"
            "\n--- 屏幕感知能力（重要！必须使用） ---\n"
            "19. 在执行任何桌面视觉操作（vision_click/vision_type/vision_send_message/vision_hover/vision_drag/vision_scroll）之前，必须先使用 screen_context 快速了解当前屏幕状态。这能大幅提高操作成功率，避免盲目点击。\n"
            "20. 在执行关键操作（如点击按钮、输入文字、打开页面、发送消息）之后，必须使用 verify_action 验证操作是否成功。如果验证失败，应使用 screen_context 重新了解状态，再考虑重试或使用替代方案。\n"
            "21. 当需要等待某个条件出现（如下载完成、页面加载、邮件到达、价格变化）时，使用 watch_screen 工具持续监控屏幕，最长等待30秒。\n"
            "22. 当操作失败或不确定当前状态时，优先使用 screen_context 获取屏幕状态，再决定下一步。绝对不要盲目重试失败的操作。\n"
            "23. 对于复杂的多步骤任务，在开始前先使用 screen_context 了解当前屏幕状态，这样可以制定更准确的执行计划。\n"
            "24. 当用户要求你在桌面应用中操作但没有明确说明当前屏幕内容时，默认先使用 screen_context 查看屏幕。\n"
            "25. 当用户说「看看屏幕，然后打开XX」、「帮我打开屏幕上的XX」、「屏幕上的XX打开一下」等需要先看屏幕再操作的指令时，使用 vision_find_and_open 工具，target 填写要打开的元素描述（如「微信图标」、「记事本」、「回收站」）。该工具会自动截屏、VLM定位、双击打开。\n"
            "26. 重要：搜索完成后**不要自动点击任何结果**！只有用户明确说「打开第一篇/第一个/第一条」或「打开第一个视频」等时，才使用 vision_click_first_result。纯搜索请求（如「在B站搜索XX」「帮我在知乎搜XX」）在搜索完成后就直接 finish，告诉用户已完成搜索即可。\n"
            "\n--- 分层操作架构（重要！优先使用低层方案） ---\n"
            "27. 操作执行优先级：**UIA（第1层） > CDP（第3层） > VLM（兜底）**。UIA 用于标准桌面应用，CDP 用于浏览器，VLM 仅在前两者都不可用时使用。\n"
            "28. 桌面操作：优先使用 uia_screen_context 代替 screen_context，uia_click 代替 vision_click，uia_type 代替 vision_type。UIA 更快（毫秒级）、更准（直接操作控件）、更便宜（零成本）。如果 UIA 失败，再回退到视觉方案。\n"
            "29. 浏览器站内搜索：当用户要求在某个网站（B站、知乎、CSDN等）搜索时，**必须使用 search_on_site 工具**，不要用 cdp_open_url + cdp_type 的组合。search_on_site 会直接跳转到搜索结果页，一步完成，比在首页找搜索框输入更可靠。\n"
            "30. 浏览器操作：当需要在当前已打开的浏览器页面中操作时，使用 CDP 工具：cdp_click 点击元素、cdp_type 输入文字、cdp_page_content 获取页面内容。CDP 直接操作 DOM，比 VLM 看屏幕再点击精准100倍。\n"
            "31. 浏览/选择视频【核心功能】：当用户在B站等视频网站想打开特定视频时，采用「列出→选择」两步流程：\n"
            "   步骤1：用 cdp_list_results 列出所有搜索结果（每条含编号、标题、时长等），让用户决定选哪个\n"
            "   步骤2a：如果用户说了编号（如第3个、第5个、第8个），**必须用 cdp_click_nth_result**，target 填数字编号\n"
            "   步骤2b：如果用户说了关键词（如教程、详解、讲解），用 cdp_click_by_keyword 按关键词匹配\n"
            "   步骤2c：如果用户说「第一个/首个/第一个视频」，用 cdp_click_first_result 直接点击\n"
            "   ⚠️ 严禁：当用户说「打开第N个视频」时，绝对不要用 open_app！open_app 是打开桌面应用的，打开浏览器视频必须用 cdp_click_nth_result！\n"
            "   ⚠️ 严禁：不要用 vision_click 或 vision_click_first_result 处理浏览器搜索结果选择，CDP 方案更精准！\n"
            "   重要：永远不要猜测或假设结果位置，必须先列出再选择！\n"
            "32. 当不确定当前操作环境是桌面还是浏览器时，先用 uia_screen_context 或 screen_context 检查，如果检测到是浏览器（标题包含 Chrome/Edge/Firefox 等），则使用 CDP 工具；如果是桌面应用（如记事本、Office、设置），则使用 UIA 工具。\n"
            "\n--- 音乐能力（新增！） ---\n"
            "33. 音乐工具：play_music（点歌播放，支持「歌名」或「平台||歌名」格式）、search_music（搜索音乐不播放）、list_music（查看歌单）、music_control（控制：pause/resume/next/prev/stop）、music_lyrics（查询歌词）。\n"
            "34. 用户说「点歌」「放歌」「听歌」「播放音乐」时，使用 play_music 工具。用户说「搜索音乐」「找歌」时用 search_music。用户说「歌单」时用 list_music。用户说「暂停」「下一首」等控制指令时用 music_control。\n"
        )

        if memory_context:
            sys_prompt += f"\n长期记忆（可参考，用于个性化回复）：\n{memory_context}\n"

        # 注入情绪状态
        if self.emotion_enabled and self.emotion:
            try:
                emotion_context = build_emotion_prompt(self.emotion)
                sys_prompt += f"\n{emotion_context}\n"
            except Exception:
                pass

        # 构造用户消息：原请求 + 已执行的操作历史
        user_content = f"用户请求：{text}"
        if observations:
            user_content += "\n\n已执行的操作历史：\n" + self._format_observations(observations)
            user_content += "\n\n请决定下一步动作（继续调用工具，或 finish 给出最终回复）。"
        else:
            user_content += "\n\n请决定第一步动作。"

        messages = [
            {"role": "system", "content": sys_prompt},
            *self._react_few_shot(),
            {"role": "user", "content": user_content},
        ]

        try:
            raw = self.llm.chat(messages, stream_callback=None, timeout=20)
            raw = raw.strip()
            m = re.search(r"\{[\s\S]*\}", raw)
            if m:
                obj = json.loads(m.group(0))
                tool = str(obj.get("tool", "finish")).lower()
                valid_tools = {t["name"] for t in self.TOOL_DEFINITIONS} | {"none"}
                if tool not in valid_tools:
                    tool = "finish"
                return {
                    "thought": str(obj.get("thought", "")),
                    "tool": tool,
                    "target": str(obj.get("target", "")).strip(),
                    "answer": str(obj.get("answer", "")),
                }
        except Exception:
            pass
        # 兜底：直接 finish（让 _generate_final_streaming 处理）
        return {"thought": "无法解析 LLM 输出", "tool": "finish", "target": "", "answer": ""}

    def _react_few_shot(self) -> List[Dict[str, str]]:
        """ReAct 小样本示例，帮助 LLM 理解协议。"""
        return [
            {"role": "user", "content": "用户请求：帮我打开微信\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户要打开微信，直接调用 open_app","tool":"open_app","target":"微信","answer":""}'},
            {"role": "user", "content": "用户请求：你好\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户在打招呼，无需工具","tool":"finish","target":"","answer":"你好！有什么可以帮你的吗？"}'},
            {"role": "user", "content": "用户请求：搜一下Python教程然后打开第一个\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"先搜索 Python 教程","tool":"search","target":"Python教程","answer":""}'},
            {"role": "user", "content": "用户请求：打开微信然后发消息给妈妈\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"先打开微信","tool":"open_app","target":"微信","answer":""}'},
            {"role": "user", "content": "用户请求：在B站搜索Python相关的视频\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户要在B站搜索Python，使用 search_on_site","tool":"search_on_site","target":"B站||Python","answer":""}'},
            {"role": "user", "content": "用户请求：帮我打开 https://www.bilibili.com\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户要打开URL，使用 open_url","tool":"open_url","target":"https://www.bilibili.com","answer":""}'},
            {"role": "user", "content": "用户请求：打开QQ并给代号0110发送你好\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户要在QQ给0110发消息，先打开QQ","tool":"open_app","target":"QQ","answer":""}'},
            {"role": "user", "content": "用户请求：在QQ里给张三发消息说晚上聚餐\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"使用视觉自动化在QQ中找张三并发送消息","tool":"vision_send_message","target":"QQ||张三||晚上聚餐","answer":""}'},
            {"role": "user", "content": "用户请求：点击屏幕上的搜索框\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"使用视觉识别点击搜索框","tool":"vision_click","target":"搜索框","answer":""}'},
            {"role": "user", "content": "用户请求：在搜索框输入Python\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"使用视觉在搜索框输入文字","tool":"vision_type","target":"搜索框||Python","answer":""}'},
            {"role": "user", "content": "用户请求：你看到了什么\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户问我看到了什么，需要截屏分析","tool":"vision_analyze","target":"你看到了什么","answer":""}'},
            {"role": "user", "content": "用户请求：帮我看看这是什么软件\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户想知道屏幕上是什么软件，使用视觉分析","tool":"vision_analyze","target":"这是什么软件","answer":""}'},
            {"role": "user", "content": "用户请求：屏幕上有什么\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户想了解屏幕内容，使用视觉分析","tool":"vision_analyze","target":"屏幕上有什么","answer":""}'},
            {"role": "user", "content": "用户请求：在CSDN上搜索多模态大模型并打开第一篇\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"先在CSDN站内搜索多模态大模型，使用 search_on_site 打开搜索结果页","tool":"search_on_site","target":"CSDN||多模态大模型","answer":""}'},
            {"role": "user", "content": "用户请求：在CSDN上搜索多模态大模型并打开第一篇\n\n已执行的操作历史：\n[步骤1] 思考: 先在CSDN站内搜索多模态大模型\n  动作: search_on_site(CSDN||多模态大模型) [成功]\n  观察: 在「CSDN」站内搜索「多模态大模型」：已打开搜索结果页\n\n请决定下一步动作（继续调用工具，或 finish 给出最终回复）。"},
            {"role": "assistant", "content": '{"thought":"搜索结果页已打开，现在用 VLM 识别并点击第一个搜索结果","tool":"vision_click_first_result","target":"第一个搜索结果","answer":""}'},
            {"role": "user", "content": "用户请求：在B站搜索Python视频然后打开第一个\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"先在B站站内搜索Python视频","tool":"search_on_site","target":"B站||Python视频","answer":""}'},
            {"role": "user", "content": "用户请求：在知乎搜索人工智能并打开第一个回答\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"先在知乎站内搜索人工智能","tool":"search_on_site","target":"知乎||人工智能","answer":""}'},
            {"role": "user", "content": "用户请求：帮我读取 C:\\Users\\test\\report.txt\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"读取用户指定的文本文件","tool":"read_file","target":"C:\\Users\\test\\report.txt","answer":""}'},
            {"role": "user", "content": "用户请求：把这段文字保存到 C:\\Users\\test\\out.txt\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"写入文件，需要用 路径||内容 格式","tool":"write_file","target":"C:\\Users\\test\\out.txt||内容","answer":""}'},
            {"role": "user", "content": "用户请求：读取 C:\\data\\sales.xlsx 并做统计分析\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"先用 excel_stats 进行统计分析","tool":"excel_stats","target":"C:\\data\\sales.xlsx","answer":""}'},
            {"role": "user", "content": "用户请求：把今天的会议纪要整理成Word文档\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户要生成Word文档，使用 create_word","tool":"create_word","target":"C:\\Users\\Documents\\会议纪要.docx||会议纪要||这里是会议内容|||参会人|发言内容","answer":""}'},
            {"role": "user", "content": "用户请求：把下载文件夹的图片按日期整理\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"按日期整理文件使用 organize_by_date","tool":"organize_by_date","target":"C:\\Users\\Downloads","answer":""}'},
            {"role": "user", "content": "用户请求：帮我设置每天早上8点播报天气\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"创建每日定时任务，使用 cron 表达式 0 8 * * *","tool":"add_schedule","target":"早间播报||0 8 * * *||播报今天的天气和日程","answer":""}'},
            {"role": "user", "content": "用户请求：查看所有定时任务\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"列出定时任务使用 list_schedules","tool":"list_schedules","target":"","answer":""}'},
            # ---------- 屏幕感知 few-shot ----------
            {"role": "user", "content": "用户请求：帮我点击浏览器的搜索框\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"执行视觉操作前必须先了解屏幕状态，使用 screen_context","tool":"screen_context","target":"浏览器搜索框","answer":""}'},
            {"role": "user", "content": "用户请求：帮我点击浏览器的搜索框\n\n已执行的操作历史：\n[步骤1] 思考: 执行视觉操作前必须先了解屏幕状态\n  动作: screen_context(浏览器搜索框) [成功]\n  观察: 【屏幕感知】活动窗口: Chrome浏览器, 应用类型: 浏览器, 关键元素: 搜索框, 地址栏, 收藏按钮, 当前状态: Chrome打开了百度首页\n\n请决定下一步动作（继续调用工具，或 finish 给出最终回复）。"},
            {"role": "assistant", "content": '{"thought":"已了解屏幕状态，现在点击搜索框","tool":"vision_click","target":"搜索框","answer":""}'},
            {"role": "user", "content": "用户请求：在微信中给张三发消息\n\n已执行的操作历史：\n[步骤1] 思考: 需要先了解当前屏幕状态\n  动作: screen_context(微信窗口) [成功]\n  观察: 【屏幕感知】活动窗口: 微信, 应用类型: 聊天, 关键元素: 搜索框, 联系人列表, 对话框, 当前状态: 微信主界面，显示最近对话列表\n\n请决定下一步动作（继续调用工具，或 finish 给出最终回复）。"},
            {"role": "assistant", "content": '{"thought":"已了解微信界面结构，现在使用视觉发送消息","tool":"vision_send_message","target":"微信||张三||你好","answer":""}'},
            {"role": "user", "content": "用户请求：点击搜索按钮\n\n已执行的操作历史：\n[步骤1] 思考: 使用 vision_click 点击搜索按钮 [成功]\n  动作: vision_click(搜索按钮)\n  观察: 已点击「搜索按钮」\n\n请决定下一步动作（继续调用工具，或 finish 给出最终回复）。"},
            {"role": "assistant", "content": '{"thought":"操作已执行，需要验证是否成功","tool":"verify_action","target":"点击搜索按钮||页面跳转或搜索结果出现","answer":""}'},
            # ---------- 看看屏幕然后打开 few-shot ----------
            {"role": "user", "content": "用户请求：看看屏幕，然后打开微信\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户要求看看屏幕然后打开微信，使用 vision_find_and_open 工具，它会自动截屏、定位并双击打开","tool":"vision_find_and_open","target":"微信图标","answer":""}'},
            {"role": "user", "content": "用户请求：帮我打开屏幕上的回收站\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户要打开屏幕上的回收站，使用 vision_find_and_open","tool":"vision_find_and_open","target":"回收站","answer":""}'},
            # ---------- 纯搜索 vs 打开第一篇 few-shot ----------
            # 纯搜索：搜索完成后直接 finish
            {"role": "user", "content": "用户请求：在B站搜索多模态大模型\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户只是要在B站搜索多模态大模型，没有要求打开第一个结果。先执行搜索","tool":"search_on_site","target":"B站||多模态大模型","answer":""}'},
            {"role": "user", "content": "用户请求：在B站搜索多模态大模型\n\n已执行的操作历史：\n[步骤1] 思考: 用户只是要在B站搜索多模态大模型，没有要求打开第一个结果\n  动作: search_on_site(B站||多模态大模型) [成功]\n  观察: 在「B站」站内搜索「多模态大模型」：已打开搜索结果页\n\n请决定下一步动作（继续调用工具，或 finish 给出最终回复）。"},
            {"role": "assistant", "content": '{"thought":"搜索已完成，页面已打开。用户没有要求打开第一篇，所以直接 finish 告诉用户搜索完成","tool":"finish","target":"","answer":"已为你在B站搜索「多模态大模型」，搜索结果页面已打开，你可以查看感兴趣的内容了。"}'},
            # 搜索并打开第一篇：搜索完成后再点击
            {"role": "user", "content": "用户请求：在B站搜索多模态大模型并打开第一个视频\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户明确要求打开第一个视频，先搜索","tool":"search_on_site","target":"B站||多模态大模型","answer":""}'},
            {"role": "user", "content": "用户请求：在B站搜索多模态大模型并打开第一个视频\n\n已执行的操作历史：\n[步骤1] 思考: 用户明确要求打开第一个视频，先搜索\n  动作: search_on_site(B站||多模态大模型) [成功]\n  观察: 在「B站」站内搜索「多模态大模型」：已打开搜索结果页\n\n请决定下一步动作（继续调用工具，或 finish 给出最终回复）。"},
            {"role": "assistant", "content": '{"thought":"搜索结果页已打开，用户明确要求打开第一个视频，用 cdp_click_first_result 点击（CDP 比 VLM 更精准）","tool":"cdp_click_first_result","target":"第一个搜索结果","answer":""}'},
            # ---------- UIA 桌面操作 few-shot ----------
            {"role": "user", "content": "用户请求：帮我点击记事本的保存按钮\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户要点击桌面应用的保存按钮，先用 UIA 查看前台窗口结构","tool":"uia_screen_context","target":"记事本","answer":""}'},
            {"role": "user", "content": "用户请求：帮我点击记事本的保存按钮\n\n已执行的操作历史：\n[步骤1] 思考: 用 UIA 查看窗口结构\n  动作: uia_screen_context(记事本) [成功]\n  观察: 【UIA 屏幕感知】- 窗口: 无标题 - 记事本- 关键控件(5个): [Button] 保存@(100,500,200,530)...\n\n请决定下一步动作。"},
            {"role": "assistant", "content": '{"thought":"已看到保存按钮，用 UIA 直接点击","tool":"uia_click","target":"保存","answer":""}'},
            # ---------- CDP 浏览器操作 few-shot ----------
            {"role": "user", "content": "用户请求：在B站搜索多模态大模型\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户要在B站搜索，使用 search_on_site 直接跳转搜索页，比手动输入更快更准","tool":"search_on_site","target":"B站||多模态大模型","answer":""}'},
            {"role": "user", "content": "用户请求：在B站搜索多模态大模型\n\n已执行的操作历史：\n[步骤1] 思考: 使用 search_on_site 在B站搜索\n  动作: search_on_site(B站||多模态大模型) [成功]\n  观察: [CDP] 在「B站」站内搜索「多模态大模型」：已打开...\\n💡 CDP浏览器已就绪，后续浏览器操作将自动通过CDP精准执行\n\n请决定下一步动作。"},
            {"role": "assistant", "content": '{"thought":"B站搜索页已打开，第一个视频就是搜索结果，用 CDP 点击第一个结果","tool":"cdp_click_first_result","target":"第一个搜索结果","answer":""}'},
            # ---------- CDP 浏览/选择视频 few-shot ----------
            {"role": "user", "content": "用户请求：在B站搜索多模态大模型并打开第三个视频\n\n已执行的操作历史：\n[步骤1] 搜索: search_on_site(B站||多模态大模型) [成功] 观察: 已打开B站搜索页\n\n请决定下一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户要第三个视频，先列出所有搜索结果看看有哪些","tool":"cdp_list_results","target":"20","answer":""}'},
            {"role": "user", "content": "用户请求：在B站搜索多模态大模型并打开第三个视频\n\n已执行的操作历史：\n[步骤1] 搜索: search_on_site(B站||多模态大模型) [成功] 观察: 已打开B站搜索页\n[步骤2] 列出: cdp_list_results(20) [成功] 观察: 【搜索结果列表】共 20 条\\n  #1 | XXX老师\\n    标题: 多模态大模型入门教程\\n  #2 | YYY讲解\\n    标题: 多模态大模型实战\\n  #3 | ZZZ\\n    标题: 多模态大模型原理详解\\n...\n\n请决定下一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户要求第三个视频，列表中 #3 是多模态大模型原理详解，点击它","tool":"cdp_click_nth_result","target":"3","answer":""}'},
            {"role": "user", "content": "用户请求：在B站搜索多模态大模型并打开讲解最详细的那个\n\n已执行的操作历史：\n[步骤1] 搜索: search_on_site(B站||多模态大模型) [成功] 观察: 已打开B站搜索页\n[步骤2] 列出: cdp_list_results(20) [成功] 观察: 【搜索结果列表】\\n  #1 | XXX老师\\n    标题: 多模态大模型入门教程\\n  #2 | YYY讲解\\n    标题: 多模态大模型详解与实战\\n  #3 | ZZZ\\n    标题: 多模态大模型原理\n\n请决定下一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户要讲解最详细的，列表中 #2 标题含详解，用关键词匹配","tool":"cdp_click_by_keyword","target":"详解","answer":""}'},
            {"role": "user", "content": "用户请求：在B站搜索多模态大模型并打开第一个视频\n\n已执行的操作历史：\n[步骤1] 搜索: search_on_site(B站||多模态大模型) [成功] 观察: 已打开B站搜索页\n[步骤2] 点击: cdp_click_first_result(第一个搜索结果) [成功] 观察: 已切换到视频页\n\n请决定下一步动作。"},
            {"role": "assistant", "content": '{"thought":"视频已在新标签页打开，播放即可，任务完成","tool":"finish","target":"","answer":"已为您在B站搜索并打开视频"}'},
            # ---------- CDP 选择视频（编号/关键词）few-shot ----------
            # 场景：用户已经在B站搜索并列出结果，现在说"打开第N个"
            {"role": "user", "content": "用户请求：在B站搜索无职转生，列出所有结果\n\n请决定第一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户要在B站搜索无职转生并列出结果","tool":"search_on_site","target":"B站||无职转生","answer":""}'},
            {"role": "user", "content": "用户请求：在B站搜索无职转生，列出所有结果\n\n已执行的操作历史：\n[步骤1] 搜索: search_on_site(B站||无职转生) [成功] 观察: [CDP] 在「B站」站内搜索「无职转生」：已打开搜索结果页\n\n请决定下一步动作。"},
            {"role": "assistant", "content": '{"thought":"搜索页已打开，用户要列出所有结果","tool":"cdp_list_results","target":"20","answer":""}'},
            {"role": "user", "content": "用户请求：打开第5个视频\n\n已执行的操作历史：\n[步骤1] 搜索: search_on_site(B站||无职转生) [成功] 观察: [CDP] 在「B站」站内搜索「无职转生」：已打开搜索结果页\n[步骤2] 列出: cdp_list_results(20) [成功] 观察: 【搜索结果列表】共 20 条\\n  #1 | UP主A\\n    标题: 无职转生 第一季\\n  #2 | UP主B\\n    标题: 无职转生 第二季\\n  ...\\n  #5 | UP主E\\n    标题: 无职转生 第三季 OVA\\n...\n\n请决定下一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户要第5个视频，直接用 cdp_click_nth_result 点击编号5，绝对不能用 open_app","tool":"cdp_click_nth_result","target":"5","answer":""}'},
            # 场景：用户说"打开第一个"
            {"role": "user", "content": "用户请求：打开第一个\n\n已执行的操作历史：\n[步骤1] 搜索: search_on_site(B站||多模态大模型) [成功] 观察: [CDP] 已打开搜索结果页\n[步骤2] 列出: cdp_list_results(20) [成功] 观察: 【搜索结果列表】共 20 条\\n  #1 | UP主\\n    标题: 多模态大模型入门教程\\n  ...\n\n请决定下一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户说打开第一个，使用 cdp_click_first_result，不要用 open_app","tool":"cdp_click_first_result","target":"第一个搜索结果","answer":""}'},
            # 场景：用户说"打开讲解最详细的"
            {"role": "user", "content": "用户请求：打开讲解最详细的\n\n已执行的操作历史：\n[步骤1] 搜索: search_on_site(B站||Python教程) [成功] 观察: [CDP] 已打开搜索结果页\n[步骤2] 列出: cdp_list_results(20) [成功] 观察: 【搜索结果列表】共 20 条\\n  #1 | UP主A\\n    标题: Python入门教程\\n  #2 | UP主B\\n    标题: Python详解与实战\\n  ...\n\n请决定下一步动作。"},
            {"role": "assistant", "content": '{"thought":"用户要讲解最详细的，列表中 #2 标题含详解，用关键词匹配","tool":"cdp_click_by_keyword","target":"详解","answer":""}'},
        ]

    def _format_observations(self, observations: List[Dict[str, Any]]) -> str:
        """格式化已执行的操作历史，供 LLM 参考。"""
        if not observations:
            return ""
        lines = []
        for i, obs in enumerate(observations, 1):
            status = "成功" if obs.get("success") else "失败"
            lines.append(
                f"[步骤{i}] 思考: {obs.get('thought','')}\n"
                f"  动作: {obs.get('tool','')}({obs.get('target','')}) [{status}]\n"
                f"  观察: {obs.get('observation','')}"
            )
        return "\n".join(lines)

    def _stream_emit(self, text: str, stream_callback, chunk_size: int = 8):
        """将完整文本按字符块模拟流式输出，保留 UI 流式体验。"""
        if not stream_callback:
            return
        for i in range(0, len(text), chunk_size):
            stream_callback(text[i:i + chunk_size])

    def _generate_final_streaming(self, text: str, observations: List[Dict[str, Any]],
                                  memory_context: str, stream_callback) -> str:
        """单独触发一次流式 LLM 调用，生成最终回复（兜底场景）。"""
        obs_text = self._format_observations(observations)
        final_prompt = text
        if obs_text:
            final_prompt = (
                f"用户原始请求：{text}\n\n"
                f"你已执行以下操作并得到观察结果：\n{obs_text}\n\n"
                f"请基于上述执行结果，给用户一个简洁友好的最终回复。"
            )
        msgs = self._build_final_messages(final_prompt, memory_context)
        return self.llm.chat(msgs, stream_callback=stream_callback)

    def _build_final_messages(self, final_prompt: str, memory_context: str) -> List[Dict[str, str]]:
        """构建最终回复的 LLM 消息序列（含 persona + 记忆 + 历史 + 情绪）。"""
        persona = self.llm.settings.get("ai_persona", "你是一个贴心的智能助手。")

        # 注入情绪状态
        emotion_context = ""
        if self.emotion_enabled and self.emotion:
            try:
                emotion_context = build_emotion_prompt(self.emotion)
            except Exception:
                pass

        full_persona = persona
        if emotion_context:
            full_persona = f"{persona}\n\n{emotion_context}"

        msgs = [{"role": "system", "content": full_persona}]
        if memory_context:
            msgs.append({"role": "system", "content": memory_context})
        msgs.extend(self._history)
        msgs.append({"role": "user", "content": final_prompt})
        return msgs

    # ---------------- 自动屏幕感知 ----------------

    def _auto_screen_context(self, observations: List[Dict], target: str) -> Optional[Dict]:
        """在桌面视觉操作前自动获取屏幕上下文，不消耗 ReAct 迭代。"""
        # 检查最近的观察中是否已有 screen_context（避免重复）
        for obs in reversed(observations):
            if obs.get("tool") == "screen_context":
                return None  # 已有最近的上下文，跳过

        # 如果 CDP 浏览器已活跃，用浏览器上下文代替视觉截图
        if self._cdp_browser_active():
            try:
                browser_ctx = self.browser.get_browser_context()
                elements = browser_ctx.get("elements", [])
                summary = (
                    f"[CDP 浏览器上下文]\n"
                    f"- URL：{browser_ctx.get('url', '未知')}\n"
                    f"- 标题：{browser_ctx.get('title', '未知')}\n"
                    f"- 关键元素({len(elements)}个)：{', '.join([e.get('text','')[:30] for e in elements[:10]])}\n"
                    f"- 💡 CDP 已就绪，后续操作将自动走 CDP 精准路径"
                )
                return {
                    "thought": "[自动] CDP活跃，获取浏览器上下文",
                    "tool": "screen_context",
                    "target": target,
                    "observation": summary,
                    "success": True,
                }
            except Exception:
                pass  # 浏览器上下文失败，继续走视觉路径

        try:
            ctx = self.vision.screen_context(target)
            if "error" in ctx:
                return {
                    "thought": "[自动] 屏幕感知",
                    "tool": "screen_context",
                    "target": target,
                    "observation": f"[自动屏幕感知失败] {ctx['error']}",
                    "success": False,
                }
            summary = (
                f"[自动屏幕感知]\n"
                f"- 活动窗口：{ctx.get('active_window', '未知')}\n"
                f"- 应用类型：{ctx.get('app_type', '未知')}\n"
                f"- 当前状态：{ctx.get('status', '未知')}\n"
                f"- 关键元素：{', '.join(ctx.get('key_elements', []))}\n"
            )
            return {
                "thought": "[自动] 执行视觉操作前的屏幕状态感知",
                "tool": "screen_context",
                "target": target,
                "observation": summary,
                "success": True,
            }
        except Exception as e:
            logger.warning(f"自动屏幕感知异常: {e}")
            return None

    def _auto_verify_action(self, tool: str, target: str, observations: List[Dict]) -> Optional[Dict]:
        """在关键操作后自动验证结果，不消耗 ReAct 迭代。"""
        # 避免紧接着的 verify_action 重复
        for obs in reversed(observations):
            if obs.get("tool") == "verify_action":
                return None

        action_desc_map = {
            "vision_click": f"点击了「{target}」",
            "vision_type": f"在「{target}」输入了文字",
            "vision_send_message": f"发送了消息到「{target}」",
            "open_app": f"打开了「{target}」",
            "open_url": f"打开了网址「{target}」",
            "vision_find_and_open": f"通过视觉定位双击打开了「{target}」",
        }
        action_desc = action_desc_map.get(tool, f"执行了「{tool}」操作")
        expected = "操作成功，界面应有相应变化"

        try:
            result = self.vision.verify_action(action_desc, expected)
            if result.get("success"):
                return {
                    "thought": "[自动] 验证操作成功",
                    "tool": "verify_action",
                    "target": f"{action_desc}||{expected}",
                    "observation": (
                        f"[自动验证] ✓ 成功\n"
                        f"- 判断依据：{result.get('details', '')}\n"
                        f"- 可见元素：{', '.join(result.get('visible_elements', []))}\n"
                    ),
                    "success": True,
                }
            else:
                return {
                    "thought": "[自动] 验证操作失败",
                    "tool": "verify_action",
                    "target": f"{action_desc}||{expected}",
                    "observation": (
                        f"[自动验证] ✗ 失败\n"
                        f"- 判断依据：{result.get('details', '')}\n"
                        f"- 建议：{result.get('next_suggestion', '重试')}\n"
                        f"- 提示：Agent 下一轮应考虑重试或使用替代方案"
                    ),
                    "success": False,
                }
        except Exception as e:
            logger.warning(f"自动操作验证异常: {e}")
            return None

    # ---------------- 统一工具执行 ----------------

    def _execute_tool(self, tool: str, target: str) -> Tuple[bool, str, Dict[str, Any]]:
        """
        统一工具执行入口。返回 (是否成功, 观察结果文本, 动作详情)。
        观察结果文本会被回喂给 LLM 用于下一步决策。
        """
        logger.info(f"🛠️ 执行工具: {tool} | 目标: {target[:80]}")
        cdp_active = self._cdp_browser_active()
        logger.info(f"   CDP活跃: {cdp_active} | UIA可用: {self.automator.available}")

        # ---- 智能路由：当 CDP 浏览器活跃时，拦截错误的工具选择 ----
        if cdp_active and target:
            # 拦截："打开第N个" 格式 → 自动路由到 cdp_click_nth_result
            nth_match = re.search(r'第\s*(\d+)\s*[个条项只视频集]', target)
            if nth_match:
                n = int(nth_match.group(1))
                logger.info(f"🔀 智能路由: 检测到「第{n}个」，CDP活跃，重定向到 cdp_click_nth_result")
                ok, msg = self.browser.click_nth_result(n)
                return ok, msg, {
                    "action": "cdp_click_nth_result", "target": str(n),
                    "success": ok, "via": "smart_route",
                }

            # 拦截："第一个/首个/第一个视频" → cdp_click_first_result
            if re.search(r'第一个|首个|第一个视频|首个视频', target):
                logger.info("🔀 智能路由: 检测到「第一个」，CDP活跃，重定向到 cdp_click_first_result")
                ok, msg = self.browser.find_and_click_first_result()
                return ok, msg, {
                    "action": "cdp_click_first_result", "target": target,
                    "success": ok, "via": "smart_route",
                }

            # 拦截："打开" + 关键词匹配视频 → cdp_click_by_keyword
            if re.search(r'打开|点击|选择', target) and not re.search(r'第\s*\d+\s*[个条项只]', target):
                # 提取可能的关键词（排除"视频/结果"等通用词）
                keyword_part = re.sub(r'^(打开|点击|选择)\s*', '', target)
                keyword_part = re.sub(r'第\s*[一二三四五六七八九十百千]+\s*[个条项只]', '', keyword_part)
                keyword_part = re.sub(r'(视频|视频集|合集|系列|的)$', '', keyword_part).strip()
                # 如果关键词有效（长度>=2，且不是已知应用名）
                _known_apps = {'微信', 'QQ', '钉钉', '飞书', 'Chrome', 'Edge', '浏览器', '记事本',
                              '计算器', '画图', '设置', '资源管理器', '终端', 'PowerShell'}
                if keyword_part and len(keyword_part) >= 2 and keyword_part not in _known_apps:
                    logger.info(f"🔀 智能路由: 目标含关键词「{keyword_part}」，CDP活跃，尝试 cdp_click_by_keyword")
                    ok, msg = self.browser.click_result_by_keyword(keyword_part)
                    if ok:
                        return ok, msg, {
                            "action": "cdp_click_by_keyword", "target": keyword_part,
                            "success": ok, "via": "smart_route",
                        }
                    logger.info(f"关键词匹配失败，继续原工具流程: {msg}")

        if tool == "open_app":
            ok, msg = self.app_ctrl.open_app(target)
            return ok, f"打开「{target}」：{msg}", {"action": "open_app", "target": target, "success": ok}

        if tool == "close_app":
            ok, msg = self.app_ctrl.close_app(target)
            return ok, f"关闭「{target}」：{msg}", {"action": "close_app", "target": target, "success": ok}

        if tool == "search_and_open":
            # ⚠️ 新搜索，清除旧上下文
            self._last_observations = []
            self._last_action_info = {}
            ok, msg, chosen = self.searcher.search_and_open_best(target)
            obs = f"搜索并打开「{target}」：{msg}"
            if chosen:
                obs += f"\n选中结果：{chosen.get('title','')} - {chosen.get('url','')}"
            return ok, obs, {
                "action": "best_open", "target": target,
                "success": ok, "chosen": chosen,
            }

        if tool == "search":
            # ⚠️ 新搜索，清除旧上下文
            self._last_observations = []
            self._last_action_info = {}
            results = self.searcher.search(target, num_results=5)
            if results:
                lines = [f"搜索「{target}」返回 {len(results)} 条结果："]
                for i, r in enumerate(results[:5], 1):
                    lines.append(f"{i}. {r.get('title','')} - {r.get('url','')}")
                obs = "\n".join(lines)
            else:
                obs = f"搜索「{target}」没有找到结果"
            return bool(results), obs, {
                "action": "search", "target": target, "results": results,
            }

        if tool == "search_on_site":
            # target 格式: "site||keyword" 或 "site:keyword"
            site, keyword = self._parse_site_target(target)
            if not site or not keyword:
                return False, f"search_on_site 参数错误，需要 site||keyword 格式", {
                    "action": "search_on_site", "site": site, "keyword": keyword, "success": False,
                }
            # ⚠️ 关键：新的搜索请求必须清除旧的跨对话上下文，防止旧结果被错误复用
            self._last_observations = []
            self._last_action_info = {}
            logger.info(f"🔄 新搜索请求，已清除旧上下文，搜索: {site}||{keyword}")

            # CDP 优先：如果浏览器自动化可用，走 CDP 路径
            url = self.searcher.build_site_search_url(site, keyword)
            if url and self.browser.available:
                cdp_ok, cdp_msg = self.browser.open_url(url)
                if cdp_ok:
                    obs_msg = f"[CDP] 在「{site}」站内搜索「{keyword}」：{cdp_msg}\n💡 CDP浏览器已就绪，后续浏览器操作将自动通过CDP精准执行（点击/输入/截图均已路由）"
                    return True, obs_msg, {
                        "action": "cdp_open_url", "site": site, "keyword": keyword,
                        "success": True, "url": url, "via": "cdp",
                    }
                logger.info(f"CDP 打开 URL 失败，回退到系统浏览器: {cdp_msg}")
            # 回退：系统浏览器
            ok, msg, final_url = self.searcher.search_on_site(site, keyword)
            return ok, f"在「{site}」站内搜索「{keyword}」：{msg}\n构造URL：{final_url}", {
                "action": "search_on_site", "site": site, "keyword": keyword,
                "success": ok, "url": final_url, "via": "system_browser",
            }

        if tool == "open_url":
            # ⚠️ 新的页面导航，清除旧上下文
            self._last_observations = []
            self._last_action_info = {}
            logger.info(f"🔄 新URL导航请求，已清除旧上下文: {target[:50]}")

            # CDP 优先
            if self.browser.available:
                cdp_ok, cdp_msg = self.browser.open_url(target.strip())
                if cdp_ok:
                    obs_msg = f"[CDP] {cdp_msg}\n💡 CDP浏览器已就绪，后续浏览器操作将自动通过CDP精准执行（点击/输入/截图均已路由）"
                    return cdp_ok, obs_msg, {
                        "action": "cdp_open_url", "target": target, "success": True, "via": "cdp",
                    }
                logger.info(f"CDP 打开 URL 失败，回退到系统浏览器: {cdp_msg}")
            # 回退：系统浏览器
            ok = self.searcher.open_url(target)
            return ok, f"打开URL：{target} {'成功' if ok else '失败'}", {
                "action": "open_url", "target": target, "success": ok, "via": "system_browser",
            }

        if tool == "desktop_click":
            # target 格式: "window||control"
            window, control = self._parse_dual_target(target)
            if not window or not control:
                return False, f"desktop_click 参数错误，需要 window||control 格式", {
                    "action": "desktop_click", "window": window, "control": control, "success": False,
                }
            self.automator.activate_window(window)
            ok = self.automator.click_by_text(window, control)
            if not ok:
                ok = self.automator.click_button(window, control)
            return ok, f"在「{window}」中点击「{control}」{'成功' if ok else '失败'}", {
                "action": "desktop_click", "window": window, "control": control, "success": ok,
            }

        if tool == "desktop_type":
            ok = self.automator.type_text(target)
            return ok, f"输入文字「{target}」{'成功' if ok else '失败'}", {
                "action": "desktop_type", "text": target, "success": ok,
            }

        if tool == "desktop_send_message":
            # target 格式: "app||contact||message"
            parts = target.split("||", 2)
            if len(parts) < 3:
                parts = target.split("|||", 2)
            if len(parts) < 3:
                return False, f"desktop_send_message 参数错误，需要 app||contact||message 格式", {
                    "action": "desktop_send_message", "success": False,
                }
            app_name, contact, message = parts[0].strip(), parts[1].strip(), parts[2].strip()
            ok, msg = self.automator.search_and_send(app_name, contact, message)
            return ok, msg, {
                "action": "desktop_send_message", "app": app_name,
                "contact": contact, "message": message, "success": ok,
            }

        if tool == "desktop_hotkey":
            # target 格式: "key1+key2" 或 "key1||key2"
            key1, key2 = self._parse_hotkey_target(target)
            if not key1 or not key2:
                return False, f"desktop_hotkey 参数错误，需要 key1+key2 格式", {
                    "action": "desktop_hotkey", "success": False,
                }
            ok = self.automator._send_hotkey(key1, key2)
            return ok, f"发送快捷键 {key1}+{key2} {'成功' if ok else '失败'}", {
                "action": "desktop_hotkey", "key1": key1, "key2": key2, "success": ok,
            }

        if tool == "vision_click":
            # CDP 路由：如果浏览器已通过 CDP 打开，优先用 CDP 操作
            if self._cdp_browser_active():
                logger.info(f"🔀 智能路由: vision_click → CDP click (目标: {target})")
                cdp_ok, cdp_msg = self.browser.smart_click(target)
                if cdp_ok:
                    return True, f"[CDP] {cdp_msg}", {
                        "action": "cdp_click", "target": target, "success": True, "via": "smart_route",
                    }
                logger.info(f"CDP click 失败，回退视觉: {cdp_msg}")
            ok, msg = self.vision.click_element(target)
            return ok, msg, {
                "action": "vision_click", "target": target, "success": ok,
            }

        if tool == "vision_type":
            # target 格式: "输入框描述||要输入的文字"
            desc, text = self._parse_dual_target(target)
            if not desc or not text:
                return False, f"vision_type 参数错误，需要 输入框描述||文字 格式", {
                    "action": "vision_type", "success": False,
                }
            # CDP 路由：如果浏览器已通过 CDP 打开，优先用 CDP 操作
            if self._cdp_browser_active():
                logger.info(f"🔀 智能路由: vision_type → CDP type (目标: {desc})")
                cdp_ok, cdp_msg = self.browser.smart_type(desc, text)
                if cdp_ok:
                    return True, f"[CDP] {cdp_msg}", {
                        "action": "cdp_type", "target": target, "success": True, "via": "smart_route",
                    }
                logger.info(f"CDP type 失败，回退视觉: {cdp_msg}")
            ok, msg = self.vision.click_and_type(desc, text)
            return ok, msg, {
                "action": "vision_type", "target": target, "success": ok,
            }

        if tool == "vision_send_message":
            # target 格式: "软件名||联系人||消息"
            parts = target.split("||", 2)
            if len(parts) < 3:
                return False, f"vision_send_message 参数错误，需要 软件||联系人||消息 格式", {
                    "action": "vision_send_message", "success": False,
                }
            app_name, contact, message = parts[0].strip(), parts[1].strip(), parts[2].strip()
            ok, msg = self.vision.search_and_send(f"{app_name}搜索框", contact, message, f"{app_name}发送按钮")
            return ok, msg, {
                "action": "vision_send_message", "app": app_name,
                "contact": contact, "message": message, "success": ok,
            }

        if tool == "vision_screenshot":
            img_b64, w, h = self.vision.capture_screen()
            if not img_b64:
                return False, "截屏失败", {"action": "vision_screenshot", "success": False}
            # 让 VLM 分析
            prompt = f"请描述当前屏幕上的主要界面元素和可交互的控件。用户可能想：{target or '了解界面'}"
            analysis = self.vision.llm.vision_chat(prompt, img_b64)
            return True, f"屏幕分析结果：\n{analysis}", {
                "action": "vision_screenshot", "success": True, "analysis": analysis,
            }

        if tool == "vision_analyze":
            img_b64, w, h = self.vision.capture_screen()
            if not img_b64:
                return False, "截屏失败", {"action": "vision_analyze", "success": False}
            # 使用视觉分析 prompt
            prompt = self._build_visual_analysis_prompt(target or "你看到了什么")
            analysis = self.vision.llm.vision_chat(prompt, img_b64)
            return True, analysis, {
                "action": "vision_analyze", "success": True,
                "screenshot_size": f"{w}x{h}", "analysis": analysis,
            }

        if tool == "vision_click_first_result":
            # CDP 路由：如果浏览器已通过 CDP 打开，优先用 CDP
            desc = target.strip() if target.strip() else "第一个搜索结果"
            if self._cdp_browser_active():
                logger.info(f"🔀 智能路由: vision_click_first_result → CDP click_first_result")
                cdp_ok, cdp_msg = self.browser.find_and_click_first_result()
                if cdp_ok:
                    return True, f"[CDP] {cdp_msg}", {
                        "action": "cdp_click_first_result", "target": desc, "success": True, "via": "smart_route",
                    }
                logger.info(f"CDP click_first_result 失败，回退视觉: {cdp_msg}")
            ok, msg, info = self.vision.click_first_result(desc)
            return ok, msg, {
                "action": "vision_click_first_result", "target": desc,
                "success": ok, "info": info,
            }

        if tool == "vision_find_and_open":
            desc = target.strip()
            if not desc:
                return False, "vision_find_and_open 需要指定要打开的元素", {
                    "action": "vision_find_and_open", "success": False,
                }
            ok, msg = self.vision.find_and_open(desc)
            return ok, msg, {
                "action": "vision_find_and_open", "target": desc, "success": ok,
            }

        # ---------- CDP 浏览器自动化（第3层） ----------
        if tool == "cdp_open_url":
            ok, msg = self.browser.open_url(target.strip())
            return ok, msg, {"action": "cdp_open_url", "target": target, "success": ok}

        if tool == "cdp_click":
            desc = target.strip()
            ok, msg = self.browser.smart_click(desc)
            return ok, msg, {"action": "cdp_click", "target": desc, "success": ok}

        if tool == "cdp_type":
            parts = target.split("||", 1)
            if len(parts) != 2:
                return False, "cdp_type 需要格式: 输入框||文字", {"action": "cdp_type", "success": False}
            
            field_name = parts[0].strip()
            text = parts[1].strip()
            
            # 智能检测：如果目标是搜索框，自动走 search_on_page
            search_keywords = ["搜索框", "搜索框", "search", "search box", "searchbox", "搜索", "search field"]
            is_search_field = any(kw in field_name.lower() for kw in search_keywords)
            
            if is_search_field and self._cdp_browser_active():
                logger.info(f"🔀 智能路由: cdp_type(搜索框) → search_on_page({text})")
                ok, msg = self.browser.search_on_page(text)
                if ok:
                    return True, f"[CDP] 检测到搜索框需求，已自动跳转搜索页：{msg}", {
                        "action": "cdp_search_page", "target": text, "success": True, "via": "smart_route",
                    }
                logger.info(f"search_on_page 失败，回退到 type_in_field: {msg}")
            
            ok, msg = self.browser.smart_type(field_name, text)
            return ok, msg, {"action": "cdp_type", "target": target, "success": ok}

        if tool == "cdp_search_page":
            ok, msg = self.browser.search_on_page(target.strip())
            return ok, msg, {"action": "cdp_search_page", "target": target, "success": ok}

        if tool == "cdp_page_content":
            text = self.browser.get_page_text(max_length=3000)
            if text:
                return True, f"【CDP 页面内容】\n{text[:2000]}", {
                    "action": "cdp_page_content", "success": True,
                }
            return False, "获取页面内容失败", {"action": "cdp_page_content", "success": False}

        if tool == "cdp_click_first_result":
            ok, msg = self.browser.find_and_click_first_result()
            return ok, msg, {"action": "cdp_click_first_result", "target": target, "success": ok}

        if tool == "cdp_list_results":
            max_n = 20
            try:
                if target.strip():
                    parsed = int(target.strip())
                    if 1 <= parsed <= 100:
                        max_n = parsed
            except ValueError:
                # 非数字参数（如描述性文字），使用默认值
                pass
            ok, msg = self.browser.list_search_results(max_results=max_n)
            return ok, msg, {"action": "cdp_list_results", "target": target, "success": ok}

        if tool == "cdp_click_nth_result":
            try:
                n = int(target.strip())
            except ValueError:
                return False, f"cdp_click_nth_result 需要数字编号，收到: {target}", {
                    "action": "cdp_click_nth_result", "success": False,
                }
            ok, msg = self.browser.click_nth_result(n)
            return ok, msg, {"action": "cdp_click_nth_result", "target": target, "success": ok}

        if tool == "cdp_click_by_keyword":
            keyword = target.strip()
            if not keyword:
                return False, "cdp_click_by_keyword 需要关键词", {
                    "action": "cdp_click_by_keyword", "success": False,
                }
            ok, msg = self.browser.click_result_by_keyword(keyword)
            return ok, msg, {"action": "cdp_click_by_keyword", "target": target, "success": ok}

        # ---------- UIA 桌面自动化（第1层） ----------
        if tool == "uia_screen_context":
            ctx = self.automator.get_screen_context()
            if not ctx.get("available", False):
                # 回退到视觉方案
                focus = target.strip()
                v_ctx = self.vision.screen_context(focus)
                return True, f"【UIA 不可用，已回退到视觉方案】\n{json.dumps(v_ctx, ensure_ascii=False, indent=2)}", {
                    "action": "uia_screen_context", "success": True, "fallback": "vision",
                }
            summary_parts = [f"【UIA 屏幕感知】\n"]
            win = ctx.get("window", {})
            summary_parts.append(f"- 窗口: {win.get('title', '未知')}")
            summary_parts.append(f"- 句柄: {win.get('handle', 'N/A')}")
            elements = ctx.get("key_elements", [])
            if elements:
                summary_parts.append(f"- 关键控件({len(elements)}个):")
                for elem in elements[:20]:
                    summary_parts.append(f"  [{elem.get('type','')}] {elem.get('name','')} @ {elem.get('rect','')}")
            else:
                summary_parts.append("- 无可识别控件")
            return True, "\n".join(summary_parts), {
                "action": "uia_screen_context", "success": True, "context": ctx,
            }

        if tool == "uia_click":
            desc = target.strip()
            ok, msg = self.automator.smart_click(desc)
            return ok, msg, {"action": "uia_click", "target": desc, "success": ok}

        if tool == "uia_type":
            parts = target.split("||", 1)
            if len(parts) == 2:
                ok, msg = self.automator.smart_type(parts[0].strip(), parts[1].strip())
            else:
                ok, msg = False, "uia_type 需要格式: 输入框名称||文字"
            return ok, msg, {"action": "uia_type", "target": target, "success": ok}

        if tool == "screen_context":
            # 获取屏幕结构化概览
            focus = target.strip()
            ctx = self.vision.screen_context(focus)
            if "error" in ctx:
                return False, f"屏幕感知失败：{ctx['error']}", {
                    "action": "screen_context", "success": False, "error": ctx["error"],
                }
            summary = (
                f"【屏幕感知】\n"
                f"- 活动窗口：{ctx.get('active_window', '未知')}\n"
                f"- 应用类型：{ctx.get('app_type', '未知')}\n"
                f"- 当前状态：{ctx.get('status', '未知')}\n"
                f"- 关键元素：{', '.join(ctx.get('key_elements', []))}\n"
                f"- 建议操作：{', '.join(ctx.get('suggestions', []))}"
            )
            return True, summary, {"action": "screen_context", "success": True, "context": ctx}

        if tool == "verify_action":
            # target 格式: "操作描述||期望结果"
            action_desc, expected = self._parse_dual_target(target)
            if not action_desc:
                return False, "verify_action 需要 操作描述||期望结果 格式", {
                    "action": "verify_action", "success": False,
                }
            result = self.vision.verify_action(action_desc, expected)
            if result.get("success"):
                return True, (
                    f"【操作验证】成功！\n"
                    f"- 判断依据：{result.get('details', '')}\n"
                    f"- 可见元素：{', '.join(result.get('visible_elements', []))}\n"
                    f"- 下一步建议：{result.get('next_suggestion', '')}"
                ), {"action": "verify_action", "success": True, "result": result}
            else:
                return False, (
                    f"【操作验证】失败。\n"
                    f"- 判断依据：{result.get('details', '')}\n"
                    f"- 下一步建议：{result.get('next_suggestion', '重试')}"
                ), {"action": "verify_action", "success": False, "result": result}

        if tool == "watch_screen":
            # 监控屏幕等待条件
            condition = target.strip()
            if not condition:
                return False, "watch_screen 需要指定等待条件", {
                    "action": "watch_screen", "success": False,
                }
            result = self.vision.watch_screen(condition, timeout=30)
            if result.get("matched"):
                return True, (
                    f"【屏幕监控】检测到条件满足：{condition}\n"
                    f"- 判断依据：{result.get('detail', '')}\n"
                    f"- 耗时：{result.get('elapsed', 0)}秒"
                ), {"action": "watch_screen", "success": True, "result": result}
            else:
                return False, (
                    f"【屏幕监控】超时，未检测到条件：{condition}\n"
                    f"- 最后状态：{result.get('last_state', '未知')}"
                ), {"action": "watch_screen", "success": False, "result": result}

        # ---------- 文件操作工具 ----------

        if tool == "read_file":
            r = self.file_op.read_file(target)
            if r.get("success"):
                content = r.get("content", "")
                preview = content[:3000] + ("..." if len(content) > 3000 else "")
                return True, f"读取文件「{target}」成功（{r.get('total_lines',0)}行）：\n{preview}", {
                    "action": "read_file", "file": target, "success": True,
                }
            return False, f"读取文件失败：{r.get('error','')}", {"action": "read_file", "success": False}

        if tool == "write_file":
            # target 格式: "路径||内容"
            path, content = self._parse_dual_target(target)
            if not path:
                return False, "write_file 需要 文件路径||内容 格式", {"action": "write_file", "success": False}
            r = self.file_op.write_text(path, content)
            if r.get("success"):
                return True, f"写入文件「{path}」成功（{r.get('bytes_written',0)}字节）", {
                    "action": "write_file", "file": path, "success": True,
                }
            return False, f"写入文件失败：{r.get('error','')}", {"action": "write_file", "success": False}

        if tool == "read_excel":
            r = self.file_op.read_excel(target)
            if r.get("success"):
                headers = r.get("headers", [])
                data = r.get("data", [])
                preview_lines = [f"读取 Excel「{target}」成功："]
                preview_lines.append(f"工作表: {r.get('sheet_name','')}, 共 {r.get('total_rows',0)} 行 {r.get('total_cols',0)} 列")
                if headers:
                    preview_lines.append(f"表头: {', '.join(str(h) for h in headers)}")
                if data:
                    preview_lines.append("前5行数据：")
                    for i, row in enumerate(data[:5], 1):
                        preview_lines.append(f"  {i}. {row}")
                return True, "\n".join(preview_lines), {
                    "action": "read_excel", "file": target, "success": True,
                }
            return False, f"读取 Excel 失败：{r.get('error','')}", {"action": "read_excel", "success": False}

        if tool == "excel_stats":
            r = self.file_op.excel_statistics(target)
            if r.get("success"):
                stats = r.get("columns", {})
                lines = [f"Excel 统计分析「{target}」："]
                for col_name, col_stats in stats.items():
                    lines.append(f"\n列「{col_name}」：")
                    for k, v in col_stats.items():
                        if isinstance(v, dict):
                            lines.append(f"  {k}: {', '.join(f'{dk}={dv}' for dk,dv in v.items())}")
                        else:
                            lines.append(f"  {k}: {v}")
                return True, "\n".join(lines), {
                    "action": "excel_stats", "file": target, "success": True,
                }
            return False, f"Excel 统计失败：{r.get('error','')}", {"action": "excel_stats", "success": False}

        if tool == "read_csv":
            r = self.file_op.read_csv(target)
            if r.get("success"):
                headers = r.get("headers", [])
                data = r.get("data", [])
                lines = [f"读取 CSV「{target}」成功（{r.get('row_count',0)}行）："]
                if headers:
                    lines.append(f"表头: {', '.join(str(h) for h in headers)}")
                if data:
                    lines.append("前5行：")
                    for i, row in enumerate(data[:5], 1):
                        lines.append(f"  {i}. {row}")
                return True, "\n".join(lines), {
                    "action": "read_csv", "file": target, "success": True,
                }
            return False, f"读取 CSV 失败：{r.get('error','')}", {"action": "read_csv", "success": False}

        if tool == "create_word":
            # target 格式: "路径||标题||段1|||段2|||表格行1|列1|列1;表格行2|列2|列2"
            parts = target.split("||", 2)
            if len(parts) < 2:
                return False, "create_word 需要 路径||标题||内容 格式", {"action": "create_word", "success": False}
            path = parts[0].strip()
            title = parts[1].strip() if len(parts) > 1 else ""
            rest = parts[2] if len(parts) > 2 else ""

            # 分离段落和表格
            paragraphs = []
            table_data = None
            if "|||" in rest:
                text_part, table_part = rest.split("|||", 1)
                paragraphs = [p.strip() for p in text_part.split("||") if p.strip()]
                if table_part.strip():
                    table_data = []
                    for row_line in table_part.split(";"):
                        cells = [c.strip() for c in row_line.split("|") if c.strip()]
                        if cells:
                            table_data.append(cells)
            else:
                paragraphs = [p.strip() for p in rest.split("||") if p.strip()]

            r = self.file_op.create_word(path, title, paragraphs, table_data)
            if r.get("success"):
                return True, f"生成 Word 文档「{path}」成功", {
                    "action": "create_word", "file": path, "success": True,
                }
            return False, f"Word 生成失败：{r.get('error','')}", {"action": "create_word", "success": False}

        if tool == "list_files":
            # target 格式: "文件夹路径||过滤模式"
            folder, pattern = self._parse_dual_target(target)
            if not folder:
                return False, "list_files 需要文件夹路径", {"action": "list_files", "success": False}
            r = self.file_op.list_files(folder, pattern or "*")
            if r.get("success"):
                files = r.get("files", [])
                lines = [f"「{folder}」中的文件（{r.get('file_count',0)}个）："]
                for f in files[:20]:
                    lines.append(f"  - {f['name']} ({self.file_op._format_size(f['size'])}, {f['modified']})")
                if len(files) > 20:
                    lines.append(f"  ... 还有 {len(files)-20} 个文件")
                return True, "\n".join(lines), {
                    "action": "list_files", "folder": folder, "success": True,
                }
            return False, f"列出文件失败：{r.get('error','')}", {"action": "list_files", "success": False}

        if tool == "search_files":
            # target 格式: "文件夹路径||关键字"
            folder, keyword = self._parse_dual_target(target)
            if not folder or not keyword:
                return False, "search_files 需要 文件夹||关键字 格式", {"action": "search_files", "success": False}
            r = self.file_op.search_files(folder, keyword)
            if r.get("success"):
                matches = r.get("matches", [])
                lines = [f"在「{folder}」中搜索「{keyword}」，找到 {r.get('match_count',0)} 个："]
                for m in matches[:15]:
                    lines.append(f"  - {m['path']}")
                return True, "\n".join(lines), {
                    "action": "search_files", "folder": folder, "keyword": keyword, "success": True,
                }
            return False, f"搜索失败：{r.get('error','')}", {"action": "search_files", "success": False}

        if tool == "organize_by_date":
            r = self.file_op.organize_by_date(target)
            if r.get("success"):
                return True, f"按日期整理「{target}」完成：移动了 {r.get('moved_count',0)} 个文件到「{r.get('target_folder','')}」", {
                    "action": "organize_by_date", "folder": target, "success": True,
                }
            return False, f"整理失败：{r.get('error','')}", {"action": "organize_by_date", "success": False}

        if tool == "organize_by_type":
            r = self.file_op.organize_by_type(target)
            if r.get("success"):
                return True, f"按类型整理「{target}」完成：移动了 {r.get('moved_count',0)} 个文件到「{r.get('target_folder','')}」", {
                    "action": "organize_by_type", "folder": target, "success": True,
                }
            return False, f"整理失败：{r.get('error','')}", {"action": "organize_by_type", "success": False}

        if tool == "file_info":
            r = self.file_op.get_file_info(target)
            if r.get("success"):
                return True, f"文件信息「{target}」：\n大小: {r.get('size_readable','')}\n创建: {r.get('created','')}\n修改: {r.get('modified','')}\n路径: {r.get('path','')}", {
                    "action": "file_info", "file": target, "success": True,
                }
            return False, f"获取文件信息失败：{r.get('error','')}", {"action": "file_info", "success": False}

        # ---------- 定时任务工具 ----------

        if tool == "add_schedule":
            if not self.scheduler:
                return False, "定时调度器未初始化", {"action": "add_schedule", "success": False}
            # target 格式: "任务名||时间表达式||prompt"
            parts = target.split("||", 2)
            if len(parts) < 3:
                return False, "add_schedule 需要 任务名||时间表达式||执行指令 格式", {"action": "add_schedule", "success": False}
            name, time_expr, prompt = parts[0].strip(), parts[1].strip(), parts[2].strip()

            # 判断时间表达式类型
            interval = 0
            cron = ""
            run_at = ""
            if time_expr.isdigit():
                interval = int(time_expr)
            elif re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", time_expr):
                run_at = time_expr
            else:
                cron = time_expr

            r = self.scheduler.add_task(name, prompt, cron=cron,
                                        interval_minutes=interval, run_at=run_at)
            if r.get("success"):
                return True, f"定时任务创建成功：\n名称: {name}\n触发: {time_expr}\n指令: {prompt}\n下次执行: {r['task'].get('next_run','')}", {
                    "action": "add_schedule", "task_id": r['task'].get('task_id',''), "success": True,
                }
            return False, f"创建任务失败", {"action": "add_schedule", "success": False}

        if tool == "list_schedules":
            if not self.scheduler:
                return False, "定时调度器未初始化", {"action": "list_schedules", "success": False}
            tasks = self.scheduler.list_tasks()
            if not tasks:
                return True, "当前没有定时任务", {"action": "list_schedules", "success": True}
            lines = [f"定时任务列表（共 {len(tasks)} 个）："]
            for t in tasks:
                status = "✅" if t.get("enabled") else "❌"
                trigger = t.get("cron") or f"每{t.get('interval_minutes',0)}分钟" or t.get("run_at", "")
                lines.append(f"  {status} [{t['task_id']}] {t['name']} | 触发: {trigger} | 下次: {t.get('next_run','')} | 已执行: {t.get('run_count',0)}次")
                lines.append(f"      指令: {t['prompt'][:80]}")
            return True, "\n".join(lines), {"action": "list_schedules", "success": True}

        if tool == "remove_schedule":
            if not self.scheduler:
                return False, "定时调度器未初始化", {"action": "remove_schedule", "success": False}
            r = self.scheduler.remove_task(target.strip())
            if r.get("success"):
                return True, f"定时任务已删除", {"action": "remove_schedule", "success": True}
            return False, f"删除失败：{r.get('error','')}", {"action": "remove_schedule", "success": False}

        if tool == "toggle_schedule":
            if not self.scheduler:
                return False, "定时调度器未初始化", {"action": "toggle_schedule", "success": False}
            # target 格式: "task_id||enable/disable"
            task_id, action = self._parse_dual_target(target)
            if not task_id:
                return False, "toggle_schedule 需要 任务ID||enable/disable 格式", {"action": "toggle_schedule", "success": False}
            enabled = action.strip().lower() in ("enable", "true", "on", "启用")
            r = self.scheduler.toggle_task(task_id, enabled=enabled)
            if r.get("success"):
                return True, f"任务已{'启用' if enabled else '禁用'}", {"action": "toggle_schedule", "success": True}
            return False, f"操作失败：{r.get('error','')}", {"action": "toggle_schedule", "success": False}

        if tool == "run_schedule_now":
            if not self.scheduler:
                return False, "定时调度器未初始化", {"action": "run_schedule_now", "success": False}
            r = self.scheduler.run_task_now(target.strip())
            if r.get("success"):
                return True, f"任务已手动触发执行", {"action": "run_schedule_now", "success": True}
            return False, f"执行失败：{r.get('error','')}", {"action": "run_schedule_now", "success": False}

        # ---------- 音乐工具 ----------
        if tool == "play_music":
            platform_name = None
            keyword = target.strip()
            for sep in ["||", "|", "：", ":"]:
                if sep in keyword:
                    parts = keyword.split(sep, 1)
                    platform_name = parts[0].strip()
                    keyword = parts[1].strip()
                    break
            if not keyword:
                return False, "请告诉我要听什么歌", {"action": "play_music", "success": False}
            ok, msg, info = self.music.play_by_name(keyword, platform_name)
            return ok, msg, {
                "action": "play_music",
                "target": keyword,
                "success": ok,
                "preview": info.get("preview", False) if isinstance(info, dict) else False,
                "song_name": info.get("song_name", "") if isinstance(info, dict) else keyword,
                "artist": info.get("artist", "") if isinstance(info, dict) else "",
            }

        if tool == "search_music":
            platform_name = None
            keyword = target.strip()
            for sep in ["||", "|", "：", ":"]:
                if sep in keyword:
                    parts = keyword.split(sep, 1)
                    platform_name = parts[0].strip()
                    keyword = parts[1].strip()
                    break
            if not keyword:
                return False, "请告诉我要搜索什么歌", {"action": "search_music", "success": False}
            ok, msg, songs = self.music.search(keyword, limit=10, platform_name=platform_name)
            return ok, msg, {"action": "search_music", "target": keyword, "success": ok, "songs_count": len(songs)}

        if tool == "list_music":
            ok, msg = self.music.list_playlist()
            return ok, msg, {"action": "list_music", "success": ok}

        if tool == "music_control":
            cmd = target.strip().lower()
            if cmd in ("pause", "暂停"):
                ok, msg = self.music.pause()
            elif cmd in ("resume", "继续", "恢复"):
                ok, msg = self.music.resume()
            elif cmd in ("next", "下一首", "下一个"):
                ok, msg, _info = self.music.play_next()
            elif cmd in ("prev", "上一首", "上一个"):
                ok, msg, _info = self.music.play_prev()
            elif cmd in ("stop", "停止", "停", "关掉"):
                ok, msg = self.music.stop()
            else:
                return False, f"未知音乐控制命令: {cmd}（支持: pause/resume/next/prev/stop）", {
                    "action": "music_control", "success": False}
            return ok, msg, {"action": "music_control", "target": cmd, "success": ok}

        if tool == "music_lyrics":
            ok, msg = self.music.get_lyrics(target.strip())
            return ok, msg, {"action": "music_lyrics", "target": target, "success": ok}

        # arXiv 论文搜索
        if tool == "search_arxiv":
            target_str = target.strip()
            category = None
            keyword = target_str
            for sep in ["||", "|", "：", ":"]:
                if sep in target_str:
                    parts = target_str.split(sep, 1)
                    category = parts[0].strip()
                    keyword = parts[1].strip()
                    break
            if not keyword:
                return False, "请告诉我要搜索什么论文", {"action": "search_arxiv", "success": False}
            ok, result = self.arxiv.search(keyword, max_results=5, category=category)
            if not ok:
                return False, f"arXiv 搜索失败：{result}", {"action": "search_arxiv", "target": keyword, "success": False}
            papers = result
            formatted = self.arxiv.format_papers(papers)
            return True, formatted, {"action": "search_arxiv", "target": keyword, "category": category, "papers_count": len(papers), "success": True}

        if tool == "arxiv_paper_detail":
            arxiv_id = target.strip()
            if not arxiv_id:
                return False, "请提供 arXiv ID", {"action": "arxiv_paper_detail", "success": False}
            ok, paper = self.arxiv.search_by_id(arxiv_id)
            if not ok:
                return False, str(paper), {"action": "arxiv_paper_detail", "target": arxiv_id, "success": False}
            formatted = self.arxiv.format_single_paper(paper)
            return True, formatted, {"action": "arxiv_paper_detail", "target": arxiv_id, "success": True}

        if tool == "arxiv_author_search":
            author = target.strip()
            if not author:
                return False, "请提供作者姓名", {"action": "arxiv_author_search", "success": False}
            ok, papers = self.arxiv.search_by_author(author, max_results=5)
            if not ok:
                return False, f"作者搜索失败：{papers}", {"action": "arxiv_author_search", "target": author, "success": False}
            formatted = self.arxiv.format_papers(papers)
            return True, formatted, {"action": "arxiv_author_search", "target": author, "papers_count": len(papers), "success": True}

        # 未知工具
        return False, f"未知工具：{tool}", {"action": "unknown", "tool": tool, "success": False}

    @staticmethod
    def _parse_site_target(target: str) -> Tuple[str, str]:
        """
        解析 search_on_site 的 target 参数。
        LLM 可能输出 "B站||python" 或 "B站:python" 或 "B站 python"。
        """
        # 尝试多种分隔符
        for sep in ["||", "|", "：", ":", " "]:
            if sep in target:
                parts = target.split(sep, 1)
                site = parts[0].strip()
                keyword = parts[1].strip()
                if site and keyword:
                    return site, keyword
        # 只有一个词，推测为网站名，keyword 为空
        return target.strip(), ""

    @staticmethod
    def _parse_dual_target(target: str) -> Tuple[str, str]:
        """解析需要两个参数的工具（如 desktop_click: window||control）。"""
        for sep in ["||", "|", "：", ":"]:
            if sep in target:
                parts = target.split(sep, 1)
                left = parts[0].strip()
                right = parts[1].strip()
                if left and right:
                    return left, right
        # 只有一个值时，直接当 window 名，control 为空
        return target.strip(), ""

    def _cdp_browser_active(self) -> bool:
        """检查 CDP 浏览器是否已打开且活跃。"""
        if not self.browser.available:
            return False
        try:
            worker = self.browser._worker
            if worker is None:
                return False
            # 通过工作线程检测页面是否存活
            result = worker._call(worker.get_current_page)
            if result is None:
                return False
            # 检查是否能访问 URL
            try:
                _ = result.url
                return True
            except Exception:
                return False
        except Exception as e:
            logger.debug(f"CDP 浏览器检测异常: {e}")
            return False

    @staticmethod
    def _parse_hotkey_target(target: str) -> Tuple[str, str]:
        """解析快捷键参数（如 ctrl+v、ctrl||v）。"""
        for sep in ["+", "||", "|", "：", ":"]:
            if sep in target:
                parts = target.split(sep, 1)
                key1 = parts[0].strip()
                key2 = parts[1].strip()
                if key1 and key2:
                    return key1, key2
        return target.strip(), ""

    # ---------------- 记忆 ----------------

    def _async_add_memory(self, conv_text: str, session_id: str):
        """后台异步提取并写入记忆。"""
        try:
            self.memory.add_memory(conv_text, session_id)
        except Exception:
            pass

    # ---------------- 正则意图识别（快速路径） ----------------

    OPEN_PATTERNS = [
        # 在X盘打开XX文件夹
        re.compile(r"^(?:请?(?:帮我|帮|替我|替))?(?:在|去)([A-Za-z])盘(?:里)?(?:打开|启动|运行|点开|开启|进|进一下|看一下)\s*(?:一下|下)?(.+?)(?:文件夹|目录|文件)?\s*[。.！!？?]*$"),
        re.compile(r"^(?:请?(?:帮我|帮|替我|替))?(?:打开|启动|运行|点开|开启|进|进一下|启动一下)\s*(?:一下|下)?([A-Za-z])盘(?:里)?[\\\\/]?(.+?)(?:文件夹|目录|文件)?\s*[。.！!？?]*$"),
        re.compile(r"^(?:请?(?:帮我|帮|替我|替))?(?:打开|启动|运行|点开|开启|进|进一下|启动一下)\s*(?:一下|下)?(.+?)(?:软件|程序|应用|文件)?\s*[。.！!？?]*$"),
        re.compile(r"^我想(?:打开|启动|运行|看一下|用)\s*(?:一下|下)?(.+?)(?:软件|程序|应用|文件)?\s*[。.！!？?]*$"),
        re.compile(r"^(.+?)(?:给我|帮我)?(?:打开|启动|运行)\s*(?:一下|下)?\s*[。.！!？?]*$"),
    ]

    CLOSE_PATTERNS = [
        re.compile(r"^(?:请?(?:帮我|帮|替我|替))?(?:关闭|关掉|关|退出|结束|停止)\s*(?:一下|下)?(.+?)(?:软件|程序|应用|进程)?\s*[。.！!？?]*$"),
        re.compile(r"^我想(?:关闭|关掉|关|退出|结束)\s*(?:一下|下)?(.+?)(?:软件|程序|应用|进程)?\s*[。.！!？?]*$"),
    ]

    SEARCH_BEST_PATTERNS = [
        # 带"打开浏览器"的模式
        re.compile(r"^(?:请?(?:帮我|帮|替我|替))?(?:打开|开|启动)?(?:浏览器|网页)?\s*(?:搜一下|搜搜|搜索|查一下|查一查|查|找一下|找找|百度一下|谷歌一下|bing一下)\s*(?:一下|下)?(.+?)(?:信息|内容|资料)?\s*[。.！!？?]*$"),
        re.compile(r"^(?:请?(?:帮我|帮|替我|替))?(?:搜一下|搜搜|搜索|查一下|查一查|查|找一下|找找|百度一下|谷歌一下|bing一下)\s*(?:一下|下)?(.+?)(?:信息|内容|资料)?\s*[。.！!？?]*$"),
        re.compile(r"^我想(?:搜索|搜一下|查一下|查一查|找一下|了解)\s*(?:一下|下)?(.+?)(?:信息|内容|资料)?\s*[。.！!？?]*$"),
    ]

    SEARCH_ONLY_PATTERNS = [
        re.compile(r"^(?:请?(?:帮我|帮))?列出?(.+?)的?搜索结果\s*[。.！!？?]*$"),
        re.compile(r"^只(?:搜索|搜|查)(?:一下|下)?(.+?)(?:不用打开|不打开|不要打开)\s*[。.！!？?]*$"),
    ]

    MUSIC_PLAY_PATTERNS = [
        re.compile(r"^(?:请?(?:帮我|帮|替我|替))?(?:点歌|点一首|来一首|放首|放一首|来首|播放|放|听|听歌|点)\s*(?:一下|下)?(.+?)(?:的歌|歌曲|音乐)?\s*[。.！!？?]*$"),
        re.compile(r"^我想(?:听|点|播放)\s*(?:一下|下)?(.+?)(?:的歌|歌曲|音乐)?\s*[。.！!？?]*$"),
        re.compile(r"^(?:推荐|放|来|点)\s*(?:一下|下)?(.+?)(?:的歌|歌曲|音乐)\s*[。.！!？?]*$"),
    ]

    MUSIC_CONTROL_PATTERNS = [
        re.compile(r"^(?:暂停|停一下|停|继续播放|继续|恢复播放|恢复|停止播放|停止)\s*[。.！!？?]*$"),
        re.compile(r"^(?:下一首|下一个|下一曲)\s*(?:.+)?[。.！!？?]*$"),
        re.compile(r"^(?:上一首|上一个|上一曲)\s*(?:.+)?[。.！!？?]*$"),
        re.compile(r"^(?:换一首|换个|换首|换|再来一首|再来一个)\s*(.+)?[。.！!？?]*$"),
        re.compile(r"^(?:歌单|我的歌单|播放列表|列出歌曲|音乐列表|查看音乐|查看歌单|当前.*?多少.*?音乐|有多少.*?歌曲|列出.*?音乐|所有.*?歌曲|看看.*?歌单|看看.*?音乐)\s*[。.！!？?]*$"),
        re.compile(r"^(?:歌词|歌词.*?)(.+?)\s*[。.！!？?]*$"),
    ]

    ARXIV_PATTERNS = [
        # 帮我搜一下/查一下/找一下 arXiv上的XX论文
        re.compile(r"(?:请?(?:帮我|帮|替我|替)\s*)?(?:搜|查|找|看看|查找|浏览)\s*(?:索)?\s*(?:一下|下)?\s*(?:arXiv|arxiv)\s*(?:上的|里的|的)?\s*(?:最新|近期|最近)?\s*(.+?)\s*[。.！!？?]*$", re.IGNORECASE),
        # arXiv上的XX论文 / arXiv最新XX  (以arXiv开头)
        re.compile(r"^(?:arXiv|arxiv)\s*(?:上的|里的|的)?\s*(?:最新|近期|最近)?\s*(.+?)\s*[。.！!？?]*$", re.IGNORECASE),
        # 最新/近期/最近 + 的 + arXiv/论文 + XX
        re.compile(r"^(?:最新|近期|最近|今日)\s*(?:的)?\s*(?:arXiv|arxiv|论文|paper)\s*(?:关于|对于|有关)?\s*(.+?)\s*[。.！!？?]*$", re.IGNORECASE),
        # XX 的 arXiv 论文
        re.compile(r"^(.+?)\s*(?:的)\s*(?:arXiv|arxiv)\s*(?:论文|paper|研究)?\s*[。.！!？?]*$", re.IGNORECASE),
        # arXiv 关于 XX 的论文
        re.compile(r"^(?:arXiv|arxiv)\s*(?:关于|对于|有关)?\s*(.+?)\s*(?:的)?\s*(?:论文|paper|研究|文章)\s*[。.！!？?]*$", re.IGNORECASE),
        # 搜索/查找 + 论文/paper + XX
        re.compile(r"(?:搜|查|找|看看|查找|浏览)\s*(?:索)?\s*(?:一下|下)?\s*(?:论文|paper)\s*(?:关于|对于|有关)?\s*(.+?)\s*[。.！!？?]*$", re.IGNORECASE),
        # arXiv论文 XX
        re.compile(r"^(?:arXiv|arxiv)\s*(?:论文|paper)?\s*(.+?)\s*[。.！!？?]*$", re.IGNORECASE),
    ]

    ARXIV_AUTHOR_PATTERNS = [
        re.compile(r"^(?:arXiv|arxiv).*?(?:作者|author).*?\s*(.+?)\s*[。.！!？?]*$", re.IGNORECASE),
        re.compile(r"^作者\s*(.+?)\s*(?:的|最新|最近)?\s*(?:论文|paper|arXiv|arxiv)\s*[。.！!？?]*$", re.IGNORECASE),
    ]

    def _fallback_bilibili_search(self, song_name: str, artist: str = "", stream_callback=None) -> str:
        """试听片段回退：在B站搜索歌曲名+歌手（最多收藏排序）并打开第一个视频。"""
        try:
            parts = [song_name]
            if artist:
                parts.append(artist)
            query = " ".join(parts)
            logger.info(f"[B站回退] 搜索关键词: {query} (排序: 最多收藏)")

            if stream_callback:
                stream_callback(f"🔍 正在B站搜索「{query}」(最多收藏)...\n")

            ok, msg = self.browser.bilibili_search(query, order="stow")
            if not ok:
                logger.warning(f"[B站回退] 打开B站搜索失败: {msg}")
                return f"无法打开B站搜索：{msg}"

            if stream_callback:
                stream_callback(f"✅ 已打开B站搜索页(最多收藏)，正在点击第一个视频...\n")

            time.sleep(1.5)

            ok, msg = self.browser.find_and_click_first_result()
            if ok:
                logger.info(f"[B站回退] 已点击第一个视频: {msg}")
                return f"已在B站打开「{song_name} - {artist}」的第一个视频，请在浏览器中观看。"
            else:
                logger.warning(f"[B站回退] 点击第一个视频失败: {msg}")
                return f"已在B站搜索「{query}」，但未能自动打开视频，请手动点击。"
        except Exception as e:
            logger.error(f"[B站回退] 异常: {e}", exc_info=True)
            return f"B站回退搜索失败：{e}"

    def _fast_intent(self, text: str) -> Tuple[str, str]:
        """正则快速意图识别。返回 (intent, target)。"""
        t = text.strip()
        # 音乐意图优先检测
        for p in self.MUSIC_PLAY_PATTERNS:
            m = p.match(t)
            if m:
                logger.info(f"[快速意图] play_music: {t} -> {self._clean_target(m.group(1))}")
                return "play_music", self._clean_target(m.group(1))
        for p in self.MUSIC_CONTROL_PATTERNS:
            m = p.match(t)
            if m:
                cmd = self._clean_target(m.group(0))
                ctrl = self._resolve_music_command(cmd)
                if ctrl:
                    logger.info(f"[快速意图] music_control: {t} -> {ctrl}")
                    return "music_control", ctrl
                if any(k in cmd for k in ("歌单", "播放列表", "列出歌曲", "音乐列表", "查看音乐", "查看歌单", "列出音乐", "所有歌曲", "多少音乐", "多少歌曲", "看看歌单", "看看音乐")):
                    logger.info(f"[快速意图] list_music: {t}")
                    return "list_music", ""
                if cmd.startswith("歌词"):
                    lyric_keyword = self._clean_target(m.group(1) if m.lastindex and m.lastindex >= 1 else "")
                    if lyric_keyword:
                        logger.info(f"[快速意图] music_lyrics: {t} -> {lyric_keyword}")
                        return "music_lyrics", lyric_keyword
        # arXiv 论文搜索意图检测
        for p in self.ARXIV_AUTHOR_PATTERNS:
            m = p.match(t)
            if m:
                author = self._clean_target(m.group(1))
                if author:
                    logger.info(f"[快速意图] arxiv_author_search: {t} -> {author}")
                    return "arxiv_author_search", author
        for p in self.ARXIV_PATTERNS:
            m = p.match(t)
            if m:
                keyword = self._clean_target(m.group(1))
                # 清理关键词：去掉前缀"关于"、"的"、后缀"论文"、"研究"等
                keyword = self._clean_arxiv_keyword(keyword)
                # 如果清理后为空（如只有"论文"），回退到默认搜索
                if not keyword:
                    keyword = "latest"
                logger.info(f"[快速意图] search_arxiv: {t} -> {keyword}")
                return "search_arxiv", keyword
        for p in self.SEARCH_ONLY_PATTERNS:
            m = p.match(t)
            if m:
                return "search_only", self._clean_target(m.group(1))
        for p in self.SEARCH_BEST_PATTERNS:
            m = p.match(t)
            if m:
                return "search", self._clean_target(m.group(1))
        for p in self.CLOSE_PATTERNS:
            m = p.match(t)
            if m:
                tgt = self._clean_target(m.group(1))
                if tgt and tgt not in ("我", "自己", "你"):
                    return "close_app", tgt
        for p in self.OPEN_PATTERNS:
            m = p.match(t)
            if m:
                groups = m.groups()
                if len(groups) == 2 and len(groups[0]) == 1 and groups[0].isalpha():
                    drive = groups[0].upper()
                    folder = self._clean_target(groups[1])
                    if folder:
                        return "open_app", f"{drive}:\\{folder}"
                    else:
                        return "open_app", f"{drive}:\\"
                tgt = self._clean_target(groups[0])
                if tgt and tgt not in ("我", "自己", "你"):
                    return "open_app", tgt
        return "chat", t

    def _clean_target(self, s: str) -> str:
        s = s.strip().strip("，。,.！!？?\"'“”‘’()（）【】[]")
        # 去掉末尾 "一下" 之类
        s = re.sub(r"(?:一下|下|哦|啊|呀|呢|吧)$", "", s).strip()
        return s

    @staticmethod
    def _clean_arxiv_keyword(keyword: str) -> str:
        """清理 arXiv 搜索关键词，去掉冗余前缀和后缀。"""
        kw = keyword.strip()
        # 循环清理，直到稳定
        prev = ""
        while prev != kw:
            prev = kw
            # 去掉前缀（包括开头的"论文/paper"）
            kw = re.sub(r"^(?:关于|对于|有关|针对|上|的|之|从|向|论文|paper|研究|文章)\s*", "", kw, flags=re.IGNORECASE)
            # 去掉后缀中的"的论文"、"的研究"等
            kw = re.sub(r"\s*(?:的|之)\s*(?:论文|paper|研究|文章|相关|方面)$", "", kw, flags=re.IGNORECASE)
            # 去掉末尾的"论文/研究/paper/文章"（不管有没有空格）
            kw = re.sub(r"(?:论文|研究|文章|paper)$", "", kw, flags=re.IGNORECASE)
            # 去掉开头的"作者"
            kw = re.sub(r"^作者\s*", "", kw)
            # 去掉"关于"、"对于"等连接词
            kw = re.sub(r"\s*(?:关于|对于|有关|针对)\s*", " ", kw)
        # 去掉多余空格
        kw = re.sub(r"\s+", " ", kw)
        # 如果清理后太短或只剩无意义词，返回空
        if len(kw) < 2 or kw in ("论文", "paper", "研究", "文章", "的", ""):
            return ""
        return kw.strip()

    @staticmethod
    def _resolve_music_command(cmd: str) -> str:
        """从命令字符串中提取音乐控制意图（支持带后缀的命令，如 "换一首周杰伦"）。"""
        cmd = cmd.strip()
        # 优先级：精确匹配 > 关键词包含
        exact = {
            "暂停": "pause", "停一下": "pause",
            "继续播放": "resume", "继续": "resume", "恢复播放": "resume", "恢复": "resume",
            "下一首": "next", "下一个": "next", "下一曲": "next",
            "上一首": "prev", "上一个": "prev", "上一曲": "prev",
            "换一首": "next", "换个": "next", "换首": "next",
            "再来一首": "next", "再来一个": "next",
            "停止播放": "stop", "停止": "stop",
            "停": "stop", "关掉": "stop",
        }
        if cmd in exact:
            return exact[cmd]
        # 关键词包含检测
        if any(k in cmd for k in ("暂停", "停一下")):
            return "pause"
        if any(k in cmd for k in ("继续播放", "恢复播放", "恢复")):
            return "resume"
        if any(k in cmd for k in ("下一首", "下一个", "下一曲", "换一首", "换个", "换首", "再来一首", "再来一个")):
            return "next"
        if any(k in cmd for k in ("上一首", "上一个", "上一曲")):
            return "prev"
        if any(k in cmd for k in ("停止播放", "关掉")):
            return "stop"
        if cmd == "停":
            return "stop"
        return ""

    def _extract_open_target(self, text: str) -> str:
        """从复合指令中提取要打开的目标。
        例如："看看屏幕然后打开微信" → "微信"
              "帮我打开屏幕上的回收站" → "回收站"
        """
        # 模式1：看看屏幕然后打开XX
        patterns = [
            re.compile(r"(?:看看|看一下|看|瞧瞧).*?(?:屏幕|桌面|界面).*?(?:然后|之后|接着|再)?\s*"
                       r"(?:打开|开启|启动|运行|点开|进去)\s*(?:一下|下)?\s*(.+?)"
                       r"(?:好吗|好|吧|哦|啊|呀|呢|吧|[。.！!？?]|$)"),
            # 模式2：帮我打开屏幕上的XX
            re.compile(r"(?:帮我|请)?(?:打开|开启|启动|运行|点开)\s*(?:一下|下)?\s*(?:屏幕上的|桌面上的|界面上的)?\s*(.+?)"
                       r"(?:好吗|好|吧|哦|啊|呀|呢|[。.！!？?]|$)"),
        ]
        
        for pattern in patterns:
            m = pattern.search(text)
            if m:
                target = self._clean_target(m.group(1))
                if target and len(target) >= 1:
                    logger.info(f"提取打开目标: 「{text}」 → 「{target}」")
                    return target
        
        # 兜底：尝试简单提取
        # 找"打开"后面的内容
        m = re.search(r"(?:打开|开启|启动|运行|点开)\s*(?:一下|下)?\s*(.+?)(?:好吗|好|吧|哦|啊|呀|呢|[。.！!？?]|$)", text)
        if m:
            target = self._clean_target(m.group(1))
            if target:
                return target
        
        return ""

    def _is_screen_reference(self, target: str) -> bool:
        """检测目标是否引用了屏幕/桌面（需要视觉定位）。"""
        SCREEN_REF_PATTERNS = re.compile(
            r"(?:屏幕上的|桌面上的|界面上的|屏幕里的|桌面上|"
            r"在屏幕|在桌面|屏幕里|桌面上|屏幕上)"
        )
        return bool(SCREEN_REF_PATTERNS.search(target or ""))

    def _push_history(self, user: str, assistant: str):
        """推入对话历史（滑动窗口，最多保留 20 条）。"""
        self._history.append({"role": "user", "content": user})
        self._history.append({"role": "assistant", "content": assistant})
        if len(self._history) > 20:
            self._history = self._history[-20:]
