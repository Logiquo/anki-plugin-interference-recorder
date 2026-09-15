"""Registration of Anki UI hooks."""

from __future__ import annotations

from aqt import gui_hooks, mw
from aqt.qt import QAction
from aqt.utils import showInfo

ADDON_NAME = "Interference Recorder"

_hooks_registered = False
_tools_action: QAction | None = None


def _show_dummy_window() -> None:
    """Show a placeholder until the graph UI is implemented."""
    showInfo(f"{ADDON_NAME} is installed and running.", parent=mw)


def _add_tools_menu_action() -> None:
    """Add the add-on's placeholder action after Anki initializes its UI."""
    global _tools_action

    if _tools_action is not None:
        return

    action = QAction(ADDON_NAME, mw)
    action.triggered.connect(_show_dummy_window)
    mw.form.menuTools.addAction(action)
    _tools_action = action


def register_hooks() -> None:
    """Register add-on hooks exactly once."""
    global _hooks_registered

    if _hooks_registered:
        return

    gui_hooks.main_window_did_init.append(_add_tools_menu_action)
    _hooks_registered = True
