"""Registration of Anki UI hooks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from anki.errors import AnkiException
from aqt import gui_hooks, mw
from aqt.main import MainWindowState
from aqt.operations import CollectionOp, QueryOp
from aqt.qt import QMenu
from aqt.reviewer import Reviewer
from aqt.utils import askUser, showInfo, showWarning

from .confirmation_dialog import ConfirmInterferenceDialog
from .graph_dialog import InterferenceGraphDialog
from .maintenance import CleanupPlan, execute_cleanup, scan_missing_card_records
from .search_dialog import CardSearchDialog
from .storage import ensure_storage, get_or_create_writer_id, grade_and_record, new_event

ADDON_NAME = "Interference Recorder"
ACTION_NAME = "Record Interference"

_hooks_registered = False
_tools_menu: QMenu | None = None
_graph_dialog: InterferenceGraphDialog | None = None


def _show_graph() -> None:
    """Show or reactivate the singleton graph window."""
    global _graph_dialog

    if _graph_dialog is None:
        _graph_dialog = InterferenceGraphDialog(__name__, parent=mw)

    _graph_dialog.show()
    _graph_dialog.raise_()
    _graph_dialog.activateWindow()


def _cleanup_summary(plan: CleanupPlan) -> str:
    return (
        f"Scanned shards: {plan.scanned_shards}\n"
        f"Total events: {plan.total_events}\n"
        f"Missing-card events to remove: {plan.missing_events}\n"
        f"Affected shards: {plan.affected_shards}\n"
        f"Empty shards to delete: {plan.empty_shards}"
    )


def _clean_missing_card_records() -> None:
    """Scan, confirm, and clean invalid events without initiating sync."""

    def scan_succeeded(plan: CleanupPlan) -> None:
        if not plan.missing_events:
            showInfo(f"{_cleanup_summary(plan)}\n\nNo cleanup is needed.", parent=mw)
            return

        warning = (
            f"{_cleanup_summary(plan)}\n\n"
            "This will delete interference link records that reference cards "
            "which no longer exist. Before continuing, manually sync every "
            "device. After cleanup, manually sync again.\n\n"
            "Interference Recorder will not start or manage synchronization.\n\n"
            "Continue with cleanup?"
        )
        if not askUser(warning, parent=mw, defaultno=True, title=ADDON_NAME):
            return

        def cleanup_succeeded(_changes: object) -> None:
            showInfo(
                f"Removed {plan.missing_events} event(s) from "
                f"{plan.affected_shards} shard(s); deleted "
                f"{plan.empty_shards} empty shard(s).\n\n"
                "Please manually sync again.",
                parent=mw,
            )

        CollectionOp(mw, lambda col: execute_cleanup(col, plan)).success(
            cleanup_succeeded
        ).failure(
            lambda error: showWarning(f"Could not clean records: {error}", parent=mw)
        ).run_in_background()

    QueryOp(parent=mw, op=scan_missing_card_records, success=scan_succeeded).with_progress(
        "Scanning interference records…"
    ).failure(
        lambda error: showWarning(f"Could not scan records: {error}", parent=mw)
    ).run_in_background()


def _add_tools_menu_action() -> None:
    """Add the add-on submenu after Anki initializes its UI."""
    global _tools_menu

    if _tools_menu is not None:
        return

    mw.addonManager.setWebExports(__name__, r"web/.*")
    menu = mw.form.menuTools.addMenu(ADDON_NAME)
    assert menu is not None
    clean_action = menu.addAction("Clean Missing Card Records…")
    graph_action = menu.addAction("Show Graph")
    assert clean_action is not None and graph_action is not None
    clean_action.triggered.connect(_clean_missing_card_records)
    graph_action.triggered.connect(_show_graph)
    _tools_menu = menu


def _reviewer_is_on_answer(reviewer: Reviewer) -> bool:
    return reviewer.state == "answer" and reviewer.card is not None


def _record_interference(reviewer: Reviewer) -> None:
    """Select and confirm a paired Again action."""
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
        ensure_storage(collection)
        writer_id = get_or_create_writer_id()
    except AnkiException:
        showInfo("The selected card no longer exists.", parent=mw)
        return
    except (OSError, RuntimeError) as error:
        showWarning(f"Could not initialize interference storage: {error}", parent=mw)
        return

    confirmation = ConfirmInterferenceDialog(
        source_card=source_card,
        target_card=target_card,
        reviewer=reviewer,
        parent=mw,
    )
    if not confirmation.exec():
        return

    if not _reviewer_is_on_answer(reviewer) or reviewer.card is None:
        showWarning("The current review card changed before confirmation.", parent=mw)
        return
    if reviewer.card.id != source_card.id:
        showWarning("The current review card changed before confirmation.", parent=mw)
        return

    reviewer.state = "transition"
    event = new_event(source_card.id, target_card.id)

    def on_success(_changes: object) -> None:
        if reviewer.card is not None:
            reviewer.card.load()
        reviewer._after_answering(1)

    def on_failure(error: Exception) -> None:
        reviewer.state = "answer"
        showWarning(f"Could not grade both cards Again: {error}", parent=mw)

    operation = CollectionOp(
        mw,
        lambda col: grade_and_record(col, event, writer_id),
    )
    operation.success(on_success).failure(on_failure).run_in_background(
        initiator=reviewer
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
