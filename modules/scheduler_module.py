"""
定时任务调度器 - 支持 cron 表达式、一次性任务、间隔任务

功能：
- cron 表达式定时（如 "0 8 * * *" = 每天早上 8 点）
- 间隔定时（如每 30 分钟、每小时）
- 一次性定时（如 2026-08-07 14:00 执行）
- 任务持久化（保存到 JSON 配置）
- 执行结果回调通知
"""

import os
import json
import re
import time
import uuid
import threading
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Callable

logger = logging.getLogger(__name__)

# cron 表达式解析：分 时 日 月 周
# 每个字段支持: *, */N, N, N-M, N,M,O 以及组合
CRON_FIELDS = [
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day", 1, 31),
    ("month", 1, 12),
    ("day_of_week", 0, 6),  # 0=Sunday
]


def parse_cron_field(field: str, min_val: int, max_val: int) -> set:
    """解析单个 cron 字段，返回所有匹配值的集合。"""
    values = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        if part == "*":
            values.update(range(min_val, max_val + 1))
        elif part.startswith("*/"):
            step = int(part[2:])
            values.update(range(min_val, max_val + 1, step))
        elif "-" in part:
            range_part, step = (part.split("/") + ["1"])[:2]
            start, end = map(int, range_part.split("-"))
            values.update(range(start, end + 1, int(step)))
        else:
            values.add(int(part))
    return values


def parse_cron(expression: str) -> Optional[Dict[str, set]]:
    """解析 cron 表达式，返回各字段的匹配值。"""
    parts = expression.strip().split()
    if len(parts) != 5:
        return None

    result = {}
    for i, (name, min_val, max_val) in enumerate(CRON_FIELDS):
        try:
            result[name] = parse_cron_field(parts[i], min_val, max_val)
        except (ValueError, IndexError):
            return None
    return result


def cron_matches(expression: str, dt: datetime) -> bool:
    """检查给定时间是否匹配 cron 表达式。"""
    parsed = parse_cron(expression)
    if not parsed:
        return False
    return (dt.minute in parsed["minute"]
            and dt.hour in parsed["hour"]
            and dt.day in parsed["day"]
            and dt.month in parsed["month"]
            and dt.weekday() + 1 in parsed["day_of_week"])


class SchedulerTask:
    """单个调度任务。"""

    def __init__(self, task_id: str = "", name: str = "",
                 cron: str = "", interval_minutes: int = 0,
                 run_at: str = "", prompt: str = "",
                 enabled: bool = True, last_run: str = "",
                 next_run: str = "", run_count: int = 0):
        self.task_id = task_id or str(uuid.uuid4())[:8]
        self.name = name
        self.cron = cron
        self.interval_minutes = interval_minutes
        self.run_at = run_at  # ISO datetime for one-shot
        self.prompt = prompt  # 要发送给 Agent 的指令
        self.enabled = enabled
        self.last_run = last_run
        self.next_run = next_run
        self.run_count = run_count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "cron": self.cron,
            "interval_minutes": self.interval_minutes,
            "run_at": self.run_at,
            "prompt": self.prompt,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "run_count": self.run_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SchedulerTask":
        return cls(
            task_id=data.get("task_id", ""),
            name=data.get("name", ""),
            cron=data.get("cron", ""),
            interval_minutes=data.get("interval_minutes", 0),
            run_at=data.get("run_at", ""),
            prompt=data.get("prompt", ""),
            enabled=data.get("enabled", True),
            last_run=data.get("last_run", ""),
            next_run=data.get("next_run", ""),
            run_count=data.get("run_count", 0),
        )

    def compute_next_run(self) -> Optional[datetime]:
        """计算下次运行时间。"""
        now = datetime.now()

        if self.interval_minutes > 0:
            return now + timedelta(minutes=self.interval_minutes)

        if self.cron:
            return self._find_next_cron_time(now)

        if self.run_at:
            try:
                return datetime.fromisoformat(self.run_at)
            except (ValueError, TypeError):
                return None

        return None

    def _find_next_cron_time(self, from_time: datetime) -> Optional[datetime]:
        """根据 cron 表达式找下一个匹配时间。"""
        parsed = parse_cron(self.cron)
        if not parsed:
            return None

        # 最多查找 366 天
        for day_offset in range(366):
            check_date = from_time + timedelta(days=day_offset)
            for minute in range(0, 60):
                check_time = check_date.replace(minute=minute, second=0, microsecond=0)
                if day_offset == 0 and check_time <= from_time:
                    continue
                if (check_time.minute in parsed["minute"]
                        and check_time.hour in parsed["hour"]
                        and check_time.day in parsed["day"]
                        and check_time.month in parsed["month"]
                        and check_time.weekday() + 1 in parsed["day_of_week"]):
                    return check_time
        return None


