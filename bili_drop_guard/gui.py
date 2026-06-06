from __future__ import annotations

import queue
import re
import sys
import threading
import traceback
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

from . import __version__
from .config import APP_DIR, MAX_CHECK_INTERVAL, MAX_WATCH_THREADS, MIN_CHECK_INTERVAL, AccountProfile, AppConfig, load_config, sanitize_config, save_config
from .cookie_capture import capture_bilibili_cookie, open_bilibili_login_page
from .notifier import send_notification
from .watcher import LiveWatcher, WatchOptions, WatchWorkerStatus
from .multi_account import MultiAccountWatcher, build_account_options


SOURCE_URL = "https://github.com/taocihei/overwatch-bilibili-drops-guard"
APP_BG = "#f5f7fb"
SURFACE = "#ffffff"
SOFT_SURFACE = "#f8fafc"
BORDER = "#e5e7eb"
TEXT = "#111827"
MUTED = "#64748b"
ACCENT = "#2563eb"
ACCENT_ACTIVE = "#1d4ed8"
SUCCESS = "#16a34a"
SUCCESS_ACTIVE = "#15803d"
DANGER = "#dc2626"
DANGER_BG = "#fff1f2"


def _resource_path(relative_path: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / relative_path


class RoundedPanel(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        fill: str = SURFACE,
        background: str = APP_BG,
        radius: int = 18,
        padding: tuple[int, int] = (20, 16),
        min_height: int = 0,
        outline: str = BORDER,
        shadow: bool = True,
        auto_height: bool = True,
    ) -> None:
        super().__init__(parent, bg=background, highlightthickness=0, borderwidth=0)
        self.fill = fill
        self.outline = outline
        self.shadow = shadow
        self.radius = radius
        self.pad_x, self.pad_y = padding
        self.min_height = min_height
        self.auto_height = auto_height
        # Canvas 自带一个很大的默认高度；未指定 min_height 的自适应卡片如果沿用它，
        # 会把说明区撑出大片空白，并挤压下面的主要功能区。
        self.configure(height=max(1, min_height))
        self.inner = tk.Frame(self, bg=fill, highlightthickness=0, borderwidth=0)
        self._window = self.create_window(self.pad_x, self.pad_y, anchor="nw", window=self.inner)
        self.bind("<Configure>", self._redraw)
        self.inner.bind("<Configure>", self._sync_height)
        if self.auto_height:
            self.after_idle(self._sync_height)

    def _sync_height(self, _event: tk.Event | None = None) -> None:
        if not self.auto_height:
            return
        requested = max(self.inner.winfo_reqheight(), self._children_reqheight()) + self.pad_y * 2
        if requested > 1:
            self.configure(height=max(self.min_height, requested))

    def _children_reqheight(self) -> int:
        children = [child for child in self.inner.winfo_children() if child.winfo_manager()]
        if not children:
            return 0
        for child in children:
            if isinstance(child, RoundedPanel):
                child._sync_height()

        managers = {child.winfo_manager() for child in children}
        if "grid" in managers:
            row_heights: dict[int, int] = {}
            for child in children:
                if child.winfo_manager() != "grid":
                    continue
                info = child.grid_info()
                try:
                    row = int(info.get("row", 0))
                except (TypeError, ValueError):
                    row = 0
                row_heights[row] = max(row_heights.get(row, 0), child.winfo_reqheight())
            return sum(row_heights.values())
        if "pack" in managers:
            return sum(child.winfo_reqheight() for child in children if child.winfo_manager() == "pack")
        return max(child.winfo_reqheight() for child in children)

    def _redraw(self, _event: tk.Event | None = None) -> None:
        self.delete("panel")
        self.delete("shadow")
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        if self.shadow and width > 8 and height > 8:
            self._rounded_rect(2, 4, width - 2, height - 1, self.radius, fill="#e8edf5", outline="", tags="shadow")
        self._rounded_rect(1, 1, width - 3, height - 4, self.radius, fill=self.fill, outline=self.outline, tags="panel")
        self.tag_lower("shadow")
        self.tag_lower("panel")
        self.coords(self._window, self.pad_x, self.pad_y)
        window_options: dict[str, int] = {"width": max(1, width - self.pad_x * 2)}
        if not self.auto_height:
            window_options["height"] = max(1, height - self.pad_y * 2)
        self.itemconfigure(self._window, **window_options)
        if self.auto_height:
            self.after_idle(self._sync_height)

    def _rounded_rect(self, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs: object) -> None:
        points = [
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1,
        ]
        outline = str(kwargs.pop("outline", ""))
        self.create_polygon(points, smooth=True, splinesteps=18, outline=outline, **kwargs)


class PillButton(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        fill: str = ACCENT,
        foreground: str = "#ffffff",
        active_fill: str | None = None,
        height: int = 40,
        width: int | None = None,
        font: tuple[str, int, str] = ("Microsoft YaHei UI", 10, "bold"),
    ) -> None:
        try:
            parent_bg = str(parent.cget("bg"))
        except tk.TclError:
            parent_bg = SURFACE
        super().__init__(parent, height=height, bg=parent_bg, highlightthickness=0, borderwidth=0, cursor="hand2")
        if width is not None:
            self.configure(width=width)
        self.text = text
        self.command = command
        self.fill = fill
        self.foreground = foreground
        self.active_fill = active_fill or fill
        self.normal_fill = fill
        self.font = font
        self._hovered = False
        self.bind("<Configure>", lambda _event: self._redraw())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", lambda _event: self.command())

    def set_appearance(self, *, text: str, fill: str, foreground: str = "#ffffff", active_fill: str | None = None) -> None:
        self.text = text
        self.fill = fill
        self.normal_fill = fill
        self.foreground = foreground
        self.active_fill = active_fill or fill
        self._redraw()

    def _enter(self, _event: tk.Event) -> None:
        self._hovered = True
        self.fill = self.active_fill
        self._redraw()

    def _leave(self, _event: tk.Event) -> None:
        self._hovered = False
        self.fill = self.normal_fill
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        radius = height // 2
        self.create_polygon(
            radius, 1, width - radius, 1, width, 1, width, radius,
            width, height - radius, width, height - 1, width - radius, height - 1,
            radius, height - 1, 1, height - 1, 1, height - radius, 1, radius, 1, 1,
            smooth=True,
            splinesteps=18,
            fill=self.fill,
            outline="",
        )
        self.create_text(width // 2, height // 2, text=self.text, fill=self.foreground, font=self.font)


class NumberInput(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        variable: tk.IntVar,
        *,
        minimum: int,
        maximum: int,
        background: str = SURFACE,
    ) -> None:
        super().__init__(parent, height=38, bg=background, highlightthickness=0, borderwidth=0)
        self.variable = variable
        self.minimum = minimum
        self.maximum = maximum
        self.pack_propagate(False)

        panel = RoundedPanel(self, fill=SOFT_SURFACE, background=background, radius=18, padding=(4, 4), min_height=38, outline=BORDER, shadow=False, auto_height=False)
        panel.pack(fill="both", expand=True)
        panel.inner.columnconfigure(1, weight=1)

        PillButton(panel.inner, "−", lambda: self._set_value(self._current_value() - 1), fill=SOFT_SURFACE, foreground=MUTED, active_fill="#eef2ff", height=28, width=34, font=("Microsoft YaHei UI", 13, "bold")).grid(row=0, column=0, sticky="nsw")
        self.entry = tk.Entry(
            panel.inner,
            textvariable=self.variable,
            justify="center",
            borderwidth=0,
            relief="flat",
            bg=SOFT_SURFACE,
            fg=TEXT,
            insertbackground=TEXT,
            font=("Microsoft YaHei UI", 10),
        )
        self.entry.grid(row=0, column=1, sticky="nsew", padx=4)
        PillButton(panel.inner, "+", lambda: self._set_value(self._current_value() + 1), fill=SOFT_SURFACE, foreground=MUTED, active_fill="#eef2ff", height=28, width=34, font=("Microsoft YaHei UI", 13, "bold")).grid(row=0, column=2, sticky="nse")

        self.entry.bind("<FocusOut>", lambda _event: self._commit())
        self.entry.bind("<Return>", lambda _event: self._commit())

    def _current_value(self) -> int:
        try:
            return int(self.variable.get())
        except (tk.TclError, ValueError):
            return self.minimum

    def _set_value(self, value: int) -> None:
        self.variable.set(min(max(int(value), self.minimum), self.maximum))

    def _commit(self) -> None:
        self._set_value(self._current_value())


class WatchStatusCard(tk.Frame):
    """右栏后台计时状态卡。原地只显示汇总，每路明细通过弹窗查看，避免挤压
    其他卡片。"""

    STATE_COLORS = {
        "正常": SUCCESS,
        "计时中": "#f59e0b",
        "启动中": "#f59e0b",
        "等待开播": MUTED,
        "暂时失败": DANGER,
    }
    STATE_TAGS = {
        "正常": "normal",
        "计时中": "warning",
        "启动中": "warning",
        "等待开播": "muted",
        "暂时失败": "danger",
    }

    def __init__(self, parent: tk.Misc, *, background: str = APP_BG) -> None:
        super().__init__(parent, bg=background, highlightthickness=0, borderwidth=0)
        self.summary_var = tk.StringVar(value="后台计时状态：未启动")
        self._snapshot: list[WatchWorkerStatus] = []
        self._detail_window: tk.Toplevel | None = None
        self._detail_text: tk.Text | None = None

        self._panel = RoundedPanel(self, fill=SURFACE, background=background, radius=18, padding=(16, 12), outline=BORDER)
        self._panel.pack(fill="both", expand=True)
        inner = self._panel.inner
        inner.columnconfigure(0, weight=1)

        ttk.Label(inner, text="后台计时状态", style="SectionTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(inner, textvariable=self.summary_var, style="Muted.TLabel", wraplength=320).grid(row=1, column=0, sticky="ew", pady=(4, 8))
        self._detail_button = PillButton(
            inner,
            "查看每路状态",
            self.show_detail_window,
            fill=SOFT_SURFACE,
            foreground=TEXT,
            active_fill="#eef2ff",
            height=28,
        )
        self._detail_button.grid(row=2, column=0, sticky="ew")

    def is_expanded(self) -> bool:
        # 兼容旧测试：弹窗存在视为展开
        return self._detail_window is not None and self._detail_window.winfo_exists()

    def toggle(self) -> None:
        # 兼容旧测试和老代码：toggle 等价于打开/关闭弹窗
        if self.is_expanded():
            try:
                self._detail_window.destroy()
            except Exception:
                pass
            self._detail_window = None
            self._detail_text = None
        else:
            self.show_detail_window()

    def show_detail_window(self) -> tk.Toplevel:
        if self._detail_window is not None and self._detail_window.winfo_exists():
            self._detail_window.lift()
            self._detail_window.focus_set()
            return self._detail_window

        top = tk.Toplevel(self)
        top.title("后台计时每路状态")
        top.geometry("520x520")
        top.configure(bg=APP_BG)
        try:
            top.transient(self.winfo_toplevel())
        except tk.TclError:
            pass

        container = tk.Frame(top, bg=APP_BG, padx=18, pady=14)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        ttk.Label(container, textvariable=self.summary_var, style="Muted.TLabel", wraplength=460).grid(row=0, column=0, sticky="ew", pady=(0, 10))

        text_wrap = tk.Frame(container, bg=APP_BG)
        text_wrap.grid(row=1, column=0, sticky="nsew")
        text_wrap.columnconfigure(0, weight=1)
        text_wrap.rowconfigure(0, weight=1)
        text = tk.Text(
            text_wrap,
            wrap="none",
            state="disabled",
            borderwidth=0,
            relief="flat",
            bg=SOFT_SURFACE,
            fg=TEXT,
            insertbackground=TEXT,
            highlightthickness=0,
            padx=12,
            pady=10,
            font=("Consolas", 10),
        )
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(text_wrap, orient="vertical", command=text.yview, style="Vertical.TScrollbar")
        scrollbar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scrollbar.set)
        text.tag_configure("normal", foreground=SUCCESS)
        text.tag_configure("warning", foreground="#f59e0b")
        text.tag_configure("muted", foreground=MUTED)
        text.tag_configure("danger", foreground=DANGER)

        PillButton(container, "关闭", top.destroy, fill=ACCENT, active_fill=ACCENT_ACTIVE, height=32, width=100).grid(row=2, column=0, sticky="e", pady=(10, 0))

        self._detail_window = top
        self._detail_text = text
        top.protocol("WM_DELETE_WINDOW", self._on_detail_closed)
        self._render_detail()
        return top

    def _on_detail_closed(self) -> None:
        if self._detail_window is not None:
            try:
                self._detail_window.destroy()
            except Exception:
                pass
        self._detail_window = None
        self._detail_text = None

    def update_snapshot(self, snapshot: list[WatchWorkerStatus], summary: str) -> None:
        self._snapshot = list(snapshot)
        self.summary_var.set(summary)
        if self._detail_window is not None and self._detail_window.winfo_exists():
            self._render_detail()

    def _render_detail(self) -> None:
        if self._detail_text is None:
            return
        self._detail_text.configure(state="normal")
        self._detail_text.delete("1.0", "end")
        width = 3 if len(self._snapshot) >= 100 else 2
        self._rendered_rows: list[dict[str, str]] = []
        for status in self._snapshot:
            label = f"#{status.worker_id:0{width}d}"
            detail = self._format_detail(status)
            tag = self.STATE_TAGS.get(status.state, "muted")
            line = f"{label}  ● {status.state:<5} {detail}\n"
            self._detail_text.insert("end", line, tag)
            self._rendered_rows.append({"label": label, "state": status.state, "detail": detail, "tag": tag})
        if not self._snapshot:
            self._detail_text.insert("end", "暂无后台计时数据。开始挂宝后这里会显示每路状态。\n", "muted")
        self._detail_text.configure(state="disabled")

    def _format_detail(self, status: WatchWorkerStatus) -> str:
        if status.state == "正常" and status.interval:
            return f"下一次 {status.interval}s"
        if status.message:
            return status.message
        return ""

    def rendered_rows_for_test(self) -> list[dict[str, str]]:
        return list(getattr(self, "_rendered_rows", []))


def build_onboarding_guide(parent: tk.Misc) -> tk.Toplevel:
    top = tk.Toplevel(parent)
    top.title("上手指引")
    top.geometry("560x620")
    top.configure(bg=APP_BG)
    try:
        top.transient(parent)
        top.grab_set()
    except tk.TclError:
        pass

    container = tk.Frame(top, bg=APP_BG, padx=24, pady=20)
    container.pack(fill="both", expand=True)
    tk.Label(container, text="上手指引", bg=APP_BG, fg=TEXT, font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
    tk.Label(container, text="跟着 4 步走就能挂宝。", bg=APP_BG, fg=MUTED, font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(2, 14))

    PillButton(container, "我知道了", top.destroy, fill=ACCENT, active_fill=ACCENT_ACTIVE, height=36, width=120).pack(side="bottom", pady=(14, 0))

    steps_frame = tk.Frame(container, bg=APP_BG, highlightthickness=0, borderwidth=0)
    steps_frame.pack(fill="both", expand=True)

    steps = (
        ("01", "获取 Cookie", "点“自动获取 Cookie”，在弹出的 Edge/Chrome 里登录 B 站即可。"),
        ("02", "确认直播间", "默认 23612045 即可。要换直播间就粘贴链接，会自动保存成房间号。"),
        ("03", "开始计时", "点“开始挂宝”。后台计时不会弹直播窗口。可调整“后台观看线程数”加速累计时长。"),
        ("04", "领取奖励", "“自动领奖”开启时会按顺序领；也可手动点“领取奖励”。看右侧“任务进度”确认状态。"),
    )
    for number, title, detail in steps:
        row = RoundedPanel(steps_frame, fill=SURFACE, background=APP_BG, radius=14, padding=(14, 10), outline=BORDER, shadow=False)
        row.pack(fill="x", pady=(0, 8))
        inner = row.inner
        inner.columnconfigure(1, weight=1)
        tk.Label(inner, text=number, bg=SURFACE, fg=ACCENT, font=("Microsoft YaHei UI", 14, "bold"), width=4).grid(row=0, column=0, rowspan=2, sticky="nw")
        tk.Label(inner, text=title, bg=SURFACE, fg=TEXT, font=("Microsoft YaHei UI", 11, "bold")).grid(row=0, column=1, sticky="w")
        tk.Label(inner, text=detail, bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9), wraplength=440, justify="left").grid(row=1, column=1, sticky="w", pady=(2, 0))

    return top


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"守望先锋 B 站直播挂宝 v{__version__}")
        self.geometry("1180x860")
        self.minsize(1080, 820)
        self.configure(bg=APP_BG)
        self._set_window_icon()

        self.config_data = load_config()
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.watcher: LiveWatcher | MultiAccountWatcher | None = None
        self.cookie_capture_thread: threading.Thread | None = None
        self.progress_events: list[str] = []
        self.progress_snapshot = ""
        self.notification_history: dict[str, float] = {}
        self.notification_failure_history: dict[str, float] = {}
        self.notification_pending: set[str] = set()

        self.cookie_var = tk.StringVar(value=self.config_data.cookie)
        self.selected_account_var = tk.StringVar(value=self.config_data.account_name)
        self.account_name_var = tk.StringVar(value=self.config_data.account_name)
        self.account_checks: dict[str, tk.BooleanVar] = {}
        self.notify_url_var = tk.StringVar(value=self.config_data.notify_url)
        self.room_var = tk.StringVar(value=self.config_data.room_id)
        self.interval_var = tk.IntVar(value=self.config_data.check_interval)
        self.auto_claim_var = tk.BooleanVar(value=self.config_data.auto_claim)
        self.watch_threads_var = tk.IntVar(value=self.config_data.watch_threads)
        self.status_var = tk.StringVar(value="未运行")
        self.version_var = tk.StringVar(value=f"v{__version__}")
        self.status_label: ttk.Label | None = None

        self._configure_style()
        self._build_ui()
        self.after(1000, self._poll_watch_status)
        self.after(100, self._clear_initial_focus)
        self.after(200, self._drain_logs)

    def _set_window_icon(self) -> None:
        icon_path = _resource_path("assets/app.ico")
        if icon_path.exists():
            try:
                self.iconbitmap(default=str(icon_path))
            except tk.TclError:
                pass

    def report_callback_exception(self, exc: type[BaseException], value: BaseException, tb: object) -> None:
        detail = "".join(traceback.format_exception(exc, value, tb))
        APP_DIR.mkdir(parents=True, exist_ok=True)
        log_path = APP_DIR / "crash.log"
        log_path.write_text(detail, encoding="utf-8")
        self._log(f"界面操作异常，详情已写入：{log_path}")
        messagebox.showerror("程序异常", f"发生异常，详情已写入：\n{log_path}\n\n{value}")

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        font = ("Microsoft YaHei UI", 10)
        style.configure(".", font=font, background=APP_BG, foreground=TEXT)
        style.configure("App.TFrame", background=APP_BG)
        style.configure("Surface.TFrame", background=SURFACE, borderwidth=0, relief="flat")
        style.configure("Rail.TFrame", background=SURFACE, borderwidth=0, relief="flat")
        style.configure("StepItem.TFrame", background=SOFT_SURFACE, borderwidth=0, relief="flat")
        style.configure("Header.TFrame", background=APP_BG)
        style.configure("ActionBar.TFrame", background=SURFACE, borderwidth=0, relief="flat")
        style.configure("Step.TFrame", background=SOFT_SURFACE)
        style.configure("Body.TLabel", background=SURFACE, foreground=TEXT)
        style.configure("Muted.TLabel", background=SURFACE, foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("RailMuted.TLabel", background=SURFACE, foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("RailTitle.TLabel", background=SURFACE, foreground=TEXT, font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("StepItemTitle.TLabel", background=SOFT_SURFACE, foreground=TEXT, font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("StepItemText.TLabel", background=SOFT_SURFACE, foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("PageTitle.TLabel", background=APP_BG, foreground=TEXT, font=("Microsoft YaHei UI", 22, "bold"))
        style.configure("PageSubtitle.TLabel", background=APP_BG, foreground=MUTED, font=("Microsoft YaHei UI", 10))
        style.configure("FreeNoticeTitle.TLabel", background=SURFACE, foreground="#b45309", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("FreeNoticeBody.TLabel", background=SURFACE, foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("FreeNoticeLink.TLabel", background=SURFACE, foreground=ACCENT, font=("Microsoft YaHei UI", 9, "underline"))
        style.configure("Eyebrow.TLabel", background=APP_BG, foreground=ACCENT, font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("SectionTitle.TLabel", background=SURFACE, foreground=TEXT, font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("StepTitle.TLabel", background=SOFT_SURFACE, foreground=ACCENT, font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("StepText.TLabel", background=SOFT_SURFACE, foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("Status.TLabel", background="#eef2f7", foreground=MUTED, padding=(14, 8), font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("StatusRunning.TLabel", background="#ecfdf5", foreground=SUCCESS, padding=(14, 8), font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Version.TLabel", background="#e0ecff", foreground=ACCENT, padding=(10, 5), font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TEntry", padding=(10, 8), fieldbackground=SOFT_SURFACE, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.configure("TSpinbox", padding=(10, 8), fieldbackground=SOFT_SURFACE, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.configure("TCombobox", padding=(8, 7), fieldbackground=SOFT_SURFACE, background=SOFT_SURFACE, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, arrowcolor=MUTED)
        style.map("TCombobox", fieldbackground=[("readonly", SOFT_SURFACE)], selectbackground=[("readonly", SOFT_SURFACE)], selectforeground=[("readonly", TEXT)])
        style.configure("TCheckbutton", background=SURFACE, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", SURFACE)])
        style.configure("Vertical.TScrollbar", background="#cbd5e1", troughcolor=SURFACE, bordercolor=SURFACE, arrowcolor=MUTED)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=(28, 16, 28, 10), style="Header.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        meta = ttk.Frame(header, style="Header.TFrame")
        meta.grid(row=0, column=0, sticky="w")
        ttk.Label(meta, text="本地直播掉宝助手", style="Eyebrow.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(meta, textvariable=self.version_var, style="Version.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Label(header, text="守望先锋 B 站直播挂宝", style="PageTitle.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Label(
            header,
            text="登录、观看计时、任务检查和领奖都在这里完成。默认直播间已填好，打开后按步骤走就行。",
            style="PageSubtitle.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.status_label = ttk.Label(header, textvariable=self.status_var, style="Status.TLabel")
        self.status_label.grid(row=1, column=1, rowspan=2, sticky="e")

        body = ttk.Frame(self, padding=(28, 0, 28, 18), style="App.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=7, uniform="main")
        body.columnconfigure(1, weight=4, uniform="main")
        body.rowconfigure(2, weight=1)

        free_panel = RoundedPanel(body, fill=SURFACE, background=APP_BG, radius=16, padding=(14, 8), outline="#fde68a", shadow=False)
        free_panel.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        free = free_panel.inner
        free.columnconfigure(0, weight=1)
        free.columnconfigure(1, weight=0)
        free.columnconfigure(2, weight=0)
        ttk.Label(
            free,
            text="本软件完全免费，购买请找商家退款；赞助只是点赞，不会解锁任何功能。",
            style="FreeNoticeTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        free.columnconfigure(3, weight=0)
        onboarding_label = ttk.Label(free, text="看上手指引", style="FreeNoticeLink.TLabel", cursor="hand2")
        onboarding_label.grid(row=0, column=1, sticky="e", padx=(14, 0))
        onboarding_label.bind("<Button-1>", lambda _event: self._show_onboarding_guide())
        source_label = ttk.Label(
            free,
            text="打开开源地址",
            style="FreeNoticeLink.TLabel",
            cursor="hand2",
        )
        source_label.grid(row=0, column=2, sticky="e", padx=(14, 0))
        source_label.bind("<Button-1>", lambda _event: self._open_source_url())
        copy_label = ttk.Label(free, text="复制地址", style="FreeNoticeLink.TLabel", cursor="hand2")
        copy_label.grid(row=0, column=3, sticky="e", padx=(14, 0))
        copy_label.bind("<Button-1>", lambda _event: self._copy_source_url())

        guide_panel = RoundedPanel(body, fill=SURFACE, background=APP_BG, radius=18, padding=(14, 10), outline=BORDER)
        guide_panel.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        guide = guide_panel.inner
        guide.columnconfigure((0, 1, 2, 3), weight=1, uniform="guide")

        center = ttk.Frame(body, style="App.TFrame")
        center.grid(row=2, column=0, sticky="nsew", padx=(0, 16))
        center.columnconfigure((0, 1), weight=1, uniform="config")
        center.rowconfigure(1, weight=1)
        center.rowconfigure(2, weight=0)

        right = ttk.Frame(body, style="App.TFrame")
        right.grid(row=2, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        self._build_step_strip(guide)
        self._build_cookie_card(center)
        self._build_task_card(center)
        self._build_actions(center)
        self._build_log_card(right)

    def _build_step_strip(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="使用说明", style="RailTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            parent,
            text="第一次先获取 Cookie；直播间默认 23612045；任务 ID 通常会自动识别，失败时可点下方说明手动填写。",
            style="RailMuted.TLabel",
            wraplength=760,
        ).grid(row=0, column=1, columnspan=3, sticky="ew", padx=(18, 0))

        items = (
            ("01", "获取 Cookie", "登录后自动回填"),
            ("02", "确认直播", "链接会保存成房间号"),
            ("03", "开始计时", "后台计时不弹直播窗口"),
            ("04", "领取奖励", "完成后自动或手动领取"),
        )
        for index, (number, title, detail) in enumerate(items):
            cell_panel = RoundedPanel(parent, fill=SOFT_SURFACE, background=SURFACE, radius=14, padding=(10, 8), outline=BORDER, shadow=False)
            cell_panel.grid(row=1, column=index, sticky="nsew", pady=(10, 0), padx=(0 if index == 0 else 8, 0))
            cell = cell_panel.inner
            cell.columnconfigure(1, weight=1)
            ttk.Label(cell, text=number, style="StepTitle.TLabel").grid(row=0, column=0, sticky="nw", padx=(0, 10))
            ttk.Label(cell, text=title, style="StepItemTitle.TLabel").grid(row=0, column=1, sticky="w")
            ttk.Label(cell, text=detail, style="StepItemText.TLabel", wraplength=180).grid(row=0, column=2, sticky="w", padx=(10, 0))

    def _build_cookie_card(self, parent: ttk.Frame) -> None:
        card = self._card(
            parent,
            row=1,
            column=0,
            title="登录 Cookie",
            subtitle="自动打开独立 Edge/Chrome。登录成功后会回填 Cookie 并关闭窗口。",
            sticky="nsew",
            min_height=205,
            subtitle_wrap=310,
            padx=(0, 9),
        )
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)
        card.rowconfigure(7, weight=1)

        ttk.Label(card, text="并行账号（勾选要挂的）", style="Body.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Label(card, text="账号名称（点账号可编辑）", style="Body.TLabel").grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        self._account_check_frame = tk.Frame(card, bg=SURFACE)
        self._account_check_frame.grid(row=3, column=0, sticky="new", pady=(6, 8), padx=(0, 8))
        self._build_account_checklist()
        account_entry = tk.Entry(card, textvariable=self.account_name_var, borderwidth=0, relief="flat", bg=SOFT_SURFACE, fg=TEXT, insertbackground=TEXT, font=("Microsoft YaHei UI", 10))
        account_entry.grid(row=3, column=1, sticky="new", pady=(6, 8), padx=(8, 0))

        PillButton(card, "保存账号", self._save_account, fill=SOFT_SURFACE, foreground=TEXT, active_fill="#eef2ff", height=34).grid(row=4, column=0, sticky="ew", pady=(0, 10), padx=(0, 8))
        PillButton(card, "删除账号", self._delete_account, fill=DANGER_BG, foreground=DANGER, active_fill="#ffe4e6", height=34).grid(row=4, column=1, sticky="ew", pady=(0, 10), padx=(8, 0))

        PillButton(card, "自动获取 Cookie", self._capture_cookie, fill=ACCENT, active_fill=ACCENT_ACTIVE).grid(row=5, column=0, sticky="ew", pady=(0, 10), padx=(0, 8))
        PillButton(card, "只打开登录页", self._open_cookie_login_page, fill=SOFT_SURFACE, foreground=TEXT, active_fill="#eef2ff").grid(row=5, column=1, sticky="ew", pady=(0, 10), padx=(8, 0))

        ttk.Label(card, text="Cookie 内容", style="Body.TLabel").grid(row=6, column=0, columnspan=2, sticky="w")
        cookie_box = RoundedPanel(card, fill=SOFT_SURFACE, background=SURFACE, radius=14, padding=(4, 4), min_height=72, outline=BORDER, shadow=False)
        cookie_box.grid(row=7, column=0, columnspan=2, sticky="nsew", pady=(6, 0))
        cookie_box.inner.columnconfigure(0, weight=1)
        cookie_box.inner.rowconfigure(0, weight=1)
        self.cookie_text = tk.Text(
            cookie_box.inner,
            height=2,
            wrap="word",
            undo=True,
            borderwidth=0,
            relief="flat",
            bg=SOFT_SURFACE,
            fg=TEXT,
            insertbackground=TEXT,
            highlightthickness=0,
            padx=8,
            pady=6,
            font=("Consolas", 9),
        )
        self.cookie_text.grid(row=0, column=0, sticky="nsew")
        self.cookie_text.insert("1.0", self.cookie_var.get())

    def _build_task_card(self, parent: ttk.Frame) -> None:
        card = self._card(
            parent,
            row=1,
            column=1,
            title="直播与任务",
            subtitle="后台观看线程用于并行累计时长；领奖请求固定由一个线程提交。",
            sticky="nsew",
            subtitle_wrap=310,
            padx=(9, 0),
        )
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)

        ttk.Label(card, text="直播间号或链接", style="Body.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        room_box = RoundedPanel(card, fill=SOFT_SURFACE, background=SURFACE, radius=14, padding=(12, 7), min_height=48, outline=BORDER, shadow=False)
        room_box.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 10))
        room_box.inner.columnconfigure(0, weight=1)
        room_entry = tk.Entry(
            room_box.inner,
            textvariable=self.room_var,
            borderwidth=0,
            relief="flat",
            bg=SOFT_SURFACE,
            fg=TEXT,
            insertbackground=TEXT,
            font=("Microsoft YaHei UI", 10),
        )
        room_entry.grid(row=0, column=0, sticky="ew")

        ttk.Label(card, text="检查间隔（秒）", style="Body.TLabel").grid(row=4, column=0, sticky="w")
        ttk.Label(card, text=f"观看线程数 (最多 {MAX_WATCH_THREADS})", style="Body.TLabel").grid(row=4, column=1, sticky="w", padx=(14, 0))

        NumberInput(card, self.interval_var, minimum=MIN_CHECK_INTERVAL, maximum=MAX_CHECK_INTERVAL).grid(row=5, column=0, sticky="ew", pady=(6, 6))
        NumberInput(card, self.watch_threads_var, minimum=1, maximum=MAX_WATCH_THREADS).grid(row=5, column=1, sticky="ew", padx=(14, 0), pady=(6, 6))
        ttk.Label(card, text="自动领奖", style="Body.TLabel").grid(row=6, column=0, sticky="w", pady=(4, 0))
        self.auto_claim_button = PillButton(card, "已开启", self._toggle_auto_claim, fill=SUCCESS, active_fill=SUCCESS_ACTIVE)
        self.auto_claim_button.grid(row=6, column=1, sticky="ew", padx=(14, 0), pady=(2, 8))
        self._refresh_auto_claim_button()
        ttk.Label(card, text="任务 ID（可留空）", style="Body.TLabel").grid(row=7, column=0, sticky="w", pady=(8, 0))
        ttk.Label(card, text="通知 URL（可留空）", style="Body.TLabel").grid(row=7, column=1, sticky="w", padx=(14, 0), pady=(8, 0))

        task_ids_box = RoundedPanel(card, fill=SOFT_SURFACE, background=SURFACE, radius=14, padding=(4, 4), min_height=44, outline=BORDER, shadow=False)
        task_ids_box.grid(row=8, column=0, sticky="ew", pady=(6, 0))
        task_ids_box.inner.columnconfigure(0, weight=1)
        task_ids_box.inner.rowconfigure(0, weight=1)
        self.task_ids_text = tk.Text(
            task_ids_box.inner,
            height=1,
            wrap="word",
            undo=True,
            borderwidth=0,
            relief="flat",
            bg=SOFT_SURFACE,
            fg=TEXT,
            insertbackground=TEXT,
            highlightthickness=0,
            padx=8,
            pady=6,
            font=("Consolas", 9),
        )
        self.task_ids_text.grid(row=0, column=0, sticky="nsew")
        self.task_ids_text.insert("1.0", self.config_data.task_ids)

        notify_box = RoundedPanel(card, fill=SOFT_SURFACE, background=SURFACE, radius=14, padding=(12, 7), min_height=44, outline=BORDER, shadow=False)
        notify_box.grid(row=8, column=1, sticky="ew", padx=(14, 0), pady=(6, 0))
        notify_box.inner.columnconfigure(0, weight=1)
        notify_entry = tk.Entry(
            notify_box.inner,
            textvariable=self.notify_url_var,
            borderwidth=0,
            relief="flat",
            bg=SOFT_SURFACE,
            fg=TEXT,
            insertbackground=TEXT,
            font=("Microsoft YaHei UI", 10),
        )
        notify_entry.grid(row=0, column=0, sticky="ew")

        PillButton(
            card,
            "自动失败？查看房间号和任务 ID 手动获取方法",
            self._show_manual_task_help,
            fill=SOFT_SURFACE,
            foreground=TEXT,
            active_fill="#eef2ff",
            height=34,
        ).grid(row=9, column=0, columnspan=2, sticky="ew", pady=(10, 0))

    def _build_actions(self, parent: ttk.Frame) -> None:
        actions_panel = RoundedPanel(parent, fill=SURFACE, background=APP_BG, radius=18, padding=(16, 12))
        actions_panel.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        actions = actions_panel.inner
        actions.columnconfigure((0, 1, 2, 3), weight=1, uniform="actions")

        PillButton(actions, "保存配置", self._save, fill=SOFT_SURFACE, foreground=TEXT, active_fill="#eef2ff").grid(row=0, column=0, sticky="ew", padx=(0, 8))
        PillButton(actions, "开始挂宝", self._start, fill=ACCENT, active_fill=ACCENT_ACTIVE).grid(row=0, column=1, sticky="ew", padx=8)
        PillButton(actions, "领取奖励", self._claim, fill=SOFT_SURFACE, foreground=TEXT, active_fill="#eef2ff").grid(row=0, column=2, sticky="ew", padx=8)
        PillButton(actions, "停止", self._stop, fill=DANGER_BG, foreground=DANGER, active_fill="#ffe4e6").grid(row=0, column=3, sticky="ew", padx=(8, 0))

    def _build_log_card(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(0, weight=3)
        parent.rowconfigure(1, weight=0)
        parent.rowconfigure(2, weight=1)

        progress_card = self._card(parent, row=0, title="任务进度", subtitle="登录、房间、计时、剩余分钟和领取结果都在这里", sticky="nsew", min_height=280, subtitle_wrap=350, auto_height=False)
        progress_card.columnconfigure(0, weight=1)
        progress_card.rowconfigure(3, weight=1)

        progress_buttons = tk.Frame(progress_card, bg=SURFACE, highlightthickness=0, borderwidth=0)
        progress_buttons.grid(row=2, column=0, columnspan=2, sticky="e", pady=(10, 0))
        self.manual_refresh_button = PillButton(
            progress_buttons,
            "↻ 刷新",
            self._handle_manual_refresh,
            fill=SOFT_SURFACE,
            foreground=TEXT,
            active_fill="#eef2ff",
            height=28,
            width=80,
        )
        self.manual_refresh_button.pack(side="left", padx=(0, 6))
        self.rediscover_button = PillButton(
            progress_buttons,
            "↻ 重新识别任务",
            self._handle_rediscover_tasks,
            fill=SOFT_SURFACE,
            foreground=TEXT,
            active_fill="#eef2ff",
            height=28,
            width=130,
        )
        self.rediscover_button.pack(side="left")

        progress_wrap = RoundedPanel(progress_card, fill=SOFT_SURFACE, background=SURFACE, radius=14, padding=(4, 4), min_height=300, outline=BORDER, shadow=False, auto_height=False)
        progress_wrap.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        progress_wrap.inner.columnconfigure(0, weight=1)
        progress_wrap.inner.rowconfigure(0, weight=1)

        self.progress_text = tk.Text(
            progress_wrap.inner,
            height=12,
            wrap="word",
            state="disabled",
            borderwidth=0,
            relief="flat",
            bg=SOFT_SURFACE,
            fg=TEXT,
            insertbackground=TEXT,
            highlightthickness=0,
            padx=14,
            pady=12,
            font=("Microsoft YaHei UI", 10),
        )
        self.progress_text.grid(row=0, column=0, sticky="nsew")
        progress_scrollbar = ttk.Scrollbar(progress_wrap.inner, orient="vertical", command=self.progress_text.yview, style="Vertical.TScrollbar")
        progress_scrollbar.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        self.progress_text.configure(yscrollcommand=progress_scrollbar.set)
        self._progress_log("等待任务检查。开始挂宝后，这里会显示本次可挂任务、剩余分钟和领取状态。")

        self.watch_status_card = WatchStatusCard(parent, background=APP_BG)
        self.watch_status_card.grid(row=1, column=0, sticky="ew", pady=(0, 12))

        card = self._card(parent, row=2, title="运行日志", subtitle="辅助记录，主要结果看上面的任务进度。", sticky="nsew", min_height=200, subtitle_wrap=330)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)

        log_wrap = RoundedPanel(card, fill=SOFT_SURFACE, background=SURFACE, radius=14, padding=(4, 4), min_height=72, outline=BORDER, shadow=False)
        log_wrap.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        log_wrap.inner.columnconfigure(0, weight=1)
        log_wrap.inner.rowconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_wrap.inner,
            height=3,
            wrap="word",
            state="disabled",
            borderwidth=0,
            relief="flat",
            bg=SOFT_SURFACE,
            fg=TEXT,
            insertbackground=TEXT,
            highlightthickness=0,
            padx=14,
            pady=12,
            font=("Consolas", 10),
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scrollbar = ttk.Scrollbar(log_wrap.inner, orient="vertical", command=self.log_text.yview, style="Vertical.TScrollbar")
        log_scrollbar.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        self.log_text.configure(yscrollcommand=log_scrollbar.set)
        self._log("辅助日志已就绪。主要进度请看上方任务进度。")


    def _card(
        self,
        parent: ttk.Frame,
        row: int,
        title: str,
        subtitle: str,
        column: int = 0,
        columnspan: int = 1,
        sticky: str = "ew",
        min_height: int = 0,
        subtitle_wrap: int = 260,
        padx: tuple[int, int] = (0, 0),
        auto_height: bool | None = None,
    ) -> ttk.Frame:
        panel_auto_height = auto_height if auto_height is not None else sticky != "nsew"
        panel = RoundedPanel(parent, fill=SURFACE, background=APP_BG, radius=18, padding=(18, 14), min_height=min_height, auto_height=panel_auto_height)
        panel.grid(row=row, column=column, columnspan=columnspan, sticky=sticky, pady=(0, 12), padx=padx)
        card = panel.inner
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text=title, style="SectionTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(card, text=subtitle, style="Muted.TLabel", wraplength=subtitle_wrap).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        return card

    def _current_config(self) -> AppConfig:
        active_accounts = [name for name, var in self.account_checks.items() if var.get()]
        editing = self.account_name_var.get().strip()
        if editing and editing not in self.account_checks:
            # 新保存的账号默认参与并行
            active_accounts.append(editing)
        return sanitize_config(AppConfig(
            cookie=self.cookie_text.get("1.0", "end").strip(),
            account_name=self.account_name_var.get().strip() or "默认账号",
            accounts=self._accounts_with_current_cookie(),
            room_id=self.room_var.get().strip(),
            check_interval=self._safe_int_var(self.interval_var, 10),
            auto_claim=bool(self.auto_claim_var.get()),
            task_ids=self.task_ids_text.get("1.0", "end").strip(),
            watch_threads=self._safe_int_var(self.watch_threads_var, 1),
            notify_url=self.notify_url_var.get().strip(),
            active_accounts=active_accounts,
        ))

    def _clear_initial_focus(self) -> None:
        self.focus_force()

    def _toggle_auto_claim(self) -> None:
        self.auto_claim_var.set(not bool(self.auto_claim_var.get()))
        self._refresh_auto_claim_button()

    def _refresh_auto_claim_button(self) -> None:
        if bool(self.auto_claim_var.get()):
            self.auto_claim_button.set_appearance(text="自动领取已开", fill=SUCCESS, foreground="#ffffff", active_fill=SUCCESS_ACTIVE)
        else:
            self.auto_claim_button.set_appearance(text="自动领取已关", fill=SOFT_SURFACE, foreground=MUTED, active_fill="#eef2ff")

    def _safe_int_var(self, variable: tk.IntVar, default: int) -> int:
        try:
            return int(variable.get())
        except (tk.TclError, ValueError):
            return default

    def _open_source_url(self) -> None:
        webbrowser.open(SOURCE_URL)
        self._log("已打开开源地址")

    def _copy_source_url(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(SOURCE_URL)
        self._log("开源地址已复制")

    def _show_manual_task_help(self) -> None:
        messagebox.showinfo(
            "手动填写方法",
            "一般不用手动填写，程序会自动获取。只有任务进度一直无数据时再按下面处理。\n\n"
            "房间号怎么填：\n"
            "1. 打开 B 站直播间链接。\n"
            "2. 复制 live.bilibili.com/ 后面的数字。\n"
            "3. 例如 https://live.bilibili.com/23612045?... 只填 23612045。\n\n"
            "任务 ID 怎么找：\n"
            "1. 用浏览器登录 B 站，打开有掉宝任务的直播间或活动页。\n"
            "2. 按 F12 打开开发者工具，切到“网络/Network”。\n"
            "3. 刷新页面，搜索 totalv2 或 task。\n"
            "4. 找到 x/task/totalv2 请求后，复制参数 task_ids= 后面的内容。\n"
            "5. 多个任务 ID 用英文逗号分隔，粘贴到本软件“任务 ID”输入框。\n\n"
            "如果找不到 totalv2，也可以在页面源码或网络响应里搜索 taskId。任务 ID 通常像 6ERAcwloghvqrb00 这样的字符串。",
        )

    def _show_onboarding_guide(self) -> tk.Toplevel:
        return build_onboarding_guide(self)

    def _account_names(self) -> list[str]:
        names = [account.name for account in self.config_data.accounts if account.name]
        if self.config_data.account_name and self.config_data.account_name not in names:
            names.insert(0, self.config_data.account_name)
        return names or ["默认账号"]

    def _accounts_with_current_cookie(self) -> list[AccountProfile]:
        account_name = self.account_name_var.get().strip() or "默认账号"
        cookie = self.cookie_text.get("1.0", "end").strip()
        accounts: list[AccountProfile] = []
        replaced = False
        for account in self.config_data.accounts:
            if account.name == account_name:
                if cookie:
                    accounts.append(AccountProfile(name=account_name, cookie=cookie))
                else:
                    accounts.append(AccountProfile(name=account.name, cookie=account.cookie))
                replaced = True
            else:
                accounts.append(AccountProfile(name=account.name, cookie=account.cookie))
        if cookie and not replaced:
            accounts.insert(0, AccountProfile(name=account_name, cookie=cookie))
        return accounts

    def _build_account_checklist(self) -> None:
        frame = self._account_check_frame
        for child in frame.winfo_children():
            child.destroy()
        self.account_checks = {}
        active = set(self.config_data.active_accounts or [])
        for name in self._account_names():
            checked = (not active) or (name in active)
            var = tk.BooleanVar(value=checked)
            self.account_checks[name] = var
            tk.Checkbutton(
                frame,
                text=name,
                variable=var,
                command=lambda n=name: self._on_account_clicked(n),
                bg=SURFACE,
                fg=TEXT,
                selectcolor=SOFT_SURFACE,
                activebackground=SURFACE,
                activeforeground=TEXT,
                anchor="w",
                highlightthickness=0,
                bd=0,
                font=("Microsoft YaHei UI", 10),
            ).pack(fill="x", anchor="w")

    def _refresh_account_selector(self) -> None:
        if hasattr(self, "_account_check_frame"):
            self._build_account_checklist()

    def _saved_cookie_for(self, name: str) -> str:
        for account in self.config_data.accounts:
            if account.name == name:
                return account.cookie
        return ""

    def _on_account_clicked(self, name: str) -> None:
        # 勾选切换的同时，把该账号设为“当前编辑账号”并回填其 Cookie，便于编辑/删除。
        # 但若当前编辑框里有未保存的改动，则不覆盖，避免丢失刚粘贴/编辑的 Cookie。
        current = self.account_name_var.get().strip()
        editor = self.cookie_text.get("1.0", "end").strip()
        if name == current:
            return
        if current and editor and editor != self._saved_cookie_for(current):
            self._log(f"“{current}”的 Cookie 有未保存改动，已保留；如需切到“{name}”请先保存账号")
            return
        self.account_name_var.set(name)
        self.cookie_text.delete("1.0", "end")
        self.cookie_text.insert("1.0", self._saved_cookie_for(name))
        self._log(f"当前编辑账号：{name}")

    def _save_account(self) -> None:
        self._save()
        self._log(f"账号已保存：{self.config_data.account_name}")

    def _delete_account(self) -> None:
        name = self.account_name_var.get().strip()
        accounts = [account for account in self.config_data.accounts if account.name != name]
        if len(accounts) == len(self.config_data.accounts):
            self._log("没有可删除的账号")
            return
        next_account = accounts[0] if accounts else AccountProfile()
        surviving = {account.name for account in accounts}
        active_accounts = [n for n, var in self.account_checks.items() if var.get() and n in surviving]
        config = sanitize_config(AppConfig(
            cookie=next_account.cookie,
            account_name=next_account.name,
            accounts=accounts,
            room_id=self.room_var.get().strip(),
            check_interval=self._safe_int_var(self.interval_var, 10),
            auto_claim=bool(self.auto_claim_var.get()),
            task_ids=self.task_ids_text.get("1.0", "end").strip(),
            watch_threads=self._safe_int_var(self.watch_threads_var, 1),
            notify_url=self.notify_url_var.get().strip(),
            active_accounts=active_accounts,
        ))
        save_config(config)
        self.config_data = config
        self.selected_account_var.set(config.account_name)
        self.account_name_var.set(config.account_name)
        self.cookie_text.delete("1.0", "end")
        self.cookie_text.insert("1.0", config.cookie)
        self._refresh_account_selector()
        self._log(f"已删除账号：{name}")

    def _save(self) -> None:
        config = self._current_config()
        save_config(config)
        self.config_data = config
        self.room_var.set(config.room_id)
        self.selected_account_var.set(config.account_name)
        self.account_name_var.set(config.account_name)
        self.notify_url_var.set(config.notify_url)
        self._refresh_account_selector()
        self._log("配置已保存")

    def _start(self) -> None:
        requested_watch_threads = self._safe_int_var(self.watch_threads_var, 1)
        config = self._current_config()
        if not config.cookie:
            messagebox.showwarning("缺少 Cookie", "请先粘贴 B 站 Cookie。")
            return
        if not config.room_id:
            messagebox.showwarning("缺少直播间号", "请先填写直播间号或直播间链接。")
            return
        if self.account_checks and not any(var.get() for var in self.account_checks.values()):
            messagebox.showwarning("没有勾选账号", "请至少勾选一个要挂的账号（不勾选则不会挂机）。")
            return

        self._save()
        if requested_watch_threads != config.watch_threads:
            self._log(f"后台观看线程数已调整为 {config.watch_threads}，当前版本最多支持 {MAX_WATCH_THREADS} 路")
        if self.watcher and self.watcher.running:
            self._log("当前已经在运行")
            return

        account_options = build_account_options(config)
        if not account_options:
            messagebox.showwarning("没有可用账号", "请至少勾选一个已保存且含 Cookie 的账号。")
            return
        total_threads = len(account_options) * config.watch_threads
        if total_threads > 20:
            self._log(
                f"提示：当前共 {len(account_options)} 个账号 × 每账号 {config.watch_threads} 路 = {total_threads} 路，"
                f"单 IP 下路数过多可能触发 B 站风控，必要时减少账号或每账号路数"
            )
        if self.watcher:
            # 停掉上一个协调器（例如此前只点过“领取”而临时建的那个），避免线程泄漏
            self.watcher.stop()
        self.watcher = MultiAccountWatcher(account_options, self._thread_log)
        self.watcher.start()
        self._set_status("运行中")
        start_message = (
            f"已启动 {len(account_options)} 个账号并行：房间 {config.room_id}，"
            f"每账号 {config.watch_threads} 路，自动领奖={'开启' if config.auto_claim else '关闭'}"
        )
        self._notify_from_message(start_message)
        self._log(start_message)
        self._progress_log(start_message)

    def _stop(self) -> None:
        if self.watcher:
            self.watcher.stop()
            # 置空，避免之后“领取”复用已停止的协调器（其停止标志已置位会导致领取空转）
            self.watcher = None
        self._set_status("未运行")

    def _capture_cookie(self) -> None:
        if self.cookie_capture_thread and self.cookie_capture_thread.is_alive():
            self._log("自动获取 Cookie 正在运行中")
            return
        self._set_status("正在获取 Cookie")
        self._log("正在准备打开 Edge/Chrome 登录 B 站，请稍等")
        self.cookie_capture_thread = threading.Thread(target=self._capture_cookie_worker, daemon=True)
        self.cookie_capture_thread.start()

    def _open_cookie_login_page(self) -> None:
        try:
            browser_name = open_bilibili_login_page(self._log)
        except Exception as exc:
            messagebox.showerror("打开失败", f"无法打开 B 站登录页：{exc}")
            self._log(f"打开 B 站登录页失败：{exc}")
            return
        self._log(f"已打开 {browser_name}。如果自动获取失败，可以在此页面登录后再尝试自动获取。")

    def _capture_cookie_worker(self) -> None:
        try:
            result = capture_bilibili_cookie(log=self._thread_log)
        except Exception as exc:
            self.log_queue.put(f"__ERROR__:自动获取 Cookie 失败：{exc}")
            self.log_queue.put("__STATUS__:未运行")
            return
        self.log_queue.put(f"__COOKIE__:{result.cookie_header}")
        self.log_queue.put("__STATUS__:未运行")
        self._thread_log(f"{result.browser} Cookie 获取成功")

    def _claim(self) -> None:
        # 还没开始挂宝时，也允许单独点击领取：临时构造一个协调器，对勾选账号各领一次
        # （不会启动心跳 worker，因为没调用 .start()）。
        if not self.watcher:
            config = self._current_config()
            if not config.cookie:
                messagebox.showwarning("缺少 Cookie", "请先粘贴 B 站 Cookie 才能领取奖励。")
                return
            if not config.room_id:
                messagebox.showwarning("缺少直播间号", "请先填写直播间号才能领取奖励。")
                return
            if self.account_checks and not any(var.get() for var in self.account_checks.values()):
                messagebox.showwarning("没有勾选账号", "请至少勾选一个要领奖的账号。")
                return
            account_options = build_account_options(config)
            if not account_options:
                messagebox.showwarning("没有可用账号", "请至少勾选一个已保存且含 Cookie 的账号。")
                return
            self.watcher = MultiAccountWatcher(account_options, self._thread_log)
            self._log("尚未挂宝，临时对勾选账号各领取一次已完成奖励")
        self.watcher.claim_completed_tasks()

    def _thread_log(self, message: str) -> None:
        self.log_queue.put(message)

    def _parse_task_ids(self, value: str) -> list[str]:
        return [item for item in re.split(r"[\s,，;；]+", value.strip()) if item]

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)
        if self.status_label is not None:
            style = "StatusRunning.TLabel" if message == "运行中" else "Status.TLabel"
            self.status_label.configure(style=style)

    def _notify_from_message(self, message: str) -> None:
        if not self._is_notification_message(message):
            return
        url = self.notify_url_var.get().strip() or self.config_data.notify_url
        if not url:
            return
        key = self._notification_key(url, message)
        now = datetime.now().timestamp()
        if now - self.notification_history.get(key, 0) < 300:
            return
        if now - self.notification_failure_history.get(key, 0) < 30:
            return
        if key in self.notification_pending:
            return
        self.notification_pending.add(key)
        level = "error" if "失败" in message or "异常" in message or "失效" in message else "info"
        threading.Thread(target=self._send_notification_worker, args=(url, key, message, level), daemon=True).start()

    def _send_notification_worker(self, url: str, key: str, message: str, level: str) -> None:
        try:
            send_notification(url, "守望先锋 B 站直播挂宝", message, level)
            self.notification_history[key] = datetime.now().timestamp()
        except Exception as exc:
            self.notification_failure_history[key] = datetime.now().timestamp()
            self.log_queue.put(f"通知发送失败：{exc}")
        finally:
            self.notification_pending.discard(key)

    @staticmethod
    def _split_account_prefix(message: str) -> tuple[str, str]:
        # 多账号会把子账号日志加上「[账号名] 」前缀。分流/通知判定要忽略它，
        # 但展示时保留前缀，让用户看清是哪个账号。
        match = re.match(r"^\[[^\]]+\]\s*", message)
        if match:
            return message[:match.end()].strip(), message[match.end():]
        return "", message

    def _is_notification_message(self, message: str) -> bool:
        _prefix, message = self._split_account_prefix(message)
        return message.startswith((
            "Cookie 获取成功",
            "已启动：",
            "已启动 ",
            "检测到 ",
            "开始领取奖励",
            "已领取：",
            "领取失败：",
            "守护循环异常",
            "登录状态失效",
            "守护已停止",
        )) or "Cookie 获取成功" in message

    def _notification_key(self, url: str, message: str) -> str:
        return f"{url.strip()}|{self._notification_account_name()}|{message.strip()}"

    def _notification_account_name(self) -> str:
        if hasattr(self, "account_name_var"):
            try:
                name = self.account_name_var.get().strip()
                if name:
                    return name
            except Exception:
                pass
        return getattr(self.config_data, "account_name", "默认账号") or "默认账号"

    def _progress_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.progress_events.append(f"[{timestamp}] {message}")
        self.progress_events = self.progress_events[-6:]
        self._render_progress_text()

    def _handle_manual_refresh(self) -> None:
        if not self.watcher or not self.watcher.running:
            self._log("请先开始挂宝，再手动刷新进度")
            return
        self.watcher.refresh_progress_once()

    def _handle_rediscover_tasks(self) -> None:
        if not self.watcher or not self.watcher.running:
            self._log("请先开始挂宝，再重新识别任务")
            return
        self.watcher.rediscover_tasks_once()

    def _progress_snapshot_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.progress_snapshot = f"[{timestamp}]\n{message}"
        self._render_progress_text()

    def _render_progress_text(self) -> None:
        parts = [*self.progress_events]
        if self.progress_snapshot:
            parts.append("")
            parts.append(self.progress_snapshot)
        self.progress_text.configure(state="normal")
        self.progress_text.delete("1.0", "end")
        self.progress_text.insert("1.0", "\n".join(parts).strip() + "\n")
        self.progress_text.see("end")
        self.progress_text.configure(state="disabled")

    def _is_progress_message(self, message: str) -> bool:
        _prefix, message = self._split_account_prefix(message)
        return message.startswith((
            "掉宝任务：",
            "账号登录正常",
            "房间 ",
            "已启动 ",
            "已启动：",
            "后台计时",
            "开始领取奖励",
            "检测到任务已完成",
            "检测到 ",
            "任务进度",
            "正在领取：",
            "已领取：",
            "已跳过：",
            "领取失败：",
            "B 站提示操作太快",
            "已有 ",
            "已找到本次活动任务",
            "活动任务已更新",
            "没有读到活动任务列表",
            "当前直播页暂时",
            "手动任务",
            "已停止领取",
            "领取前刷新任务进度",
            "已刷新任务进度",
        ))

    def _drain_logs(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            if message.startswith("__COOKIE__:"):
                self.cookie_text.delete("1.0", "end")
                self.cookie_text.insert("1.0", message.removeprefix("__COOKIE__:"))
                self._save()
                continue
            if message.startswith("__STATUS__:"):
                self._set_status(message.removeprefix("__STATUS__:"))
                continue
            if message.startswith("__ERROR__:"):
                detail = message.removeprefix("__ERROR__:")
                self._notify_from_message(detail)
                self._log(detail)
                messagebox.showerror("Cookie 获取失败", detail)
                continue
            self._notify_from_message(message)
            account_prefix, body = self._split_account_prefix(message)
            if body.startswith("掉宝任务："):
                snapshot = body.removeprefix("掉宝任务：").strip()
                if account_prefix:
                    snapshot = f"{account_prefix}\n{snapshot}"
                self._progress_snapshot_log(snapshot)
                continue
            if self._is_progress_message(message):
                self._progress_log(message)
                continue
            self._log(message)
        self.after(200, self._drain_logs)

    def _poll_watch_status(self) -> None:
        try:
            if self.watcher and self.watcher.running:
                snapshot, summary = self.watcher.get_watch_status_snapshot()
                self.watch_status_card.update_snapshot(snapshot, summary)
            else:
                self.watch_status_card.update_snapshot([], "后台计时状态：未启动")
        finally:
            self.after(1000, self._poll_watch_status)

    def destroy(self) -> None:
        if self.watcher:
            self.watcher.stop()
        super().destroy()


def main() -> None:
    app = App()
    app.mainloop()
