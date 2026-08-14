"""
桌面自动化模块 - 基于 pywinauto 的 Windows UIA 操作
第1层（优先方案）：直接通过 UIA 控件树操作，无需 VLM
支持：窗口管理、元素枚举、语义查找、直接点击/输入

优势：毫秒级响应、100%准确率、零成本、能获取控件完整信息
"""

import time
import logging
from typing import Tuple, Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class DesktopAutomator:
    """桌面自动化控制器 - UIA 优先方案。
    
    当 UIA 不可用时（比如游戏、Electron 自定义渲染界面），
    调用方应回退到 vision_automation.py 的 VLM 方案。
    """

    def __init__(self):
        self._app_cache = {}

    # ---------------- 能力检测 ----------------

    @property
    def available(self) -> bool:
        """检查 pywinauto 是否可用。"""
        try:
            import pywinauto
            return True
        except ImportError:
            return False

    # ---------------- 窗口管理 ----------------

    def get_foreground_window(self) -> Optional[object]:
        """获取当前前台（活动）窗口。"""
        try:
            import win32gui
            import win32con
            hwnd = win32gui.GetForegroundWindow()
            if hwnd and hwnd != 0:
                from pywinauto import Desktop
                desktop = Desktop(backend="uia")
                win = desktop.window(handle=hwnd)
                return win
        except ImportError:
            logger.warning("win32gui 或 pywinauto 不可用")
        except Exception as e:
            logger.warning(f"获取前台窗口失败: {e}")
        
        # 回退：尝试通过 pywinauto 的 windows() 列表获取
        try:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            windows = desktop.windows()
            if windows:
                # 返回第一个可见的顶层窗口
                for win in windows:
                    try:
                        if win.is_visible():
                            return win
                    except Exception:
                        continue
                return windows[0]
        except Exception as e:
            logger.warning(f"回退获取前台窗口也失败: {e}")
        
        return None

    def get_foreground_window_info(self) -> Dict[str, Any]:
        """获取当前前台窗口的基本信息。"""
        win = self.get_foreground_window()
        if win is None:
            return {"error": "无法获取前台窗口"}
        try:
            return {
                "title": win.window_text(),
                "class_name": win.class_name(),
                "handle": win.handle,
                "rect": str(win.rectangle()),
                "framework_id": win.framework_id,
                "process_id": win.process_id,
            }
        except Exception as e:
            return {"error": str(e)}

    def find_window(self, title_keyword: str, timeout: float = 5.0) -> Optional[object]:
        """根据标题关键字查找窗口。"""
        try:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            end_time = time.time() + timeout
            while time.time() < end_time:
                windows = desktop.windows()
                for win in windows:
                    try:
                        if title_keyword.lower() in win.window_text().lower():
                            return win
                    except Exception:
                        continue
                time.sleep(0.5)
            return None
        except ImportError:
            logger.error("pywinauto 未安装")
            return None
        except Exception as e:
            logger.error(f"查找窗口失败: {e}")
            return None

    def activate_window(self, title_keyword: str) -> bool:
        """根据标题关键字激活窗口。"""
        win = self.find_window(title_keyword)
        if win is None:
            logger.warning(f"未找到窗口: {title_keyword}")
            return False
        try:
            win.set_focus()
            return True
        except Exception as e:
            logger.error(f"激活窗口失败: {e}")
            return False

    def minimize_window(self, title_keyword: str) -> bool:
        win = self.find_window(title_keyword)
        if win is None:
            return False
        try:
            win.minimize()
            return True
        except Exception as e:
            logger.error(f"最小化窗口失败: {e}")
            return False

    # ---------------- 元素枚举 ----------------

    def list_elements(self, window: Optional[object] = None, 
                      max_depth: int = 5) -> List[Dict[str, Any]]:
        """列出窗口中所有可访问的 UIA 元素。
        
        Args:
            window: 目标窗口，None 时使用前台窗口
            max_depth: 最大遍历深度
            
        Returns:
            元素列表，每个元素包含 name, control_type, automation_id, rect 等
        """
        if window is None:
            window = self.get_foreground_window()
        if window is None:
            return []
        
        elements = []
        self._walk_elements(window, elements, max_depth=max_depth)
        return elements

    def _walk_elements(self, ctrl, elements: list, depth: int = 0, 
                       max_depth: int = 5):
        """递归遍历控件树。"""
        if depth > max_depth:
            return
        try:
            info = self._get_element_info(ctrl)
            if info:
                elements.append(info)
            # 递归子元素
            try:
                children = ctrl.children()
                for child in children:
                    self._walk_elements(child, elements, depth + 1, max_depth)
            except Exception:
                pass
        except Exception:
            pass

    def _get_element_info(self, ctrl) -> Optional[Dict[str, Any]]:
        """获取单个元素的详细信息。"""
        try:
            info = {
                "name": "",
                "control_type": "",
                "automation_id": "",
                "rect": "",
                "depth": 0,
            }
            try:
                info["name"] = ctrl.window_text() or ctrl.name or ""
            except Exception:
                try:
                    info["name"] = ctrl.name or ""
                except Exception:
                    pass
            
            try:
                info["control_type"] = ctrl.element_info.control_type or ""
            except Exception:
                try:
                    info["control_type"] = ctrl.friendly_class_name() or ""
                except Exception:
                    pass
            
            try:
                info["automation_id"] = ctrl.element_info.automation_id or ""
            except Exception:
                pass
            
            try:
                r = ctrl.rectangle()
                info["rect"] = f"({r.left},{r.top},{r.right},{r.bottom})"
            except Exception:
                pass
            
            # 过滤空元素
            if not info["name"] and not info["control_type"]:
                return None
                
            return info
        except Exception:
            return None

    def get_element_tree(self, window: Optional[object] = None,
                         max_depth: int = 4) -> str:
        """获取可读的元素树（调试用）。"""
        elements = self.list_elements(window, max_depth)
        lines = []
        for e in elements:
            indent = "  " * min(max_depth, e.get("depth", 0))
            name = e.get("name", "")
            ctype = e.get("control_type", "")
            rect = e.get("rect", "")
            aid = e.get("automation_id", "")
            parts = [f"{indent}[{ctype}] {name}"]
            if aid:
                parts.append(f"(id={aid})")
            if rect:
                parts.append(f"@{rect}")
            lines.append(" ".join(parts))
        return "\n".join(lines)

    # ---------------- 语义查找（核心） ----------------

    def find_element_by_name(self, name: str, 
                             window: Optional[object] = None,
                             timeout: float = 3.0,
                             fuzzy: bool = True) -> Optional[object]:
        """通过名称查找元素（支持模糊匹配）。
        
        Args:
            name: 要查找的元素名称（如"保存"、"关闭"、"搜索"）
            window: 目标窗口，None 时使用前台窗口
            timeout: 等待超时时间
            fuzzy: 是否使用模糊匹配
            
        Returns:
            找到的元素对象，未找到返回 None
        """
        if window is None:
            window = self.get_foreground_window()
        if window is None:
            return None

        # 精确匹配
        try:
            ctrl = window.child_window(title=name)
            ctrl.wait("exists", timeout=timeout)
            return ctrl
        except Exception:
            pass

        # 模糊匹配：遍历所有元素找
        if fuzzy:
            elements = self.list_elements(window, max_depth=8)
            name_lower = name.lower()
            for elem_info in elements:
                elem_name = elem_info.get("name", "").lower()
                if elem_name and name_lower in elem_name:
                    # 通过元素信息重建控件引用
                    ctrl = self._reconstruct_control(window, elem_info)
                    if ctrl:
                        return ctrl
        
        return None

    def find_element_by_type(self, control_type: str, 
                             window: Optional[object] = None,
                             index: int = 0) -> Optional[object]:
        """按控件类型查找（如 "Button", "Edit", "ComboBox"）。"""
        if window is None:
            window = self.get_foreground_window()
        if window is None:
            return None

        elements = self.list_elements(window, max_depth=8)
        matches = [e for e in elements 
                   if e.get("control_type", "").lower() == control_type.lower()]
        if matches and len(matches) > index:
            return self._reconstruct_control(window, matches[index])
        return None

    def find_all_by_name(self, name: str, 
                         window: Optional[object] = None) -> List[object]:
        """查找所有匹配名称的元素。"""
        if window is None:
            window = self.get_foreground_window()
        if window is None:
            return []

        elements = self.list_elements(window, max_depth=8)
        name_lower = name.lower()
        results = []
        for elem_info in elements:
            elem_name = elem_info.get("name", "").lower()
            if elem_name and name_lower in elem_name:
                ctrl = self._reconstruct_control(window, elem_info)
                if ctrl:
                    results.append(ctrl)
        return results

    def _reconstruct_control(self, window, elem_info: Dict) -> Optional[object]:
        """根据元素信息重建控件引用。"""
        try:
            name = elem_info.get("name", "")
            ctype = elem_info.get("control_type", "")
            aid = elem_info.get("automation_id", "")
            
            # 优先用 automation_id
            if aid:
                try:
                    ctrl = window.child_window(automation_id=aid)
                    ctrl.wait("exists", timeout=1.0)
                    return ctrl
                except Exception:
                    pass
            
            # 用 name + control_type
            if name and ctype:
                try:
                    ctrl = window.child_window(title=name, control_type=ctype)
                    ctrl.wait("exists", timeout=1.0)
                    return ctrl
                except Exception:
                    pass
            
            # 只用 name
            if name:
                try:
                    ctrl = window.child_window(title=name)
                    ctrl.wait("exists", timeout=1.0)
                    return ctrl
                except Exception:
                    pass
        except Exception:
            pass
        return None

    # ---------------- 直接操作（核心） ----------------

    def invoke_element(self, ctrl) -> bool:
        """使用 UIA Invoke Pattern 触发控件（最可靠的点击方式）。"""
        try:
            if ctrl.is_invokable:
                ctrl.invoke()
                return True
        except Exception:
            pass
        
        # 回退到 click_input
        return self._safe_click(ctrl)

    def _safe_click(self, ctrl) -> bool:
        """安全点击（多种方式回退）。"""
        methods = [
            lambda: ctrl.click_input(),
            lambda: ctrl.select(),
            lambda: ctrl.double_click_input(),
        ]
        for method in methods:
            try:
                method()
                return True
            except Exception:
                continue
        return False

    def click_element(self, name: str, 
                      window: Optional[object] = None,
                      use_invoke: bool = True) -> Tuple[bool, str]:
        """语义化点击：通过名称找到元素并点击。
        
        Args:
            name: 元素名称（如 "保存"、"关闭"、"确定"）
            window: 目标窗口，None 时使用前台窗口
            use_invoke: 是否优先使用 Invoke Pattern
            
        Returns:
            (是否成功, 结果描述)
        """
        ctrl = self.find_element_by_name(name, window)
        if ctrl is None:
            return False, f"未找到「{name}」元素"
        
        if use_invoke and ctrl.is_invokable:
            if self.invoke_element(ctrl):
                return True, f"已通过 UIA Invoke 点击「{name}」"
            return False, f"元素「{name}」支持 Invoke 但执行失败"
        
        if self._safe_click(ctrl):
            return True, f"已点击「{name}」"
        return False, f"点击「{name}」失败"

    def click_button(self, title_keyword: str, button_text: str, 
                     timeout: float = 3.0) -> bool:
        """在指定窗口中点击按钮（兼容旧接口）。"""
        win = self.find_window(title_keyword)
        if win is None:
            return False
        ctrl = self.find_element_by_name(button_text, win, timeout=timeout)
        if ctrl is None:
            return False
        return self.invoke_element(ctrl)

    def click_by_text(self, title_keyword: str, text: str,
                      timeout: float = 3.0) -> bool:
        """在指定窗口中通过文本点击任意控件（兼容旧接口）。"""
        win = self.find_window(title_keyword)
        if win is None:
            return False
        ctrl = self.find_element_by_name(text, win, timeout=timeout)
        if ctrl is None:
            return False
        return self._safe_click(ctrl)

    def double_click(self, title_keyword: str, text: str) -> bool:
        win = self.find_window(title_keyword)
        if win is None:
            return False
        ctrl = self.find_element_by_name(text, win)
        if ctrl is None:
            return False
        try:
            ctrl.double_click_input()
            return True
        except Exception as e:
            logger.error(f"双击失败: {e}")
            return False

    def set_text(self, name: str, text: str,
                 window: Optional[object] = None) -> Tuple[bool, str]:
        """在指定元素中设置文本（适用于 Edit/Input 控件）。"""
        ctrl = self.find_element_by_name(name, window)
        if ctrl is None:
            return False, f"未找到「{name}」输入框"
        
        try:
            if ctrl.is_text_available:
                ctrl.set_text(text)
                return True, f"已在「{name}」输入: {text}"
        except Exception:
            pass
        
        # 回退：先聚焦再用 type_text
        try:
            ctrl.click_input()
            time.sleep(0.1)
            if self.type_text(text):
                return True, f"已在「{name}」输入: {text}"
        except Exception as e:
            logger.error(f"设置文本失败: {e}")
        
        return False, f"设置「{name}」文本失败"

    def get_element_text(self, name: str,
                         window: Optional[object] = None) -> Optional[str]:
        """获取元素的文本内容。"""
        ctrl = self.find_element_by_name(name, window)
        if ctrl is None:
            return None
        try:
            if ctrl.is_text_available:
                return ctrl.get_text()
        except Exception:
            pass
        try:
            return ctrl.window_text() or ctrl.name
        except Exception:
            return None

    def select_combo_item(self, combo_name: str, item_text: str,
                          window: Optional[object] = None) -> Tuple[bool, str]:
        """在下拉框中选择选项。"""
        ctrl = self.find_element_by_name(combo_name, window)
        if ctrl is None:
            return False, f"未找到下拉框「{combo_name}」"
        
        try:
            ctrl.select(item_text)
            return True, f"已在「{combo_name}」中选择「{item_text}」"
        except Exception as e:
            logger.error(f"选择下拉项失败: {e}")
            return False, f"选择失败"

    def toggle_checkbox(self, name: str, window: Optional[object] = None,
                        check: bool = True) -> Tuple[bool, str]:
        """勾选或取消勾选复选框。"""
        ctrl = self.find_element_by_name(name, window)
        if ctrl is None:
            return False, f"未找到复选框「{name}」"
        
        try:
            if check:
                ctrl.check()
            else:
                ctrl.uncheck()
            return True, f"已{'勾选' if check else '取消勾选'}「{name}」"
        except Exception as e:
            logger.error(f"复选框操作失败: {e}")
            return False, f"操作失败"

    # ---------------- 键盘操作 ----------------

    def type_text(self, text: str, interval: float = 0.05) -> bool:
        """向当前焦点窗口输入文本（使用粘贴方式，支持中文）。"""
        try:
            import pyperclip
            pyperclip.copy(text)
            self._send_hotkey('ctrl', 'a')
            time.sleep(0.1)
            self._send_hotkey('ctrl', 'v')
            return True
        except ImportError:
            try:
                from pywinauto import Desktop
                desktop = Desktop(backend="uia")
                desktop.set_foreground().__class__.type_keys(text, with_spaces=True)
                return True
            except Exception as e:
                logger.error(f"降级输入失败: {e}")
                return False
        except Exception as e:
            logger.error(f"输入文本失败: {e}")
            return False

    def send_keys(self, keys: str) -> bool:
        try:
            from pywinauto.keyboard import send_keys
            send_keys(keys)
            return True
        except ImportError:
            return False
        except Exception as e:
            logger.error(f"发送按键失败: {e}")
            return False

    def _send_hotkey(self, key1: str, key2: str) -> bool:
        try:
            from pywinauto.keyboard import send_keys
            key_map = {'ctrl': '^', 'shift': '+', 'alt': '%'}
            prefix = key_map.get(key1.lower(), '')
            single = key2[0].lower() if len(key2) > 0 else key2
            send_keys(f"{prefix}{single}")
            return True
        except Exception as e:
            logger.error(f"快捷键失败: {e}")
            return False

    def press_enter(self) -> bool:
        return self.send_keys("{ENTER}")

    def press_tab(self) -> bool:
        return self.send_keys("{TAB}")

    def press_escape(self) -> bool:
        return self.send_keys("{ESC}")

    # ---------------- 复合操作 ----------------

    def search_and_send(self, app_keyword: str, contact_name: str,
                        message: str, search_field_text: str = "搜索") -> Tuple[bool, str]:
        if not self.activate_window(app_keyword):
            return False, f"未能激活 {app_keyword} 窗口"
        
        time.sleep(1.0)
        
        ok, msg = self.click_element(search_field_text)
        if not ok:
            if not self.click_button(app_keyword, "搜索"):
                return False, f"未找到搜索框/搜索按钮"
        
        time.sleep(0.5)
        self.type_text(contact_name)
        time.sleep(1.0)
        self.press_enter()
        time.sleep(1.0)
        self.type_text(message)
        time.sleep(0.3)
        self.press_enter()
        
        return True, f"已向「{contact_name}」发送消息：{message}"

    def quick_type(self, text: str) -> Tuple[bool, str]:
        if not self.type_text(text):
            return False, "输入失败"
        return True, f"已输入：{text}"

    def quick_click(self, window_title: str, control_text: str) -> Tuple[bool, str]:
        if not self.click_by_text(window_title, control_text):
            if not self.click_button(window_title, control_text):
                return False, f"未找到控件：{control_text}"
        return True, f"已点击「{control_text}」"

    # ---------------- 智能操作（供 agent_core 调用） ----------------

    def smart_click(self, description: str) -> Tuple[bool, str]:
        """智能点击：在前台窗口中查找并点击元素。
        
        按优先级尝试：
        1. UIA Invoke Pattern（最可靠）
        2. 精确名称匹配
        3. 模糊名称匹配
        4. 回退到视觉定位（返回 False 让调用方使用 VLM）
        """
        window = self.get_foreground_window()
        if window is None:
            return False, "无法获取前台窗口，可能需要使用视觉定位"
        
        # 1. 先尝试直接语义查找
        ctrl = self.find_element_by_name(description, window, fuzzy=True)
        if ctrl is not None:
            # 优先用 Invoke
            if ctrl.is_invokable:
                try:
                    ctrl.invoke()
                    return True, f"[UIA] 已通过 Invoke 点击「{description}」"
                except Exception:
                    pass
            # 回退到点击
            if self._safe_click(ctrl):
                return True, f"[UIA] 已点击「{description}」"
        
        # 2. 尝试作为按钮查找
        btn = self.find_element_by_type("Button", window)
        if btn is not None:
            try:
                name = btn.window_text() or ""
                if description.lower() in name.lower():
                    self._safe_click(btn)
                    return True, f"[UIA] 已点击按钮「{name}」"
            except Exception:
                pass
        
        return False, f"UIA 未找到「{description}」，建议使用视觉定位"

    def smart_type(self, field_name: str, text: str) -> Tuple[bool, str]:
        """智能输入：在指定输入框中输入文本。"""
        window = self.get_foreground_window()
        if window is None:
            return False, "无法获取前台窗口"
        
        return self.set_text(field_name, text, window)

    def smart_get_text(self, field_name: str) -> Tuple[bool, str]:
        """智能获取：读取指定元素的文本。"""
        window = self.get_foreground_window()
        if window is None:
            return False, "无法获取前台窗口"
        
        text = self.get_element_text(field_name, window)
        if text is not None:
            return True, text
        return False, f"未找到「{field_name}」"

    def get_screen_context(self) -> Dict[str, Any]:
        """获取当前屏幕的 UIA 上下文（供 LLM 决策）。"""
        window = self.get_foreground_window()
        if window is None:
            return {"available": False, "reason": "无前台窗口"}
        
        try:
            win_info = self.get_foreground_window_info()
            elements = self.list_elements(window, max_depth=5)
            
            # 提取关键元素信息
            key_elements = []
            for e in elements:
                name = e.get("name", "")
                ctype = e.get("control_type", "")
                if name and ctype in ("Button", "Edit", "ComboBox", "Text", "Hyperlink", "MenuItem", "TabItem"):
                    key_elements.append({
                        "name": name,
                        "type": ctype,
                        "rect": e.get("rect", ""),
                    })
            
            return {
                "available": True,
                "window": win_info,
                "key_elements": key_elements[:30],  # 限制数量
                "total_elements": len(elements),
            }
        except Exception as e:
            return {"available": False, "reason": str(e)}
