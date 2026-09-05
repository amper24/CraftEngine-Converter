"""Entry point for launching the GUI (used by build.bat / PyInstaller)."""

from converter.gui import run_gui

if __name__ == "__main__":
    raise SystemExit(run_gui())