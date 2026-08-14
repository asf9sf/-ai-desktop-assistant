"""
视觉桌面自动化模块 - 基于 VLM（视觉语言模型）的屏幕感知 + pyautogui 操作
通过截屏 → VLM 识别坐标 → 精准点击/输入，实现通用桌面自动化

DPI 处理说明：
- Qt 设置 PER_MONITOR_AWARE_V2 后，进程使用物理像素坐标
- BitBlt 截图返回物理像素图片
- SetCursorPos / mouse_event 使用物理像素坐标
- pyautogui 在 DPI 感知进程中也会使用物理坐标
- 因此截图坐标和点击坐标在同一坐标系中
"""

import os
import time
import base64
import json
import logging
import ctypes
from typing import Tuple, Optional, Dict, Any

logger = logging.getLogger(__name__)


class VisionAutomator:
    """基于 VLM 的视觉桌面自动化控制器。"""

    def __init__(self, llm_client):
        """
        Args:
            llm_client: LLMClient 实例，用于调用 VLM
        """
        self.llm = llm_client
        
        # DPI 缩放因子（Qt 设置了 DPI 感知后，此值通常为 1.0 或 1.25/1.5）
        self._dpi_scale = self._detect_dpi_scale()
        logger.info(f"🖥️ VisionAutomator 初始化: DPI缩放={self._dpi_scale:.2f}")
    
    def _detect_dpi_scale(self) -> float:
        """检测系统 DPI 缩放因子。"""
        try:
            # 方法1：使用 GetDpiForWindow (Windows 10+)
            try:
                user32 = ctypes.windll.user32
                GetDpiForWindow = user32.GetDpiForWindow
                GetDpiForWindow.argtypes = [ctypes.c_void_p]
                GetDpiForWindow.restype = ctypes.c_uint
                dpi = GetDpiForWindow(0)  # 0 = 主显示器
                return dpi / 96.0
            except Exception:
                pass
            
            # 方法2：使用 GetDeviceCaps
            try:
                from ctypes import wintypes
                hdc = ctypes.windll.user32.GetDC(0)
                if hdc:
                    LOGPIXELSX = 88
                    dpi_x = ctypes.windll.gdi32.GetDeviceCaps(hdc, LOGPIXELSX)
                    ctypes.windll.user32.ReleaseDC(0, hdc)
                    return dpi_x / 96.0
            except Exception:
                pass
            
            # 方法3：使用 pyautogui
            try:
                import pyautogui
                screen_width, screen_height = pyautogui.size()
                # 如果屏幕尺寸和 GetSystemMetrics 不同，说明有 DPI 缩放
                sm_cx = ctypes.windll.user32.GetSystemMetrics(0)
                if sm_cx > 0 and screen_width > 0:
                    return screen_width / sm_cx
            except Exception:
                pass
            
            # 默认 1.0
            return 1.0
        except Exception as e:
            logger.warning(f"DPI 检测失败: {e}")
            return 1.0

    # ---------------- 截屏 ----------------

    # 固定截图文件路径，每次覆盖
    SCREENSHOT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "temp", "latest_screenshot.png")

    def capture_screen(self, region: Optional[Tuple[int, int, int, int]] = None) -> Tuple[str, int, int]:
        """
        截取屏幕并返回 (base64_png, 宽度, 高度)。
        region: (left, top, right, bottom) 可选，只截取指定区域
        
        使用 win32 API 直接截屏，绕过 PIL.ImageGrab 的缓存问题。
        返回的 width/height 是图片的实际像素尺寸（物理像素）。
        """
        try:
            # 小延迟确保屏幕已刷新
            time.sleep(0.2)

            # 强制刷新 GDI 缓存
            user32 = ctypes.windll.user32
            user32.InvalidateRect(0, None, True)
            user32.UpdateWindow(0)
            time.sleep(0.05)

            # 使用 win32 API 截屏
            from PIL import Image
            import win32gui
            import win32ui
            import win32con

            # 获取屏幕物理尺寸
            screen_width = user32.GetSystemMetrics(0)
            screen_height = user32.GetSystemMetrics(1)

            if region:
                left, top, right, bottom = region
                width = right - left
                height = bottom - top
            else:
                left, top = 0, 0
                width = screen_width
                height = screen_height

            # 记录截图区域（便于调试）
            logger.debug(f"截图区域: ({left},{top})-({right if region else screen_width},{bottom if region else screen_height}) 尺寸={width}x{height}")

            # 创建设备上下文并截图
            hdc = win32gui.GetWindowDC(0)
            mfc_dc = win32ui.CreateDCFromHandle(hdc)
            save_dc = mfc_dc.CreateCompatibleDC()

            bmp = win32ui.CreateBitmap()
            bmp.CreateCompatibleBitmap(mfc_dc, width, height)
            save_dc.SelectObject(bmp)

            # 执行 BitBlt 截屏
            save_dc.BitBlt((0, 0), (width, height), mfc_dc, (left, top), win32con.SRCCOPY)

            # 转为 PIL Image
            bmpinfo = bmp.GetInfo()
            bmpstr = bmp.GetBitmapBits(True)
            img = Image.frombuffer(
                'RGB',
                (bmpinfo['bmWidth'], bmpinfo['bmHeight']),
                bmpstr, 'raw', 'BGRX', 0, 1
            )

            # 释放资源
            win32gui.DeleteObject(bmp.GetHandle())
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(0, hdc)

            # 使用图片实际尺寸（而不是请求的尺寸）
            actual_width, actual_height = img.size
            
            # 保存到固定文件路径
            try:
                os.makedirs(os.path.dirname(self.SCREENSHOT_PATH), exist_ok=True)
                img.save(self.SCREENSHOT_PATH, format='PNG')
            except Exception as e:
                logger.warning(f"保存截图文件失败（不影响功能）: {e}")

            # 转为 base64
            from io import BytesIO
            buf = BytesIO()
            img.save(buf, format='PNG')
            img_base64 = base64.b64encode(buf.getvalue()).decode('utf-8')

            # 关键日志：记录图片的实际像素尺寸
            logger.info(f"📸 截图完成: 请求尺寸={width}x{height} 实际尺寸={actual_width}x{actual_height}")

            img.close()
            buf.close()

            # 返回实际图片尺寸
            return img_base64, actual_width, actual_height
            
        except ImportError:
            # 回退到 PIL.ImageGrab
            logger.warning("win32 不可用，回退到 PIL.ImageGrab")
            try:
                time.sleep(0.2)
                from PIL import ImageGrab
                img = ImageGrab.grab(bbox=region, all_screens=True)
                width, height = img.size

                from io import BytesIO
                buf = BytesIO()
                img.save(buf, format='PNG')
                img_base64 = base64.b64encode(buf.getvalue()).decode('utf-8')

                logger.info(f"📸 PIL 截图完成: {width}x{height}")
                img.close()
                buf.close()
                return img_base64, width, height
            except Exception as e:
                logger.error(f"截屏失败: {e}")
                return "", 0, 0
        except Exception as e:
            logger.error(f"截屏失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return "", 0, 0

    # ---------------- VLM 识别 ----------------

    def find_element(self, description: str, region: Optional[Tuple[int, int, int, int]] = None) -> Optional[Dict[str, Any]]:
        """
        截屏并让 VLM 找到指定元素的位置。
        
        Args:
            description: 要找的元素描述，如 "搜索框"、"发送按钮"、"联系人清风"
            region: 可选，限定搜索区域 (left, top, right, bottom)
            
        Returns:
            {"x": int, "y": int, "element": str, "confidence": float} 或 None
        """
        img_b64, width, height = self.capture_screen(region)
        if not img_b64:
            return None

        # 关键改进：明确告诉 VLM 图片的实际分辨率
        prompt = f"""你是一个桌面自动化助手。请在这张屏幕截图中找到【{description}】。

图片实际分辨率：{width}x{height} 像素（DPI缩放={self._dpi_scale:.2f}）

要求：
1. 返回该元素中心点的屏幕坐标 (x, y)
2. 坐标范围：x ∈ [0, {width}], y ∈ [0, {height}]
3. 如果找不到，返回 null

请严格按以下 JSON 格式返回（不要输出其他文字）：
{{
    "x": 坐标X（整数，像素值）,
    "y": 坐标Y（整数，像素值）,
    "element": "找到的元素描述",
    "confidence": 置信度0-1
}}

如果找不到，返回：
{{"x": null, "y": null, "element": null, "confidence": 0}}"""

        try:
            response = self.llm.vision_chat(prompt, img_b64)
            # 解析 JSON
            import re
            m = re.search(r"\{[\s\S]*\}", response)
            if not m:
                logger.error(f"VLM 返回无法解析: {response}")
                return None
            data = json.loads(m.group(0))
            
            x = data.get("x")
            y = data.get("y")
            if x is None or y is None:
                logger.info(f"VLM 未找到元素: {description}")
                return None
            
            x, y = int(x), int(y)
            
            # 边界校验：确保坐标在图片范围内
            x = max(0, min(width - 1, x))
            y = max(0, min(height - 1, y))
            
            # 如果指定了 region，需要加上偏移
            if region:
                x += region[0]
                y += region[1]
            
            logger.info(f"VLM 定位结果: '{description}' → ({x},{y}) 图片尺寸={width}x{height}")
            
            return {
                "x": x,
                "y": y,
                "element": data.get("element", description),
                "confidence": data.get("confidence", 0.5),
            }
        except Exception as e:
            logger.error(f"VLM 识别失败: {e}")
            return None

    def find_all_elements(self, descriptions: list, region: Optional[Tuple[int, int, int, int]] = None) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        一次性截屏，让 VLM 同时找多个元素。
        返回 {描述: 位置信息} 字典。
        """
        img_b64, width, height = self.capture_screen(region)
        if not img_b64:
            return {d: None for d in descriptions}

        desc_list = "\n".join([f"{i+1}. {d}" for i, d in enumerate(descriptions)])
        prompt = f"""你是一个桌面自动化助手。请在这张屏幕截图中找到以下元素的位置：

图片实际分辨率：{width}x{height} 像素（DPI缩放={self._dpi_scale:.2f}）

{desc_list}

请为每个元素返回中心点屏幕坐标 (x, y)。坐标范围：x ∈ [0, {width}], y ∈ [0, {height}]
严格按以下 JSON 格式返回：
[
    {{"element": "{descriptions[0]}", "x": 坐标X, "y": 坐标Y, "confidence": 置信度0-1}},
    ...
]

如果某个元素找不到，其 x 和 y 返回 null。"""

        try:
            response = self.llm.vision_chat(prompt, img_b64)
            import re
            m = re.search(r"\[[\s\S]*\]", response)
            if not m:
                return {d: None for d in descriptions}
            data = json.loads(m.group(0))
            
            result = {}
            for i, desc in enumerate(descriptions):
                if i < len(data):
                    item = data[i]
                    x = item.get("x")
                    y = item.get("y")
                    if x is not None and y is not None:
                        x, y = int(x), int(y)
                        # 边界校验
                        x = max(0, min(width - 1, x))
                        y = max(0, min(height - 1, y))
                        if region:
                            x += region[0]
                            y += region[1]
                        result[desc] = {
                            "x": x,
                            "y": y,
                            "element": item.get("element", desc),
                            "confidence": item.get("confidence", 0.5),
                        }
                    else:
                        result[desc] = None
                else:
                    result[desc] = None
            return result
        except Exception as e:
            logger.error(f"VLM 批量识别失败: {e}")
            return {d: None for d in descriptions}

    # ---------------- 操作执行 ----------------

    def _get_cursor_pos(self) -> Tuple[int, int]:
        """获取当前鼠标位置，用于点击后确认。"""
        try:
            import win32api
            p = win32api.GetCursorPos()
            return int(p[0]), int(p[1])
        except Exception:
            try:
                class POINT(ctypes.Structure):
                    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
                pt = POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                return int(pt.x), int(pt.y)
            except Exception:
                return -1, -1

    def _move_mouse_to(self, x: int, y: int) -> bool:
        """移动鼠标到指定坐标（优先使用 SetCursorPos，物理坐标最可靠）。"""
        # 方法 1：SetCursorPos（最可靠，直接用物理坐标）
        try:
            ctypes.windll.user32.SetCursorPos(int(x), int(y))
            time.sleep(0.08)
            cx, cy = self._get_cursor_pos()
            if abs(cx - x) <= 3 and abs(cy - y) <= 3:
                logger.debug(f"鼠标移动成功: ({cx},{cy})")
                return True
            logger.warning(f"SetCursorPos 位置偏差: 目标({x},{y}) 实际({cx},{cy}) 差=({abs(cx-x)},{abs(cy-y)})")
        except Exception as e:
            logger.warning(f"SetCursorPos 失败: {e}")

        # 方法 2：win32api
        try:
            import win32api
            win32api.SetCursorPos((int(x), int(y)))
            time.sleep(0.08)
            cx, cy = self._get_cursor_pos()
            if abs(cx - x) <= 3 and abs(cy - y) <= 3:
                return True
        except Exception as e:
            logger.warning(f"win32api.SetCursorPos 失败: {e}")

        # 方法 3：pyautogui（可能有坐标偏移，仅作回退）
        try:
            import pyautogui
            pyautogui.moveTo(int(x), int(y))
            time.sleep(0.1)
            cx, cy = self._get_cursor_pos()
            logger.info(f"pyautogui 移动: 目标({x},{y}) 实际({cx},{cy})")
            return True  # pyautogui.moveTo 会自己处理坐标
        except Exception as e:
            logger.error(f"pyautogui.moveTo 失败: {e}")
            return False

    def _send_click_win32(self, x: int, y: int, button: str = "left") -> bool:
        """用 mouse_event 发送点击事件（最可靠，绕过 pyautogui failsafe）。"""
        try:
            import win32api
            import win32con
            if button == "left":
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
                time.sleep(0.05)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
            elif button == "right":
                win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, x, y, 0, 0)
                time.sleep(0.05)
                win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, x, y, 0, 0)
            elif button == "double":
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
                time.sleep(0.05)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
                time.sleep(0.05)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
                time.sleep(0.05)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
            return True
        except Exception as e:
            logger.warning(f"win32 mouse_event 失败: {e}")
            return False

    def _send_click_ctypes(self, x: int, y: int, button: str = "left") -> bool:
        """用 ctypes 直接调用 mouse_event（最终回退方案）。"""
        try:
            MOUSEEVENTF_LEFTDOWN = 0x0002
            MOUSEEVENTF_LEFTUP = 0x0004
            MOUSEEVENTF_RIGHTDOWN = 0x0008
            MOUSEEVENTF_RIGHTUP = 0x0010
            user32 = ctypes.windll.user32
            if button == "left":
                user32.mouse_event(MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
                time.sleep(0.05)
                user32.mouse_event(MOUSEEVENTF_LEFTUP, x, y, 0, 0)
            elif button == "right":
                user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, x, y, 0, 0)
                time.sleep(0.05)
                user32.mouse_event(MOUSEEVENTF_RIGHTUP, x, y, 0, 0)
            elif button == "double":
                user32.mouse_event(MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
                time.sleep(0.05)
                user32.mouse_event(MOUSEEVENTF_LEFTUP, x, y, 0, 0)
                time.sleep(0.05)
                user32.mouse_event(MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
                time.sleep(0.05)
                user32.mouse_event(MOUSEEVENTF_LEFTUP, x, y, 0, 0)
            return True
        except Exception as e:
            logger.error(f"ctypes mouse_event 失败: {e}")
            return False

    def _click_at(self, x: int, y: int, button: str = "left") -> bool:
        """在指定坐标点击（多重回退，确保点击生效）。"""
        # 1. 先移动鼠标
        if not self._move_mouse_to(x, y):
            logger.error(f"无法移动鼠标到 ({x},{y})")
            return False
        time.sleep(0.1)  # 移动后短暂等待，让目标控件能正确响应 hover

        # 2. 确认鼠标位置
        cx, cy = self._get_cursor_pos()
        if cx >= 0 and abs(cx - x) > 5:
            logger.warning(f"鼠标位置偏差较大: 目标({x},{y}) 实际({cx},{cy})，重试移动")
            self._move_mouse_to(x, y)
            time.sleep(0.1)

        # 3. 发送点击事件（多重回退）
        # 方式 A：win32api.mouse_event
        if self._send_click_win32(x, y, button):
            return True
        # 方式 B：ctypes mouse_event
        if self._send_click_ctypes(x, y, button):
            return True
        # 方式 C：pyautogui
        try:
            import pyautogui
            if button == "left":
                pyautogui.click(x=x, y=y)
            elif button == "right":
                pyautogui.rightClick(x=x, y=y)
            elif button == "double":
                pyautogui.doubleClick(x=x, y=y)
            return True
        except Exception as e:
            logger.error(f"所有点击方式都失败: {e}")
            return False

    def click(self, x: int, y: int, clicks: int = 1) -> bool:
        """在指定坐标点击（带坐标验证）。"""
        logger.info(f"🖱️ 准备点击: ({x},{y}) 次数={clicks} DPI={self._dpi_scale:.2f}")
        
        # 边界检查
        try:
            screen_w = ctypes.windll.user32.GetSystemMetrics(0)
            screen_h = ctypes.windll.user32.GetSystemMetrics(1)
            if x < 0 or y < 0 or x >= screen_w or y >= screen_h:
                logger.warning(f"坐标越界: ({x},{y}) 屏幕范围(0,0)-({screen_w},{screen_h})")
                # 自动修正到屏幕范围内
                x = max(1, min(screen_w - 1, x))
                y = max(1, min(screen_h - 1, y))
                logger.info(f"修正后坐标: ({x},{y})")
        except Exception:
            pass
        
        try:
            for i in range(clicks):
                ok = self._click_at(x, y, "left")
                if not ok:
                    logger.error(f"第 {i+1} 次点击失败")
                    return False
                time.sleep(0.1)
            
            # 点击后确认
            cx, cy = self._get_cursor_pos()
            logger.info(f"✅ 点击完成: 目标({x},{y}) 实际({cx},{cy}) 偏差({abs(cx-x)},{abs(cy-y)})")
            return True
        except Exception as e:
            logger.error(f"点击失败: {e}")
            return False

    def double_click(self, x: int, y: int) -> bool:
        """在指定坐标双击。"""
        try:
            self._click_at(x, y, "left")
            time.sleep(0.05)
            self._click_at(x, y, "left")
            return True
        except Exception as e:
            logger.error(f"双击失败: {e}")
            return False

    def right_click(self, x: int, y: int) -> bool:
        """在指定坐标右键点击。"""
        try:
            self._click_at(x, y, "right")
            return True
        except Exception as e:
            logger.error(f"右键点击失败: {e}")
            return False

    def type_text(self, text: str, interval: float = 0.05) -> bool:
        """向当前焦点窗口输入文字（通过剪贴板粘贴，支持中文）。"""
        try:
            import pyperclip
            import pyautogui
            pyperclip.copy(text)
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.1)
            pyautogui.hotkey('ctrl', 'v')
            return True
        except ImportError:
            try:
                import pyperclip
                import win32clipboard
                import win32con
                import win32api
                import win32gui
                
                # 复制到剪贴板
                pyperclip.copy(text)
                
                # 模拟 Ctrl+A
                self._send_key_combo([
                    {'vk': win32con.VK_CONTROL, 'flags': 0},
                    {'vk': ord('A'), 'flags': 0},
                ])
                time.sleep(0.1)
                # 模拟 Ctrl+V
                self._send_key_combo([
                    {'vk': win32con.VK_CONTROL, 'flags': 0},
                    {'vk': ord('V'), 'flags': 0},
                ])
                return True
            except ImportError:
                # 最终回退：只用 pyperclip + 键盘 keybd_event
                try:
                    import pyperclip
                    import ctypes
                    pyperclip.copy(text)
                    
                    KEYEVENTF_KEYUP = 0x0002
                    VK_CONTROL = 0x11
                    VK_A = 0x41
                    VK_V = 0x56
                    
                    # Ctrl+A
                    ctypes.windll.user32.keybd_event(VK_CONTROL, 0, 0, 0)
                    ctypes.windll.user32.keybd_event(VK_A, 0, 0, 0)
                    ctypes.windll.user32.keybd_event(VK_A, 0, KEYEVENTF_KEYUP, 0)
                    ctypes.windll.user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
                    time.sleep(0.1)
                    # Ctrl+V
                    ctypes.windll.user32.keybd_event(VK_CONTROL, 0, 0, 0)
                    ctypes.windll.user32.keybd_event(VK_V, 0, 0, 0)
                    ctypes.windll.user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
                    ctypes.windll.user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
                    return True
                except Exception as e:
                    logger.error(f"输入文字失败: {e}")
                    return False
        except Exception as e:
            logger.error(f"输入文字失败: {e}")
            return False

    def _send_key_combo(self, keys):
        """发送组合键（win32api 方式）。"""
        import win32api
        import win32con
        import time
        for key in keys:
            win32api.keybd_event(key['vk'], 0, 0, 0)
        # 逆序释放
        for key in reversed(keys):
            win32api.keybd_event(key['vk'], 0, win32con.KEYEVENTF_KEYUP, 0)

    def press_key(self, key: str) -> bool:
        """按单个键。"""
        try:
            import pyautogui
            pyautogui.press(key)
            return True
        except ImportError:
            key_map = {
                'enter': 0x0D, 'tab': 0x09, 'escape': 0x1B,
                'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27,
                'backspace': 0x08, 'delete': 0x2E, 'space': 0x20,
            }
            vk = key_map.get(key.lower(), ord(key.upper()[0]) if len(key) == 1 else None)
            if vk is None:
                return False
            try:
                import ctypes
                KEYEVENTF_KEYUP = 0x0002
                ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
                ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
                return True
            except Exception:
                return False

    def hotkey(self, *keys) -> bool:
        """发送组合键，如 ('ctrl', 'c')。"""
        try:
            import pyautogui
            pyautogui.hotkey(*keys)
            return True
        except ImportError:
            try:
                import ctypes
                KEYEVENTF_KEYUP = 0x0002
                vk_map = {
                    'ctrl': 0x11, 'control': 0x11,
                    'shift': 0x10, 'alt': 0x12,
                    'c': 0x43, 'v': 0x56, 'a': 0x41, 's': 0x53,
                    't': 0x54, 'z': 0x5A,
                }
                vks = [vk_map.get(k.lower(), ord(k.upper()[0]) if len(k) == 1 else 0) for k in keys]
                vks = [v for v in vks if v]
                if not vks:
                    return False
                for vk in vks:
                    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
                for vk in reversed(vks):
                    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
                return True
            except Exception:
                return False

    # ---------------- 复合操作 ----------------

    def click_element(self, description: str) -> Tuple[bool, str]:
        """
        截屏 → VLM 识别 → 点击指定元素。
        """
        pos = self.find_element(description)
        if pos is None:
            return False, f"未找到「{description}」"
        
        ok = self.click(pos["x"], pos["y"])
        if ok:
            return True, f"已点击「{description}」({pos['x']},{pos['y']})"
        return False, f"点击「{description}」失败"

    def find_and_open(self, description: str) -> Tuple[bool, str]:
        """
        看看屏幕 → VLM 定位 → 双击打开指定元素。
        
        适用于"看看屏幕，然后打开XX"类指令。
        先截屏展示屏幕内容，再用 VLM 找到目标元素，最后执行双击打开。
        如果双击失败，会回退到单击。
        
        Args:
            description: 要打开的元素描述（如 "微信图标"、"记事本"、"文件管理器"）
            
        Returns:
            (是否成功, 结果描述)
        """
        logger.info(f"🔍 find_and_open: 开始查找并打开「{description}」")
        
        # 1. 截屏
        img_b64, width, height = self.capture_screen()
        if not img_b64:
            return False, "截屏失败，无法查看屏幕"
        
        logger.info(f"📸 已截取屏幕 {width}x{height}，正在查找「{description}」")
        
        # 2. 用 VLM 在截图中找到目标
        pos = self.find_element(description)
        if pos is None:
            return False, f"在当前屏幕上未找到「{description}」。请确认该元素是否可见，或尝试描述更具体的特征。"
        
        x, y = pos["x"], pos["y"]
        element_name = pos.get("element", description)
        confidence = pos.get("confidence", 0.5)
        
        logger.info(f"🎯 找到目标: 「{element_name}」在({x},{y}) 置信度={confidence:.2f}")
        
        # 3. 先尝试双击（打开文件/程序的标准操作）
        logger.info(f"🖱️ 尝试双击打开「{element_name}」...")
        ok = self.double_click(x, y)
        time.sleep(0.5)  # 等待打开效果
        
        # 4. 验证是否成功打开
        verify_result = self._verify_open_action(element_name)
        
        if verify_result.get("success"):
            return True, f"已通过 VLM 定位并双击打开了「{element_name}」（位置：{x},{y}）"
        
        # 5. 双击可能没生效，回退到单击
        logger.info(f"⚠️ 双击可能未生效，回退到单击...")
        ok = self.click(x, y)
        time.sleep(0.5)
        
        verify_result2 = self._verify_open_action(element_name)
        if verify_result2.get("success"):
            return True, f"已通过 VLM 定位并单击打开了「{element_name}」（位置：{x},{y}）"
        
        # 6. 如果都没成功，报告情况
        if confidence < 0.5:
            return False, f"找到了疑似「{element_name}」但置信度较低（{confidence:.2f}），可能点错了位置。建议更精确地描述目标。"
        
        return False, f"找到了「{element_name}」在({x},{y})，但双击和单击都未能成功打开。可能该元素不支持此操作。"

    def _verify_open_action(self, element_name: str) -> Dict[str, Any]:
        """验证打开操作是否成功（通过截屏检查界面是否变化）。"""
        try:
            img_b64, w, h = self.capture_screen()
            if not img_b64:
                return {"success": False, "detail": "验证截屏失败"}
            
            prompt = f"""你是一个操作验证助手。请判断上一步的"双击打开"操作是否成功。

目标元素：{element_name}
当前是操作后的屏幕截图。

请判断：
1. 是否看到了目标元素被打开（出现了新窗口、新界面、或焦点变化）
2. 返回 JSON：{{"success": true/false, "detail": "判断依据"}}"""

            response = self.llm.vision_chat(prompt, img_b64)
            import re
            m = re.search(r"\{[\s\S]*\}", response)
            if m:
                return json.loads(m.group(0))
            return {"success": False, "detail": "无法解析 VLM 验证结果"}
        except Exception as e:
            logger.warning(f"验证打开操作异常: {e}")
            return {"success": False, "detail": str(e)}

    def click_and_type(self, element_desc: str, text: str) -> Tuple[bool, str]:
        """
        点击元素 + 输入文字。
        """
        pos = self.find_element(element_desc)
        if pos is None:
            return False, f"未找到「{element_desc}」"
        
        self.click(pos["x"], pos["y"])
        time.sleep(0.3)
        self.type_text(text)
        return True, f"已在「{element_desc}」输入文字"

    def search_and_send(self, search_desc: str, contact_name: str, 
                        message: str, send_desc: str = "发送按钮") -> Tuple[bool, str]:
        """
        通用聊天发送流程：
        1. 点击搜索框
        2. 输入联系人名
        3. 按回车选择
        4. 输入消息
        5. 点击发送
        """
        # 1. 截屏识别所有需要的元素
        elements = self.find_all_elements([search_desc, send_desc])
        
        # 2. 点击搜索框
        search_pos = elements.get(search_desc)
        if search_pos:
            self.click(search_pos["x"], search_pos["y"])
            time.sleep(0.5)
        else:
            # 尝试直接点击搜索框关键词
            ok, msg = self.click_element(search_desc)
            if not ok:
                return False, f"未找到搜索框"
            time.sleep(0.5)
        
        # 3. 输入联系人名并回车
        self.type_text(contact_name)
        time.sleep(1.5)  # 等待搜索结果
        self.press_key("enter")
        time.sleep(1.0)  # 等待对话打开
        
        # 4. 输入消息
        self.type_text(message)
        time.sleep(0.3)
        
        # 5. 点击发送
        send_pos = elements.get(send_desc)
        if send_pos:
            self.click(send_pos["x"], send_pos["y"])
        else:
            # 重新找发送按钮
            ok, msg = self.click_element(send_desc)
            if not ok:
                # 退化为按回车发送
                self.press_key("enter")
        
        return True, f"已向「{contact_name}」发送消息：{message}"

    def click_first_result(self, result_description: str = "第一个搜索结果",
                           max_retries: int = 3) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        在浏览器搜索结果页面中，用 VLM 找到并点击第一个搜索结果。

        两阶段精确定位策略（避免 VLM 坐标偏差）：
          阶段1 - 粗定位：让 VLM 返回第一篇文章的 bounding box (x1,y1,x2,y2) + 标题文字
          阶段2 - 精定位：裁剪该 bounding box 区域，让 VLM 在小图里精确定位标题关键词的中心点
                         （小区域内 VLM 精度极高，不会跑偏）

        Args:
            result_description: 要找的结果描述
            max_retries: 最多重试次数

        Returns:
            (是否成功, 描述信息, 位置详情 dict)
        """
        import re as re_mod
        from PIL import Image, ImageDraw
        from io import BytesIO

        for attempt in range(max_retries):
            logger.info(f"click_first_result 第 {attempt+1}/{max_retries} 次尝试 (两阶段定位)")

            # 等待页面加载
            wait_time = 4.0 if attempt == 0 else 2.5
            logger.info(f"等待页面加载 {wait_time}s ...")
            time.sleep(wait_time)

            # 截屏
            img_b64, width, height = self.capture_screen()
            if not img_b64:
                logger.warning("截屏失败，重试")
                continue

            # 安全区域
            y_min = int(height * 0.25)
            y_max = int(height * 0.92)
            logger.info(f"安全区域: y ∈ [{y_min}, {y_max}] (屏幕 {width}x{height})")

            # ==================== 阶段1：粗定位 - 找第一篇文章的 bounding box ====================
            box = self._find_first_result_box(img_b64, width, height, y_min, y_max)
            if not box:
                logger.warning(f"阶段1 未找到第一篇文章区域 (第{attempt+1}次)")
                continue

            x1, y1, x2, y2, title = box
            logger.info(f"阶段1 成功: 第一篇文章区域=({x1},{y1})-({x2},{y2}) 标题='{title}'")

            # ==================== 阶段2：精定位 - 在 bounding box 内找关键词中心点 ====================
            click_pos = self._find_keyword_in_box(img_b64, x1, y1, x2, y2, title, width, height)
            if not click_pos:
                logger.warning(f"阶段2 未能在区域内定位到关键词，回退到 bounding box 中心点")
                # 回退：用 bounding box 中心点
                click_x = (x1 + x2) // 2
                click_y = (y1 + y2) // 2
            else:
                click_x, click_y = click_pos
                logger.info(f"阶段2 成功: 关键词中心点=({click_x},{click_y})")

            # ==================== 可视化调试 ====================
            debug_path = ""
            try:
                img_raw = base64.b64decode(img_b64)
                debug_img = Image.open(BytesIO(img_raw))
                draw = ImageDraw.Draw(debug_img)
                # 画安全区域（绿色）
                draw.rectangle([(0, y_min), (width, y_max)], outline=(0, 255, 0), width=2)
                # 画 bounding box（蓝色）
                draw.rectangle([(x1, y1), (x2, y2)], outline=(0, 120, 255), width=3)
                draw.text((x1, y1 - 20), f"Box: {title[:30]}", fill=(0, 120, 255))
                # 画最终点击点（红色十字）
                r = 18
                draw.ellipse([(click_x - r, click_y - r), (click_x + r, click_y + r)],
                             outline=(255, 0, 0), width=3)
                draw.line([(click_x - r - 10, click_y), (click_x + r + 10, click_y)],
                          fill=(255, 0, 0), width=2)
                draw.line([(click_x, click_y - r - 10), (click_x, click_y + r + 10)],
                          fill=(255, 0, 0), width=2)
                draw.text((click_x + r + 5, click_y - 10),
                          f"Click ({click_x},{click_y})", fill=(255, 0, 0))
                debug_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "temp", f"debug_click_attempt{attempt+1}_{int(time.time())}.png"
                )
                os.makedirs(os.path.dirname(debug_path), exist_ok=True)
                debug_img.save(debug_path)
                logger.info(f"可视化调试图已保存: {debug_path}")
                debug_img.close()
            except Exception as e:
                logger.warning(f"生成可视化调试图失败: {e}")

            # ==================== 执行点击 ====================
            logger.info(f"准备点击: ({click_x},{click_y}) 标题='{title[:40]}'")
            ok = self.click(click_x, click_y)
            if not ok:
                logger.error("点击执行失败")
                continue

            # 等待页面跳转
            time.sleep(2.5)

            result_info = {
                "x": click_x, "y": click_y,
                "box": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                "title": title,
                "attempt": attempt + 1,
                "screen_size": f"{width}x{height}",
                "safe_area": f"y[{y_min},{y_max}]",
                "debug_image": debug_path,
            }
            return True, (
                f"已点击第一个搜索结果「{title}」"
                f"(区域({x1},{y1})-({x2},{y2}), 点击({click_x},{click_y}))"
            ), result_info

        return False, f"经过 {max_retries} 次尝试仍未找到并点击到第一个搜索结果", None

    def _find_first_result_box(self, img_b64: str, width: int, height: int,
                                y_min: int, y_max: int) -> Optional[Tuple[int, int, int, int, str]]:
        """
        阶段1：让 VLM 找到第一篇文章的 bounding box。

        Returns:
            (x1, y1, x2, y2, title) 或 None
        """
        import re as re_mod

        prompt = f"""你是一个浏览器搜索结果定位专家。当前屏幕显示的是一个搜索结果页面（CSDN/B站/知乎/Google等）。

任务：找到页面中【第一个真正的搜索结果】的矩形边界框 (bounding box)。

=== 重要排除项 ===
1. 页面顶部区域（y < {y_min}）：浏览器标签栏、地址栏、导航栏、搜索框、广告、筛选标签
2. AI 生成的摘要/回答块：CSDN的"C知道 AI搜索结果"、百度的"AI智能回答"、知乎的"AI 回答"、Google的"AI Overview"等
   - 特征：带 AI 标识徽章，内容是总结性回答而非文章列表，通常有"展开/收起"按钮
   - 即使出现在最前面也必须跳过，找它下面真正的文章列表
3. 页面底部（y > {y_max}）：页脚、翻页按钮、相关推荐
4. 侧边栏：热门推荐、相关文章、广告位
5. 带有「广告」「推广」「AD」「Sponsored」标识的条目

=== 真正搜索结果的特征 ===
- 出现在 AI 摘要块下方（如果有）
- 包含：标题（彩色/蓝色超链接）+ 摘要（灰色小字）+ 作者/日期/阅读量
- 是文章列表形式，每行一条

屏幕尺寸：{width}x{height}

请返回第一篇文章的完整矩形区域（包含标题+摘要+来源信息的整体块），严格按以下 JSON 格式：
{{
    "x1": 左上角X（整数像素）,
    "y1": 左上角Y（整数像素）,
    "x2": 右下角X（整数像素）,
    "y2": 右下角Y（整数像素）,
    "title": "该文章的标题文字",
    "confidence": 0.9
}}

如果找不到，返回：
{{"x1": null, "y1": null, "x2": null, "y2": null, "title": "", "confidence": 0}}"""

        try:
            response = self.llm.vision_chat(prompt, img_b64)
            logger.info(f"阶段1 VLM 返回 ({len(response)} chars): {response[:400]}")
            
            # 更健壮的 JSON 提取：尝试多种策略
            data = None
            # 策略1：用正则提取第一个 { ... } 块
            m = re_mod.search(r"\{[\s\S]*?\}", response)
            if m:
                try:
                    data = json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
            
            # 策略2：如果策略1失败，尝试直接解析整个响应
            if data is None:
                try:
                    data = json.loads(response.strip())
                except json.JSONDecodeError:
                    pass
            
            # 策略3：手动清理常见问题后再解析
            if data is None:
                try:
                    cleaned = response.strip()
                    # 移除 markdown 代码块标记
                    if cleaned.startswith("```"):
                        cleaned = cleaned[cleaned.index("\n")+1:] if "\n" in cleaned else cleaned[3:]
                    if cleaned.endswith("```"):
                        cleaned = cleaned[:-3]
                    cleaned = cleaned.strip()
                    m2 = re_mod.search(r"\{[\s\S]*\}", cleaned)
                    if m2:
                        data = json.loads(m2.group(0))
                except Exception:
                    pass
            
            if data is None:
                logger.warning(f"阶段1 VLM 返回无法解析为 JSON")
                return None

            x1, y1 = data.get("x1"), data.get("y1")
            x2, y2 = data.get("x2"), data.get("y2")
            title = data.get("title", "")

            if None in (x1, y1, x2, y2):
                logger.info(f"阶段1 VLM 未找到第一篇文章")
                return None

            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)

            # 校验 bounding box 合理性
            if x2 <= x1 or y2 <= y1:
                logger.warning(f"阶段1 bounding box 不合理: ({x1},{y1})-({x2},{y2})")
                return None
            if x1 < 0 or y1 < 0 or x2 > width or y2 > height:
                logger.warning(f"阶段1 bounding box 越界: ({x1},{y1})-({x2},{y2}) 屏幕={width}x{height}")
                # 修正到屏幕范围内
                x1 = max(0, x1)
                y1 = max(0, y1)
                x2 = min(width, x2)
                y2 = min(height, y2)

            # 扩展 bounding box 一点点（防止 VLM 给的框太小，裁剪时漏掉关键词）
            pad = 8
            x1 = max(0, x1 - pad)
            y1 = max(0, y1 - pad)
            x2 = min(width, x2 + pad)
            y2 = min(height, y2 + pad)

            return (x1, y1, x2, y2, title)

        except json.JSONDecodeError as e:
            logger.error(f"阶段1 JSON 解析失败: {e}")
            return None
        except Exception as e:
            logger.error(f"阶段1 异常: {e}")
            return None

    def _find_keyword_in_box(self, img_b64: str, x1: int, y1: int, x2: int, y2: int,
                              title: str, screen_w: int, screen_h: int) -> Optional[Tuple[int, int]]:
        """
        阶段2：裁剪 bounding box 区域，让 VLM 在小图里精确定位标题关键词的中心点。

        VLM 在小区域内精度极高，能准确找到蓝色超链接文字的中心。

        Returns:
            (click_x, click_y) 全屏绝对坐标，或 None
        """
        import re as re_mod
        from PIL import Image
        from io import BytesIO

        try:
            # 裁剪 bounding box 区域
            img_raw = base64.b64decode(img_b64)
            full_img = Image.open(BytesIO(img_raw))
            crop_img = full_img.crop((x1, y1, x2, y2))

            crop_w, crop_h = crop_img.size
            logger.info(f"阶段2 裁剪区域: ({x1},{y1})-({x2},{y2}) 尺寸={crop_w}x{crop_h}")

            # 转为 base64
            buf = BytesIO()
            crop_img.save(buf, format='PNG')
            crop_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
            buf.close()
            full_img.close()
            crop_img.close()

            # 关键词：从标题中提取核心词（去掉标点和常见停用词）
            # 如果标题过长，取前 10 个字作为关键词
            keyword = title[:15] if title else "标题文字"

            prompt = f"""这是一张从搜索结果页面裁剪出来的小图，尺寸 {crop_w}x{crop_h} 像素。
这张图里包含一篇文章的标题和摘要。

任务：找到图中【标题文字】（通常是蓝色/彩色的超链接文字）的中心点坐标。

标题文字内容（近似）：「{keyword}」

注意：
1. 标题是蓝色/彩色的可点击超链接文字，通常在图片上方
2. 摘要是灰色/黑色小字，在标题下方
3. 你要返回的是【标题文字本身的中心点】，不是整个区域的中心
4. 坐标是相对于这张小图的（左上角为 0,0）

请严格按以下 JSON 格式返回：
{{
    "x": 标题文字中心X（整数，相对于小图）,
    "y": 标题文字中心Y（整数，相对于小图）,
    "found": true/false,
    "actual_text": "你实际看到的标题文字"
}}

如果找不到标题文字，返回 {{"x": null, "y": null, "found": false, "actual_text": ""}}"""

            response = self.llm.vision_chat(prompt, crop_b64)
            logger.info(f"阶段2 VLM 返回: {response[:300]}")
            m = re_mod.search(r"\{[\s\S]*\}", response)
            if not m:
                logger.warning("阶段2 VLM 返回无法解析")
                return None
            data = json.loads(m.group(0))

            if not data.get("found", False):
                logger.info(f"阶段2 VLM 表示未找到标题文字")
                return None

            local_x = data.get("x")
            local_y = data.get("y")
            if local_x is None or local_y is None:
                return None

            local_x, local_y = int(local_x), int(local_y)

            # 转换回全屏坐标
            abs_x = x1 + local_x
            abs_y = y1 + local_y

            # 边界校验
            abs_x = max(0, min(screen_w - 1, abs_x))
            abs_y = max(0, min(screen_h - 1, abs_y))

            actual_text = data.get("actual_text", "")
            logger.info(f"阶段2 定位到关键词: 局部({local_x},{local_y}) → 全屏({abs_x},{abs_y}) 文字='{actual_text}'")

            return (abs_x, abs_y)

        except Exception as e:
            logger.error(f"阶段2 异常: {e}")
            return None

    # ---------------- 屏幕感知 ----------------

    def screen_context(self, focus: str = "") -> Dict[str, Any]:
        """
        快速获取当前屏幕的结构化概览信息。
        告诉 Agent 当前屏幕上有哪些窗口、关键 UI 元素、状态等。

        Args:
            focus: 可选，聚焦描述（如 "浏览器地址栏"、"微信对话框"）

        Returns:
            结构化屏幕信息字典
        """
        img_b64, width, height = self.capture_screen()
        if not img_b64:
            return {"error": "截屏失败"}

        prompt = f"""你是一个屏幕感知助手。请分析这张屏幕截图，返回结构化信息。

当前屏幕尺寸：{width}x{height}
聚焦区域：{focus if focus else '整体概览'}

请返回以下 JSON 格式的屏幕描述：
{{
    "active_window": "当前活动窗口标题",
    "app_type": "应用类型（浏览器/聊天/编辑器/桌面等）",
    "key_elements": ["关键元素1", "关键元素2", ...],
    "status": "当前状态描述（如：浏览器打开了B站首页/微信正在聊天中/文件管理器显示了文档文件夹）",
    "suggestions": ["建议操作1", "建议操作2"]
}}

要求：
1. key_elements 列出屏幕上最重要的 3-5 个可交互元素
2. status 用一句话总结当前屏幕状态
3. suggestions 列出基于当前状态可以执行的操作
4. 如果是浏览器，要说明当前网页的主要内容
5. 如果是聊天软件，要说明当前打开的对话/联系人
6. 如果有对话框/弹窗/错误提示，请特别标注"""

        try:
            response = self.llm.vision_chat(prompt, img_b64)
            import re as re_mod
            m = re_mod.search(r"\{[\s\S]*\}", response)
            if not m:
                return {"error": f"VLM 返回无法解析", "raw": response[:200]}
            data = json.loads(m.group(0))
            return data
        except Exception as e:
            logger.error(f"屏幕感知失败: {e}")
            return {"error": str(e)}

    def verify_action(self, action_description: str, expected_result: str = "") -> Dict[str, Any]:
        """
        操作后验证：截屏检查上一步操作是否成功。

        Args:
            action_description: 上一步执行的操作描述（如 "点击了搜索按钮"、"输入了文字"）
            expected_result: 期望看到的结果（如 "搜索结果出现"、"文字出现在输入框中"）

        Returns:
            {"success": bool, "details": str, "visible_elements": [...]}
        """
        time.sleep(0.8)  # 等待操作生效
        img_b64, width, height = self.capture_screen()
        if not img_b64:
            return {"success": False, "details": "截屏失败", "visible_elements": []}

        prompt = f"""你是一个操作验证助手。请检查屏幕截图，判断上一步操作是否成功。

上一步操作：{action_description}
期望结果：{expected_result if expected_result else '操作应已成功执行'}

请返回 JSON：
{{
    "success": true/false,
    "details": "判断依据和详细说明",
    "visible_elements": ["当前可见的关键元素"],
    "next_suggestion": "下一步建议（如成功则说'继续下一步'，失败则说'重试'或替代方案）"
}}

注意：
- 如果看到操作产生了预期的界面变化（如页面跳转/按钮状态变化/文字出现），则 success=true
- 如果界面没有变化或出现错误提示，success=false
- 如果看到错误弹窗或警告，请在 details 中说明"""

        try:
            response = self.llm.vision_chat(prompt, img_b64)
            import re as re_mod
            m = re_mod.search(r"\{[\s\S]*\}", response)
            if not m:
                return {"success": False, "details": f"无法解析 VLM 输出", "raw": response[:200]}
            data = json.loads(m.group(0))
            return data
        except Exception as e:
            logger.error(f"操作验证失败: {e}")
            return {"success": False, "details": str(e), "visible_elements": []}

    def watch_screen(self, condition: str, timeout: int = 30, interval: float = 2.0) -> Dict[str, Any]:
        """
        持续监控屏幕，直到满足指定条件。

        Args:
            condition: 等待的条件描述（如 "下载完成"、"邮件到达"、"价格低于100"）
            timeout: 最大等待秒数
            interval: 检查间隔秒数

        Returns:
            {"matched": bool, "detail": str, "elapsed": float}
        """
        start_time = time.time()
        last_context = {}

        while time.time() - start_time < timeout:
            img_b64, width, height = self.capture_screen()
            if not img_b64:
                time.sleep(interval)
                continue

            prompt = f"""请检查这张屏幕截图，判断是否满足以下条件：
条件：{condition}

返回 JSON：
{{
    "matched": true/false,
    "detail": "判断依据",
    "current_state": "当前屏幕状态描述"
}}"""

            try:
                response = self.llm.vision_chat(prompt, img_b64)
                import re as re_mod
                m = re_mod.search(r"\{[\s\S]*\}", response)
                if m:
                    data = json.loads(m.group(0))
                    if data.get("matched", False):
                        return {
                            "matched": True,
                            "detail": data.get("detail", ""),
                            "current_state": data.get("current_state", ""),
                            "elapsed": round(time.time() - start_time, 1),
                        }
                    last_context = data
            except Exception:
                pass

            time.sleep(interval)

        return {
            "matched": False,
            "detail": f"在 {timeout} 秒内未检测到条件满足",
            "last_state": last_context.get("current_state", "未知"),
            "elapsed": timeout,
        }

    def get_active_window_info(self) -> Dict[str, Any]:
        """获取当前活动窗口的基本信息（不调用 VLM，纯 win32 API，速度快）。"""
        try:
            import win32gui
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd) if hwnd else ""
            class_name = win32gui.GetClassName(hwnd) if hwnd else ""
            rect = win32gui.GetWindowRect(hwnd) if hwnd else (0, 0, 0, 0)
            return {
                "title": title,
                "class": class_name,
                "rect": rect,
                "hwnd": hwnd,
            }
        except Exception as e:
            return {"error": str(e)}

    # ---------------- 坐标校准与诊断 ----------------

    def calibrate_coordinates(self) -> Dict[str, Any]:
        """
        坐标校准：验证截图坐标与点击坐标是否一致。
        这是一个诊断工具，用于发现 DPI 缩放导致的坐标偏移问题。
        
        Returns:
            {"success": bool, "details": str, "offsets": list}
        """
        logger.info("🔧 开始坐标校准...")
        
        results = {
            "success": True,
            "dpi_scale": self._dpi_scale,
            "tests": [],
            "max_offset": 0,
        }
        
        # 获取屏幕尺寸
        try:
            screen_w = ctypes.windll.user32.GetSystemMetrics(0)
            screen_h = ctypes.windll.user32.GetSystemMetrics(1)
        except Exception:
            return {"success": False, "details": "无法获取屏幕尺寸"}
        
        logger.info(f"屏幕物理尺寸: {screen_w}x{screen_h}")
        logger.info(f"DPI 缩放因子: {self._dpi_scale:.2f}")
        
        # 测试点（四个角和中心）
        test_points = [
            ("左上", int(screen_w * 0.1), int(screen_h * 0.1)),
            ("右上", int(screen_w * 0.9), int(screen_h * 0.1)),
            ("左下", int(screen_w * 0.1), int(screen_h * 0.9)),
            ("右下", int(screen_w * 0.9), int(screen_h * 0.9)),
            ("中心", int(screen_w * 0.5), int(screen_h * 0.5)),
        ]
        
        for name, target_x, target_y in test_points:
            try:
                # 移动鼠标
                ctypes.windll.user32.SetCursorPos(target_x, target_y)
                time.sleep(0.1)
                
                # 读取实际位置
                actual_x, actual_y = self._get_cursor_pos()
                offset_x = abs(actual_x - target_x)
                offset_y = abs(actual_y - target_y)
                offset = max(offset_x, offset_y)
                
                test_result = {
                    "point": name,
                    "target": (target_x, target_y),
                    "actual": (actual_x, actual_y),
                    "offset": offset,
                    "ok": offset <= 5,
                }
                results["tests"].append(test_result)
                
                if offset > results["max_offset"]:
                    results["max_offset"] = offset
                    
                status = "✅" if offset <= 5 else "⚠️" if offset <= 20 else "❌"
                logger.info(f"{status} {name}: 目标({target_x},{target_y}) 实际({actual_x},{actual_y}) 偏移={offset}px")
                
            except Exception as e:
                results["tests"].append({
                    "point": name,
                    "error": str(e),
                })
                logger.error(f"❌ {name} 测试失败: {e}")
                results["success"] = False
        
        # 总结
        if results["max_offset"] > 20:
            results["success"] = False
            results["details"] = f"坐标偏移过大 (最大 {results['max_offset']}px)，可能存在 DPI 缩放问题"
        elif results["max_offset"] > 5:
            results["success"] = True
            results["details"] = f"坐标基本准确 (最大偏移 {results['max_offset']}px)，对 VLM 定位影响较小"
        else:
            results["success"] = True
            results["details"] = f"坐标精准 (最大偏移 {results['max_offset']}px)，VLM 定位应该准确"
        
        logger.info(f"📊 校准结果: {results['details']}")
        return results

    def get_diagnostics(self) -> Dict[str, Any]:
        """获取当前视觉自动化模块的诊断信息。"""
        try:
            screen_w = ctypes.windll.user32.GetSystemMetrics(0)
            screen_h = ctypes.windll.user32.GetSystemMetrics(1)
        except Exception:
            screen_w, screen_h = 0, 0
        
        # 尝试截屏获取实际图片尺寸
        img_b64, img_w, img_h = self.capture_screen()
        
        return {
            "dpi_scale": self._dpi_scale,
            "screen_physical": f"{screen_w}x{screen_h}",
            "screenshot_size": f"{img_w}x{img_h}",
            "size_match": screen_w == img_w and screen_h == img_h,
            "image_saved": os.path.exists(self.SCREENSHOT_PATH),
            "cursor_pos": self._get_cursor_pos(),
        }
