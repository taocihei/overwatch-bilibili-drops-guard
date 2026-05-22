from __future__ import annotations

import queue
import re
import threading
import traceback
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Callable

from . import __version__
from .config import APP_DIR, MAX_CHECK_INTERVAL, MAX_WATCH_WINDOWS, MIN_CHECK_INTERVAL, AppConfig, load_config, sanitize_config, save_config
from .cookie_capture import capture_bilibili_cookie, open_bilibili_login_page
from .watcher import LiveWatcher, WatchOptions


SOURCE_URL = "https://github.com/taocihei/overwatch-bilibili-drops-guard"


class RoundedPanel(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        fill: str = "#fffaf4",
        background: str = "#f7f2ea",
        radius: int = 22,
        padding: tuple[int, int] = (20, 16),
        min_height: int = 0,
        outline: str = "#eadfce",
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
        if min_height:
            self.configure(height=min_height)
        self.inner = tk.Frame(self, bg=fill, highlightthickness=0, borderwidth=0)
        self._window = self.create_window(self.pad_x, self.pad_y, anchor="nw", window=self.inner)
        self.bind("<Configure>", self._redraw)
        self.inner.bind("<Configure>", self._sync_height)

    def _sync_height(self, _event: tk.Event) -> None:
        if not self.auto_height:
            return
        requested = self.inner.winfo_reqheight() + self.pad_y * 2
        if requested > 1:
            self.configure(height=max(self.min_height, requested))

    def _redraw(self, _event: tk.Event | None = None) -> None:
        self.delete("panel")
        self.delete("shadow")
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        if self.shadow and width > 8 and height > 8:
            self._rounded_rect(3, 5, width - 2, height - 1, self.radius, fill="#eee6da", outline="", tags="shadow")
        self._rounded_rect(1, 1, width - 3, height - 4, self.radius, fill=self.fill, outline=self.outline, tags="panel")
        self.tag_lower("shadow")
        self.tag_lower("panel")
        self.coords(self._window, self.pad_x, self.pad_y)
        self.itemconfigure(self._window, width=max(1, width - self.pad_x * 2), height=max(1, height - self.pad_y * 2))

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
        fill: str = "#5f7f67",
        foreground: str = "#ffffff",
        active_fill: str | None = None,
        height: int = 40,
        width: int | None = None,
        font: tuple[str, int, str] = ("Microsoft YaHei UI", 10, "bold"),
    ) -> None:
        try:
            parent_bg = str(parent.cget("bg"))
        except tk.TclError:
            parent_bg = "#fffaf4"
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


