import os
import re
import shutil
import subprocess
import threading
import time
from difflib import SequenceMatcher
import psutil
from typing import Optional, List, Dict, Tuple
from pypinyin import lazy_pinyin, Style


COMMON_APP_ALIASES = {
    "浏览器": ["chrome.exe", "msedge.exe", "firefox.exe", "iexplore.exe", "brave.exe"],
    "chrome": ["chrome.exe"],
    "谷歌浏览器": ["chrome.exe"],
    "edge": ["msedge.exe"],
    "edeg": ["msedge.exe"],
    "微软浏览器": ["msedge.exe"],
    "edge浏览器": ["msedge.exe"],
    "微信": ["wechat.exe", "WeChat.exe"],
    "wechat": ["wechat.exe", "WeChat.exe"],
    "腾讯会议": ["wemeetapp.exe", "TencentMeeting.exe"],
    "tencent meeting": ["wemeetapp.exe"],
    "wemeet": ["wemeetapp.exe"],
    "qq": ["qq.exe", "QQ.exe", "QQScLauncher.exe"],
    "腾讯qq": ["qq.exe", "QQ.exe"],
    "钉钉": ["dingtalk.exe", "DingtalkLauncher.exe"],
    "dingding": ["dingtalk.exe"],
    "wps": ["wps.exe", "wpscenter.exe", "et.exe", "wpp.exe"],
    "word": ["WINWORD.EXE", "wps.exe"],
    "excel": ["EXCEL.EXE", "et.exe"],
    "ppt": ["POWERPNT.EXE", "wpp.exe"],
    "记事本": ["notepad.exe"],
    "notepad": ["notepad.exe"],
    "计算器": ["calc.exe"],
    "calc": ["calc.exe"],
    "画图": ["mspaint.exe"],
    "cmd": ["cmd.exe"],
    "命令行": ["cmd.exe"],
    "终端": ["cmd.exe", "powershell.exe", "WindowsTerminal.exe"],
    "powershell": ["powershell.exe"],
    "任务管理器": ["taskmgr.exe"],
    "设置": ["ms-settings:", "control.exe"],
    "控制面板": ["control.exe"],
    "文件资源管理器": ["explorer.exe"],
    "我的电脑": ["explorer.exe"],
    "此电脑": ["explorer.exe"],
    "vscode": ["Code.exe", "code.exe"],
    "代码": ["Code.exe", "code.exe"],
    "pycharm": ["pycharm64.exe", "pycharm.exe"],
    "idea": ["idea64.exe", "idea.exe"],
    "网易云音乐": ["cloudmusic.exe", "netease cloud music.exe"],
    "网易云": ["cloudmusic.exe"],
    "qq音乐": ["qqmusic.exe", "QQMusic.exe"],
    "腾讯视频": ["qqlive.exe", "QQLive.exe"],
    "爱奇艺": ["qiyisetup.exe", "iQIYI.exe", "VideoProcessor.exe"],
    "剪映": ["JianyingPro.exe", "CapCut.exe"],
    "photoshop": ["Photoshop.exe"],
    "ps": ["Photoshop.exe"],
    "steam": ["steam.exe", "Steam.exe"],
    "obs": ["obs64.exe", "OBS-Studio.exe"],
    "录屏": ["obs64.exe", "OBS-Studio.exe"],
    "potplayer": ["PotPlayerMini64.exe", "PotPlayerMini.exe"],
    "vlc": ["vlc.exe"],
    "百度网盘": ["BaiduNetdisk.exe"],
    "迅雷": ["ThunderNetwork.exe", "Thunder.exe"],
    "向日葵": ["SunloginClient.exe"],
    "teamviewer": ["TeamViewer.exe"],
    "navicat": ["navicat.exe", "navicatpremium.exe"],
    "mysql": ["MySQLWorkbench.exe"],
    "sql": ["SQLyog.exe", "SSMS.exe"],
    "postman": ["Postman.exe"],
    "notepad++": ["notepad++.exe"],
    "notepadplusplus": ["notepad++.exe"],
    "git": ["git-bash.exe", "git.exe"],
    "gitbash": ["git-bash.exe"],
    "matlab": ["MATLAB.exe"],
    "origin": ["Origin.exe"],
    "wegame": ["wegame.exe", "WeApp.exe"],
    "战网": ["Battle.net.exe"],
    "epic": ["EpicGamesLauncher.exe"],
    "uplay": ["Uplay.exe", "UbisoftConnect.exe"],
    "360": ["360browser.exe", "360chrome.exe"],
    "360浏览器": ["360browser.exe", "360chrome.exe"],
    "火狐": ["firefox.exe"],
    "firefox": ["firefox.exe"],
    "safari": ["Safari.exe"],
    "tor": ["tor.exe"],
    "电报": ["Telegram.exe"],
    "telegram": ["Telegram.exe"],
    "discord": ["Discord.exe"],
    "飞书": ["Feishu.exe", "Lark.exe"],
    "lark": ["Feishu.exe", "Lark.exe"],
    "飞书文档": ["FeishuDocs.exe"],
    "飞书会议": ["FeishuMeeting.exe"],
    "zoom": ["Zoom.exe", "ZoomMeeting.exe"],
    "腾讯文档": ["TencentDocs.exe"],
    "腾讯课堂": ["tencentclass.exe"],
    "哔哩哔哩": ["bilibili.exe", "BilibiliLive.exe"],
    "bilibili": ["bilibili.exe"],
    "b站": ["bilibili.exe"],
    "虎牙": ["Huya.exe"],
    "斗鱼": ["Douyu.exe"],
    "yy": ["YY.exe"],
    "酷狗": ["KuGou.exe"],
    "酷我音乐": ["KuMusic.exe"],
    "kmusic": ["KuMusic.exe"],
    "foobar": ["foobar2000.exe"],
    "spotify": ["Spotify.exe"],
    "audacity": ["audacity.exe"],
    "cubase": ["Cubase.exe"],
    "flstudio": ["FL Studio.exe"],
    "unity": ["Unity.exe"],
    "unreal": ["UE5Editor.exe", "UnrealEditor.exe"],
    "blender": ["blender.exe"],
    "figma": ["Figma.exe"],
    "sketch": ["Sketch.exe"],
    "excel": ["EXCEL.EXE", "et.exe"],
    "outlook": ["OUTLOOK.EXE"],
    "powerpoint": ["POWERPNT.EXE", "wpp.exe"],
    "access": ["MSACCESS.EXE"],
    "publisher": ["MSPUB.EXE"],
    "onenote": ["ONENOTE.EXE"],
    "visio": ["VISIO.EXE"],
}


