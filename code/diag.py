"""
统一诊断日志
============
提供集中式日志、异常追踪和全局异常钩子。

主要 API:
    - log(msg, *args)            统一日志输出（带时间戳 + 进程/线程）
    - log_exc(msg)               输出消息 + 当前异常 traceback
    - section(name)              输出醒目的分节标题
    - install_global_hook()      安装 sys.excepthook / threading.excepthook
"""

import os
import sys
import time
import threading
import traceback
from datetime import datetime

# ─── 进程级锁，避免多线程输出交错 ───────────────────────────────────────────
_log_lock = threading.RLock()

# ─── 日志文件路径（可选）──────────────────────────────────────────────────
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_log_file_path: str | None = None
_log_to_file_enabled: bool = False


def _ensure_log_file() -> str | None:
    """
    懒初始化日志文件路径。

    第一次写日志时按日期创建 logs/YYYY-MM-DD.log。
    若当前目录不可写则降级为不写文件。
    """
    global _log_file_path
    if _log_file_path is not None:
        return _log_file_path
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        fname = datetime.now().strftime("%Y-%m-%d") + ".log"
        _log_file_path = os.path.join(_LOG_DIR, fname)
        return _log_file_path
    except Exception:
        return None


def _format(msg: str) -> str:
    """统一格式：时间戳 | 进程/线程 | 消息"""
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    pid = os.getpid()
    tid = threading.get_ident()
    return f"[{ts}][{pid}/T{tid}] {msg}"


# ══════════════════════════════════════════════════════════════════════════════
#  公共 API
# ══════════════════════════════════════════════════════════════════════════════

def log(msg: str, *args) -> None:
    """
    输出一条日志（线程安全）。

    Args:
        msg:   消息文本
        *args: printf 风格占位符
    """
    if args:
        try:
            msg = msg % args
        except Exception:
            pass
    line = _format(msg)
    with _log_lock:
        # 1. 标准输出
        try:
            print(line, file=sys.stdout, flush=True)
        except Exception:
            pass
        # 2. 写日志文件（可选）
        if _log_to_file_enabled:
            path = _ensure_log_file()
            if path:
                try:
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
                except Exception:
                    pass


def debug(msg: str, *args) -> None:
    """Opt-in verbose output; errors and lifecycle messages remain visible."""
    if os.environ.get('EEG_VR_DEBUG', '').lower() in {'1', 'true', 'yes'}:
        log(msg, *args)


def log_exc(msg: str = "") -> None:
    """输出消息 + 当前异常 traceback（如果在 except 块中调用）"""
    if msg:
        log(msg)
    tb = traceback.format_exc()
    if tb and tb.strip() != "NoneType: None":
        for line in tb.rstrip().splitlines():
            log(line)


def section(name: str) -> None:
    """输出醒目的分节标题"""
    bar = "═" * 60
    log(f"{bar}\n▶ {name}\n{bar}")


# ══════════════════════════════════════════════════════════════════════════════
#  全局异常钩子
# ══════════════════════════════════════════════════════════════════════════════

def _excepthook(exc_type, exc_value, exc_tb):
    """主线程未捕获异常 → 写日志 + 不弹窗（避免生产环境被打断）"""
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log("⚠ 未捕获异常 (主线程):")
    for line in msg.rstrip().splitlines():
        log(line)
    # 也走默认钩子，确保 Qt 等子框架能看到
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def _thread_excepthook(args):
    """子线程未捕获异常 → 写日志"""
    log(f"⚠ 未捕获异常 (子线程: {args.thread.name}):")
    for line in traceback.format_exception(
        args.exc_type, args.exc_value, args.exc_traceback
    ):
        for ln in line.rstrip().splitlines():
            log(ln)


def install_global_hook(enable_file_log: bool = False) -> None:
    """
    安装全局异常钩子。

    Args:
        enable_file_log: 是否同时写文件到 logs/YYYY-MM-DD.log
    """
    global _log_to_file_enabled
    _log_to_file_enabled = enable_file_log

    # 主线程
    sys.excepthook = _excepthook

    # 子线程（Python 3.8+）
    if hasattr(threading, "excepthook"):
        try:
            threading.excepthook = _thread_excepthook
        except Exception:
            pass

    log("全局异常钩子已安装")
