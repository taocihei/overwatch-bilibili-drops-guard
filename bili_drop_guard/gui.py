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

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
except Exception:  # Pillow 是界面抗锯齿增强；缺失时回退到 Tk 原生绘制。
    Image = ImageDraw = ImageFilter = ImageTk = None  # type: ignore[assignment]

from . import __version__
from .config import APP_DIR, DEFAULT_ROOM_ID, MAX_CHECK_INTERVAL, MAX_WATCH_THREADS, MIN_CHECK_INTERVAL, AccountProfile, AppConfig, load_config, parse_task_ids, sanitize_config, save_config
from .cookie_capture import capture_bilibili_cookie, open_bilibili_login_page
from .notifier import send_notification
from .watcher import LiveWatcher, WatchOptions, WatchWorkerStatus
from .multi_account import MultiAccountWatcher, build_account_options


SOURCE_URL = "https://github.com/taocihei/overwatch-bilibili-drops-guard"
APP_BG = "#f3f7fc"
SURFACE = "#ffffff"
GLASS = "#fcfeff"
SOFT_SURFACE = "#f6f9fd"
BORDER = "#edf2f8"
TEXT = "#172033"
MUTED = "#65748a"
FAINT = "#95a3b6"
ACCENT = "#2868e8"
ACCENT_ACTIVE = "#1f55c8"
ACCENT_SOFT = "#eaf2ff"
ACCENT_SOFT_ACTIVE = "#dbeafe"
ACCENT_BORDER = "#b9d2ff"
INFO = "#2563eb"
SUCCESS = "#22a06b"
SUCCESS_ACTIVE = "#17885a"
DANGER = "#c84d44"
DANGER_BG = "#fff0ef"
WARNING_BG = "#fffaf0"
WARNING_BORDER = "#f1d79a"
PANEL_SHADOW = "#d7deea"
FIELD_BG = "#f8fbfe"
FIELD_OUTLINE = ""
BUTTON_OUTLINE = "#e4ebf5"
SUBTLE_OUTLINE = "#e7edf6"
SECONDARY = "#f6f9fd"
SECONDARY_ACTIVE = "#edf3fa"
HEADER_BG = "#fbfdff"
HEADER_MUTED = "#73839a"
HEADER_INPUT = "#ffffff"
PRIMARY = "#2467e8"
PRIMARY_ACTIVE = "#1d55c9"


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
        radius: int = 10,
        padding: tuple[int, int] = (20, 16),
        min_height: int = 0,
        outline: str = BORDER,
        shadow: bool = False,
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
        self._panel_image: object | None = None
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
        self.delete("panel_image")
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        if not self._draw_antialiased_panel(width, height):
            if self.shadow and width > 8 and height > 8:
                self._rounded_rect(2, 4, width - 2, height - 1, self.radius, fill=PANEL_SHADOW, outline="", tags="shadow")
            self._rounded_rect(1, 1, width - 3, height - 4, self.radius, fill=self.fill, outline=self.outline, tags="panel")
            self.tag_lower("panel", self._window)
            self.tag_lower("shadow", "panel")
        self.coords(self._window, self.pad_x, self.pad_y)
        window_options: dict[str, int] = {"width": max(1, width - self.pad_x * 2)}
        if not self.auto_height:
            window_options["height"] = max(1, height - self.pad_y * 2)
        self.itemconfigure(self._window, **window_options)
        if self.auto_height:
            self.after_idle(self._sync_height)

    def _draw_antialiased_panel(self, width: int, height: int) -> bool:
        if Image is None or ImageDraw is None or ImageTk is None:
            return False
        try:
            scale = 3
            image = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
            radius = self.radius * scale
            if self.shadow and width > 12 and height > 12:
                shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
                shadow_draw = ImageDraw.Draw(shadow)
                shadow_draw.rounded_rectangle(
                    (4 * scale, 7 * scale, (width - 4) * scale, (height - 4) * scale),
                    radius=radius,
                    fill=self._rgba(PANEL_SHADOW, 92),
                )
                if ImageFilter is not None:
                    shadow = shadow.filter(ImageFilter.GaussianBlur(5 * scale))
                image.alpha_composite(shadow)

            draw = ImageDraw.Draw(image)
            box = (0, 0, (width - 1) * scale, (height - 1) * scale)
            outline = self._rgba(self.outline, 255) if self.outline else None
            draw.rounded_rectangle(
                box,
                radius=radius,
                fill=self._rgba(self.fill, 246),
                outline=outline,
                width=scale if outline else 1,
            )
            image = image.resize((width, height), Image.Resampling.LANCZOS)
            self._panel_image = ImageTk.PhotoImage(image)
            self.create_image(0, 0, anchor="nw", image=self._panel_image, tags="panel_image")
            self.tag_lower("panel_image", self._window)
            return True
        except Exception:
            self._panel_image = None
            return False

    def _rgba(self, color: str, alpha: int) -> tuple[int, int, int, int]:
        try:
            red, green, blue = self.winfo_rgb(color)
        except tk.TclError:
            red, green, blue = 65535, 65535, 65535
        return red // 256, green // 256, blue // 256, alpha

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
        outline: str = "",
    ) -> None:
        try:
            parent_bg = str(parent.cget("bg"))
        except tk.TclError:
            parent_bg = SURFACE
        self._button_height = height
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
        self.outline = outline
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
        height = max(self._button_height, self.winfo_height())
        if self.winfo_height() < self._button_height:
            self.configure(height=self._button_height)
        radius = min(7, height // 2)
        self.create_polygon(
            radius, 1, width - radius, 1, width, 1, width, radius,
            width, height - radius, width, height - 1, width - radius, height - 1,
            radius, height - 1, 1, height - 1, 1, height - radius, 1, radius, 1, 1,
            smooth=True,
            splinesteps=18,
            fill=self.fill,
            outline=self.outline,
        )
        self.create_text(width // 2, height // 2 - 1, text=self.text, fill=self.foreground, font=self.font, anchor="center")


class FlatButton(tk.Button):
    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        fill: str,
        foreground: str = TEXT,
        active_fill: str | None = None,
        active_foreground: str | None = None,
        font: tuple[str, int, str] = ("Microsoft YaHei UI", 10, "bold"),
    ) -> None:
        super().__init__(
            parent,
            text=text,
            command=command,
            bg=fill,
            fg=foreground,
            activebackground=active_fill or fill,
            activeforeground=active_foreground or foreground,
            font=font,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            cursor="hand2",
            padx=12,
            pady=8,
            takefocus=True,
        )
        self.normal_fill = fill
        self.normal_foreground = foreground
        self.active_fill = active_fill or fill
        self.active_foreground = active_foreground or foreground
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)

    def set_appearance(self, *, text: str, fill: str, foreground: str = "#ffffff", active_fill: str | None = None) -> None:
        self.normal_fill = fill
        self.normal_foreground = foreground
        self.active_fill = active_fill or fill
        self.active_foreground = foreground
        self.configure(
            text=text,
            bg=fill,
            fg=foreground,
            activebackground=self.active_fill,
            activeforeground=foreground,
        )

    def _enter(self, _event: tk.Event) -> None:
        self.configure(bg=self.active_fill, fg=self.active_foreground)

    def _leave(self, _event: tk.Event) -> None:
        self.configure(bg=self.normal_fill, fg=self.normal_foreground)


