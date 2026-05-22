from __future__ import annotations

import queue
import re
import threading
import traceback
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from .config import APP_DIR, MAX_CHECK_INTERVAL, MAX_WATCH_WINDOWS, MIN_CHECK_INTERVAL, AppConfig, load_config, sanitize_config, save_config
from .cookie_capture import capture_bilibili_cookie, open_bilibili_login_page
from .watcher import LiveWatcher, WatchOptions


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("守望先锋 B 站直播挂宝")
        self.geometry("1180x820")
        self.minsize(1040, 720)
        self.configure(bg="#f5f6f8")

        self.config_data = load_config()
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.watcher: LiveWatcher | None = None
        self.cookie_capture_thread: threading.Thread | None = None

        self.cookie_var = tk.StringVar(value=self.config_data.cookie)
        self.room_var = tk.StringVar(value=self.config_data.room_id)
        self.interval_var = tk.IntVar(value=self.config_data.check_interval)
        self.auto_claim_var = tk.BooleanVar(value=self.config_data.auto_claim)
        self.watch_threads_var = tk.IntVar(value=self.config_data.watch_threads)
        self.status_var = tk.StringVar(value="未运行")

        self._configure_style()
        self._build_ui()
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
        style.configure(".", font=font, background="#f5f6f8", foreground="#1d1d1f")
        style.configure("App.TFrame", background="#f5f6f8")
        style.configure("Card.TFrame", background="#ffffff", borderwidth=1, relief="solid")
        style.configure("Header.TFrame", background="#f5f6f8")
        style.configure("ActionBar.TFrame", background="#ffffff")
        style.configure("Step.TFrame", background="#eef4ff")
        style.configure("Body.TLabel", background="#ffffff", foreground="#1d1d1f")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#6b7280", font=("Microsoft YaHei UI", 9))
        style.configure("PageTitle.TLabel", background="#f5f6f8", foreground="#111827", font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("PageSubtitle.TLabel", background="#f5f6f8", foreground="#5f6673", font=("Microsoft YaHei UI", 10))
        style.configure("SectionTitle.TLabel", background="#ffffff", foreground="#111827", font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("StepTitle.TLabel", background="#eef4ff", foreground="#0a4fbf", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("StepText.TLabel", background="#eef4ff", foreground="#314158", font=("Microsoft YaHei UI", 9))
        style.configure("Status.TLabel", background="#e8f2ff", foreground="#075cb8", padding=(12, 7), font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TEntry", padding=(8, 7), fieldbackground="#fbfbfd")
        style.configure("TSpinbox", padding=(8, 7), fieldbackground="#fbfbfd")
        style.configure("TCheckbutton", background="#ffffff", foreground="#1d1d1f")
        style.map("TCheckbutton", background=[("active", "#ffffff")])

        style.configure("Primary.TButton", padding=(14, 8), background="#0a84ff", foreground="#ffffff", borderwidth=0, font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Primary.TButton", background=[("active", "#006edb"), ("pressed", "#005bb5")], foreground=[("active", "#ffffff")])
        style.configure("Secondary.TButton", padding=(12, 8), background="#ffffff", foreground="#1d1d1f", bordercolor="#d6dae1", borderwidth=1)
        style.map("Secondary.TButton", background=[("active", "#f2f5f8"), ("pressed", "#e9edf2")])
        style.configure("Danger.TButton", padding=(12, 8), background="#fff1f0", foreground="#b42318", bordercolor="#ffd1cc", borderwidth=1)
        style.map("Danger.TButton", background=[("active", "#ffe4e0"), ("pressed", "#ffd2cc")])

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=(26, 22, 26, 14), style="Header.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="守望先锋 B 站直播挂宝", style="PageTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="获取 Cookie、打开直播窗口、检查进度和领取奖励，都在这个控制台完成。",
            style="PageSubtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").grid(row=0, column=1, rowspan=2, sticky="e")

        body = ttk.Frame(self, padding=(26, 0, 26, 24), style="App.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=5, uniform="main")
        body.columnconfigure(1, weight=4, uniform="main")
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body, style="App.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)

        right = ttk.Frame(body, style="App.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        self._build_step_strip(left)
        self._build_cookie_card(left)
        self._build_task_card(left)
        self._build_actions(left)
        self._build_log_card(right)

    def _build_step_strip(self, parent: ttk.Frame) -> None:
        steps = ttk.Frame(parent, padding=(12, 8), style="Step.TFrame")
        steps.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        for index in range(4):
            steps.columnconfigure(index, weight=1, uniform="steps")

        items = (
            ("1", "获取 Cookie"),
            ("2", "设置直播"),
            ("3", "开始计时"),
            ("4", "领取奖励"),
        )
        for index, (number, title) in enumerate(items):
            cell = ttk.Frame(steps, style="Step.TFrame")
            cell.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 8, 0))
            ttk.Label(cell, text=f"{number}. {title}", style="StepTitle.TLabel").grid(row=0, column=0, sticky="w")

    def _build_cookie_card(self, parent: ttk.Frame) -> None:
        card = self._card(parent, row=1, title="登录 Cookie", subtitle="推荐自动获取：程序会打开 B 站登录页，检测到登录后自动回填 Cookie。")
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)
        card.rowconfigure(4, weight=1)

        ttk.Button(card, text="自动获取 Cookie", command=self._capture_cookie, style="Primary.TButton").grid(row=2, column=0, sticky="ew", pady=(10, 10), padx=(0, 8))
        ttk.Button(card, text="只打开登录页", command=self._open_cookie_login_page, style="Secondary.TButton").grid(row=2, column=1, sticky="ew", pady=(10, 10), padx=(8, 0))

        ttk.Label(card, text="Cookie 内容", style="Body.TLabel").grid(row=3, column=0, columnspan=2, sticky="w")
        self.cookie_text = tk.Text(
            card,
            height=3,
            wrap="word",
            undo=True,
            borderwidth=0,
            relief="flat",
            bg="#fbfbfd",
            fg="#111827",
            insertbackground="#111827",
            highlightthickness=1,
            highlightbackground="#d8dde6",
            highlightcolor="#0a84ff",
            font=("Consolas", 9),
        )
        self.cookie_text.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(6, 0))
        self.cookie_text.insert("1.0", self.cookie_var.get())

    def _build_task_card(self, parent: ttk.Frame) -> None:
        card = self._card(parent, row=2, title="直播与任务", subtitle="直播窗口数用于并行观看累计时长；领奖请求固定由一个线程提交。", sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)
        card.columnconfigure(2, weight=1)
        card.rowconfigure(7, weight=1)

        ttk.Label(card, text="直播间号或链接", style="Body.TLabel").grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Entry(card, textvariable=self.room_var).grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 10))

        ttk.Label(card, text="检查间隔（秒）", style="Body.TLabel").grid(row=4, column=0, sticky="w")
        ttk.Label(card, text="直播窗口数", style="Body.TLabel").grid(row=4, column=1, sticky="w", padx=(12, 0))
        ttk.Label(card, text="领奖策略", style="Body.TLabel").grid(row=4, column=2, sticky="w", padx=(12, 0))

        ttk.Spinbox(card, from_=MIN_CHECK_INTERVAL, to=MAX_CHECK_INTERVAL, textvariable=self.interval_var, width=8).grid(row=5, column=0, sticky="ew", pady=(6, 6))
        ttk.Spinbox(card, from_=1, to=MAX_WATCH_WINDOWS, textvariable=self.watch_threads_var, width=8).grid(row=5, column=1, sticky="ew", padx=(12, 0), pady=(6, 6))
        ttk.Checkbutton(card, text="自动领奖", variable=self.auto_claim_var).grid(row=5, column=2, sticky="w", padx=(12, 0), pady=(6, 6))

        ttk.Label(card, text="任务 ID（可留空自动识别，可用空格、逗号或分号分隔）", style="Body.TLabel").grid(
            row=6,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(4, 6),
        )
        self.task_ids_text = tk.Text(
            card,
            height=2,
            wrap="word",
            undo=True,
            borderwidth=0,
            relief="flat",
            bg="#fbfbfd",
            fg="#111827",
            insertbackground="#111827",
            highlightthickness=1,
            highlightbackground="#d8dde6",
            highlightcolor="#0a84ff",
            font=("Consolas", 9),
        )
        self.task_ids_text.grid(row=7, column=0, columnspan=3, sticky="nsew")
        self.task_ids_text.insert("1.0", self.config_data.task_ids)

    def _build_actions(self, parent: ttk.Frame) -> None:
        actions = ttk.Frame(parent, padding=(16, 12), style="ActionBar.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        actions.columnconfigure((0, 1, 2, 3), weight=1, uniform="actions")

        ttk.Button(actions, text="保存配置", command=self._save, style="Secondary.TButton").grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(actions, text="开始挂宝", command=self._start, style="Primary.TButton").grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(actions, text="领取奖励", command=self._claim, style="Secondary.TButton").grid(row=0, column=2, sticky="ew", padx=8)
        ttk.Button(actions, text="停止", command=self._stop, style="Danger.TButton").grid(row=0, column=3, sticky="ew", padx=(8, 0))

    def _build_log_card(self, parent: ttk.Frame) -> None:
        card = self._card(parent, row=0, title="运行日志", subtitle="Cookie、浏览器窗口、任务检查和领奖结果都会实时显示。", sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(3, weight=1)

        tips = ttk.Frame(card, padding=(12, 10), style="Step.TFrame")
        tips.grid(row=2, column=0, sticky="ew", pady=(14, 12))
        tips.columnconfigure(0, weight=1)
        ttk.Label(tips, text="获取 Cookie 时不要关闭弹出的浏览器；登录成功后会自动回填并保存。", style="StepText.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(tips, text="如果自动获取失败，可以先点“只打开登录页”，确认浏览器能正常启动。", style="StepText.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 0))

        log_wrap = ttk.Frame(card, style="Card.TFrame")
        log_wrap.grid(row=3, column=0, sticky="nsew")
        log_wrap.columnconfigure(0, weight=1)
        log_wrap.rowconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_wrap,
            wrap="word",
            state="disabled",
            borderwidth=0,
            relief="flat",
            bg="#111827",
            fg="#e5e7eb",
            insertbackground="#e5e7eb",
            highlightthickness=0,
            padx=12,
            pady=10,
            font=("Consolas", 10),
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(log_wrap, command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

    def _card(self, parent: ttk.Frame, row: int, title: str, subtitle: str, sticky: str = "ew") -> ttk.Frame:
        card = ttk.Frame(parent, padding=(16, 12), style="Card.TFrame")
        card.grid(row=row, column=0, sticky=sticky, pady=(0, 10))
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text=title, style="SectionTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(card, text=subtitle, style="Muted.TLabel", wraplength=520).grid(row=1, column=0, sticky="ew", pady=(5, 0))
        return card

    def _current_config(self) -> AppConfig:
        return sanitize_config(AppConfig(
            cookie=self.cookie_text.get("1.0", "end").strip(),
            room_id=self.room_var.get().strip(),
            check_interval=self._safe_int_var(self.interval_var, 60),
            auto_claim=bool(self.auto_claim_var.get()),
            task_ids=self.task_ids_text.get("1.0", "end").strip(),
            watch_threads=self._safe_int_var(self.watch_threads_var, 1),
        ))

    def _safe_int_var(self, variable: tk.IntVar, default: int) -> int:
        try:
            return int(variable.get())
        except (tk.TclError, ValueError):
            return default

    def _save(self) -> None:
        config = self._current_config()
        save_config(config)
        self.config_data = config
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
            self._log(f"直播窗口数已限制为 {MAX_WATCH_WINDOWS}，避免浏览器窗口过多导致系统卡顿或闪退")
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
        self._log("已启动守护")

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
            self._log(message)
        self.after(200, self._drain_logs)

    def destroy(self) -> None:
        if self.watcher:
            self.watcher.stop()
        super().destroy()


def main() -> None:
    app = App()
    app.mainloop()
