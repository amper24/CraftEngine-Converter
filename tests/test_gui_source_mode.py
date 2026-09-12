"""Exercise the tkinter GUI's source-detection path without a display.

tkinter is not installable in every environment, so this test injects a minimal
stand-in module and then drives the real ``App`` methods that decide how an
input is handled. It verifies the ItemsAdder branch of the preflight actually
runs, reports the layout, and hands off to conversion.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from make_itemsadder_fixture import build as build_fixture  # noqa: E402


class _Var:
    def __init__(self, value=""):
        self._v = value

    def get(self):
        return self._v

    def set(self, value):
        self._v = value


class _Widget:
    def __init__(self, *a, **kw):
        self.kwargs = kw

    def pack(self, *a, **kw):
        return self

    def grid(self, *a, **kw):
        return self

    def configure(self, **kw):
        self.kwargs.update(kw)

    def insert(self, *a, **kw):
        return self

    def see(self, *a, **kw):
        return self

    def start(self, *a, **kw):
        return self

    def stop(self, *a, **kw):
        return self

    def columnconfigure(self, *a, **kw):
        return self

    def after(self, _ms, fn=None, *a, **kw):
        # Run callbacks synchronously so the test sees real behaviour.
        if fn is not None:
            fn(*a, **kw)

    def wait_window(self, *a, **kw):
        return self


def _install_fake_tkinter() -> None:
    tk = types.ModuleType("tkinter")
    ttk = types.ModuleType("tkinter.ttk")

    class Tk(_Widget):
        def title(self, *a, **kw):
            return self

        def geometry(self, *a, **kw):
            return self

        def minsize(self, *a, **kw):
            return self

    tk.Tk = Tk
    tk.Toplevel = type("Toplevel", (_Widget,), {})
    tk.StringVar = _Var
    tk.BooleanVar = _Var
    tk.StringVar
    tk.TclError = type("TclError", (Exception,), {})
    tk.Misc = object
    tk.Frame = _Widget
    tk.Label = _Widget
    tk.Entry = _Widget

    for name in ("Style", "Frame", "Label", "Entry", "Button", "Checkbutton",
                 "Combobox", "LabelFrame", "Progressbar", "Treeview", "Notebook",
                 "Spinbox", "Scrollbar", "Separator"):
        setattr(ttk, name, _Widget)

    scrolledtext = types.ModuleType("tkinter.scrolledtext")
    scrolledtext.ScrolledText = _Widget
    filedialog = types.ModuleType("tkinter.filedialog")
    filedialog.askopenfilename = lambda *a, **kw: ""
    filedialog.askdirectory = lambda *a, **kw: ""
    messagebox = types.ModuleType("tkinter.messagebox")
    messagebox.showinfo = lambda *a, **kw: None
    messagebox.showwarning = lambda *a, **kw: None
    messagebox.showerror = lambda *a, **kw: None

    tk.ttk = ttk
    tk.scrolledtext = scrolledtext
    tk.filedialog = filedialog
    tk.messagebox = messagebox
    sys.modules["tkinter"] = tk
    sys.modules["tkinter.ttk"] = ttk
    sys.modules["tkinter.scrolledtext"] = scrolledtext
    sys.modules["tkinter.filedialog"] = filedialog
    sys.modules["tkinter.messagebox"] = messagebox


def test_gui_reports_itemsadder_layout_and_converts():
    _install_fake_tkinter()
    pack = build_fixture()

    from converter import gui as gui_module
    from converter.config import Settings

    app = gui_module.App.__new__(gui_module.App)
    app.root = _Widget()
    app.settings_manager = type("M", (), {"settings": Settings(), "save": lambda self, s: None})()
    app.settings = app.settings_manager.settings
    app.mod_var = _Var(str(pack))
    app.out_var = _Var("")
    app.minecraft_var = _Var("1.21.4")
    app.ce_var = _Var("26.8")
    app.source_var = _Var("auto")
    app.interactive_var = _Var(True)
    app.strict_var = _Var(False)
    app.sliceboard_var = _Var(True)
    app.status_var = _Var("")
    app.counts_var = _Var("")
    app.progress = _Widget()
    app.log = _Widget()
    app.last_output = None

    logged: list[str] = []
    app._log = lambda text: logged.append(text)  # type: ignore[method-assign]

    started = []
    app._start_conversion = lambda: started.append(True)  # type: ignore[method-assign]

    from converter import driver

    info = driver.describe_source(str(pack), app.settings)
    app._on_source_described(info, convert=True)

    text = "\n".join(logged)
    assert "ItemsAdder" in text, text
    assert "rubbishpack" in text, text
    assert "decoration" in text, text
    # The namespace dialog must be skipped entirely for this source.
    assert started == [True]


def test_gui_falls_back_to_namespace_dialog_for_mods():
    _install_fake_tkinter()
    jar = ROOT / "tests" / "farmersdelight_fixture.jar"
    if not jar.exists():
        import pytest

        pytest.skip("mod fixture not built")

    from converter import gui as gui_module
    from converter.config import Settings

    # The mod branch does its work on a worker thread; run it inline so the
    # assertions below observe a finished callback rather than a race.
    class _SyncThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    original_thread = gui_module.threading.Thread
    gui_module.threading.Thread = _SyncThread  # type: ignore[attr-defined]

    try:
        app = gui_module.App.__new__(gui_module.App)
        app.root = _Widget()
        app.settings_manager = type("M", (), {"settings": Settings(), "save": lambda self, s: None})()
        app.settings = app.settings_manager.settings
        app.mod_var = _Var(str(jar))
        app.out_var = _Var("")
        app.minecraft_var = _Var("1.21.4")
        app.status_var = _Var("")
        app.progress = _Widget()
        app.log = _Widget()
        app._log = lambda text: None  # type: ignore[method-assign]

        shown = []
        app._show_mapping_then_convert = lambda data: shown.append(data)  # type: ignore[method-assign]

        from converter import driver

        info = driver.describe_source(str(jar), app.settings)
        assert info["kind"] == "mod"
        app._on_source_described(info, convert=True)

        assert len(shown) == 1
        # The synthetic fixture only references its own namespace, so there is
        # nothing to remap; what matters is that the dialog data was produced.
        assert set(shown[0]) >= {"mod", "namespaces", "excluded"}
        assert shown[0]["mod"]["namespace"] == "farmersdelight"
    finally:
        gui_module.threading.Thread = original_thread  # type: ignore[attr-defined]