class Scheduler:
    """定时任务调度器。"""

    def __init__(self, save_path: str = ""):
        self._tasks: Dict[str, SchedulerTask] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._on_task_execute: Optional[Callable[[str], None]] = None
        self._on_status_change: Optional[Callable[[str], None]] = None
        self._tick_count = 0

        if not save_path:
            save_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config", "scheduler_tasks.json"
            )
        self._save_path = save_path
        self._load_tasks()

    # ---------- 配置 ----------

    def set_on_task_execute(self, callback: Callable[[str], None]):
        """设置任务执行回调（接收 prompt 文本）。"""
        self._on_task_execute = callback

    def set_on_status_change(self, callback: Callable[[str], None]):
        """设置状态变化回调。"""
        self._on_status_change = callback

    # ---------- 任务管理 ----------

    def add_task(self, name: str, prompt: str,
                 cron: str = "", interval_minutes: int = 0,
                 run_at: str = "", enabled: bool = True) -> Dict[str, Any]:
        """添加定时任务。"""
        with self._lock:
            task = SchedulerTask(
                name=name, prompt=prompt,
                cron=cron, interval_minutes=interval_minutes,
                run_at=run_at, enabled=enabled,
            )
            next_run = task.compute_next_run()
            if next_run:
                task.next_run = next_run.strftime("%Y-%m-%d %H:%M:%S")
            self._tasks[task.task_id] = task
            self._save_tasks()
            logger.info(f"添加任务: {name} (ID: {task.task_id}), 下次运行: {task.next_run}")
            return {"success": True, "task": task.to_dict()}

    def remove_task(self, task_id: str) -> Dict[str, Any]:
        """删除任务。"""
        with self._lock:
            if task_id in self._tasks:
                name = self._tasks[task_id].name
                del self._tasks[task_id]
                self._save_tasks()
                logger.info(f"删除任务: {name} (ID: {task_id})")
                return {"success": True}
            return {"success": False, "error": "任务不存在"}

    def toggle_task(self, task_id: str, enabled: Optional[bool] = None) -> Dict[str, Any]:
        """启用/禁用任务。"""
        with self._lock:
            if task_id not in self._tasks:
                return {"success": False, "error": "任务不存在"}
            task = self._tasks[task_id]
            if enabled is None:
                task.enabled = not task.enabled
            else:
                task.enabled = enabled
            self._save_tasks()
            logger.info(f"{'启用' if task.enabled else '禁用'}任务: {task.name}")
            return {"success": True}

    def list_tasks(self) -> List[Dict[str, Any]]:
        """列出所有任务。"""
        with self._lock:
            # 更新 next_run
            for task in self._tasks.values():
                if task.enabled and not task.next_run:
                    nr = task.compute_next_run()
                    if nr:
                        task.next_run = nr.strftime("%Y-%m-%d %H:%M:%S")
            return [t.to_dict() for t in self._tasks.values()]

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取单个任务。"""
        with self._lock:
            task = self._tasks.get(task_id)
            return task.to_dict() if task else None

    def run_task_now(self, task_id: str) -> Dict[str, Any]:
        """立即执行某个任务。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"success": False, "error": "任务不存在"}
            if not task.enabled:
                return {"success": False, "error": "任务已禁用"}
            prompt = task.prompt

        # 释放锁后执行
        self._execute_task(task, force=True)
        return {"success": True, "task": task.to_dict()}

    # ---------- 运行控制 ----------

    def start(self):
        """启动调度器。"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="scheduler-loop")
        self._thread.start()
        logger.info("调度器已启动")
        self._notify_status("调度器已启动")

    def stop(self):
        """停止调度器。"""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None
        logger.info("调度器已停止")
        self._notify_status("调度器已停止")

    def is_running(self) -> bool:
        return self._running

    # ---------- 内部逻辑 ----------

    def _run_loop(self):
        """调度器主循环（每秒检查一次）。"""
        logger.info("调度器主循环已启动")
        while self._running:
            try:
                self._tick_count += 1
                self._check_and_run_tasks()
                time.sleep(1)
            except Exception as e:
                logger.error(f"调度器循环异常: {e}")
                time.sleep(5)

    def _check_and_run_tasks(self):
        """检查并运行到期任务。"""
        now = datetime.now()
        tasks_to_run = []

        with self._lock:
            expired_ids = []
            for task_id, task in self._tasks.items():
                if not task.enabled:
                    continue

                # 一次性任务
                if task.run_at:
                    try:
                        run_time = datetime.fromisoformat(task.run_at)
                        if now >= run_time:
                            tasks_to_run.append(task)
                            expired_ids.append(task_id)
                    except (ValueError, TypeError):
                        pass

                # 间隔任务
                elif task.interval_minutes > 0:
                    if not task.next_run:
                        nr = task.compute_next_run()
                        if nr:
                            task.next_run = nr.strftime("%Y-%m-%d %H:%M:%S")
                    try:
                        next_time = datetime.strptime(task.next_run, "%Y-%m-%d %H:%M:%S")
                        if now >= next_time:
                            tasks_to_run.append(task)
                            # 计算下次运行
                            nr = task.compute_next_run()
                            task.next_run = nr.strftime("%Y-%m-%d %H:%M:%S") if nr else ""
                    except (ValueError, TypeError):
                        pass

                # cron 任务
                elif task.cron:
                    # 每分钟检查一次
                    if now.second == 0:
                        if cron_matches(task.cron, now):
                            tasks_to_run.append(task)
                            nr = task._find_next_cron_time(now)
                            task.next_run = nr.strftime("%Y-%m-%d %H:%M:%S") if nr else ""

            # 删除已执行的一次性任务
            for tid in expired_ids:
                if tid in self._tasks:
                    del self._tasks[tid]

            if tasks_to_run or expired_ids:
                self._save_tasks()

        # 释放锁后执行任务
        for task in tasks_to_run:
            self._execute_task(task)

    def _execute_task(self, task: SchedulerTask, force: bool = False):
        """执行单个任务。"""
        logger.info(f"执行任务: {task.name} - {task.prompt}")
        self._notify_status(f"执行定时任务: {task.name}")

        # 更新运行信息
        with self._lock:
            if task.task_id in self._tasks:
                t = self._tasks[task.task_id]
                t.last_run = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                t.run_count += 1
                self._save_tasks()

        # 调用回调
        if self._on_task_execute:
            try:
                self._on_task_execute(task.prompt)
            except Exception as e:
                logger.error(f"任务执行回调异常: {e}")

    # ---------- 持久化 ----------

    def _save_tasks(self):
        """保存任务到 JSON 文件。"""
        try:
            os.makedirs(os.path.dirname(self._save_path) or ".", exist_ok=True)
            data = [t.to_dict() for t in self._tasks.values()]
            with open(self._save_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存任务失败: {e}")

    def _load_tasks(self):
        """从 JSON 文件加载任务。"""
        try:
            if not os.path.exists(self._save_path):
                return
            with open(self._save_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                task = SchedulerTask.from_dict(item)
                # 重置 next_run
                nr = task.compute_next_run()
                if nr:
                    task.next_run = nr.strftime("%Y-%m-%d %H:%M:%S")
                self._tasks[task.task_id] = task
            logger.info(f"已加载 {len(self._tasks)} 个定时任务")
        except Exception as e:
            logger.error(f"加载任务失败: {e}")

    def _notify_status(self, msg: str):
        if self._on_status_change:
            try:
                self._on_status_change(msg)
            except Exception:
                pass

    def cleanup(self):
        """清理资源。"""
        self.stop()
        self._save_tasks()
        logger.info("调度器已清理")
