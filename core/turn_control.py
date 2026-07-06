"""Agent 运行中的暂停 / 展开 / 中止控制。"""

from __future__ import annotations

import re
import sys
import threading
import time


class TurnAborted(Exception):
    """用户中止当前轮次。"""


class TurnController:
    def __init__(self) -> None:
        self._active = False
        self._tui_mode = False
        self._paused = threading.Event()
        self._abort = threading.Event()
        self._pause_pending = threading.Event()
        self._listener: threading.Thread | None = None
        self._pause_notice_shown = False

    @property
    def active(self) -> bool:
        return self._active

    @property
    def tui_mode(self) -> bool:
        return self._tui_mode

    def set_tui_mode(self, enabled: bool) -> None:
        self._tui_mode = enabled

    def start(self) -> None:
        if self._tui_mode:
            return
        self._active = True
        self._paused.clear()
        self._abort.clear()
        self._pause_pending.clear()
        self._pause_notice_shown = False
        self._listener = threading.Thread(target=self._listen_stdin, daemon=True)
        self._listener.start()

    def stop(self) -> None:
        if self._tui_mode:
            self._paused.clear()
            self._pause_pending.clear()
            return
        self._active = False
        self._paused.set()
        self._pause_pending.set()

    def request_pause(self) -> None:
        self._pause_pending.set()

    def request_resume(self) -> None:
        self._pause_pending.clear()
        self._paused.clear()
        self._pause_notice_shown = False

    def request_abort(self) -> None:
        self._abort.set()
        self._paused.set()
        self._pause_pending.clear()

    def _expand_slot(self, slot: int = 1) -> None:
        from ui import ui

        ui.expand_slot(slot)

    def _list_collapsed(self) -> None:
        from ui import ui

        ui.list_collapsed()

    def _handle_cmd(self, cmd: str) -> None:
        cmd = cmd.strip().lower()
        if not cmd:
            if self._paused.is_set() or self._pause_pending.is_set():
                self.request_resume()
            return

        if cmd in ("pause", "p", "/pause"):
            self.request_pause()
            return
        if cmd in ("resume", "r", "/resume", "continue", "c"):
            self.request_resume()
            return
        if cmd in ("abort", "a", "/abort", "stop", "q"):
            self.request_abort()
            return

        if cmd in ("e", "/e", "/expand", "expand"):
            self._expand_slot(1)
            return
        if cmd in ("list", "/list", "l"):
            self._list_collapsed()
            return

        m = re.match(r"^(?:/e\s*|e)(\d+)$", cmd)
        if m:
            self._expand_slot(int(m.group(1)))
            return
        if re.fullmatch(r"[1-9]", cmd):
            self._expand_slot(int(cmd))
            return

        m = re.match(r"^/e\s+(\d+)$", cmd)
        if m:
            self._expand_slot(int(m.group(1)))

    def _on_escape(self) -> None:
        self.request_pause()

    def _on_immediate_key(self, ch: str, buf: str) -> bool:
        """空缓冲区时单键即时响应（展开 / 列表）。"""
        if buf:
            return False
        low = ch.lower()
        if low == "e":
            self._expand_slot(1)
            return True
        if low == "l":
            self._list_collapsed()
            return True
        if ch in "123456789":
            self._expand_slot(int(ch))
            return True
        return False

    def _listen_stdin(self) -> None:
        if sys.platform == "win32":
            self._listen_stdin_windows()
        else:
            self._listen_stdin_unix()

    def _listen_stdin_windows(self) -> None:
        import msvcrt

        buf = ""
        while self._active:
            if not msvcrt.kbhit():
                time.sleep(0.1)
                continue
            ch = msvcrt.getwch()
            if ch == "\x03":
                self.request_abort()
                continue
            if ch == "\x1b":
                time.sleep(0.03)
                if msvcrt.kbhit():
                    while msvcrt.kbhit():
                        msvcrt.getwch()
                else:
                    self._on_escape()
                continue
            if self._on_immediate_key(ch, buf):
                continue
            if ch in ("\r", "\n"):
                self._handle_cmd(buf)
                buf = ""
            else:
                buf += ch

    def _listen_stdin_unix(self) -> None:
        import select

        buf = ""
        while self._active:
            ready, _, _ = select.select([sys.stdin], [], [], 0.25)
            if not ready:
                continue
            ch = sys.stdin.read(1)
            if not ch:
                continue
            if ch == "\x03":
                self.request_abort()
                continue
            if ch == "\x1b":
                time.sleep(0.03)
                if select.select([sys.stdin], [], [], 0)[0]:
                    while select.select([sys.stdin], [], [], 0)[0]:
                        sys.stdin.read(1)
                else:
                    self._on_escape()
                continue
            if self._on_immediate_key(ch, buf):
                continue
            if ch == "\n":
                self._handle_cmd(buf)
                buf = ""
            else:
                buf += ch

    def checkpoint(self, label: str = "") -> None:
        if self._abort.is_set():
            raise TurnAborted()

        if self._pause_pending.is_set():
            self._paused.set()

        while self._paused.is_set() and not self._abort.is_set():
            if not self._pause_notice_shown:
                from ui import ui

                hint = f" · {label}" if label else ""
                ui.warn(
                    f"已暂停{hint} — e/1-9 展开  list 列表  resume 继续  abort 中止"
                )
                self._pause_notice_shown = True
            time.sleep(0.15)

        if self._abort.is_set():
            raise TurnAborted()

        if not self._pause_pending.is_set():
            self._pause_notice_shown = False


turn_control = TurnController()