def _name_variants(name: str) -> List[str]:
    """生成名称的各种变体：原名称、全小写、拼音、拼音首字母、去除扩展名。"""
    variants = set()
    name = name.strip()
    if not name:
        return []
    variants.add(name)
    variants.add(name.lower())

    # 去扩展名
    base = os.path.splitext(name)[0]
    variants.add(base)
    variants.add(base.lower())

    # 拼音
    py = lazy_pinyin(name)
    variants.add("".join(py))
    variants.add("".join(py).lower())
    variants.add(" ".join(py))
    variants.add(" ".join(py).lower())

    # 拼音首字母
    py_initials = lazy_pinyin(name, style=Style.FIRST_LETTER)
    variants.add("".join(py_initials))
    variants.add("".join(py_initials).lower())

    # base的拼音
    if base != name:
        py2 = lazy_pinyin(base)
        variants.add("".join(py2))
        variants.add("".join(py2).lower())
        py2i = lazy_pinyin(base, style=Style.FIRST_LETTER)
        variants.add("".join(py2i))
        variants.add("".join(py2i).lower())

    return [v for v in variants if v]


def _similarity(a: str, b: str) -> float:
    """计算两个字符串的相似度（0~1）。
    综合考虑：原文、小写、拼音三种形式的相似度，取最大值。
    """
    if not a or not b:
        return 0.0
    a = a.strip()
    b = b.strip()
    if not a or not b:
        return 0.0

    # 去掉扩展名
    a_base = os.path.splitext(a)[0]
    b_base = os.path.splitext(b)[0]

    # 候选对：原文/小写/拼音
    a_variants = [a, a.lower(), a_base, a_base.lower()]
    b_variants = [b, b.lower(), b_base, b_base.lower()]

    # 拼音形式
    try:
        a_py = "".join(lazy_pinyin(a_base))
        b_py = "".join(lazy_pinyin(b_base))
        if a_py:
            a_variants.append(a_py)
            a_variants.append(a_py.lower())
        if b_py:
            b_variants.append(b_py)
            b_variants.append(b_py.lower())
    except Exception:
        pass

    best = 0.0
    for av in a_variants:
        if not av:
            continue
        for bv in b_variants:
            if not bv:
                continue
            r = SequenceMatcher(None, av, bv).ratio()
            if r > best:
                best = r
            if best >= 1.0:
                return 1.0
    return best