class Stepper(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        variable: tk.IntVar,
        *,
        minimum: int,
        maximum: int,
        background: str = "#fffaf4",
    ) -> None:
        super().__init__(parent, height=38, bg=background, highlightthickness=0, borderwidth=0, cursor="hand2")
        self.variable = variable
        self.minimum = minimum
        self.maximum = maximum
        self.bind("<Configure>", lambda _event: self._redraw())
        self.bind("<Button-1>", self._click)
        self.variable.trace_add("write", lambda *_args: self._redraw())

    def _click(self, event: tk.Event) -> None:
        width = max(1, self.winfo_width())
        if event.x < 42:
            self._set_value(self.variable.get() - 1)
        elif event.x > width - 42:
            self._set_value(self.variable.get() + 1)

    def _set_value(self, value: int) -> None:
        self.variable.set(min(max(int(value), self.minimum), self.maximum))

    def _redraw(self) -> None:
        self.delete("all")
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        self.create_polygon(
            18, 1, width - 18, 1, width, 1, width, 18,
            width, height - 18, width, height - 1, width - 18, height - 1,
            18, height - 1, 1, height - 1, 1, height - 18, 1, 18, 1, 1,
            smooth=True,
            splinesteps=18,
            fill="#fffdf9",
            outline="#eadfce",
        )
        self.create_text(22, height // 2, text="−", fill="#7a6d61", font=("Microsoft YaHei UI", 14, "bold"))
        self.create_text(width - 22, height // 2, text="+", fill="#7a6d61", font=("Microsoft YaHei UI", 14, "bold"))
        self.create_text(width // 2, height // 2, text=str(self.variable.get()), fill="#302b26", font=("Microsoft YaHei UI", 10))


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"守望先锋 B 站直播挂宝 v{__version__}")
        self.geometry("1200x900")
        self.minsize(1080, 820)
        self.configure(bg="#f6f1e9")

        self.config_data = load_config()
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.watcher: LiveWatcher | None = None
        self.cookie_capture_thread: threading.Thread | None = None
        self.progress_events: list[str] = []
        self.progress_snapshot = ""

        self.cookie_var = tk.StringVar(value=self.config_data.cookie)
        self.room_var = tk.StringVar(value=self.config_data.room_id)
        self.interval_var = tk.IntVar(value=self.config_data.check_interval)
        self.auto_claim_var = tk.BooleanVar(value=self.config_data.auto_claim)
        self.watch_threads_var = tk.IntVar(value=self.config_data.watch_threads)
        self.status_var = tk.StringVar(value="未运行")
        self.version_var = tk.StringVar(value=f"v{__version__}")

        self._configure_style()
        self._build_ui()
        self.after(100, self._clear_initial_focus)
        self.after(200, self._drain_logs)

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
        style.configure(".", font=font, background="#f6f1e9", foreground="#2e2b28")
        style.configure("App.TFrame", background="#f6f1e9")
        style.configure("Surface.TFrame", background="#fffaf4", borderwidth=0, relief="flat")
        style.configure("Rail.TFrame", background="#efe5d7", borderwidth=0, relief="flat")
        style.configure("StepItem.TFrame", background="#fbf6ee", borderwidth=0, relief="flat")
        style.configure("Header.TFrame", background="#f6f1e9")
        style.configure("ActionBar.TFrame", background="#fffaf4", borderwidth=0, relief="flat")
        style.configure("Step.TFrame", background="#f7efe4")
        style.configure("Body.TLabel", background="#fffaf4", foreground="#2e2b28")
        style.configure("Muted.TLabel", background="#fffaf4", foreground="#81776c", font=("Microsoft YaHei UI", 9))
        style.configure("RailMuted.TLabel", background="#efe5d7", foreground="#7a6d61", font=("Microsoft YaHei UI", 9))
        style.configure("RailTitle.TLabel", background="#efe5d7", foreground="#302b26", font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("StepItemTitle.TLabel", background="#fbf6ee", foreground="#4d6d55", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("StepItemText.TLabel", background="#fbf6ee", foreground="#7a6d61", font=("Microsoft YaHei UI", 9))
        style.configure("PageTitle.TLabel", background="#f6f1e9", foreground="#2a2622", font=("Microsoft YaHei UI", 21, "bold"))
        style.configure("PageSubtitle.TLabel", background="#f6f1e9", foreground="#796f65", font=("Microsoft YaHei UI", 10))
        style.configure("FreeNoticeTitle.TLabel", background="#fff2ec", foreground="#a23f32", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("FreeNoticeBody.TLabel", background="#fff2ec", foreground="#6d5d55", font=("Microsoft YaHei UI", 9))
        style.configure("FreeNoticeLink.TLabel", background="#fff2ec", foreground="#1f6feb", font=("Microsoft YaHei UI", 9, "underline"))
        style.configure("Eyebrow.TLabel", background="#f6f1e9", foreground="#9a6b3d", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("SectionTitle.TLabel", background="#fffaf4", foreground="#2a2622", font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("StepTitle.TLabel", background="#f1eadf", foreground="#5f725e", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("StepText.TLabel", background="#f1eadf", foreground="#6d6259", font=("Microsoft YaHei UI", 9))
        style.configure("Status.TLabel", background="#e8f0df", foreground="#3f6b50", padding=(14, 8), font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Version.TLabel", background="#f1eadf", foreground="#7a6d61", padding=(10, 5), font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TEntry", padding=(10, 8), fieldbackground="#fffdf9", bordercolor="#e4d8ca", lightcolor="#e4d8ca", darkcolor="#e4d8ca")
        style.configure("TSpinbox", padding=(10, 8), fieldbackground="#fffdf9", bordercolor="#e4d8ca", lightcolor="#e4d8ca", darkcolor="#e4d8ca")
        style.configure("TCheckbutton", background="#fffaf4", foreground="#2e2b28")
        style.map("TCheckbutton", background=[("active", "#fffaf4")])
        style.configure("Vertical.TScrollbar", background="#eadfce", troughcolor="#fffaf4", bordercolor="#fffaf4", arrowcolor="#7b6f63")

        style.configure("Primary.TButton", padding=(16, 9), background="#5f7f67", foreground="#ffffff", borderwidth=0, font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Primary.TButton", background=[("active", "#536f5a"), ("pressed", "#48614f")], foreground=[("active", "#ffffff")])
        style.configure("Secondary.TButton", padding=(14, 9), background="#f6eee4", foreground="#3c3732", bordercolor="#f6eee4", borderwidth=0)
        style.map("Secondary.TButton", background=[("active", "#f4ece2"), ("pressed", "#ede2d6")])
        style.configure("Danger.TButton", padding=(14, 9), background="#fff0eb", foreground="#a44e3f", bordercolor="#fff0eb", borderwidth=0)
        style.map("Danger.TButton", background=[("active", "#fbe2d9"), ("pressed", "#f4d3c8")])
        style.configure("SwitchOn.TButton", padding=(14, 9), background="#5f7f67", foreground="#ffffff", borderwidth=0, font=("Microsoft YaHei UI", 10, "bold"))
        style.map("SwitchOn.TButton", background=[("active", "#536f5a"), ("pressed", "#48614f")], foreground=[("active", "#ffffff")])
        style.configure("SwitchOff.TButton", padding=(14, 9), background="#f1eadf", foreground="#7a6d61", borderwidth=0, font=("Microsoft YaHei UI", 10, "bold"))
        style.map("SwitchOff.TButton", background=[("active", "#eadfce"), ("pressed", "#e3d5c2")])

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=(30, 14, 30, 10), style="Header.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        meta = ttk.Frame(header, style="Header.TFrame")
        meta.grid(row=0, column=0, sticky="w")
        ttk.Label(meta, text="本地直播掉宝助手", style="Eyebrow.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(meta, textvariable=self.version_var, style="Version.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Label(header, text="守望先锋 B 站直播挂宝", style="PageTitle.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(
            header,
            text="登录、观看计时、任务检查和领奖都在这里完成。默认直播间已填好，打开后按步骤走就行。",
            style="PageSubtitle.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").grid(row=1, column=1, rowspan=2, sticky="e")

        body = ttk.Frame(self, padding=(30, 0, 30, 18), style="App.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=7, uniform="main")
        body.columnconfigure(1, weight=4, uniform="main")
        body.rowconfigure(2, weight=1)

        free_panel = RoundedPanel(body, fill="#fff2ec", background="#f6f1e9", radius=18, padding=(14, 8), outline="#f1cabb", shadow=False)
        free_panel.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        free = free_panel.inner
        free.columnconfigure(0, weight=1)
        free.columnconfigure(1, weight=0)
        free.columnconfigure(2, weight=0)
        ttk.Label(
            free,
            text="本软件完全免费，购买请找商家退款；赞助只是点赞，不会解锁任何功能。",
            style="FreeNoticeTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        source_label = ttk.Label(
            free,
            text="打开开源地址",
            style="FreeNoticeLink.TLabel",
            cursor="hand2",
        )
        source_label.grid(row=0, column=1, sticky="e", padx=(14, 0))
        source_label.bind("<Button-1>", lambda _event: self._open_source_url())
        copy_label = ttk.Label(free, text="复制地址", style="FreeNoticeLink.TLabel", cursor="hand2")
        copy_label.grid(row=0, column=2, sticky="e", padx=(14, 0))
        copy_label.bind("<Button-1>", lambda _event: self._copy_source_url())

        guide_panel = RoundedPanel(body, fill="#efe5d7", background="#f6f1e9", radius=20, padding=(14, 10), outline="#e5d8c7")
        guide_panel.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        guide = guide_panel.inner
        guide.columnconfigure((0, 1, 2, 3), weight=1, uniform="guide")

        center = ttk.Frame(body, style="App.TFrame")
        center.grid(row=2, column=0, sticky="nsew", padx=(0, 18))
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
            text="第一次先获取 Cookie；直播间默认 23612045；任务 ID 通常留空，程序会自动识别。",
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
            cell_panel = RoundedPanel(parent, fill="#fbf6ee", background="#efe5d7", radius=14, padding=(10, 8), outline="#eadfce", shadow=False)
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
        card.rowconfigure(4, weight=1)

        PillButton(card, "自动获取 Cookie", self._capture_cookie, fill="#5f7f67", active_fill="#536f5a").grid(row=2, column=0, sticky="ew", pady=(10, 10), padx=(0, 8))
        PillButton(card, "只打开登录页", self._open_cookie_login_page, fill="#f1eadf", foreground="#3c3732", active_fill="#eadfce").grid(row=2, column=1, sticky="ew", pady=(10, 10), padx=(8, 0))

        ttk.Label(card, text="Cookie 内容", style="Body.TLabel").grid(row=3, column=0, columnspan=2, sticky="w")
        cookie_box = RoundedPanel(card, fill="#fffdf9", background="#fffaf4", radius=16, padding=(4, 4), min_height=72, outline="#eadfce", shadow=False)
        cookie_box.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(6, 0))
        cookie_box.inner.columnconfigure(0, weight=1)
        cookie_box.inner.rowconfigure(0, weight=1)
        self.cookie_text = tk.Text(
            cookie_box.inner,
            height=2,
            wrap="word",
            undo=True,
            borderwidth=0,
            relief="flat",
            bg="#fffdf9",
            fg="#302b26",
            insertbackground="#302b26",
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
        card.rowconfigure(8, weight=1)

        ttk.Label(card, text="直播间号或链接", style="Body.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        room_box = RoundedPanel(card, fill="#fffdf9", background="#fffaf4", radius=16, padding=(12, 7), min_height=48, outline="#eadfce", shadow=False)
        room_box.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 10))
        room_box.inner.columnconfigure(0, weight=1)
        room_entry = tk.Entry(
            room_box.inner,
            textvariable=self.room_var,
            borderwidth=0,
            relief="flat",
            bg="#fffdf9",
            fg="#302b26",
            insertbackground="#302b26",
            font=("Microsoft YaHei UI", 10),
        )
        room_entry.grid(row=0, column=0, sticky="ew")

        ttk.Label(card, text="检查间隔（秒）", style="Body.TLabel").grid(row=4, column=0, sticky="w")
        ttk.Label(card, text="后台观看线程数", style="Body.TLabel").grid(row=4, column=1, sticky="w", padx=(14, 0))

        Stepper(card, self.interval_var, minimum=MIN_CHECK_INTERVAL, maximum=MAX_CHECK_INTERVAL).grid(row=5, column=0, sticky="ew", pady=(6, 6))
        Stepper(card, self.watch_threads_var, minimum=1, maximum=MAX_WATCH_WINDOWS).grid(row=5, column=1, sticky="ew", padx=(14, 0), pady=(6, 6))
        ttk.Label(card, text="自动领奖", style="Body.TLabel").grid(row=6, column=0, sticky="w", pady=(4, 0))
        self.auto_claim_button = PillButton(card, "已开启", self._toggle_auto_claim, fill="#5f7f67", active_fill="#536f5a")
        self.auto_claim_button.grid(row=6, column=1, sticky="ew", padx=(14, 0), pady=(2, 8))
        self._refresh_auto_claim_button()
        ttk.Label(card, text="任务 ID（可留空）", style="Body.TLabel").grid(
            row=7,
            column=0,
            sticky="w",
            pady=(8, 0),
        )
        task_ids_box = RoundedPanel(card, fill="#fffdf9", background="#fffaf4", radius=16, padding=(4, 4), min_height=44, outline="#eadfce", shadow=False)
        task_ids_box.grid(row=7, column=1, sticky="ew", padx=(14, 0), pady=(6, 0))
        task_ids_box.inner.columnconfigure(0, weight=1)
        task_ids_box.inner.rowconfigure(0, weight=1)
        self.task_ids_text = tk.Text(
            task_ids_box.inner,
            height=1,
            wrap="word",
            undo=True,
            borderwidth=0,
            relief="flat",
            bg="#fffdf9",
            fg="#302b26",
            insertbackground="#302b26",
            highlightthickness=0,
            padx=8,
            pady=6,
            font=("Consolas", 9),
        )
        self.task_ids_text.grid(row=0, column=0, sticky="nsew")
        self.task_ids_text.insert("1.0", self.config_data.task_ids)

        rule_panel = RoundedPanel(card, fill="#f7efe4", background="#fffaf4", radius=17, padding=(12, 8), outline="#eadfce", shadow=False)
        rule_panel.grid(row=8, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        rules = rule_panel.inner
        rules.columnconfigure(0, weight=1)
        for index, text in enumerate((
            "后台计时只发观看心跳，不打开直播间页面。",
            "自动领奖固定 1 个线程；任务 ID 通常留空自动识别。",
        )):
            ttk.Label(rules, text=text, style="StepText.TLabel", wraplength=290).grid(row=index, column=0, sticky="ew", pady=(0 if index == 0 else 5, 0))

    def _build_actions(self, parent: ttk.Frame) -> None:
        actions_panel = RoundedPanel(parent, fill="#fffaf4", background="#f6f1e9", radius=24, padding=(16, 12))
        actions_panel.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        actions = actions_panel.inner
        actions.columnconfigure((0, 1, 2, 3), weight=1, uniform="actions")

        PillButton(actions, "保存配置", self._save, fill="#f1eadf", foreground="#3c3732", active_fill="#eadfce").grid(row=0, column=0, sticky="ew", padx=(0, 8))
        PillButton(actions, "开始挂宝", self._start, fill="#5f7f67", active_fill="#536f5a").grid(row=0, column=1, sticky="ew", padx=8)
        PillButton(actions, "领取奖励", self._claim, fill="#f1eadf", foreground="#3c3732", active_fill="#eadfce").grid(row=0, column=2, sticky="ew", padx=8)
        PillButton(actions, "停止", self._stop, fill="#fff0eb", foreground="#a44e3f", active_fill="#fbe2d9").grid(row=0, column=3, sticky="ew", padx=(8, 0))

    def _build_log_card(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(0, weight=1)
        parent.rowconfigure(1, weight=0)

        progress_card = self._card(parent, row=0, title="任务进度", subtitle="登录、房间、计时、剩余分钟和领取结果都在这里", sticky="nsew", min_height=400, subtitle_wrap=350, auto_height=False)
        progress_card.columnconfigure(0, weight=1)
        progress_card.rowconfigure(2, weight=1)

        progress_wrap = RoundedPanel(progress_card, fill="#fffdf9", background="#fffaf4", radius=18, padding=(4, 4), min_height=300, outline="#eadfce", shadow=False, auto_height=False)
        progress_wrap.grid(row=2, column=0, sticky="nsew", pady=(14, 0))
        progress_wrap.inner.columnconfigure(0, weight=1)
        progress_wrap.inner.rowconfigure(0, weight=1)

        self.progress_text = tk.Text(
            progress_wrap.inner,
            height=12,
            wrap="word",
            state="disabled",
            borderwidth=0,
            relief="flat",
            bg="#fffdf9",
            fg="#3f3a35",
            insertbackground="#3f3a35",
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

        card = self._card(parent, row=1, title="运行日志", subtitle="辅助记录，主要结果看上面的任务进度。", sticky="ew", min_height=150, subtitle_wrap=330)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)

        log_wrap = RoundedPanel(card, fill="#fffdf9", background="#fffaf4", radius=18, padding=(4, 4), min_height=72, outline="#eadfce", shadow=False)
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
            bg="#fffdf9",
            fg="#3f3a35",
            insertbackground="#3f3a35",
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
        panel = RoundedPanel(parent, fill="#fffaf4", background="#f6f1e9", radius=24, padding=(19, 14), min_height=min_height, auto_height=panel_auto_height)
        panel.grid(row=row, column=column, columnspan=columnspan, sticky=sticky, pady=(0, 12), padx=padx)
        card = panel.inner
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text=title, style="SectionTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(card, text=subtitle, style="Muted.TLabel", wraplength=subtitle_wrap).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        return card

    def _current_config(self) -> AppConfig:
        return sanitize_config(AppConfig(
            cookie=self.cookie_text.get("1.0", "end").strip(),
            room_id=self.room_var.get().strip(),
            check_interval=self._safe_int_var(self.interval_var, 10),
            auto_claim=bool(self.auto_claim_var.get()),
            task_ids=self.task_ids_text.get("1.0", "end").strip(),
            watch_threads=self._safe_int_var(self.watch_threads_var, 1),
        ))

    def _clear_initial_focus(self) -> None:
        self.focus_force()

    def _toggle_auto_claim(self) -> None:
        self.auto_claim_var.set(not bool(self.auto_claim_var.get()))
        self._refresh_auto_claim_button()

    def _refresh_auto_claim_button(self) -> None:
        if bool(self.auto_claim_var.get()):
            self.auto_claim_button.set_appearance(text="已开启", fill="#5f7f67", foreground="#ffffff", active_fill="#536f5a")
        else:
            self.auto_claim_button.set_appearance(text="已关闭", fill="#f1eadf", foreground="#7a6d61", active_fill="#eadfce")

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

    def _save(self) -> None:
        config = self._current_config()
        save_config(config)
        self.config_data = config
        self.room_var.set(config.room_id)
        self._log("配置已保存")

    def _start(self) -> None:
        config = self._current_config()
        if not config.cookie:
            messagebox.showwarning("缺少 Cookie", "请先粘贴 B 站 Cookie。")
            return
        if not config.room_id:
            messagebox.showwarning("缺少直播间号", "请先填写直播间号或直播间链接。")
            return

        self._save()
        if config.watch_threads >= MAX_WATCH_WINDOWS:
            self._log(f"后台观看线程数已限制为 {MAX_WATCH_WINDOWS}，避免请求过多导致账号或网络异常")
        if self.watcher and self.watcher.running:
            self._log("当前已经在运行")
            return

        options = WatchOptions(
            cookie=config.cookie,
            room_id=config.room_id,
            check_interval=config.check_interval,
            auto_claim=config.auto_claim,
            task_ids=self._parse_task_ids(config.task_ids),
            watch_threads=config.watch_threads,
        )
        self.watcher = LiveWatcher(options, self._thread_log)
        self.watcher.start()
        self.status_var.set("运行中")
        self._log(
            f"已启动：房间 {config.room_id}，后台计时 {config.watch_threads} 路，"
            f"检查间隔 {config.check_interval} 秒，自动领奖={'开启' if config.auto_claim else '关闭'}"
        )
        self._progress_log(
            f"已启动：房间 {config.room_id}，后台计时 {config.watch_threads} 路，"
            f"每 {config.check_interval} 秒刷新一次，自动领奖={'开启' if config.auto_claim else '关闭'}"
        )

    def _stop(self) -> None:
        if self.watcher:
            self.watcher.stop()
        self.status_var.set("未运行")

    def _capture_cookie(self) -> None:
        if self.cookie_capture_thread and self.cookie_capture_thread.is_alive():
            self._log("自动获取 Cookie 正在运行中")
            return
        self.status_var.set("正在获取 Cookie")
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
        if not self.watcher:
            self._log("请先开始挂宝，等待程序识别完成任务")
            return
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

    def _progress_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.progress_events.append(f"[{timestamp}] {message}")
        self.progress_events = self.progress_events[-6:]
        self._render_progress_text()

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
                self.status_var.set(message.removeprefix("__STATUS__:"))
                continue
            if message.startswith("__ERROR__:"):
                detail = message.removeprefix("__ERROR__:")
                self._log(detail)
                messagebox.showerror("Cookie 获取失败", detail)
                continue
            if message.startswith("掉宝任务："):
                self._progress_snapshot_log(message.removeprefix("掉宝任务：").strip())
                continue
            if self._is_progress_message(message):
                self._progress_log(message)
                continue
            self._log(message)
        self.after(200, self._drain_logs)

    def destroy(self) -> None:
        if self.watcher:
            self.watcher.stop()
        super().destroy()


def main() -> None:
    app = App()
    app.mainloop()
