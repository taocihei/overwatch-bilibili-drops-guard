from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TkStartupRegressionTest(unittest.TestCase):
    def test_retry_does_not_leave_a_blank_tk_window(self) -> None:
        self._run_startup_probe(1)

    def test_retry_exhaustion_cleans_up_all_partial_roots(self) -> None:
        self._run_startup_probe(3)

    def test_non_retryable_tcl_error_also_cleans_up_the_root(self) -> None:
        self._run_startup_probe(1, error="non-retryable initialization error")

    def test_repeated_process_restarts_leave_no_root_or_process(self) -> None:
        for launch in range(12):
            with self.subTest(launch=launch):
                self._run_startup_probe(launch % 3)

    def _run_startup_probe(self, failures: int, *, error: str = "couldn't read file ttk.tcl") -> None:
        # Separate process: multiple Tcl interpreters must not affect other GUI tests.
        probe = r'''
import sys
import tkinter as tk
from unittest.mock import patch
from bili_drop_guard import gui

original_loadtk = tk.Tk._loadtk
interpreters = []
app = None
failures = int(sys.argv[1])
error = sys.argv[2]
expect_error = failures >= 3 or not error.startswith("couldn't read file")

def interrupted_loadtk(self):
    original_loadtk(self)
    interpreters.append(self.tk)
    self.tk.call('wm', 'withdraw', '.')
    if len(interpreters) <= failures:
        raise tk.TclError(error)

def exists(interpreter):
    try:
        return bool(int(interpreter.call('winfo', 'exists', '.')))
    except tk.TclError:
        return False

try:
    with patch.object(tk.Tk, '_loadtk', interrupted_loadtk):
        try:
            app = gui.App(preview_mode=True)
        except tk.TclError as exc:
            assert expect_error and str(exc) == error, str(exc)
        else:
            assert not expect_error, 'startup should have failed'
    if app is not None:
        app.withdraw()
        app.update_idletasks()
        assert app.room_var._tk is app.tk, 'variables use an old default root'
        assert tk._default_root is app, 'default root points at an old window'
    candidates = interpreters if app is None else interpreters[:-1]
    leftovers = [i for i in candidates if exists(i)]
    titles = [str(i.call('wm', 'title', '.')) for i in leftovers]
    assert not leftovers, f'orphan root windows after retry: {titles}'
finally:
    try:
        if app is not None:
            app.destroy()
        assert not any(exists(i) for i in interpreters), 'root retained after shutdown'
    finally:
        for interpreter in interpreters:
            if exists(interpreter):
                interpreter.call('destroy', '.')
assert tk._default_root is None, 'default root retained after shutdown'
'''
        result = subprocess.run(
            [sys.executable, '-c', probe, str(failures), error], cwd=ROOT,
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
