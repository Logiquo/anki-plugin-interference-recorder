"""Registration of Anki UI hooks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from anki.errors import AnkiException
from aqt import gui_hooks, mw
from aqt.main import MainWindowState
from aqt.qt import QAction, QMenu
from aqt.reviewer import Reviewer
from aqt.utils import showInfo

from .search_dialog import CardSearchDialog

ADDON_NAME = "Interference Recorder"
ACTION_NAME = "Record Interference"

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


def _reviewer_is_on_answer(reviewer: Reviewer) -> bool:
    return reviewer.state == "answer" and reviewer.card is not None


def _record_interference(reviewer: Reviewer) -> None:
    """Select a target card and report both identifiers for phase two."""
    if not _reviewer_is_on_answer(reviewer):
        return

    source_card = reviewer.card
    assert source_card is not None

    dialog = CardSearchDialog(
        source_card_id=source_card.id,
        reviewer=reviewer,
        parent=mw,
    )
    if not dialog.exec():
        return

    target_card_id = dialog.selected_card_id
    if target_card_id is None or target_card_id == source_card.id:
        return

    collection = mw.col
    if collection is None:
        showInfo("No Anki collection is currently open.", parent=mw)
        return

    try:
        target_card = collection.get_card(target_card_id)
    except AnkiException:
        showInfo("The selected card no longer exists.", parent=mw)
        return

    showInfo(
        "Interference selection\n\n"
        f"A — Card ID: {source_card.id}, Note ID: {source_card.nid}\n"
        f"B — Card ID: {target_card.id}, Note ID: {target_card.nid}",
        parent=mw,
    )


def _record_current_interference() -> None:
    reviewer = getattr(mw, "reviewer", None)
    if reviewer is not None:
        _record_interference(reviewer)


def _add_reviewer_shortcut(
    state: MainWindowState, shortcuts: list[tuple[str, Callable[..., Any]]]
) -> None:
    if state != "review":
        return

    config = mw.addonManager.getConfig(__name__) or {}
    shortcut = str(config.get("shortcut", "0")).strip()
    if shortcut:
        shortcuts.append((shortcut, _record_current_interference))


def _add_reviewer_context_menu(reviewer: Reviewer, menu: QMenu) -> None:
    if not _reviewer_is_on_answer(reviewer):
        return

    action = menu.addAction(ACTION_NAME)
    assert action is not None
    action.triggered.connect(lambda: _record_interference(reviewer))


def register_hooks() -> None:
    """Register add-on hooks exactly once."""
    global _hooks_registered

    if _hooks_registered:
        return

    gui_hooks.main_window_did_init.append(_add_tools_menu_action)
    gui_hooks.state_shortcuts_will_change.append(_add_reviewer_shortcut)
    gui_hooks.reviewer_will_show_context_menu.append(_add_reviewer_context_menu)
    _hooks_registered = True