class LabelButton(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        fill: str,
        foreground: str = TEXT,
        active_fill: str | None = None,
        height: int = 46,
        width: int | None = None,
        font: tuple[str, int, str] = ("Microsoft YaHei UI", 10, "bold"),
        outline: str = "",
        radius: int | None = None,
        shadow: bool = False,
    ) -> None:
        try:
            parent_bg = str(parent.cget("bg"))
        except tk.TclError:
            parent_bg = APP_BG
        canvas_options: dict[str, object] = {
            "bg": parent_bg,
            "height": height,
            "highlightthickness": 0,
            "borderwidth": 0,
            "cursor": "hand2",
        }
        canvas_options["width"] = width if width is not None else 1
        super().__init__(parent, **canvas_options)
        self.text = text
        self.command = command
        self.normal_fill = fill
        self.normal_foreground = foreground
        self.active_fill = active_fill or fill
        self.active_foreground = foreground
        self.outline = outline
        self._height = height
        self.radius = radius if radius is not None else min(12, height // 2)
        self.font = font
        self.shadow = shadow
        self._button_image: object | None = None
        self.bind("<Configure>", lambda _event: self._redraw())
        self.bind("<Button-1>", lambda _event: self.command())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self._redraw()

    def set_appearance(self, *, text: str, fill: str, foreground: str = "#ffffff", active_fill: str | None = None) -> None:
        self.text = text
        self.normal_fill = fill
        self.normal_foreground = foreground
        self.active_fill = active_fill or fill
        self.active_foreground = foreground
        self._redraw()

    def _enter(self, _event: tk.Event) -> None:
        self._draw_button(self.active_fill, self.active_foreground)

    def _leave(self, _event: tk.Event) -> None:
        self._redraw()

    def _redraw(self) -> None:
        self._draw_button(self.normal_fill, self.normal_foreground)

    def _draw_button(self, fill: str, foreground: str) -> None:
        self.delete("all")
        width = max(1, self.winfo_width())
        height = max(self._height, self.winfo_height())
        if self.winfo_height() < self._height:
            self.configure(height=self._height)
        if Image is not None and ImageDraw is not None and ImageTk is not None and width > 3 and height > 3:
            try:
                scale = 3
                image = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
                if self.shadow:
                    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
                    shadow_draw = ImageDraw.Draw(shadow)
                    shadow_draw.rounded_rectangle(
                        (2 * scale, 4 * scale, (width - 2) * scale, (height - 1) * scale),
                        radius=self.radius * scale,
                        fill=self._rgba(fill, 66),
                    )
                    if ImageFilter is not None:
                        shadow = shadow.filter(ImageFilter.GaussianBlur(3 * scale))
                    image.alpha_composite(shadow)

                draw = ImageDraw.Draw(image)
                box = (0, 0, (width - 1) * scale, (height - 1) * scale)
                draw.rounded_rectangle(
                    box,
                    radius=self.radius * scale,
                    fill=self._rgba(fill, 255),
                    outline=self._rgba(self.outline, 255) if self.outline else None,
                    width=scale if self.outline else 1,
                )
                image = image.resize((width, height), Image.Resampling.LANCZOS)
                self._button_image = ImageTk.PhotoImage(image)
                self.create_image(0, 0, anchor="nw", image=self._button_image)
            except Exception:
                self._draw_canvas_button(width, height, fill)
        else:
            self._draw_canvas_button(width, height, fill)
        self.create_text(width // 2, height // 2 - 1, text=self.text, fill=foreground, font=self.font, anchor="center")

    def _draw_canvas_button(self, width: int, height: int, fill: str) -> None:
        radius = self.radius
        self.create_polygon(
            radius, 1, width - radius, 1, width - 1, 1, width - 1, radius,
            width - 1, height - radius, width - 1, height - 1, width - radius, height - 1,
            radius, height - 1, 1, height - 1, 1, height - radius,
            1, radius, 1, 1, radius, 1,
            smooth=True,
            splinesteps=18,
            fill=fill,
            outline=self.outline,
        )

    def _rgba(self, color: str, alpha: int) -> tuple[int, int, int, int]:
        try:
            red, green, blue = self.winfo_rgb(color)
        except tk.TclError:
            red, green, blue = 65535, 65535, 65535
        return red // 256, green // 256, blue // 256, alpha


class ProgressRing(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        text: str = "待机",
        caption: str = "未开始",
        background: str = GLASS,
        size: int = 112,
        value: float = 0.0,
        color: str = ACCENT,
    ) -> None:
        super().__init__(parent, width=size, height=size, bg=background, highlightthickness=0, borderwidth=0)
        self.size = size
        self.text = text
        self.caption = caption
        self.background = background
        self.value = max(0.0, min(1.0, value))
        self.color = color
        self._ring_image: object | None = None
        self.bind("<Configure>", lambda _event: self._redraw())
        self._redraw()

    def set_state(self, *, text: str, caption: str, value: float | None = None, color: str | None = None) -> None:
        self.text = text
        self.caption = caption
        if value is not None:
            self.value = max(0.0, min(1.0, value))
        if color is not None:
            self.color = color
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        size = max(96, min(max(self.winfo_width(), self.winfo_height()), 132))
        if Image is not None and ImageDraw is not None and ImageTk is not None:
            try:
                scale = 3
                image = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
                draw = ImageDraw.Draw(image)
                pad = 10 * scale
                width = 10 * scale
                box = (pad, pad, size * scale - pad, size * scale - pad)
                draw.ellipse(box, outline=self._rgba("#e5edf7", 255), width=width)
                extent = max(0.0, min(1.0, self.value)) * 360
                if extent > 0:
                    draw.arc(box, start=-90, end=-90 + extent, fill=self._rgba(self.color, 255), width=width)
                else:
                    draw.arc(box, start=-90, end=-62, fill=self._rgba(ACCENT, 180), width=width)
                image = image.resize((size, size), Image.Resampling.LANCZOS)
                self._ring_image = ImageTk.PhotoImage(image)
                self.create_image((self.winfo_width() or size) // 2, (self.winfo_height() or size) // 2, anchor="center", image=self._ring_image)
            except Exception:
                self._draw_canvas_ring(size)
        else:
            self._draw_canvas_ring(size)
        cx = (self.winfo_width() or size) // 2
        cy = (self.winfo_height() or size) // 2
        self.create_text(cx, cy - 7, text=self.text, fill=TEXT, font=("Microsoft YaHei UI", 15, "bold"), anchor="center")
        self.create_text(cx, cy + 18, text=self.caption, fill=MUTED, font=("Microsoft YaHei UI", 8), anchor="center")

    def _draw_canvas_ring(self, size: int) -> None:
        cx = (self.winfo_width() or size) // 2
        cy = (self.winfo_height() or size) // 2
        radius = size // 2 - 12
        self.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, outline="#e5edf7", width=10)
        extent = max(0.0, min(1.0, self.value)) * 360
        if extent > 0:
            self.create_arc(cx - radius, cy - radius, cx + radius, cy + radius, start=90 - extent, extent=extent, outline=self.color, width=10, style="arc")
        else:
            self.create_arc(cx - radius, cy - radius, cx + radius, cy + radius, start=62, extent=28, outline=ACCENT, width=10, style="arc")

    def _rgba(self, color: str, alpha: int) -> tuple[int, int, int, int]:
        try:
            red, green, blue = self.winfo_rgb(color)
        except tk.TclError:
            red, green, blue = 65535, 65535, 65535
        return red // 256, green // 256, blue // 256, alpha


class NumberInput(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        variable: tk.IntVar,
        *,
        minimum: int,
        maximum: int,
        background: str = SURFACE,
        width: int = 112,
    ) -> None:
        super().__init__(parent, width=width, height=38, bg=background, highlightthickness=0, borderwidth=0)
        self.variable = variable
        self.minimum = minimum
        self.maximum = maximum
        self.pack_propagate(False)
        self.grid_propagate(False)

        panel = RoundedPanel(self, fill=SOFT_SURFACE, background=background, radius=18, padding=(4, 4), min_height=38, outline=BORDER, shadow=False, auto_height=False)
        panel.pack(fill="both", expand=True)
        panel.inner.columnconfigure(1, weight=1)

        PillButton(panel.inner, "−", lambda: self._set_value(self._current_value() - 1), fill=SOFT_SURFACE, foreground=MUTED, active_fill=SECONDARY_ACTIVE, height=28, width=34, font=("Microsoft YaHei UI", 13, "bold")).grid(row=0, column=0, sticky="nsw")
        self.entry = tk.Entry(
            panel.inner,
            textvariable=self.variable,
            justify="center",
            borderwidth=0,
            relief="flat",
            bg=SOFT_SURFACE,
            fg=TEXT,
            insertbackground=TEXT,
            font=("Microsoft YaHei UI", 11, "bold"),
            width=4,
        )
        self.entry.grid(row=0, column=1, sticky="nsew", padx=4)
        PillButton(panel.inner, "+", lambda: self._set_value(self._current_value() + 1), fill=SOFT_SURFACE, foreground=MUTED, active_fill=SECONDARY_ACTIVE, height=28, width=34, font=("Microsoft YaHei UI", 13, "bold")).grid(row=0, column=2, sticky="nse")

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


class ToggleSwitch(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        checked: bool = False,
        background: str = HEADER_BG,
        width: int = 92,
        height: int = 30,
    ) -> None:
        super().__init__(parent, width=width, height=height, bg=background, highlightthickness=0, borderwidth=0, cursor="hand2")
        self.text = text
        self.command = command
        self.checked = checked
        self.width = width
        self.height = height
        self._image: object | None = None
        self.bind("<Button-1>", lambda _event: self.command())
        self.bind("<Configure>", lambda _event: self._draw())
        self._draw()

    def set_checked(self, checked: bool, text: str | None = None) -> None:
        self.checked = checked
        if text is not None:
            self.text = text
        self._draw()

    def set_appearance(self, *, text: str, fill: str, foreground: str = "#ffffff", active_fill: str | None = None) -> None:
        del fill, foreground, active_fill
        self.set_checked("开启" in text, text)

    def _draw(self) -> None:
        self.delete("all")
        width = max(self.width, self.winfo_width())
        height = max(self.height, self.winfo_height())
        track = ACCENT_SOFT if self.checked else SECONDARY
        knob = ACCENT if self.checked else FAINT
        text_color = ACCENT if self.checked else MUTED
        if Image is not None and ImageDraw is not None and ImageTk is not None:
            try:
                scale = 3
                image = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
                draw = ImageDraw.Draw(image)
                draw.rounded_rectangle(
                    (0, 0, (width - 1) * scale, (height - 1) * scale),
                    radius=(height // 2) * scale,
                    fill=self._rgba(track, 255),
                    outline=self._rgba(ACCENT_BORDER if self.checked else BORDER, 255),
                    width=scale,
                )
                knob_size = (height - 10) * scale
                knob_left = (width - height + 5) * scale if self.checked else 5 * scale
                knob_top = 5 * scale
                draw.ellipse((knob_left, knob_top, knob_left + knob_size, knob_top + knob_size), fill=self._rgba(knob, 255))
                image = image.resize((width, height), Image.Resampling.LANCZOS)
                self._image = ImageTk.PhotoImage(image)
                self.create_image(0, 0, anchor="nw", image=self._image)
            except Exception:
                self.create_oval(5, 5, height - 5, height - 5, fill=knob, outline="")
        else:
            self.create_oval(5, 5, height - 5, height - 5, fill=knob, outline="")
        text_x = 36 if self.checked else 58
        self.create_text(text_x, height // 2 - 1, text=self.text, fill=text_color, font=("Microsoft YaHei UI", 9, "bold"), anchor="center")

    def _rgba(self, color: str, alpha: int) -> tuple[int, int, int, int]:
        try:
            red, green, blue = self.winfo_rgb(color)
        except tk.TclError:
            red, green, blue = 65535, 65535, 65535
        return red // 256, green // 256, blue // 256, alpha


class WatchStatusCard(tk.Frame):
    """右栏后台计时状态卡。默认用色块概览每路状态，弹窗明细保留为备用。"""

    STATE_COLORS = {
        "正常": SUCCESS,
        "计时中": "#f59e0b",
        "启动中": "#f59e0b",
        "等待开播": MUTED,
        "暂时失败": DANGER,
    }
    STATE_LABELS = (
        ("正常", SUCCESS),
        ("启动/计时", "#f59e0b"),
        ("等待", MUTED),
        ("失败", DANGER),
    )
    STATE_TAGS = {
        "正常": "normal",
        "计时中": "warning",
        "启动中": "warning",
        "等待开播": "muted",
        "暂时失败": "danger",
    }

    def __init__(self, parent: tk.Misc, *, background: str = APP_BG) -> None:
        super().__init__(parent, bg=background, highlightthickness=0, borderwidth=0)
        self._background = background
        self.summary_var = tk.StringVar(value="后台计时状态：未启动")
        self._snapshot: list[WatchWorkerStatus] = []
        self._detail_window: tk.Toplevel | None = None
        self._detail_text: tk.Text | None = None

        inner = self
        inner.columnconfigure(0, weight=1)

        tk.Label(inner, text="后台计时状态", bg=background, fg=TEXT, font=("Microsoft YaHei UI", 12, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(inner, textvariable=self.summary_var, bg=background, fg=MUTED, font=("Microsoft YaHei UI", 9), wraplength=520, justify="left").grid(row=1, column=0, sticky="ew", pady=(4, 8))
        self._status_grid = tk.Frame(inner, bg=background, highlightthickness=0, borderwidth=0)
        self._status_grid.grid(row=2, column=0, sticky="ew")
        self._legend = tk.Frame(inner, bg=background, highlightthickness=0, borderwidth=0)
        self._legend.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self._build_legend()
        self._render_status_grid()

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
        self._render_status_grid()
        if self._detail_window is not None and self._detail_window.winfo_exists():
            self._render_detail()

    def _build_legend(self) -> None:
        for child in self._legend.winfo_children():
            child.destroy()
        for index, (label, color) in enumerate(self.STATE_LABELS):
            item = tk.Frame(self._legend, bg=self._background, highlightthickness=0, borderwidth=0)
            item.grid(row=0, column=index, sticky="w", padx=(0, 12))
            swatch = tk.Canvas(item, width=10, height=10, bg=self._background, highlightthickness=0, borderwidth=0)
            swatch.grid(row=0, column=0, sticky="w", padx=(0, 4))
            swatch.create_rectangle(1, 1, 9, 9, fill=color, outline=color)
            tk.Label(item, text=label, bg=self._background, fg=MUTED, font=("Microsoft YaHei UI", 8)).grid(row=0, column=1, sticky="w")

    def _render_status_grid(self) -> None:
        for child in self._status_grid.winfo_children():
            child.destroy()
        if not self._snapshot:
            self._legend.grid_remove()
            tk.Label(
                self._status_grid,
                text="开始后显示每路状态",
                bg=SOFT_SURFACE,
                fg=MUTED,
                anchor="w",
                padx=8,
                pady=6,
                font=("Microsoft YaHei UI", 9),
            ).grid(row=0, column=0, sticky="ew")
            self._status_grid.columnconfigure(0, weight=1)
            return
        self._legend.grid()
        max_cols = 20 if len(self._snapshot) > 40 else 12
        for index, status in enumerate(self._snapshot):
            row, column = divmod(index, max_cols)
            color = self.STATE_COLORS.get(status.state, MUTED)
            cell = tk.Canvas(self._status_grid, width=14, height=14, bg=self._background, highlightthickness=0, borderwidth=0)
            cell.grid(row=row, column=column, padx=2, pady=2)
            cell.create_rectangle(2, 2, 12, 12, fill=color, outline=color)

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
            line = f"{label}  | {status.state:<5} {detail}\n"
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
    def __init__(self, *, preview_mode: bool = False) -> None:
        super().__init__()
        self.title(f"守望先锋 B 站直播挂宝 v{__version__}")
        self.geometry("1280x840")
        self.minsize(980, 720)
        self.configure(bg=APP_BG)
        self._set_window_icon()

        self.preview_mode = preview_mode
        self.config_data = (
            AppConfig(room_id=DEFAULT_ROOM_ID, auto_claim=False, watch_threads=1)
            if preview_mode
            else load_config()
        )
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.watcher: LiveWatcher | MultiAccountWatcher | None = None
        self.cookie_capture_thread: threading.Thread | None = None
        self.progress_events: list[str] = []
        self.progress_snapshot = ""
        self.notification_history: dict[str, float] = {}
        self.notification_failure_history: dict[str, float] = {}
        self.notification_pending: set[str] = set()
        self._ui_images: list[object] = []

        self.cookie_var = tk.StringVar(value=self.config_data.cookie)
        self.selected_account_var = tk.StringVar(value=self.config_data.account_name)
        self.account_name_var = tk.StringVar(value=self.config_data.account_name)
        self.account_checks: dict[str, tk.BooleanVar] = {}
        self.notify_url_var = tk.StringVar(value=self.config_data.notify_url)
        self.room_var = tk.StringVar(value=self.config_data.room_id)
        self.interval_var = tk.IntVar(value=self.config_data.check_interval)
        self.auto_claim_var = tk.BooleanVar(value=self.config_data.auto_claim)
        self.auto_scroll_var = tk.BooleanVar(value=True)
        self.watch_threads_var = tk.IntVar(value=self.config_data.watch_threads)
        self.status_var = tk.StringVar(value="未运行")
        self.room_status_var = tk.StringVar(value=f"直播间：{self.config_data.room_id or '未填写'}")
        self.room_hint_var = tk.StringVar(value=self._room_hint_text(self.config_data.room_id))
        self.task_id_status_var = tk.StringVar(value="任务 ID：按直播间自动获取")
        self.credential_status_var = tk.StringVar(value=f"凭据：{'已填写' if self.config_data.cookie else '未填写'}")
        self.cookie_validation_var = tk.StringVar(value=f"Cookie {'未填写' if not self.config_data.cookie else '已填写'}")
        self.elapsed_status_var = tk.StringVar(value="计时：未开始")
        self.reward_status_var = tk.StringVar(value="领奖：未开始")
        self.status_hint_var = tk.StringVar(value="点击「开始挂宝」启动")
        self.progress_title_var = tk.StringVar(value="未开始")
        self.progress_detail_var = tk.StringVar(value="开始后显示观看分钟数")
        self.started_at: datetime | None = None
        self.backend_start_var = tk.StringVar(value="启动后更新")
        self.backend_elapsed_var = tk.StringVar(value="启动后更新")
        self.backend_next_var = tk.StringVar(value="启动后更新")
        self.backend_network_var = tk.StringVar(value="未启动")
        self.reward_title_var = tk.StringVar(value="待检查")
        self.reward_detail_var = tk.StringVar(value="开始后显示可领取次数")
        self.advanced_visible_var = tk.BooleanVar(value=True)
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
        style.configure("Body.TLabel", background=APP_BG, foreground=TEXT)
        style.configure("Muted.TLabel", background=APP_BG, foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("RailMuted.TLabel", background=APP_BG, foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("RailTitle.TLabel", background=APP_BG, foreground=TEXT, font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("StepItemTitle.TLabel", background=SOFT_SURFACE, foreground=TEXT, font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("StepItemText.TLabel", background=SOFT_SURFACE, foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("PageTitle.TLabel", background=APP_BG, foreground=TEXT, font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("PageSubtitle.TLabel", background=APP_BG, foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("FreeNoticeTitle.TLabel", background=WARNING_BG, foreground="#9a3412", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("FreeNoticeBody.TLabel", background=WARNING_BG, foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("FreeNoticeLink.TLabel", background=WARNING_BG, foreground="#2563eb", font=("Microsoft YaHei UI", 9, "underline"))
        style.configure("Eyebrow.TLabel", background=APP_BG, foreground=MUTED, font=("Microsoft YaHei UI", 8, "bold"))
        style.configure("SectionTitle.TLabel", background=APP_BG, foreground=TEXT, font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("StepTitle.TLabel", background=SOFT_SURFACE, foreground=ACCENT, font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("StepText.TLabel", background=SOFT_SURFACE, foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("Status.TLabel", background=HEADER_BG, foreground=MUTED, padding=(0, 0), font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("StatusRunning.TLabel", background=HEADER_BG, foreground=SUCCESS, padding=(0, 0), font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Version.TLabel", background=APP_BG, foreground=MUTED, padding=(0, 0), font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TEntry", padding=(10, 8), fieldbackground=SOFT_SURFACE, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.configure("TSpinbox", padding=(10, 8), fieldbackground=SOFT_SURFACE, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.configure("TCombobox", padding=(8, 7), fieldbackground=SOFT_SURFACE, background=SOFT_SURFACE, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, arrowcolor=MUTED)
        style.map("TCombobox", fieldbackground=[("readonly", SOFT_SURFACE)], selectbackground=[("readonly", SOFT_SURFACE)], selectforeground=[("readonly", TEXT)])
        style.configure("TCheckbutton", background=SURFACE, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", SURFACE)])
        try:
            style.layout(
                "Vertical.TScrollbar",
                [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"unit": "1", "sticky": "nswe"})]})],
            )
        except tk.TclError:
            pass
        style.configure(
            "Vertical.TScrollbar",
            width=8,
            gripcount=0,
            background="#d4deeb",
            troughcolor=FIELD_BG,
            bordercolor=FIELD_BG,
            lightcolor=FIELD_BG,
            darkcolor=FIELD_BG,
            arrowcolor=FIELD_BG,
            relief="flat",
            borderwidth=0,
        )

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        commandbar = tk.Frame(self, bg=HEADER_BG, height=142, highlightthickness=0, borderwidth=0)
        commandbar.grid(row=0, column=0, sticky="ew")
        commandbar.grid_propagate(False)
        commandbar.columnconfigure(0, weight=0, minsize=300)
        commandbar.columnconfigure(1, weight=1)
        commandbar.columnconfigure(2, weight=0)

        brand = tk.Frame(commandbar, bg=HEADER_BG, highlightthickness=0, borderwidth=0)
        brand.grid(row=0, column=0, sticky="w", padx=(22, 16), pady=(24, 0))
        logo = tk.Canvas(brand, width=44, height=44, bg=HEADER_BG, highlightthickness=0, borderwidth=0)
        logo.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 12))
        self._draw_brand_logo(logo)
        tk.Label(brand, text="守望先锋 B站直播挂宝", bg=HEADER_BG, fg=TEXT, font=("Microsoft YaHei UI", 18, "bold")).grid(row=0, column=1, sticky="w")
        sub = tk.Frame(brand, bg=HEADER_BG, highlightthickness=0, borderwidth=0)
        sub.grid(row=1, column=1, sticky="w", pady=(2, 0))
        tk.Label(sub, text="Bilibili Drops Helper", bg=HEADER_BG, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left")
        tk.Label(sub, textvariable=self.version_var, bg="#edf3fb", fg=TEXT, font=("Microsoft YaHei UI", 8, "bold"), padx=10, pady=2).pack(side="left", padx=(12, 0))

        controls = tk.Frame(commandbar, bg=HEADER_BG, highlightthickness=0, borderwidth=0)
        controls.grid(row=0, column=1, sticky="ew", pady=(22, 0))
        controls.columnconfigure(0, weight=1, minsize=300)
        controls.columnconfigure(1, weight=0, minsize=102)
        controls.columnconfigure(2, weight=0, minsize=86)
        controls.columnconfigure(3, weight=0, minsize=220)
        controls.columnconfigure(4, weight=0, minsize=86)
        controls.rowconfigure(0, minsize=22)
        controls.rowconfigure(1, minsize=46)

        label_font = ("Microsoft YaHei UI", 9, "bold")
        label_pady = (0, 6)
        tk.Label(controls, text="直播间房号", bg=HEADER_BG, fg=TEXT, font=label_font).grid(row=0, column=0, sticky="sw", padx=(0, 22), pady=label_pady)
        tk.Label(controls, text="观看线程", bg=HEADER_BG, fg=TEXT, font=label_font).grid(row=0, column=1, sticky="sw", padx=(0, 22), pady=label_pady)
        tk.Label(controls, text="自动领取", bg=HEADER_BG, fg=TEXT, font=label_font).grid(row=0, column=2, sticky="sw", padx=(0, 24), pady=label_pady)
        tk.Label(controls, text="操作", bg=HEADER_BG, fg=TEXT, font=label_font).grid(row=0, column=3, sticky="sw", padx=(0, 14), pady=label_pady)
        tk.Label(controls, text="状态", bg=HEADER_BG, fg=TEXT, font=label_font).grid(row=0, column=4, sticky="sw", pady=label_pady)

        room_box = RoundedPanel(controls, fill=HEADER_INPUT, background=HEADER_BG, radius=12, padding=(14, 7), min_height=44, outline=SUBTLE_OUTLINE, shadow=True, auto_height=False)
        room_box.grid(row=1, column=0, sticky="ew", padx=(0, 22))
        room_box.inner.columnconfigure(0, weight=1)
        self.room_entry = tk.Entry(room_box.inner, textvariable=self.room_var, borderwidth=0, relief="flat", bg=HEADER_INPUT, fg=TEXT, insertbackground=TEXT, font=("Microsoft YaHei UI", 11))
        self.room_entry.grid(row=0, column=0, sticky="ew")
        self.room_placeholder = tk.Label(room_box.inner, text="填写直播间房号", bg=HEADER_INPUT, fg=FAINT, font=("Microsoft YaHei UI", 10))
        self.room_placeholder.grid(row=0, column=0, sticky="w")
        self.room_placeholder.bind("<Button-1>", lambda _event: self.room_entry.focus_set())
        self.room_entry.bind("<FocusIn>", lambda _event: self._refresh_room_placeholder())
        self.room_entry.bind("<FocusOut>", lambda _event: self._refresh_room_placeholder())
        self.room_var.trace_add("write", lambda *_args: self._refresh_room_placeholder())
        LabelButton(room_box.inner, "粘贴", self._paste_room_id, fill=SECONDARY, foreground=TEXT, active_fill=SECONDARY_ACTIVE, height=30, width=62, font=("Microsoft YaHei UI", 9, "bold")).grid(row=0, column=1, sticky="e", padx=(10, 0))
        tk.Label(controls, textvariable=self.room_hint_var, bg=HEADER_BG, fg=HEADER_MUTED, font=("Microsoft YaHei UI", 8)).grid(row=2, column=0, sticky="w", padx=(0, 22), pady=(6, 0))

        NumberInput(controls, self.watch_threads_var, minimum=1, maximum=MAX_WATCH_THREADS, background=HEADER_BG, width=116).grid(row=1, column=1, sticky="nw", padx=(0, 22))

        self.auto_claim_button = ToggleSwitch(controls, "已关闭", self._toggle_auto_claim, checked=bool(self.auto_claim_var.get()), background=HEADER_BG, width=86, height=30)
        self.auto_claim_button.grid(row=1, column=2, sticky="nw", padx=(0, 24), pady=(7, 0))

        actions = tk.Frame(controls, bg=HEADER_BG, highlightthickness=0, borderwidth=0)
        actions.grid(row=1, column=3, sticky="nw", padx=(0, 14))
        self.start_button = LabelButton(actions, "▶ 开始挂宝", self._toggle_run, fill=PRIMARY, foreground="#ffffff", active_fill=PRIMARY_ACTIVE, height=44, width=138, font=("Microsoft YaHei UI", 10, "bold"), radius=14, shadow=True)
        self.start_button.pack(side="left", padx=(0, 8))
        LabelButton(actions, "领取奖励", self._claim, fill=SURFACE, foreground=TEXT, active_fill=SECONDARY_ACTIVE, height=44, width=104, font=("Microsoft YaHei UI", 9, "bold"), radius=14, outline=SUBTLE_OUTLINE).pack(side="left")

        status_card = RoundedPanel(controls, fill="#f6f9fd", background=HEADER_BG, radius=16, padding=(12, 5), min_height=44, outline=SUBTLE_OUTLINE, shadow=False, auto_height=False)
        status_card.configure(width=96)
        status_card.grid(row=1, column=4, sticky="nw")
        status_inner = status_card.inner
        status_inner.columnconfigure(1, weight=1)
        self._status_dot(status_inner, color=FAINT, background="#f6f9fd", size=7).grid(row=0, column=0, sticky="w", padx=(0, 8))
        tk.Label(status_inner, textvariable=self.status_var, bg="#f6f9fd", fg=TEXT, font=("Microsoft YaHei UI", 9, "bold")).grid(row=0, column=1, sticky="w")

        body = tk.Frame(self, bg=APP_BG, highlightthickness=0, borderwidth=0)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1, minsize=360)
        body.columnconfigure(1, weight=2, minsize=560)
        body.rowconfigure(0, weight=1)

        side = tk.Frame(body, bg=APP_BG, highlightthickness=0, borderwidth=0)
        side.grid(row=0, column=0, sticky="nsew", padx=(18, 8), pady=(0, 14))
        side.columnconfigure(0, weight=1)

        work = tk.Frame(body, bg=APP_BG, highlightthickness=0, borderwidth=0)
        work.grid(row=0, column=1, sticky="nsew", padx=(0, 18), pady=(0, 14))
        work.columnconfigure(0, weight=1)
        work.rowconfigure(0, weight=1)

        self._build_settings_workspace(side)
        self._build_monitor_workspace(work)
        self._refresh_auto_claim_button()

        statusbar = tk.Frame(self, bg=HEADER_BG, height=48, highlightthickness=0, borderwidth=0)
        statusbar.grid(row=2, column=0, sticky="ew")
        statusbar.grid_propagate(False)
        statusbar.columnconfigure(1, weight=1)
        status_left = tk.Frame(statusbar, bg=HEADER_BG, highlightthickness=0, borderwidth=0)
        status_left.grid(row=0, column=0, sticky="w", padx=28)
        self._computer_icon(status_left, background=HEADER_BG).pack(side="left", padx=(0, 8))
        tk.Label(status_left, text="本地模式", bg=HEADER_BG, fg=MUTED, font=("Microsoft YaHei UI", 9, "bold")).pack(side="left", padx=(0, 8))
        self._status_dot(status_left, color=SUCCESS, background=HEADER_BG, size=8).pack(side="left", padx=(0, 16))
        tk.Label(status_left, textvariable=self.room_status_var, bg=HEADER_BG, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(0, 14))
        tk.Label(status_left, textvariable=self.task_id_status_var, bg=HEADER_BG, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(0, 14))
        tk.Label(status_left, textvariable=self.version_var, bg=HEADER_BG, fg=FAINT, font=("Microsoft YaHei UI", 9)).pack(side="left")

        status_right = tk.Frame(statusbar, bg=HEADER_BG, highlightthickness=0, borderwidth=0)
        status_right.grid(row=0, column=1, sticky="e", padx=28)
        tk.Label(status_right, text="本软件完全免费，请勿购买；购买请找商家退款。", bg=HEADER_BG, fg="#a97412", font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(0, 16))
        self._footer_link(status_right, "开源地址", self._open_source_url, icon="source", accent=True).pack(side="left", padx=(0, 14))
        self._footer_link(status_right, "帮助", self._show_onboarding_guide, icon="help").pack(side="left", padx=(0, 14))
        self._footer_link(status_right, "关于", self._show_about_dialog, icon="about").pack(side="left")
        self.after_idle(self._refresh_room_placeholder)

    def _build_step_strip(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="流程", style="RailTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            parent,
            text="获取 Cookie、确认直播间、开始挂宝、领取奖励",
            style="RailMuted.TLabel",
            wraplength=880,
        ).grid(row=0, column=1, columnspan=3, sticky="ew", padx=(16, 0))

    def _build_settings_workspace(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)

        credential_panel = RoundedPanel(parent, fill=GLASS, background=APP_BG, radius=18, padding=(20, 16), min_height=640, outline=SUBTLE_OUTLINE, shadow=True, auto_height=False)
        credential_panel.grid(row=0, column=0, sticky="nsew")
        cookie = credential_panel.inner
        cookie.columnconfigure(0, weight=1)
        cookie.rowconfigure(3, weight=1)

        self._section_title(cookie, "登录凭据", "credential").grid(row=0, column=0, sticky="w")
        tk.Label(cookie, text="选择账号，获取或粘贴登录 Cookie。", bg=GLASS, fg=MUTED, font=("Microsoft YaHei UI", 9), wraplength=390, justify="left").grid(row=1, column=0, sticky="w", pady=(4, 8))

        account_panel = RoundedPanel(cookie, fill=FIELD_BG, background=GLASS, radius=14, padding=(14, 10), min_height=150, outline=SUBTLE_OUTLINE, shadow=False, auto_height=True)
        account_panel.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        account = account_panel.inner
        account.columnconfigure(0, weight=1)

        list_header = tk.Frame(account, bg=FIELD_BG, highlightthickness=0, borderwidth=0)
        list_header.grid(row=0, column=0, sticky="ew")
        list_header.columnconfigure(0, weight=1)
        tk.Label(list_header, text="参与挂机账号", bg=FIELD_BG, fg=TEXT, font=("Microsoft YaHei UI", 11, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(list_header, text="勾选要挂的账号", bg=FIELD_BG, fg=MUTED, font=("Microsoft YaHei UI", 8)).grid(row=0, column=1, sticky="e")
        self._account_check_frame = tk.Frame(account, bg=FIELD_BG, highlightthickness=0, borderwidth=0)
        self._account_check_frame.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self._build_account_checklist()

        tk.Frame(account, bg="#e6edf4", height=1, highlightthickness=0, borderwidth=0).grid(row=2, column=0, sticky="ew", pady=(12, 10))

        tk.Label(account, text="当前编辑账号", bg=FIELD_BG, fg=MUTED, font=("Microsoft YaHei UI", 9, "bold")).grid(row=3, column=0, sticky="w")
        account_entry_box = RoundedPanel(account, fill=SURFACE, background=FIELD_BG, radius=10, padding=(12, 8), min_height=36, outline=SUBTLE_OUTLINE, shadow=False, auto_height=False)
        account_entry_box.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        account_entry_box.inner.columnconfigure(0, weight=1)
        tk.Entry(account_entry_box.inner, textvariable=self.account_name_var, borderwidth=0, relief="flat", bg=SURFACE, fg=TEXT, insertbackground=TEXT, font=("Microsoft YaHei UI", 10)).grid(row=0, column=0, sticky="ew")

        flow = tk.Frame(cookie, bg=GLASS, highlightthickness=0, borderwidth=0)
        flow.grid(row=3, column=0, sticky="nsew")
        flow.columnconfigure(0, weight=1)
        flow.rowconfigure(11, weight=1)

        capture_header = tk.Frame(flow, bg=GLASS, highlightthickness=0, borderwidth=0)
        capture_header.grid(row=0, column=0, sticky="ew")
        capture_header.columnconfigure(0, weight=1)
        tk.Label(capture_header, text="主要操作", bg=GLASS, fg=TEXT, font=("Microsoft YaHei UI", 12, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(capture_header, text="推荐", bg=GLASS, fg=ACCENT, font=("Microsoft YaHei UI", 8, "bold")).grid(row=0, column=1, sticky="e")
        tk.Label(flow, text="打开独立自动获取窗口；登录后会自动写入下方 Cookie。", bg=GLASS, fg=MUTED, font=("Microsoft YaHei UI", 9), wraplength=360, justify="left").grid(row=1, column=0, sticky="w", pady=(3, 7))
        LabelButton(flow, "自动获取 Cookie", self._capture_cookie, fill=ACCENT, foreground="#ffffff", active_fill=ACCENT_ACTIVE, height=40, font=("Microsoft YaHei UI", 10, "bold"), radius=13, shadow=True).grid(row=2, column=0, sticky="ew")

        login_header = tk.Frame(flow, bg=GLASS, highlightthickness=0, borderwidth=0)
        login_header.grid(row=3, column=0, sticky="ew", pady=(9, 0))
        login_header.columnconfigure(0, weight=1)
        tk.Label(login_header, text="备用方式", bg=GLASS, fg=MUTED, font=("Microsoft YaHei UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        LabelButton(flow, "只打开登录页（手动）", self._open_cookie_login_page, fill=SURFACE, foreground=TEXT, active_fill=SECONDARY_ACTIVE, height=34, font=("Microsoft YaHei UI", 9, "bold"), radius=11, outline=BUTTON_OUTLINE).grid(row=4, column=0, sticky="ew", pady=(6, 0))

        self._soft_divider(flow).grid(row=5, column=0, sticky="ew", pady=8)

        cookie_header = tk.Frame(flow, bg=GLASS, highlightthickness=0, borderwidth=0)
        cookie_header.grid(row=6, column=0, sticky="ew")
        cookie_header.columnconfigure(0, weight=1)
        tk.Label(cookie_header, text="Cookie 内容", bg=GLASS, fg=TEXT, font=("Microsoft YaHei UI", 12, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(cookie_header, textvariable=self.cookie_validation_var, bg=GLASS, fg=MUTED, font=("Microsoft YaHei UI", 8, "bold")).grid(row=0, column=1, sticky="e")
        tk.Label(flow, text="读取成功后自动填入，可手动修改。", bg=GLASS, fg=MUTED, font=("Microsoft YaHei UI", 9)).grid(row=7, column=0, sticky="w", pady=(3, 6))

        cookie_box = RoundedPanel(flow, fill=FIELD_BG, background=GLASS, radius=14, padding=(5, 5), min_height=112, outline=SUBTLE_OUTLINE, shadow=False, auto_height=False)
        cookie_box.grid(row=8, column=0, sticky="ew")
        cookie_box.inner.columnconfigure(0, weight=1)
        cookie_box.inner.rowconfigure(0, weight=1)
        self.cookie_text = tk.Text(cookie_box.inner, height=4, wrap="word", undo=True, borderwidth=0, relief="flat", bg=FIELD_BG, fg=TEXT, insertbackground=TEXT, highlightthickness=0, padx=12, pady=8, font=("Consolas", 9))
        self.cookie_text.grid(row=0, column=0, sticky="nsew")
        self.cookie_text.insert("1.0", self.cookie_var.get())
        self.cookie_empty_label = tk.Label(cookie_box.inner, text="等待 Cookie 写入", bg=FIELD_BG, fg=FAINT, font=("Microsoft YaHei UI", 10, "bold"))
        self.cookie_empty_label.place(relx=0.5, rely=0.5, anchor="center")
        self.cookie_text.bind("<KeyRelease>", lambda _event: self._refresh_cookie_placeholder())
        self.cookie_text.bind("<FocusOut>", lambda _event: self._refresh_cookie_placeholder())
        self.after_idle(self._refresh_cookie_placeholder)

        cookie_actions = tk.Frame(flow, bg=GLASS, highlightthickness=0, borderwidth=0)
        cookie_actions.grid(row=9, column=0, sticky="ew", pady=(10, 0))
        cookie_actions.columnconfigure((0, 1), weight=1, uniform="cookie_actions")
        cookie_actions.columnconfigure((2, 3), weight=0)
        LabelButton(cookie_actions, "保存账号", self._save_account, fill=SECONDARY, foreground=TEXT, active_fill=SECONDARY_ACTIVE, height=34, font=("Microsoft YaHei UI", 9, "bold"), radius=11, outline=SUBTLE_OUTLINE).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        LabelButton(cookie_actions, "新增账号", self._new_account, fill=ACCENT_SOFT, foreground=ACCENT, active_fill=ACCENT_SOFT_ACTIVE, height=34, font=("Microsoft YaHei UI", 9, "bold"), radius=11, outline="").grid(row=0, column=1, sticky="ew", padx=(0, 8))
        LabelButton(cookie_actions, "验证", self._validate_cookie_text, fill=GLASS, foreground=MUTED, active_fill=SECONDARY_ACTIVE, height=34, width=58, font=("Microsoft YaHei UI", 8, "bold"), radius=11, outline="").grid(row=0, column=2, sticky="e", padx=(0, 4))
        LabelButton(cookie_actions, "清空", self._clear_cookie_text, fill=GLASS, foreground=MUTED, active_fill=SECONDARY_ACTIVE, height=34, width=58, font=("Microsoft YaHei UI", 8, "bold"), radius=11, outline="").grid(row=0, column=3, sticky="e")

        notice = RoundedPanel(cookie, fill=WARNING_BG, background=GLASS, radius=12, padding=(12, 8), min_height=38, outline=WARNING_BORDER, shadow=False, auto_height=False)
        notice.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        tk.Label(notice.inner, text="本机保存凭据，不上传到任何服务器。", bg=WARNING_BG, fg="#9a6a10", font=("Microsoft YaHei UI", 9, "bold")).grid(row=0, column=0, sticky="w")

        hidden = tk.Frame(parent, bg=APP_BG, highlightthickness=0, borderwidth=0)
        hidden.grid(row=1, column=0, sticky="ew")
        task_ids_box = tk.Frame(hidden, bg=APP_BG)
        task_ids_box.grid(row=0, column=0)
        task_ids_box.columnconfigure(0, weight=1)
        self.task_ids_text = tk.Text(task_ids_box, height=1, width=1, wrap="word", undo=True, borderwidth=0, relief="flat", bg=APP_BG, fg=TEXT, insertbackground=TEXT, highlightthickness=0)
        self.task_ids_text.grid(row=0, column=0)
        self.task_ids_text.insert("1.0", self.config_data.task_ids)
        hidden.grid_remove()

    def _credential_flow_header(self, parent: tk.Misc, number: str, title: str, status: str, *, row: int) -> None:
        header = tk.Frame(parent, bg=GLASS, highlightthickness=0, borderwidth=0)
        header.grid(row=row, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        self._step_badge(header, number, active=number == "1").grid(row=0, column=0, sticky="w", padx=(0, 10))
        tk.Label(header, text=title, bg=GLASS, fg=TEXT, font=("Microsoft YaHei UI", 12, "bold")).grid(row=0, column=1, sticky="w")
        tk.Label(header, text=status, bg=GLASS, fg=FAINT, font=("Microsoft YaHei UI", 8, "bold")).grid(row=0, column=2, sticky="e")

    def _step_badge(self, parent: tk.Misc, number: str, *, active: bool = False) -> tk.Canvas:
        fill = ACCENT if active else "#dbe5f2"
        foreground = "#ffffff" if active else MUTED
        badge = tk.Canvas(parent, width=24, height=24, bg=GLASS, highlightthickness=0, borderwidth=0)
        badge.create_oval(1, 1, 23, 23, fill=fill, outline="")
        badge.create_text(12, 12, text=number, fill=foreground, font=("Microsoft YaHei UI", 8, "bold"))
        return badge

    def _soft_divider(self, parent: tk.Misc) -> tk.Frame:
        return tk.Frame(parent, bg="#eef3f8", height=1, highlightthickness=0, borderwidth=0)

    def _credential_step_chip(self, parent: tk.Misc, column: int, number: str, text: str, *, active: bool) -> tk.Misc:
        del column
        foreground = TEXT if active else MUTED
        step = tk.Frame(parent, bg=GLASS, highlightthickness=0, borderwidth=0)
        step.columnconfigure(1, weight=1)
        tk.Label(step, text=number, bg=GLASS, fg=MUTED, font=("Microsoft YaHei UI", 8), width=2).grid(row=0, column=0, sticky="w", padx=(0, 4))
        tk.Label(step, text=text, bg=GLASS, fg=foreground, font=("Microsoft YaHei UI", 8, "bold" if active else "normal")).grid(row=0, column=1, sticky="w")
        return step

    def _credential_step(self, parent: tk.Misc, column: int, number: str, text: str, *, active: bool) -> tk.Frame:
        bg = SURFACE if active else GLASS
        fg = ACCENT if active else MUTED
        frame = tk.Frame(parent, bg=bg, highlightthickness=0, borderwidth=0)
        dot = tk.Label(frame, text=number, bg=ACCENT if active else "#e7edf6", fg="#ffffff" if active else MUTED, font=("Microsoft YaHei UI", 8, "bold"), width=2)
        dot.pack(side="left", padx=(6, 5), pady=7)
        tk.Label(frame, text=text, bg=bg, fg=fg, font=("Microsoft YaHei UI", 8, "bold")).pack(side="left", padx=(0, 6))
        return frame


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
            padx=(0, 14),
        )
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)
        card.rowconfigure(7, weight=1)

        ttk.Label(card, text="并行账号（勾选要挂的）", style="Body.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Label(card, text="账号名称", style="Body.TLabel").grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        self._account_check_frame = tk.Frame(card, bg=APP_BG)
        self._account_check_frame.grid(row=3, column=0, sticky="new", pady=(6, 8), padx=(0, 8))
        self._build_account_checklist()
        account_box = RoundedPanel(card, fill=SOFT_SURFACE, background=APP_BG, radius=13, padding=(10, 6), min_height=36, outline=BORDER, shadow=False, auto_height=False)
        account_box.grid(row=3, column=1, sticky="new", pady=(6, 8), padx=(8, 0))
        account_box.inner.columnconfigure(0, weight=1)
        account_entry = tk.Entry(account_box.inner, textvariable=self.account_name_var, borderwidth=0, relief="flat", bg=SOFT_SURFACE, fg=TEXT, insertbackground=TEXT, font=("Microsoft YaHei UI", 10))
        account_entry.grid(row=0, column=0, sticky="ew")

        PillButton(card, "保存账号", self._save_account, fill=SOFT_SURFACE, foreground=TEXT, active_fill=SECONDARY_ACTIVE, height=34).grid(row=4, column=0, sticky="ew", pady=(0, 10), padx=(0, 8))
        PillButton(card, "删除账号", self._delete_account, fill=DANGER_BG, foreground=DANGER, active_fill="#ffe4e6", height=34).grid(row=4, column=1, sticky="ew", pady=(0, 10), padx=(8, 0))

        PillButton(card, "自动获取 Cookie", self._capture_cookie, fill=ACCENT, active_fill=ACCENT_ACTIVE).grid(row=5, column=0, sticky="ew", pady=(0, 10), padx=(0, 8))
        PillButton(card, "只打开登录页", self._open_cookie_login_page, fill=SOFT_SURFACE, foreground=TEXT, active_fill=SECONDARY_ACTIVE).grid(row=5, column=1, sticky="ew", pady=(0, 10), padx=(8, 0))

        ttk.Label(card, text="Cookie 内容", style="Body.TLabel").grid(row=6, column=0, columnspan=2, sticky="w")
        cookie_box = RoundedPanel(card, fill=SOFT_SURFACE, background=APP_BG, radius=14, padding=(4, 4), min_height=72, outline=BORDER, shadow=False)
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
            padx=(14, 0),
        )
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)

        ttk.Label(card, text="直播间号或链接", style="Body.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        room_box = RoundedPanel(card, fill=SOFT_SURFACE, background=APP_BG, radius=14, padding=(12, 7), min_height=48, outline=BORDER, shadow=False)
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

        task_ids_box = RoundedPanel(card, fill=SOFT_SURFACE, background=APP_BG, radius=14, padding=(4, 4), min_height=44, outline=BORDER, shadow=False)
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

        notify_box = RoundedPanel(card, fill=SOFT_SURFACE, background=APP_BG, radius=14, padding=(12, 7), min_height=44, outline=BORDER, shadow=False)
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

    def _surface_rule(self, parent: tk.Misc, row: int) -> None:
        rule = tk.Frame(parent, bg="#edf3fa", height=1, highlightthickness=0, borderwidth=0)
        rule.grid(row=row, column=0, sticky="ew")

    def _section_title(self, parent: tk.Misc, text: str, icon: str, *, background: str = GLASS) -> tk.Frame:
        del icon
        title = tk.Frame(parent, bg=background, highlightthickness=0, borderwidth=0)
        mark = tk.Frame(title, bg=ACCENT, width=3, height=15, highlightthickness=0, borderwidth=0)
        mark.pack(side="left", padx=(0, 9), pady=(3, 0))
        mark.pack_propagate(False)
        tk.Label(title, text=text, bg=background, fg=TEXT, font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
        return title

    def _draw_title_icon(self, canvas: tk.Canvas, icon: str) -> None:
        canvas.delete("all")
        if Image is not None and ImageDraw is not None and ImageTk is not None:
            try:
                scale = 4
                size = 24
                image = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
                draw = ImageDraw.Draw(image)
                blue = self._rgba_from_widget(canvas, ACCENT, 255)
                soft = self._rgba_from_widget(canvas, ACCENT_SOFT, 255)
                white = (255, 255, 255, 255)
                draw.rounded_rectangle(
                    (2 * scale, 2 * scale, 22 * scale, 22 * scale),
                    radius=7 * scale,
                    fill=soft,
                )
                if icon == "progress":
                    for index, height in enumerate((7, 11, 15)):
                        x = (7 + index * 4) * scale
                        draw.rounded_rectangle(
                            (x, (18 - height) * scale, (x + 2) * scale, 18 * scale),
                            radius=1 * scale,
                            fill=blue,
                        )
                elif icon == "status":
                    for y in (7, 13):
                        draw.rounded_rectangle(
                            (7 * scale, y * scale, 17 * scale, (y + 4) * scale),
                            radius=2 * scale,
                            fill=blue,
                        )
                        draw.ellipse((8 * scale, (y + 1) * scale, 10 * scale, (y + 3) * scale), fill=white)
                elif icon == "reward":
                    draw.rounded_rectangle((7 * scale, 10 * scale, 17 * scale, 18 * scale), radius=2 * scale, fill=blue)
                    draw.rounded_rectangle((6 * scale, 8 * scale, 18 * scale, 11 * scale), radius=1 * scale, fill=blue)
                    draw.line((12 * scale, 8 * scale, 12 * scale, 18 * scale), fill=white, width=scale)
                    draw.arc((7 * scale, 5 * scale, 12 * scale, 11 * scale), 210, 30, fill=blue, width=2 * scale)
                    draw.arc((12 * scale, 5 * scale, 17 * scale, 11 * scale), 150, -30, fill=blue, width=2 * scale)
                elif icon == "credential":
                    draw.polygon(
                        [
                            (12 * scale, 5 * scale),
                            (18 * scale, 8 * scale),
                            (17 * scale, 14 * scale),
                            (12 * scale, 19 * scale),
                            (7 * scale, 14 * scale),
                            (6 * scale, 8 * scale),
                        ],
                        fill=blue,
                    )
                    draw.line((9 * scale, 12 * scale, 11 * scale, 14 * scale, 15 * scale, 9 * scale), fill=white, width=scale)
                else:
                    for y in (8, 12, 16):
                        draw.rounded_rectangle((7 * scale, y * scale, 17 * scale, (y + 2) * scale), radius=scale, fill=blue)
                image = image.resize((size, size), Image.Resampling.LANCZOS)
                title_icon_image = ImageTk.PhotoImage(image)
                self._ui_images.append(title_icon_image)
                canvas.create_image(0, 0, anchor="nw", image=title_icon_image)
                return
            except Exception:
                pass
        self._draw_title_mark(canvas)

    def _draw_title_mark(self, canvas: tk.Canvas) -> None:
        canvas.delete("all")
        if Image is not None and ImageDraw is not None and ImageTk is not None:
            try:
                scale = 4
                image = Image.new("RGBA", (7 * scale, 20 * scale), (0, 0, 0, 0))
                draw = ImageDraw.Draw(image)
                draw.rounded_rectangle(
                    (1 * scale, 3 * scale, 6 * scale, 17 * scale),
                    radius=3 * scale,
                    fill=self._rgba_from_widget(canvas, ACCENT, 255),
                )
                image = image.resize((7, 20), Image.Resampling.LANCZOS)
                title_mark_image = ImageTk.PhotoImage(image)
                self._ui_images.append(title_mark_image)
                canvas.create_image(0, 0, anchor="nw", image=title_mark_image)
                return
            except Exception:
                pass
        canvas.create_oval(1, 3, 6, 8, fill=ACCENT, outline="")
        canvas.create_rectangle(1, 6, 6, 14, fill=ACCENT, outline="")
        canvas.create_oval(1, 12, 6, 17, fill=ACCENT, outline="")

    def _rgba_from_widget(self, widget: tk.Misc, color: str, alpha: int) -> tuple[int, int, int, int]:
        try:
            red, green, blue = widget.winfo_rgb(color)
        except tk.TclError:
            red, green, blue = 65535, 65535, 65535
        return red // 256, green // 256, blue // 256, alpha

    def _draw_brand_logo(self, canvas: tk.Canvas) -> None:
        canvas.delete("all")
        if Image is not None and ImageDraw is not None and ImageTk is not None:
            try:
                scale = 4
                size = 44
                image = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
                draw = ImageDraw.Draw(image)
                draw.rounded_rectangle(
                    (2 * scale, 2 * scale, 42 * scale, 42 * scale),
                    radius=11 * scale,
                    fill=(18, 48, 92, 255),
                )
                draw.rounded_rectangle(
                    (3 * scale, 3 * scale, 41 * scale, 23 * scale),
                    radius=10 * scale,
                    fill=(28, 70, 130, 64),
                )
                draw.ellipse(
                    (12 * scale, 14 * scale, 32 * scale, 34 * scale),
                    outline=(255, 255, 255, 238),
                    width=2 * scale,
                )
                draw.line(
                    (19 * scale, 28 * scale, 32 * scale, 15 * scale),
                    fill=(255, 255, 255, 244),
                    width=2 * scale,
                )
                draw.ellipse(
                    (26 * scale, 10 * scale, 34 * scale, 18 * scale),
                    fill=(96, 165, 250, 255),
                )
                image = image.resize((size, size), Image.Resampling.LANCZOS)
                self._brand_logo_image = ImageTk.PhotoImage(image)
                canvas.create_image(0, 0, anchor="nw", image=self._brand_logo_image)
                return
            except Exception:
                self._brand_logo_image = None
        canvas.create_polygon(10, 10, 34, 10, 34, 34, 10, 34, smooth=True, fill="#17376a", outline="")
        canvas.create_oval(14, 17, 30, 33, outline="#ffffff", width=2)
        canvas.create_line(20, 27, 31, 16, fill="#ffffff", width=2)
        canvas.create_oval(25, 12, 31, 18, fill="#6ea8ff", outline="")

    def _draw_section_icon(self, canvas: tk.Canvas, icon: str) -> None:
        canvas.delete("all")
        canvas.create_oval(1, 1, 17, 17, fill=ACCENT_SOFT, outline="")
        if icon == "progress":
            for index, height in enumerate((6, 10, 13)):
                x = 5 + index * 3
                canvas.create_rectangle(x, 14 - height, x + 2, 14, fill=ACCENT, outline=ACCENT)
        elif icon == "status":
            canvas.create_rectangle(5, 5, 13, 7, fill=ACCENT, outline=ACCENT)
            canvas.create_rectangle(5, 9, 13, 11, fill=ACCENT, outline=ACCENT)
            canvas.create_oval(4, 4, 6, 6, fill="#ffffff", outline="#ffffff")
            canvas.create_oval(4, 8, 6, 10, fill="#ffffff", outline="#ffffff")
        elif icon == "reward":
            canvas.create_rectangle(5, 8, 13, 14, fill=ACCENT, outline=ACCENT)
            canvas.create_rectangle(4, 6, 14, 8, fill=ACCENT, outline=ACCENT)
            canvas.create_line(9, 5, 9, 14, fill="#ffffff", width=1)
        elif icon == "credential":
            canvas.create_polygon(9, 4, 14, 6, 13, 11, 9, 14, 5, 11, 4, 6, fill=ACCENT, outline=ACCENT)
            canvas.create_line(7, 9, 8.5, 10.5, 12, 7, fill="#ffffff", width=1)
        else:
            for y in (6, 9, 12):
                canvas.create_line(5, y, 13, y, fill=ACCENT, width=2)

    def _status_dot(self, parent: tk.Misc, *, color: str, background: str, size: int = 10) -> tk.Canvas:
        canvas = tk.Canvas(parent, width=size, height=size, bg=background, highlightthickness=0, borderwidth=0)
        pad = 2
        canvas.create_oval(pad, pad, size - pad, size - pad, fill=color, outline="")
        return canvas

    def _metric_icon(self, parent: tk.Misc, icon: str, *, background: str) -> tk.Canvas:
        canvas = tk.Canvas(parent, width=16, height=16, bg=background, highlightthickness=0, borderwidth=0)
        if Image is not None and ImageDraw is not None and ImageTk is not None:
            try:
                scale = 4
                image = Image.new("RGBA", (16 * scale, 16 * scale), (0, 0, 0, 0))
                draw = ImageDraw.Draw(image)
                color = self._rgba_from_widget(canvas, "#8fa0b7", 255)
                accent = self._rgba_from_widget(canvas, ACCENT, 210)
                if icon in {"start", "elapsed", "next"}:
                    draw.ellipse((2 * scale, 2 * scale, 14 * scale, 14 * scale), outline=color, width=scale)
                    draw.line((8 * scale, 8 * scale, 8 * scale, 5 * scale), fill=accent, width=scale)
                    end = (10 * scale, 10 * scale) if icon != "next" else (11 * scale, 8 * scale)
                    draw.line((8 * scale, 8 * scale, *end), fill=accent, width=scale)
                    if icon == "start":
                        draw.polygon(
                            [(7 * scale, 5 * scale), (7 * scale, 11 * scale), (12 * scale, 8 * scale)],
                            fill=accent,
                        )
                    elif icon == "next":
                        draw.arc((3 * scale, 3 * scale, 13 * scale, 13 * scale), 300, 80, fill=accent, width=scale)
                else:
                    draw.arc((2 * scale, 8 * scale, 14 * scale, 20 * scale), 200, 340, fill=color, width=scale)
                    draw.arc((5 * scale, 10 * scale, 11 * scale, 16 * scale), 205, 335, fill=accent, width=scale)
                    draw.ellipse((7 * scale, 12 * scale, 9 * scale, 14 * scale), fill=accent)
                image = image.resize((16, 16), Image.Resampling.LANCZOS)
                metric_icon_image = ImageTk.PhotoImage(image)
                self._ui_images.append(metric_icon_image)
                canvas.create_image(0, 0, anchor="nw", image=metric_icon_image)
                return canvas
            except Exception:
                pass
        if icon == "network":
            canvas.create_arc(2, 8, 14, 20, start=200, extent=140, outline=FAINT, width=1)
            canvas.create_oval(7, 12, 9, 14, fill=ACCENT, outline="")
        else:
            canvas.create_oval(2, 2, 14, 14, outline=FAINT, width=1)
            canvas.create_line(8, 8, 8, 5, fill=ACCENT, width=1)
            canvas.create_line(8, 8, 10, 10, fill=ACCENT, width=1)
        return canvas

    def _computer_icon(self, parent: tk.Misc, *, background: str) -> tk.Canvas:
        canvas = tk.Canvas(parent, width=18, height=18, bg=background, highlightthickness=0, borderwidth=0)
        canvas.create_rectangle(3, 4, 15, 12, outline=MUTED, width=1)
        canvas.create_line(7, 15, 11, 15, fill=MUTED, width=1)
        canvas.create_line(9, 12, 9, 15, fill=MUTED, width=1)
        return canvas

    def _footer_link(self, parent: tk.Misc, text: str, command: Callable[[], object], *, icon: str, accent: bool = False, button: bool = False) -> tk.Frame:
        del icon
        foreground = ACCENT if accent else MUTED
        background = SECONDARY if button else HEADER_BG
        link = tk.Frame(
            parent,
            bg=background,
            highlightthickness=1 if button else 0,
            highlightbackground=SUBTLE_OUTLINE,
            highlightcolor=SUBTLE_OUTLINE,
            borderwidth=0,
            cursor="hand2",
            padx=8 if button else 0,
            pady=4 if button else 0,
        )
        label = tk.Label(link, text=text, bg=background, fg=foreground, font=("Microsoft YaHei UI", 9, "bold" if button else "normal"), cursor="hand2")
        label.pack(side="left")
        for widget in (link, label):
            widget.bind("<Button-1>", lambda _event, action=command: action())
        return link

    def _draw_footer_icon(self, canvas: tk.Canvas, icon: str, color: str) -> None:
        canvas.delete("all")
        if icon == "source":
            canvas.create_rectangle(3, 3, 12, 12, outline=color, width=1)
            canvas.create_line(6, 8, 9, 5, fill=color, width=1)
            canvas.create_line(8, 5, 11, 5, fill=color, width=1)
            canvas.create_line(11, 5, 11, 8, fill=color, width=1)
        elif icon == "settings":
            canvas.create_oval(4, 4, 11, 11, outline=color, width=1)
            canvas.create_oval(6, 6, 9, 9, fill=color, outline=color)
            for x1, y1, x2, y2 in ((7, 1, 7, 3), (7, 12, 7, 14), (1, 7, 3, 7), (12, 7, 14, 7)):
                canvas.create_line(x1, y1, x2, y2, fill=color, width=1)
        elif icon == "help":
            canvas.create_oval(2, 2, 13, 13, outline=color, width=1)
            canvas.create_text(7.5, 7, text="?", fill=color, font=("Microsoft YaHei UI", 8, "bold"))
        elif icon == "about":
            canvas.create_oval(2, 2, 13, 13, outline=color, width=1)
            canvas.create_text(7.5, 8, text="i", fill=color, font=("Microsoft YaHei UI", 8, "bold"))
        else:
            canvas.create_oval(3, 3, 12, 12, outline=color, width=1)

    def _toolbar_link(self, parent: tk.Misc, text: str, command: Callable[[], object], *, icon: str) -> tk.Frame:
        try:
            background = str(parent.cget("bg"))
        except tk.TclError:
            background = GLASS
        link = tk.Frame(
            parent,
            bg=SECONDARY,
            highlightthickness=1,
            highlightbackground=SUBTLE_OUTLINE,
            highlightcolor=SUBTLE_OUTLINE,
            borderwidth=0,
            cursor="hand2",
            padx=8,
            pady=5,
        )
        icon_canvas = tk.Canvas(link, width=15, height=15, bg=SECONDARY, highlightthickness=0, borderwidth=0, cursor="hand2")
        icon_canvas.pack(side="left", padx=(0, 5))
        self._draw_toolbar_icon(icon_canvas, icon, MUTED)
        label = tk.Label(link, text=text, bg=SECONDARY, fg=MUTED, font=("Microsoft YaHei UI", 8, "bold"), cursor="hand2")
        label.pack(side="left")
        for widget in (link, icon_canvas, label):
            widget.bind("<Button-1>", lambda _event, action=command: action())
        return link

    def _draw_toolbar_icon(self, canvas: tk.Canvas, icon: str, color: str) -> None:
        canvas.delete("all")
        if icon == "trash":
            canvas.create_line(4, 5, 11, 5, fill=color, width=1)
            canvas.create_line(6, 3, 9, 3, fill=color, width=1)
            canvas.create_rectangle(5, 6, 10, 12, outline=color, width=1)
            canvas.create_line(7, 7, 7, 11, fill=color, width=1)
            canvas.create_line(8.5, 7, 8.5, 11, fill=color, width=1)
        elif icon == "copy":
            canvas.create_rectangle(5, 3, 12, 10, outline=color, width=1)
            canvas.create_rectangle(3, 5, 10, 12, outline=color, width=1)
        else:
            canvas.create_oval(3, 3, 12, 12, outline=color, width=1)

    def _build_actions(self, parent: ttk.Frame, *, row: int = 1, background: str = APP_BG) -> None:
        actions_panel = tk.Frame(parent, bg=background, highlightthickness=0, borderwidth=0)
        actions_panel.grid(row=row, column=0, sticky="ew", pady=(4, 0))
        actions_panel.configure(height=42)
        actions_panel.grid_propagate(False)
        actions = actions_panel
        actions.columnconfigure(0, weight=0, minsize=96)
        actions.columnconfigure(1, weight=2, uniform="actions")
        actions.columnconfigure((2, 3, 4), weight=1, uniform="actions")
        actions.rowconfigure(0, minsize=36)

        tk.Label(actions, text="运行", bg=background, fg=TEXT, font=("Microsoft YaHei UI", 11, "bold")).grid(row=0, column=0, sticky="w")
        self.start_button = LabelButton(
            actions,
            "开始挂宝",
            self._toggle_run,
            fill=ACCENT,
            foreground="#ffffff",
            active_fill=ACCENT_ACTIVE,
            height=36,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.start_button.grid(row=0, column=1, sticky="nsew", padx=(0, 10))
        LabelButton(actions, "领取奖励", self._claim, fill=SECONDARY, foreground=TEXT, active_fill=SECONDARY_ACTIVE, height=36, font=("Microsoft YaHei UI", 9, "bold")).grid(row=0, column=2, sticky="nsew", padx=(0, 10))
        LabelButton(actions, "保存配置", self._save, fill=SECONDARY, foreground=TEXT, active_fill=SECONDARY_ACTIVE, height=36, font=("Microsoft YaHei UI", 9, "bold")).grid(row=0, column=3, sticky="nsew", padx=(0, 10))
        LabelButton(actions, "上手指引", self._show_onboarding_guide, fill=SURFACE, foreground=MUTED, active_fill=SOFT_SURFACE, height=36, font=("Microsoft YaHei UI", 9, "bold"), outline=BORDER).grid(row=0, column=4, sticky="nsew")

    def _build_monitor_workspace(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        monitor = tk.Frame(parent, bg=APP_BG, highlightthickness=0, borderwidth=0)
        monitor.grid(row=0, column=0, sticky="nsew")
        monitor.columnconfigure(0, weight=1)
        monitor.rowconfigure(0, weight=0, minsize=214)
        monitor.rowconfigure(1, weight=1)

        top = tk.Frame(monitor, bg=APP_BG, highlightthickness=0, borderwidth=0)
        top.grid(row=0, column=0, sticky="nsew", pady=(0, 14))
        top.columnconfigure(0, weight=1)
        top.rowconfigure(0, weight=1)

        overview_panel = RoundedPanel(top, fill=GLASS, background=APP_BG, radius=20, padding=(18, 14), min_height=204, outline=SUBTLE_OUTLINE, shadow=True, auto_height=False)
        overview_panel.grid(row=0, column=0, sticky="nsew")
        overview = overview_panel.inner
        overview.columnconfigure((0, 1, 2), weight=1, uniform="overview")
        overview.rowconfigure(0, weight=1)

        progress_cell = RoundedPanel(overview, fill=SURFACE, background=GLASS, radius=16, padding=(14, 14), min_height=176, outline=SUBTLE_OUTLINE, shadow=False, auto_height=False)
        progress_cell.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        progress_pane = progress_cell.inner
        progress_pane.columnconfigure(0, weight=1)
        self._section_title(progress_pane, "观看进度", "progress", background=SURFACE).grid(row=0, column=0, sticky="w")
        progress_body = tk.Frame(progress_pane, bg=SURFACE, highlightthickness=0, borderwidth=0)
        progress_body.grid(row=1, column=0, sticky="nsew", pady=(16, 0))
        progress_body.columnconfigure(1, weight=1)
        self.progress_ring = ProgressRing(progress_body, text="待启动", caption="点开始挂宝", background=SURFACE, size=86)
        self.progress_ring.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 14))
        tk.Label(progress_body, textvariable=self.progress_title_var, bg=SURFACE, fg=TEXT, font=("Microsoft YaHei UI", 15, "bold")).grid(row=0, column=1, sticky="sw", pady=(6, 5))
        tk.Label(progress_body, textvariable=self.progress_detail_var, bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9), wraplength=135, justify="left").grid(row=1, column=1, sticky="nw")
        self.progress_text = tk.Text(progress_pane, height=1, wrap="word", state="disabled", borderwidth=0, relief="flat", bg=SURFACE, fg=SURFACE, insertbackground=SURFACE, highlightthickness=0, font=("Microsoft YaHei UI", 1))
        self.progress_text.grid(row=2, column=0, sticky="ew")
        self._progress_log("等待任务检查。开始挂宝后，这里会显示本次可挂任务、剩余分钟和领取状态。")

        status_cell = RoundedPanel(overview, fill=SURFACE, background=GLASS, radius=16, padding=(14, 14), min_height=176, outline=SUBTLE_OUTLINE, shadow=False, auto_height=False)
        status_cell.grid(row=0, column=1, sticky="nsew", padx=12)
        status_pane = status_cell.inner
        status_pane.columnconfigure(0, weight=1)
        self._section_title(status_pane, "运行状态", "status", background=SURFACE).grid(row=0, column=0, sticky="w")
        status_grid = tk.Frame(status_pane, bg=SURFACE, highlightthickness=0, borderwidth=0)
        status_grid.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        status_grid.columnconfigure(1, weight=1)
        for index, (icon, label, variable) in enumerate((
            ("start", "启动时间", self.backend_start_var),
            ("elapsed", "运行时长", self.backend_elapsed_var),
            ("next", "下次计时", self.backend_next_var),
            ("network", "网络状态", self.backend_network_var),
        )):
            self._metric_icon(status_grid, icon, background=SURFACE).grid(row=index, column=0, sticky="w", pady=3)
            tk.Label(status_grid, text=label, bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).grid(row=index, column=1, sticky="w", padx=(8, 0), pady=3)
            tk.Label(status_grid, textvariable=variable, bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 8, "bold")).grid(row=index, column=2, sticky="e", pady=3)
        self.watch_status_card = WatchStatusCard(status_pane, background=SURFACE)
        self.watch_status_card.grid(row=2, column=0, sticky="ew", pady=(8, 0))

        reward_cell = RoundedPanel(overview, fill=SURFACE, background=GLASS, radius=16, padding=(14, 14), min_height=176, outline=SUBTLE_OUTLINE, shadow=False, auto_height=False)
        reward_cell.grid(row=0, column=2, sticky="nsew", padx=(12, 0))
        reward = reward_cell.inner
        reward.columnconfigure(0, weight=1)
        self._section_title(reward, "领取结果", "reward", background=SURFACE).grid(row=0, column=0, sticky="w")
        tk.Label(reward, textvariable=self.reward_title_var, bg=SURFACE, fg=TEXT, font=("Microsoft YaHei UI", 15, "bold")).grid(row=1, column=0, sticky="ew", pady=(26, 8))
        tk.Label(reward, textvariable=self.reward_detail_var, bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9), wraplength=180, justify="center").grid(row=2, column=0, sticky="ew")

        log_panel = RoundedPanel(monitor, fill=GLASS, background=APP_BG, radius=18, padding=(20, 16), min_height=392, outline=SUBTLE_OUTLINE, shadow=True, auto_height=False)
        log_panel.grid(row=1, column=0, sticky="nsew")
        log_pane = log_panel.inner
        log_pane.columnconfigure(0, weight=1)
        log_pane.rowconfigure(1, weight=1)
        log_head = tk.Frame(log_pane, bg=GLASS, highlightthickness=0, borderwidth=0)
        log_head.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        log_head.columnconfigure(0, weight=1)
        title_group = tk.Frame(log_head, bg=GLASS, highlightthickness=0, borderwidth=0)
        title_group.grid(row=0, column=0, sticky="w")
        self._section_title(title_group, "运行日志", "log").grid(row=0, column=0, sticky="w")
        tk.Label(title_group, text="实时记录程序运行与任务处理情况", bg=GLASS, fg=MUTED, font=("Microsoft YaHei UI", 9)).grid(row=1, column=0, sticky="w", pady=(5, 0))
        tools = tk.Frame(log_head, bg=GLASS, highlightthickness=0, borderwidth=0)
        tools.grid(row=0, column=1, sticky="e")
        self.auto_scroll_button = ToggleSwitch(
            tools,
            "自动滚动",
            self._toggle_auto_scroll,
            checked=bool(self.auto_scroll_var.get()),
            background=GLASS,
            width=106,
            height=30,
        )
        self.auto_scroll_button.pack(side="left", padx=(0, 12))
        LabelButton(tools, "清空日志", self._clear_log, fill=SECONDARY, foreground=MUTED, active_fill=SECONDARY_ACTIVE, height=34, width=88, font=("Microsoft YaHei UI", 8, "bold"), radius=11, outline=SUBTLE_OUTLINE).pack(side="left", padx=(0, 10))
        LabelButton(tools, "复制日志", self._copy_log, fill=SECONDARY, foreground=MUTED, active_fill=SECONDARY_ACTIVE, height=34, width=88, font=("Microsoft YaHei UI", 8, "bold"), radius=11, outline=SUBTLE_OUTLINE).pack(side="left")

        log_wrap = RoundedPanel(log_pane, fill=FIELD_BG, background=GLASS, radius=14, padding=(5, 5), min_height=312, outline=SUBTLE_OUTLINE, shadow=False, auto_height=False)
        log_wrap.grid(row=1, column=0, sticky="nsew")
        log_wrap.inner.columnconfigure(0, weight=1)
        log_wrap.inner.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_wrap.inner, height=14, wrap="word", state="disabled", borderwidth=0, relief="flat", bg=FIELD_BG, fg=TEXT, insertbackground=TEXT, highlightthickness=0, padx=16, pady=12, font=("Consolas", 10))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_scrollbar = ttk.Scrollbar(log_wrap.inner, orient="vertical", command=self.log_text.yview, style="Vertical.TScrollbar")
        self.log_scrollbar.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        self.log_scrollbar.grid_remove()
        self.log_text.configure(yscrollcommand=self._update_log_scrollbar)
        self.log_empty_canvas = tk.Canvas(log_wrap.inner, bg=FIELD_BG, highlightthickness=0, borderwidth=0)
        self.log_empty_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.log_empty_canvas.bind("<Configure>", lambda _event: self._draw_log_empty_skeleton())
        self.log_empty_label = tk.Label(log_wrap.inner, text="日志尚未开始", bg=FIELD_BG, fg=FAINT, font=("Microsoft YaHei UI", 12, "bold"))
        self.log_empty_label.place(relx=0.5, rely=0.45, anchor="center")
        self.log_empty_detail_label = tk.Label(log_wrap.inner, text="开始挂宝后自动记录登录、计时和领取结果", bg=FIELD_BG, fg=FAINT, font=("Microsoft YaHei UI", 9))
        self.log_empty_detail_label.place(relx=0.5, rely=0.5, anchor="center")

    def _draw_log_empty_skeleton(self) -> None:
        if not hasattr(self, "log_empty_canvas"):
            return
        canvas = self.log_empty_canvas
        canvas.delete("all")
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        inset = 22
        self._rounded_rect_on(canvas, inset, inset, width - inset, height - inset, 16, fill="#fbfdff", outline="")
        center_y = height // 2 - 48
        self._rounded_rect_on(canvas, width // 2 - 82, center_y, width // 2 + 82, center_y + 8, 4, fill="#edf3fa", outline="")
        self._rounded_rect_on(canvas, width // 2 - 128, center_y + 24, width // 2 + 128, center_y + 32, 4, fill="#f1f5fa", outline="")

    def _rounded_rect_on(self, canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs: object) -> None:
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
        canvas.create_polygon(points, smooth=True, splinesteps=18, **kwargs)

    def _monitor_pane(self, parent: tk.Misc, title: str, subtitle: str, *, column: int, padx: tuple[int, int]) -> tk.Frame:
        pane = tk.Frame(parent, bg=SURFACE, highlightthickness=0, borderwidth=0)
        pane.grid(row=0, column=column, sticky="nsew", padx=padx)
        pane.columnconfigure(0, weight=1)
        pane.rowconfigure(2, weight=1)
        tk.Label(pane, text=title, bg=SURFACE, fg=TEXT, font=("Microsoft YaHei UI", 11, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(pane, text=subtitle, bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9), wraplength=260, justify="left").grid(row=1, column=0, sticky="w", pady=(3, 0))
        return pane

    def _select_monitor_page(self, name: str) -> None:
        for key, page in getattr(self, "_monitor_pages", {}).items():
            if key == name:
                page.grid(row=0, column=0, sticky="nsew")
                page.tkraise()
            else:
                page.grid_remove()
        for key, button in getattr(self, "_monitor_tab_buttons", {}).items():
            if key == name:
                button.set_appearance(text=button.text, fill=ACCENT, foreground="#ffffff", active_fill=ACCENT_ACTIVE)
            else:
                button.set_appearance(text=button.text, fill=SECONDARY, foreground=MUTED, active_fill=SECONDARY_ACTIVE)

    def _build_status_column(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=2, uniform="monitor")
        parent.columnconfigure(1, weight=1, uniform="monitor")
        parent.columnconfigure(2, weight=1, uniform="monitor")
        parent.rowconfigure(0, weight=1)

        progress_card = self._card(parent, row=0, column=0, title="任务进度", subtitle="登录、房间、计时、剩余分钟和领取结果都在这里", sticky="nsew", min_height=260, subtitle_wrap=520, auto_height=False, padx=(0, 28))
        progress_card.columnconfigure(0, weight=1)
        progress_card.rowconfigure(3, weight=1)

        progress_buttons = tk.Frame(progress_card, bg=APP_BG, highlightthickness=0, borderwidth=0)
        progress_buttons.grid(row=2, column=0, columnspan=2, sticky="e", pady=(10, 0))
        self.manual_refresh_button = PillButton(
            progress_buttons,
            "↻ 刷新",
            self._handle_manual_refresh,
            fill=SOFT_SURFACE,
            foreground=TEXT,
            active_fill=SECONDARY_ACTIVE,
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
            active_fill=SECONDARY_ACTIVE,
            height=28,
            width=130,
        )
        self.rediscover_button.pack(side="left")

        progress_wrap = RoundedPanel(progress_card, fill=SOFT_SURFACE, background=APP_BG, radius=14, padding=(4, 4), min_height=250, outline=BORDER, shadow=False, auto_height=False)
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
            font=("Microsoft YaHei UI", 11),
        )
        self.progress_text.grid(row=0, column=0, sticky="nsew")
        progress_scrollbar = ttk.Scrollbar(progress_wrap.inner, orient="vertical", command=self.progress_text.yview, style="Vertical.TScrollbar")
        progress_scrollbar.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        self.progress_text.configure(yscrollcommand=progress_scrollbar.set)
        self._progress_log("等待任务检查。开始挂宝后，这里会显示本次可挂任务、剩余分钟和领取状态。")

        self.watch_status_card = WatchStatusCard(parent, background=APP_BG)
        self.watch_status_card.grid(row=0, column=1, sticky="nsew", padx=(0, 28))

    def _build_log_card(self, parent: ttk.Frame) -> None:
        card = self._card(parent, row=0, column=2, columnspan=1, title="运行日志", subtitle="Cookie、任务识别和领奖问题会记录在这里。", sticky="nsew", min_height=180, subtitle_wrap=330, auto_height=False)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)

        log_wrap = RoundedPanel(card, fill=SOFT_SURFACE, background=APP_BG, radius=14, padding=(4, 4), min_height=70, outline=BORDER, shadow=False, auto_height=False)
        log_wrap.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
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
        card = tk.Frame(parent, bg=APP_BG, highlightthickness=0, borderwidth=0)
        card.grid(row=row, column=column, columnspan=columnspan, sticky=sticky, pady=(0, 18), padx=padx)
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text=title, style="SectionTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        if subtitle:
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

    def _paste_room_id(self) -> None:
        try:
            value = self.clipboard_get().strip()
        except tk.TclError:
            self._log("剪贴板没有可粘贴的房间号")
            return
        if value:
            self.room_var.set(value)
            self._refresh_summary_bar()
            self._log("已从剪贴板粘贴直播间房号")

    def _toggle_run(self) -> None:
        if self.watcher and self.watcher.running:
            self._stop()
            return
        self._start()

    def _toggle_advanced_settings(self) -> None:
        self.advanced_visible_var.set(not bool(self.advanced_visible_var.get()))
        self._refresh_advanced_settings()

    def _refresh_advanced_settings(self) -> None:
        if not hasattr(self, "advanced_frame"):
            return
        if bool(self.advanced_visible_var.get()):
            self.advanced_frame.grid()
            if hasattr(self, "advanced_button"):
                self.advanced_button.set_appearance(text="收起", fill=SECONDARY, foreground=TEXT, active_fill=SECONDARY_ACTIVE)
        else:
            self.advanced_frame.grid_remove()
            if hasattr(self, "advanced_button"):
                self.advanced_button.set_appearance(text="展开", fill=SECONDARY, foreground=TEXT, active_fill=SECONDARY_ACTIVE)

    def _toggle_auto_claim(self) -> None:
        self.auto_claim_var.set(not bool(self.auto_claim_var.get()))
        self._refresh_auto_claim_button()

    def _toggle_auto_scroll(self) -> None:
        if hasattr(self, "auto_scroll_button"):
            self.auto_scroll_var.set(not bool(self.auto_scroll_var.get()))
            checked = bool(self.auto_scroll_var.get())
            self.auto_scroll_button.set_checked(checked, "自动滚动" if checked else "手动滚动")

    def _refresh_auto_claim_button(self) -> None:
        if bool(self.auto_claim_var.get()):
            if isinstance(self.auto_claim_button, ToggleSwitch):
                self.auto_claim_button.set_checked(True, "已开启")
            else:
                self.auto_claim_button.set_appearance(text="已开启", fill=ACCENT_SOFT, foreground=ACCENT, active_fill=ACCENT_SOFT_ACTIVE)
        else:
            if isinstance(self.auto_claim_button, ToggleSwitch):
                self.auto_claim_button.set_checked(False, "已关闭")
            else:
                self.auto_claim_button.set_appearance(text="已关闭", fill=SOFT_SURFACE, foreground=MUTED, active_fill=SECONDARY_ACTIVE)

    def _refresh_summary_bar(self) -> None:
        room = self.room_var.get().strip() if hasattr(self, "room_var") else ""
        self.room_status_var.set(f"直播间：{room or '未填写'}")
        if hasattr(self, "room_hint_var"):
            self.room_hint_var.set(self._room_hint_text(room))
        task_ids = ""
        if hasattr(self, "task_ids_text"):
            task_ids = self.task_ids_text.get("1.0", "end").strip()
        elif hasattr(self, "config_data"):
            task_ids = self.config_data.task_ids
        task_count = len(parse_task_ids(task_ids))
        if hasattr(self, "task_id_status_var"):
            if task_count:
                self.task_id_status_var.set(f"任务 ID：已填写 {task_count} 个，启动后继续自动校准")
            else:
                self.task_id_status_var.set("任务 ID：按直播间自动获取")
        cookie = ""
        if hasattr(self, "cookie_text"):
            cookie = self.cookie_text.get("1.0", "end").strip()
        else:
            cookie = self.cookie_var.get().strip()
        self.credential_status_var.set(f"凭据：{'已填写' if cookie else '未填写'}")

    def _room_hint_text(self, room: str) -> str:
        room = room.strip()
        if not room:
            return f"默认房间号 {DEFAULT_ROOM_ID}；留空保存后也会恢复默认。任务 ID 启动后自动获取。"
        if room == DEFAULT_ROOM_ID:
            return f"默认房间号 {DEFAULT_ROOM_ID} 已填好；任务 ID 启动后按此房间自动获取。"
        return "已使用当前房间号；任务 ID 启动后按此房间自动获取。"

    def _refresh_room_placeholder(self) -> None:
        if not hasattr(self, "room_placeholder") or not hasattr(self, "room_var"):
            return
        self._refresh_summary_bar()
        if self.room_var.get().strip():
            self.room_placeholder.grid_remove()
        else:
            self.room_placeholder.grid()

    def _refresh_cookie_placeholder(self) -> None:
        if not hasattr(self, "cookie_text") or not hasattr(self, "cookie_empty_label"):
            return
        content = self.cookie_text.get("1.0", "end").strip()
        if content:
            self.cookie_empty_label.place_forget()
            if hasattr(self, "cookie_validation_var") and self.cookie_validation_var.get() in {"Cookie 未验证", "Cookie 未填写", "Cookie 待验证"}:
                self.cookie_validation_var.set("Cookie 已填写")
        else:
            self.cookie_empty_label.place(relx=0.5, rely=0.5, anchor="center")
            if hasattr(self, "cookie_validation_var"):
                self.cookie_validation_var.set("Cookie 未填写")

    def _refresh_backend_summary(self, snapshot: list[WatchWorkerStatus] | None = None) -> None:
        if self.started_at is None:
            self.backend_start_var.set("启动后更新")
            self.backend_elapsed_var.set("启动后更新")
            self.backend_next_var.set("启动后更新")
            self.backend_network_var.set("未启动")
            return

        now = datetime.now()
        elapsed_seconds = max(0, int((now - self.started_at).total_seconds()))
        rows = snapshot or []
        intervals = [row.interval for row in rows if row.interval is not None]
        states = {row.state for row in rows}

        self.backend_start_var.set(self.started_at.strftime("%H:%M:%S"))
        self.backend_elapsed_var.set(self._format_elapsed_time(elapsed_seconds))
        self.backend_next_var.set(f"约 {min(intervals)} 秒" if intervals else "等待返回")
        if not rows:
            self.backend_network_var.set("检查中")
        elif any("失败" in state or "异常" in state for state in states):
            self.backend_network_var.set("异常")
        elif any("等待" in state for state in states):
            self.backend_network_var.set("等待开播")
        elif any("正常" in state for state in states):
            self.backend_network_var.set("正常")
        else:
            self.backend_network_var.set("检查中")

    def _format_elapsed_time(self, seconds: int) -> str:
        if seconds < 60:
            return f"{seconds} 秒"
        minutes, rest = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes} 分 {rest} 秒"
        hours, minutes = divmod(minutes, 60)
        return f"{hours} 时 {minutes} 分"

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

    def _focus_setup_area(self) -> None:
        if hasattr(self, "room_entry"):
            self.room_entry.focus_set()

    def _show_about_dialog(self) -> None:
        messagebox.showinfo(
            "关于守望先锋 B站直播挂宝",
            f"守望先锋 B站直播挂宝\n"
            f"版本：v{__version__}\n"
            "运行方式：本地模式\n\n"
            "本软件完全免费，请勿购买；购买请找商家退款。\n"
            "凭据仅保存在本机，不会上传到任何服务器。\n\n"
            f"开源地址：{SOURCE_URL}",
        )

    def _clear_cookie_text(self) -> None:
        self.cookie_text.delete("1.0", "end")
        self._refresh_cookie_placeholder()
        self._refresh_summary_bar()
        self.cookie_validation_var.set("Cookie 未填写")
        self._log("Cookie 内容已清空")

    def _validate_cookie_text(self) -> None:
        cookie = self.cookie_text.get("1.0", "end").strip()
        if not cookie:
            self.cookie_validation_var.set("Cookie 未填写")
            messagebox.showwarning("缺少 Cookie", "请先读取或粘贴 Cookie。")
            return
        required_fields = ("SESSDATA=", "bili_jct=", "DedeUserID=")
        missing = [field.rstrip("=") for field in required_fields if field not in cookie]
        if missing:
            self.cookie_validation_var.set("Cookie 缺少字段")
            messagebox.showwarning("Cookie 不完整", "Cookie 缺少必要字段：\n" + "、".join(missing))
            return
        self.cookie_validation_var.set("Cookie 格式正常")
        self._log("Cookie 本地格式校验通过")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._log("日志已清空")

    def _copy_log(self) -> None:
        content = self.log_text.get("1.0", "end").strip()
        self.clipboard_clear()
        self.clipboard_append(content)
        self._log("日志已复制到剪贴板")

    def _update_log_scrollbar(self, first: str, last: str) -> None:
        if not hasattr(self, "log_scrollbar"):
            return
        self.log_scrollbar.set(first, last)
        try:
            needs_scrollbar = float(first) > 0.0 or float(last) < 1.0
        except ValueError:
            needs_scrollbar = True
        if needs_scrollbar:
            self.log_scrollbar.grid()
        else:
            self.log_scrollbar.grid_remove()

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
        try:
            frame_bg = str(frame.cget("bg"))
        except tk.TclError:
            frame_bg = APP_BG
        for child in frame.winfo_children():
            child.destroy()
        self.account_checks = {}
        active = set(self.config_data.active_accounts or [])
        has_saved_accounts = bool(self.config_data.accounts)
        default_editing_account = self.config_data.account_name or "默认账号"
        for name in self._account_names():
            checked = (name in active) or (not has_saved_accounts and name == default_editing_account)
            var = tk.BooleanVar(value=checked)
            self.account_checks[name] = var
            row = tk.Frame(frame, bg=frame_bg, highlightthickness=0, borderwidth=0)
            row.columnconfigure(0, weight=1)
            row.pack(fill="x", anchor="w")
            tk.Checkbutton(
                row,
                text=name,
                variable=var,
                command=lambda n=name: self._on_account_check_toggled(n),
                bg=frame_bg,
                fg=TEXT,
                selectcolor=SOFT_SURFACE,
                activebackground=frame_bg,
                activeforeground=TEXT,
                anchor="w",
                highlightthickness=0,
                bd=0,
                font=("Microsoft YaHei UI", 10),
            ).grid(row=0, column=0, sticky="ew")
            LabelButton(
                row,
                "编辑",
                lambda n=name: self._select_account_for_edit(n),
                fill=SURFACE,
                foreground=ACCENT,
                active_fill=SECONDARY_ACTIVE,
                height=24,
                width=50,
                font=("Microsoft YaHei UI", 8, "bold"),
                radius=9,
                outline=SUBTLE_OUTLINE,
            ).grid(row=0, column=1, sticky="e", padx=(8, 0))

    def _refresh_account_selector(self) -> None:
        if hasattr(self, "_account_check_frame"):
            self._build_account_checklist()

    def _saved_cookie_for(self, name: str) -> str:
        for account in self.config_data.accounts:
            if account.name == name:
                return account.cookie
        return ""

    def _next_account_name(self) -> str:
        existing = {name for name in self._account_names() if name}
        index = 2 if existing else 1
        while True:
            name = f"账号 {index}"
            if name not in existing:
                return name
            index += 1

    def _on_account_check_toggled(self, name: str) -> None:
        checked = bool(self.account_checks.get(name) and self.account_checks[name].get())
        self._log(f"{'已勾选' if checked else '已取消'}账号参与挂机：{name}")

    def _select_account_for_edit(self, name: str) -> None:
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

    def _new_account(self) -> None:
        current = self.account_name_var.get().strip()
        editor = self.cookie_text.get("1.0", "end").strip()
        if current and editor and editor != self._saved_cookie_for(current):
            saved = self._save()
            self._log(f"已先保存当前账号：{saved.account_name}")
        name = self._next_account_name()
        self.account_name_var.set(name)
        self.cookie_text.delete("1.0", "end")
        if hasattr(self, "cookie_validation_var"):
            self.cookie_validation_var.set("Cookie 未填写")
        self._refresh_cookie_placeholder()
        self._refresh_summary_bar()
        self._log(f"已新建账号编辑位：{name}；请获取或粘贴 Cookie 后点击保存账号")

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

    def _save(self) -> AppConfig:
        config = self._current_config()
        save_config(config)
        self.config_data = config
        self.room_var.set(config.room_id)
        self.selected_account_var.set(config.account_name)
        self.account_name_var.set(config.account_name)
        self.notify_url_var.set(config.notify_url)
        if hasattr(self, "task_ids_text"):
            self.task_ids_text.delete("1.0", "end")
            self.task_ids_text.insert("1.0", config.task_ids)
        self._refresh_account_selector()
        self._refresh_summary_bar()
        self._log("配置已保存")
        return config

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

        config = self._save()
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
        self.started_at = datetime.now()
        try:
            snapshot, summary = self.watcher.get_watch_status_snapshot()
        except Exception:
            snapshot, summary = [], "后台计时状态：启动中"
        self.watch_status_card.update_snapshot(snapshot, summary)
        self._refresh_backend_summary(snapshot)
        self._set_status("运行中")
        self.elapsed_status_var.set("计时：运行中")
        self.reward_status_var.set("领奖：等待任务")
        self.reward_title_var.set("检查中")
        self.reward_detail_var.set("正在识别可领取奖励")
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
        self.started_at = None
        self._refresh_backend_summary()
        self.elapsed_status_var.set("计时：已停止")
        if hasattr(self, "progress_ring"):
            self.progress_ring.set_state(text="已停止", caption="可再次启动", value=0.08, color=FAINT)
            self.progress_title_var.set("已停止")
            self.progress_detail_var.set("再次点击开始挂宝后继续检查任务。")

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
        self._log(f"已打开 {browser_name} 登录页。手动模式不会自动读取 Cookie；需要自动读取请点击“自动获取 Cookie”。")

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
            config = self._save()
            account_options = build_account_options(config)
            if not account_options:
                messagebox.showwarning("没有可用账号", "请至少勾选一个已保存且含 Cookie 的账号。")
                return
            self.watcher = MultiAccountWatcher(account_options, self._thread_log)
            self._log("尚未挂宝，临时对勾选账号各领取一次已完成奖励")
        self.reward_status_var.set("领奖：领取中")
        self.reward_title_var.set("领取中")
        self.reward_detail_var.set("正在刷新任务进度并提交领取请求")
        self._progress_log("开始领取奖励")
        self.watcher.claim_completed_tasks()

    def _thread_log(self, message: str) -> None:
        self.log_queue.put(message)

    def _parse_task_ids(self, value: str) -> list[str]:
        return parse_task_ids(value)

    def _log(self, message: str) -> None:
        entry = self._format_log_entry(message)
        if hasattr(self, "log_empty_canvas"):
            self.log_empty_canvas.place_forget()
        if hasattr(self, "log_empty_label"):
            self.log_empty_label.place_forget()
        if hasattr(self, "log_empty_detail_label"):
            self.log_empty_detail_label.place_forget()
        self.log_text.configure(state="normal")
        self.log_text.insert("end", entry)
        if not hasattr(self, "auto_scroll_var") or bool(self.auto_scroll_var.get()):
            self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _format_log_entry(self, message: str) -> str:
        timestamp = datetime.now().strftime("%H:%M:%S")
        lines = str(message).splitlines() or [""]
        first = f"[{timestamp}] {lines[0]}"
        if len(lines) == 1:
            return first + "\n"
        continuation = "\n".join(f"           {line}" for line in lines[1:])
        return f"{first}\n{continuation}\n\n"

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)
        if hasattr(self, "status_hint_var"):
            if message == "运行中":
                self.status_hint_var.set("正在累计观看时长")
            elif "Cookie" in message or "凭据" in message:
                self.status_hint_var.set("正在读取本机凭据")
            else:
                self.status_hint_var.set("点击「开始挂宝」启动")
        if self.status_label is not None:
            style = "StatusRunning.TLabel" if message == "运行中" else "Status.TLabel"
            self.status_label.configure(style=style)
        if hasattr(self, "start_button"):
            if message == "运行中":
                self.start_button.set_appearance(text="停止挂宝", fill=SURFACE, foreground=DANGER, active_fill=DANGER_BG)
            else:
                self.start_button.set_appearance(text="▶ 开始挂宝", fill=PRIMARY, foreground="#ffffff", active_fill=PRIMARY_ACTIVE)

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
        self._sync_progress_visual(message)
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
        self._sync_progress_visual(message)
        self._render_progress_text()

    def _sync_progress_visual(self, message: str) -> None:
        if not hasattr(self, "progress_ring"):
            return
        text = message.strip()
        claimable_match = re.search(r"(?:检测到\s*(\d+)\s*个奖励可以领取|已有\s*(\d+)\s*个任务完成)", text)
        if claimable_match and hasattr(self, "reward_title_var"):
            count = claimable_match.group(1) or claimable_match.group(2)
            self.reward_title_var.set(f"{count} 次")
            self.reward_detail_var.set("当前有奖励可领取")
            self.reward_status_var.set(f"领奖：{count} 次可领")
            self.progress_ring.set_state(text="可领", caption="等待领取", value=1.0, color=SUCCESS)
            self.progress_title_var.set(f"{count} 个奖励可领取")
            self.progress_detail_var.set("自动领取开启时会自动提交，也可以手动点击领取。")
            return
        progress_match = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*分钟", text)
        if progress_match:
            current = float(progress_match.group(1))
            target = max(float(progress_match.group(2)), 0.01)
            ratio = min(max(current / target, 0.0), 1.0)
            percent = int(round(ratio * 100))
            self.progress_ring.set_state(text=f"{percent}%", caption="当前进度", value=ratio, color=SUCCESS if ratio >= 1 else ACCENT)
            self.progress_title_var.set(f"{self._format_progress_number(current)} / {self._format_progress_number(target)} 分钟")
            self.progress_detail_var.set("任务进度已同步，完成后可领取奖励。")
            return
        remaining_match = re.search(r"还差\s*(\d+(?:\.\d+)?)\s*分钟", text)
        if remaining_match:
            remaining = float(remaining_match.group(1))
            self.progress_ring.set_state(text="计时中", caption="剩余时长", value=0.32, color=ACCENT)
            self.progress_title_var.set(f"还差 {self._format_progress_number(remaining)} 分钟")
            self.progress_detail_var.set("后台正在累计观看时长。")
            return
        if "已领取" in text or "已跳过" in text or "领取成功" in text:
            self.progress_ring.set_state(text="完成", caption="奖励状态", value=1.0, color=SUCCESS)
            if "已跳过" in text:
                self.progress_title_var.set("已跳过")
                self.progress_detail_var.set("该奖励已经处理过，后续日志会继续记录。")
                self.reward_title_var.set("已跳过")
                self.reward_detail_var.set("该奖励此前已经领取或处理")
            else:
                self.progress_title_var.set("已领取")
                self.progress_detail_var.set("奖励领取完成，后续日志会继续记录。")
                self.reward_title_var.set("已领取")
                self.reward_detail_var.set("领取结果已写入运行日志")
            self.reward_status_var.set("领奖：已完成")
            return
        if "领取线程正在运行中" in text:
            self.progress_ring.set_state(text="领取", caption="运行中", value=0.82, color=ACCENT)
            self.progress_title_var.set("领取中")
            self.progress_detail_var.set("已有领取任务在执行，请等待日志结果。")
            self.reward_title_var.set("领取中")
            self.reward_detail_var.set("已有领取任务在执行")
            self.reward_status_var.set("领奖：领取中")
            return
        if "开始领取奖励" in text or "正在领取" in text or "领取前刷新任务进度" in text:
            self.progress_ring.set_state(text="领取", caption="提交中", value=0.86, color=ACCENT)
            self.progress_title_var.set("领取中")
            self.progress_detail_var.set("正在刷新任务进度并提交领取请求。")
            self.reward_title_var.set("领取中")
            self.reward_detail_var.set("正在刷新任务进度并提交领取请求")
            self.reward_status_var.set("领奖：领取中")
            return
        if "未检测到可领取任务" in text or "暂时无法领取" in text or "缺少主播 UID" in text:
            self.progress_ring.set_state(text="计时", caption="暂无可领", value=0.38, color=ACCENT)
            self.progress_title_var.set("暂无可领取")
            self.progress_detail_var.set("继续累计观看时长，或稍后手动刷新进度。")
            self.reward_title_var.set("暂无可领")
            self.reward_detail_var.set("没有检测到已完成奖励")
            self.reward_status_var.set("领奖：暂无可领")
            return
        if "未登录" in text or "登录状态失效" in text or "Cookie 未登录" in text:
            self.progress_ring.set_state(text="失效", caption="重新获取", value=0.1, color=DANGER)
            self.progress_title_var.set("账号未登录")
            self.progress_detail_var.set("请重新自动获取 Cookie 后再开始挂宝。")
            self.reward_title_var.set("不可领取")
            self.reward_detail_var.set("账号未登录或 Cookie 已过期")
            self.reward_status_var.set("领奖：账号未登录")
            if hasattr(self, "cookie_validation_var"):
                self.cookie_validation_var.set("Cookie 未登录")
            return
        if "账号登录正常" in text:
            self.progress_ring.set_state(text="登录", caption="账号正常", value=0.18, color=ACCENT)
            self.progress_title_var.set("账号已登录")
            self.progress_detail_var.set("正在识别直播间和掉宝任务。")
            if hasattr(self, "cookie_validation_var"):
                self.cookie_validation_var.set("Cookie 已登录")
            return
        if "没有读到活动任务列表" in text or "暂时没有读到可跟踪的掉宝任务" in text or "任务进度检查失败" in text:
            self.progress_ring.set_state(text="等待", caption="任务列表", value=0.16, color=ACCENT)
            self.progress_title_var.set("等待任务进度")
            self.progress_detail_var.set("暂时没有读到任务列表，程序会自动重试。")
            if hasattr(self, "reward_title_var") and self.reward_title_var.get() in {"待检查", "检查中", "等待同步", "--"}:
                self.reward_title_var.set("待同步")
                self.reward_detail_var.set("等待 B 站返回任务列表")
            return
        if "已找到本次活动任务" in text or "活动任务已识别" in text or "已自动找到任务列表" in text:
            self.progress_ring.set_state(text="已识别", caption="任务列表", value=0.2, color=ACCENT)
            self.progress_title_var.set("任务已识别")
            self.progress_detail_var.set("正在等待 B 站返回真实奖励进度。")
            if hasattr(self, "reward_title_var") and self.reward_title_var.get() in {"待检查", "检查中", "待同步", "等待同步", "--"}:
                self.reward_title_var.set("检查中")
                self.reward_detail_var.set("正在识别可领取奖励")
                self.reward_status_var.set("领奖：检查中")
            return
        if "后台计时" in text and ("暂时失败" in text or "稍后重试" in text):
            self.progress_ring.set_state(text="计时", caption="重试中", value=0.24, color=ACCENT)
            self.progress_title_var.set("后台重试中")
            self.progress_detail_var.set("单路计时暂时失败，后台会自动重试。")
            return
        if "领取失败" in text or "守护循环异常" in text:
            self.progress_ring.set_state(text="异常", caption="需要查看", value=0.18, color=DANGER)
            self.progress_title_var.set("需要处理")
            self.progress_detail_var.set("查看运行日志里的失败原因。")
            self.reward_title_var.set("领取失败")
            self.reward_detail_var.set("查看运行日志里的失败原因")
            self.reward_status_var.set("领奖：失败")
            return
        if "已启动" in text or "后台计时" in text or "计时" in text:
            self.progress_ring.set_state(text="计时", caption="后台运行", value=0.22, color=ACCENT)
            self.progress_title_var.set("运行中")
            self.progress_detail_var.set("正在等待 B 站返回任务进度。")
            if hasattr(self, "reward_title_var") and self.reward_title_var.get() in {"待检查", "待同步", "等待同步", "--"}:
                self.reward_title_var.set("检查中")
                self.reward_detail_var.set("正在识别可领取奖励")
            return
        if "等待任务检查" in text or "未启动" in text:
            self.progress_ring.set_state(text="待启动", caption="点开始挂宝", value=0.0, color=ACCENT)
            self.progress_title_var.set("未开始")
            self.progress_detail_var.set("开始后显示观看分钟数")
            self.reward_title_var.set("待检查")
            self.reward_detail_var.set("开始后显示可领取次数")

    def _format_progress_number(self, value: float) -> str:
        if abs(value - round(value)) < 0.01:
            return str(int(round(value)))
        return f"{value:.1f}".rstrip("0").rstrip(".")

    def _render_progress_text(self) -> None:
        parts = [*self.progress_events]
        if self.progress_snapshot:
            parts.append("")
            parts.append(self.progress_snapshot)
        current_content = self.progress_text.get("1.0", "end").strip() if hasattr(self, "progress_text") else ""
        next_content = "\n".join(parts).strip()
        if current_content == next_content:
            return
        self.progress_text.configure(state="normal")
        self.progress_text.delete("1.0", "end")
        self.progress_text.insert("1.0", next_content + "\n")
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
            "掉宝任务进度检查失败",
            "活动任务进度检查失败",
            "开始领取奖励",
            "检测到任务已完成",
            "检测到 ",
            "任务进度",
            "正在领取：",
            "已领取：",
            "已跳过：",
            "领取失败：",
            "缺少主播 UID",
            "B 站提示操作太快",
            "已有 ",
            "已找到本次活动任务",
            "活动任务已更新",
            "没有读到活动任务列表",
            "当前直播页暂时",
            "账号未登录",
            "Cookie 未登录",
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
                self._refresh_cookie_placeholder()
                self.cookie_validation_var.set("Cookie 已登录")
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
                self._log(message)
                continue
            if self._is_progress_message(message):
                self._progress_log(message)
                self._log(message)
                continue
            self._log(message)
        self.after(200, self._drain_logs)

    def _poll_watch_status(self) -> None:
        try:
            if self.watcher and self.watcher.running:
                snapshot, summary = self.watcher.get_watch_status_snapshot()
                self.watch_status_card.update_snapshot(snapshot, summary)
                self._refresh_backend_summary(snapshot)
            else:
                self.watch_status_card.update_snapshot([], "后台计时状态：未启动")
                self._refresh_backend_summary([])
        finally:
            self.after(1000, self._poll_watch_status)

    def destroy(self) -> None:
        if self.watcher:
            self.watcher.stop()
        super().destroy()


def main() -> None:
    app = App()
    app.mainloop()