class AppController:
    """控制电脑软件的打开/关闭/搜索。"""

    def __init__(self):
        self._app_cache: Dict[str, str] = {}  # 搜索结果缓存
        self._cache_lock = threading.Lock()

    # ---------- 公开接口 ----------

    def open_app(self, keyword: str) -> Tuple[bool, str]:
        """根据关键词打开应用/文件。返回(是否成功, 描述信息)。"""
        keyword = keyword.strip()
        if not keyword:
            return False, "请告诉我要打开什么"

        # 0. 如果是显式路径（带盘符 F:\xxx 或 F:/xxx），直接打开
        if re.match(r'^[A-Za-z]:[\\/]', keyword):
            path = keyword.replace('/', '\\')
            # 先尝试直接打开
            if os.path.exists(path):
                try:
                    self._launch_path(path)
                    kind = "文件夹" if os.path.isdir(path) else "文件"
                    return True, f"已打开{kind}: {path}"
                except Exception as e:
                    return False, f"无法打开: {path}，原因: {e}"
            # 路径不存在，尝试把盘符+剩下部分当关键词搜索该盘
            drive, rest = os.path.splitdrive(path)
            rest = rest.strip('\\/')
            if rest:
                target_variants = set(v.lower() for v in _name_variants(rest))
                # 只在该盘搜索，严格排除系统目录
                hit = self._search_in_dirs([drive + '\\'], target_variants, max_depth=8,
                                           exclude_dirs={
                                               "$Recycle.Bin", "$RECYCLE.BIN", "System Volume Information",
                                               "Windows", "ProgramData", "Recovery",
                                           },
                                           timeout=20,
                                           prefer_exact=True,
                                           search_term=rest)
                if hit:
                    try:
                        self._launch_path(hit)
                        with self._cache_lock:
                            self._app_cache[keyword.lower()] = hit
                        kind = "文件夹" if os.path.isdir(hit) else "文件"
                        return True, f"已在{drive.upper()}盘找到并打开{kind}: {hit}"
                    except Exception as e:
                        return False, f"找到但无法打开: {hit}"
                return False, f"在{drive.upper()}盘未找到「{rest}」，请确认路径或文件夹名"
            else:
                # 只给了盘符：打开该盘根目录
                drive_path = drive + '\\'
                if os.path.exists(drive_path):
                    try:
                        self._launch_path(drive_path)
                        return True, f"已打开 {drive.upper()}盘"
                    except Exception as e:
                        return False, f"无法打开 {drive.upper()}盘: {e}"
                return False, f"{drive.upper()}盘不存在"

        # 1. 常见别名直接启动
        exe_names = self._resolve_aliases(keyword)
        for exe in exe_names:
            try:
                ok, msg = self._launch(exe)
                if ok:
                    return True, f"已打开 {keyword}"
            except Exception:
                pass

        # 2. 查缓存
        with self._cache_lock:
            cached = self._app_cache.get(keyword.lower())
        if cached and os.path.exists(cached):
            try:
                self._launch_path(cached)
                return True, f"已打开: {os.path.basename(cached)}"
            except Exception:
                pass

        # 2.5 桌面快捷方式相似度匹配（6成相似即打开）
        desktop_hit = self._search_desktop_shortcut_by_similarity(keyword, threshold=0.6)
        if desktop_hit:
            try:
                self._launch_path(desktop_hit)
                with self._cache_lock:
                    self._app_cache[keyword.lower()] = desktop_hit
                name = os.path.splitext(os.path.basename(desktop_hit))[0]
                return True, f"已通过桌面快捷方式打开: {name}"
            except Exception as e:
                return False, f"找到快捷方式但无法打开: {desktop_hit}，原因: {e}"

        # 3. 全系统搜索
        found = self.search_file_or_app(keyword)
        if found:
            try:
                self._launch_path(found)
                with self._cache_lock:
                    self._app_cache[keyword.lower()] = found
                kind = "文件夹" if os.path.isdir(found) else "文件"
                return True, f"已找到并打开{kind}: {found}"
            except Exception as e:
                return False, f"找到文件但无法打开: {e}"

        # 4. 最后兜底：尝试直接用原始关键词启动（可能命中 App Paths 注册表）
        try:
            ok, msg = self._launch(keyword)
            if ok:
                return True, f"已打开 {keyword}"
        except Exception:
            pass
        try:
            ok, msg = self._launch(keyword + ".exe")
            if ok:
                return True, f"已打开 {keyword}"
        except Exception:
            pass

        return False, f"没找到「{keyword}」对应的软件或文件"

    def close_app(self, keyword: str) -> Tuple[bool, str]:
        """关闭进程。关键词可以是进程名(带或不带.exe)或显示名称。"""
        keyword = keyword.strip().lower()
        if not keyword:
            return False, "请告诉我要关闭什么"

        # 别名
        aliases = set()
        if keyword in COMMON_APP_ALIASES:
            for a in COMMON_APP_ALIASES[keyword]:
                aliases.add(a.lower())
        aliases.add(keyword)
        if not keyword.endswith(".exe"):
            aliases.add(keyword + ".exe")
        # 拼音匹配
        variants = set(_name_variants(keyword))
        target_names = aliases | variants

        killed = []
        for proc in psutil.process_iter(["pid", "name", "exe"]):
            try:
                pname = (proc.info.get("name") or "").lower()
                pexe = (proc.info.get("exe") or "").lower()
                pbase = os.path.basename(pexe).lower() if pexe else ""
                proc_variants = set(_name_variants(pname))
                if (
                    pname in target_names
                    or pbase in target_names
                    or any(v in target_names for v in proc_variants)
                    or any(t in pname for t in aliases if len(t) >= 3)
                ):
                    try:
                        proc.terminate()
                        killed.append(proc.info.get("name", str(proc.pid)))
                    except Exception:
                        try:
                            proc.kill()
                            killed.append(proc.info.get("name", str(proc.pid)))
                        except Exception:
                            pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if killed:
            return True, f"已关闭: {', '.join(set(killed))}"
        return False, f"未找到运行中的「{keyword}」进程"

    def search_file_or_app(self, keyword: str) -> Optional[str]:
        """
        在所有驱动器中搜索匹配的文件/快捷方式。
        优先匹配桌面和开始菜单，然后全盘搜索。
        匹配中文名、英文名、拼音名。
        """
        keyword = keyword.strip()
        if not keyword:
            return None

        target_variants = set(v.lower() for v in _name_variants(keyword))

        # 优先搜索的目录（快速命中）
        fast_dirs = self._get_fast_search_dirs()
        hit = self._search_in_dirs(fast_dirs, target_variants, max_depth=4,
                                   search_term=keyword)
        if hit:
            return hit

        # 全盘搜索（慢速）
        drives = self._get_windows_drives()
        exclude = {
            "$Recycle.Bin", "$RECYCLE.BIN", "System Volume Information",
            "Windows", "ProgramData", "Recovery", "Config.Msi",
        }
        hit = self._search_in_dirs(
            drives, target_variants, max_depth=7, exclude_dirs=exclude,
            timeout=15, prefer_exact=True, search_term=keyword
        )
        return hit

    # ---------- 内部方法 ----------

    def _resolve_aliases(self, keyword: str) -> List[str]:
        """只返回已知别名对应的 exe 列表，不返回原始关键词作为回退。"""
        kw = keyword.lower()
        result = []
        for k, v in COMMON_APP_ALIASES.items():
            if k == kw:
                result.extend(v)
        # 如果是路径格式（如含盘符），不在这里处理
        return result

    def _launch(self, exe_or_cmd: str) -> Tuple[bool, str]:
        """启动 exe 或协议。多策略保证成功，不弹系统错误对话框。"""
        exe = exe_or_cmd.strip()

        # 协议形式（如 ms-settings:）
        if exe.endswith(":") and len(exe) <= 15 and not os.path.exists(exe):
            try:
                self._startfile_silent(exe)
                return True, "ok"
            except Exception as e:
                return False, str(e)

        # 策略1: 如果是完整路径且存在，直接启动
        if os.path.isabs(exe) and os.path.exists(exe):
            work_dir = os.path.dirname(exe) if os.path.isfile(exe) else exe
            try:
                self._startfile_silent(exe)
                return True, "ok"
            except Exception:
                try:
                    subprocess.Popen([exe], cwd=work_dir, shell=False)
                    return True, "ok"
                except Exception as e:
                    return False, str(e)

        # 策略2: 用 shutil.which 解析 PATH 中的完整路径
        resolved = shutil.which(exe)
        if resolved:
            work_dir = os.path.dirname(resolved)
            try:
                self._startfile_silent(resolved)
                return True, "ok"
            except Exception:
                try:
                    subprocess.Popen([resolved], cwd=work_dir, shell=False)
                    return True, "ok"
                except Exception as e:
                    return False, str(e)

        # 策略3: 未知 exe 名 — 用 start 命令（不弹错误框）
        # 先尝试不带 .exe 的名字
        try:
            if os.name == 'nt':
                result = self._try_start(exe)
                if result:
                    return True, "ok"
        except Exception:
            pass

        # 策略4: 带 .exe 后缀再试
        if not exe.lower().endswith('.exe'):
            try:
                if os.name == 'nt':
                    result = self._try_start(exe + '.exe')
                    if result:
                        return True, "ok"
            except Exception:
                pass

        return False, f"找不到文件: {exe}"

    @staticmethod
    def _startfile_silent(path: str):
        """静默启动文件，不弹 Windows 错误对话框。"""
        if os.name == 'nt':
            import ctypes
            SEM_FAILCRITICALERRORS = 0x00000001
            SEM_NOOPENFILEERRORBOX = 0x00008000
            old_mode = ctypes.windll.kernel32.SetErrorMode(
                SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX
            )
            try:
                os.startfile(path)
            finally:
                ctypes.windll.kernel32.SetErrorMode(old_mode)
        else:
            os.startfile(path)

    @staticmethod
    def _try_start(exe_name: str) -> bool:
        """用 start 命令静默启动（不弹错误框）。成功返回 True。"""
        if os.name != 'nt':
            return False
        try:
            # CREATE_NO_WINDOW = 0x08000000, DETACHED_PROCESS = 0x00000008
            CREATE_NO_WINDOW = 0x08000000
            DETACHED_PROCESS = 0x00000008
            # 用 cmd /c start 来启动，不弹错误框
            proc = subprocess.Popen(
                f'cmd /c start "" "{exe_name}"',
                shell=False,
                creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # 等待极短时间检查是否成功（start 命令本身几乎不耗时）
            try:
                proc.wait(timeout=2)
                # 如果 start 命令成功执行，返回码为 0
                # 但即使返回 0，也可能只是 shell 成功，应用未必启动
                # 这里认为 shell 级成功就够了
                return proc.returncode == 0
            except subprocess.TimeoutExpired:
                proc.kill()
                return True  # 超时说明 start 已发起，认为成功
        except Exception:
            return False

    def _launch_path(self, path: str):
        """启动路径（文件/文件夹/快捷方式）。"""
        try:
            self._startfile_silent(path)
            return
        except Exception:
            pass
        # 回退：用 start 命令
        try:
            if os.name == 'nt':
                subprocess.Popen(
                    f'cmd /c start "" "{path}"',
                    shell=False,
                    creationflags=0x08000000 | 0x00000008,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen([path], shell=True)
        except Exception as e:
            raise

    def _get_fast_search_dirs(self) -> List[str]:
        dirs = []
        home = os.path.expanduser("~")
        candidates = [
            os.path.join(home, "Desktop"),
            os.path.join(home, "桌面"),
            os.path.join(home, "OneDrive", "Desktop"),
            os.path.join(home, "OneDrive", "桌面"),
            os.path.join(home, "Downloads"),
            os.path.join(home, "下载"),
            os.path.join(home, "Documents"),
            os.path.join(home, "文档"),
            os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                         "Microsoft", "Windows", "Start Menu", "Programs"),
            os.path.join(home, "AppData", "Roaming", "Microsoft", "Windows", "Start Menu", "Programs"),
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Common Files"),
            os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Common Files"),
        ]
        for p in candidates:
            if p and os.path.isdir(p):
                dirs.append(p)
        return dirs

    def _get_desktop_dirs(self) -> List[str]:
        """获取所有桌面目录（含 OneDrive 桌面）。"""
        dirs = []
        home = os.path.expanduser("~")
        candidates = [
            os.path.join(home, "Desktop"),
            os.path.join(home, "桌面"),
            os.path.join(home, "OneDrive", "Desktop"),
            os.path.join(home, "OneDrive", "桌面"),
            # 公共桌面
            os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop"),
            os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "桌面"),
        ]
        for p in candidates:
            if p and os.path.isdir(p):
                dirs.append(p)
        return dirs

    def _search_desktop_shortcut_by_similarity(
        self, keyword: str, threshold: float = 0.6
    ) -> Optional[str]:
        """在桌面搜索快捷方式，按名称相似度匹配。
        相似度 >= threshold 即视为命中，返回相似度最高的快捷方式路径。
        比对的是快捷方式的文件名（去掉 .lnk 后缀）与用户输入。
        """
        keyword = keyword.strip()
        if not keyword:
            return None

        desktop_dirs = self._get_desktop_dirs()
        if not desktop_dirs:
            return None

        # 收集所有桌面快捷方式
        shortcuts: List[Tuple[str, str]] = []  # (display_name, path)
        for d in desktop_dirs:
            try:
                with os.scandir(d) as it:
                    for entry in it:
                        if entry.is_file(follow_symlinks=False):
                            name = entry.name
                            ext = os.path.splitext(name)[1].lower()
                            # 快捷方式或可执行文件
                            if ext in {".lnk", ".exe", ".bat", ".cmd", ".url"}:
                                display_name = os.path.splitext(name)[0]
                                shortcuts.append((display_name, entry.path))
            except (PermissionError, OSError):
                continue

        if not shortcuts:
            return None

        # 计算相似度并排序
        scored: List[Tuple[float, str, str]] = []  # (similarity, display_name, path)
        for display_name, path in shortcuts:
            sim = _similarity(keyword, display_name)
            if sim >= threshold:
                scored.append((sim, display_name, path))

        if not scored:
            return None

        # 按相似度降序，取最高
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][2]

    def _get_windows_drives(self) -> List[str]:
        try:
            import string
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            return drives
        except Exception:
            return ["C:\\"]

    def _search_in_dirs(
        self,
        roots: List[str],
        target_variants: set,
        max_depth: int = 6,
        exclude_dirs: set = None,
        timeout: int = 20,
        prefer_exact: bool = False,
        search_term: str = "",
    ) -> Optional[str]:
        exclude = exclude_dirs or set()
        exclude_lower = {d.lower() for d in exclude}
        start = time.time()
        exts = {".exe", ".lnk", ".bat", ".cmd", ".com", ".ps1"}
        raw_search = (search_term or "").strip()
        raw_search_lower = raw_search.lower()

        # 大小写敏感的原始 variants（直接用原字符不转小写）
        raw_variants_sensitive = set()
        if raw_search:
            for v in _name_variants(raw_search):
                if v:
                    raw_variants_sensitive.add(v)
        # 原始文件名（去掉扩展名）的大小写敏感集合
        raw_search_base = os.path.splitext(raw_search)[0] if raw_search else ""
        raw_search_base_variants_sensitive = set()
        if raw_search_base:
            for v in _name_variants(raw_search_base):
                if v:
                    raw_search_base_variants_sensitive.add(v)

        def match(filename: str) -> bool:
            fvars = set(v.lower() for v in _name_variants(filename))
            for t in target_variants:
                if len(t) < 2:
                    continue
                for fv in fvars:
                    if t == fv:
                        return True
                    if t in fv and len(t) >= 3:
                        return True
                    if fv in t and len(fv) >= 3:
                        return True
            return False

        def score_match(filename: str, is_dir: bool) -> int:
            """
            打分：分数越低越匹配。
            10000 起底，按匹配档位减去不同的分数。
            精确（大小写敏感）: -5000
            精确（大小写不敏感）: -3000
            文件名包含搜索词（敏感）: -2000
            文件名包含搜索词（不敏感）: -1500
            搜索词包含文件名（敏感）: -800
            搜索词包含文件名（不敏感）: -500
            加上深度惩罚: + depth * 5
            扩展名不匹配的文件夹：如果是".exe/.lnk"等可执行文件匹配到文件夹，+ 500
            """
            base = os.path.splitext(filename)[0]
            ext = os.path.splitext(filename)[1].lower()
            name_sensitive = filename
            base_sensitive = base
            name_lower = filename.lower()
            base_lower = base.lower()

            # 生成文件名的 variants（敏感和不敏感）
            fvars_sensitive = set(_name_variants(name_sensitive)) | set(_name_variants(base_sensitive))
            fvars_lower = {v.lower() for v in fvars_sensitive}

            score = 10000

            # === 档位1: 大小写敏感 精确匹配 ===
            if raw_variants_sensitive:
                for t in raw_variants_sensitive:
                    if t in (name_sensitive, base_sensitive) or t in fvars_sensitive:
                        score -= 5000
                        break
                if score < 10000:
                    pass  # 命中1档
                else:
                    # === 档位2: 大小写不敏感 精确匹配 ===
                    for t in target_variants:
                        if len(t) < 2:
                            continue
                        if t == name_lower or t == base_lower or t in fvars_lower:
                            score -= 3000
                            break

            # 如果精确都没命中，但在敏感方向上完全相等，手动加
            if raw_search and (raw_search == name_sensitive or raw_search == base_sensitive):
                score -= 5000
            if raw_search and (raw_search_lower == name_lower or raw_search_lower == base_lower):
                score -= 3000

            # 精确命中扩展名（带扩展名输入时）
            if raw_search and raw_search == name_sensitive:
                score -= 6000

            # === 档位3: 包含（敏感）文件名含搜索词 ===
            if raw_search and len(raw_search) >= 3:
                if raw_search in name_sensitive or raw_search in base_sensitive:
                    score -= 2000
                # 档位3b: 不敏感包含
                elif raw_search_lower in name_lower or raw_search_lower in base_lower:
                    score -= 1500
                # 档位4: 搜索词包含文件名
                elif name_sensitive and name_sensitive in raw_search:
                    score -= 800
                elif base_sensitive and base_sensitive in raw_search:
                    score -= 800
                elif name_lower and len(name_lower) >= 3 and name_lower in raw_search_lower:
                    score -= 500
                elif base_lower and len(base_lower) >= 3 and base_lower in raw_search_lower:
                    score -= 500

            # 文件夹如果跟可执行扩展名相关的匹配，不优先
            if is_dir and ext and ext in exts:
                score += 500

            return score

        candidates = []  # List[(score, depth, path)] 分数越低越优先

        def walk(root: str, depth: int):
            if time.time() - start > timeout:
                return
            if depth > max_depth:
                return
            try:
                with os.scandir(root) as it:
                    for entry in it:
                        if time.time() - start > timeout:
                            return
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                # 排除系统目录（大小写不敏感）
                                if entry.name.lower() in exclude_lower:
                                    continue
                                if entry.name.startswith("$") or entry.name.startswith("."):
                                    continue
                                # 判断该文件夹是否匹配
                                if match(entry.name):
                                    sc = score_match(entry.name, is_dir=True)
                                    sc += depth * 5
                                    candidates.append((sc, depth, entry.path))
                                walk(entry.path, depth + 1)
                            else:
                                name = entry.name
                                ext = os.path.splitext(name)[1].lower()
                                matched = False
                                if ext in exts:
                                    if match(name):
                                        matched = True
                                elif match(name):
                                    matched = True
                                if matched:
                                    sc = score_match(name, is_dir=False)
                                    sc += depth * 5
                                    candidates.append((sc, depth, entry.path))
                        except (PermissionError, OSError):
                            continue
            except (PermissionError, OSError, FileNotFoundError):
                return

        for root in roots:
            if not os.path.isdir(root):
                continue
            walk(root, 0)

        if not candidates:
            return None

        # 按分数排序（分数最低 = 最佳匹配），同分按深度最小
        candidates.sort(key=lambda x: (x[0], x[1]))
        best = candidates[0]

        # 调试：打印 top3 候选
        # for i, (sc, d, p) in enumerate(candidates[:5]):
        #     print(f"  [{i}] score={sc} depth={d} path={p}")

        return best[2]
