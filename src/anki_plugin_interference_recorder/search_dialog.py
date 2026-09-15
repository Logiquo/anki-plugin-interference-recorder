"""Paginated Anki card search dialog."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import TypeVar

from anki.cards import CardId
from anki.errors import AnkiException
from anki.utils import strip_html
from aqt import gui_hooks, mw
from aqt.qt import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    Qt,
    QTableWidget,
    QTableWidgetItem,
    QTimer,
    QVBoxLayout,
    QWidget,
)
from aqt.reviewer import Reviewer
from aqt.theme import theme_manager
from aqt.webview import AnkiWebView, AnkiWebViewKind

PAGE_SIZE = 100
_ItemId = TypeVar("_ItemId", bound=int)


def page_count(item_count: int, page_size: int = PAGE_SIZE) -> int:
    return max(1, math.ceil(item_count / page_size))


def page_slice(
    items: Sequence[_ItemId], page: int, page_size: int = PAGE_SIZE
) -> Sequence[_ItemId]:
    start = page * page_size
    return items[start : start + page_size]


class CardSearchDialog(QDialog):
    """Search all matching IDs and load one page of card details at a time."""

    def __init__(
        self,
        source_card_id: CardId,
        reviewer: Reviewer,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.source_card_id = source_card_id
        self.selected_card_id: CardId | None = None
        self._matching_card_ids: list[CardId] = []
        self._page_card_ids: list[CardId] = []
        self._page = 0

        self.setWindowTitle("Record Interference")
        self.resize(960, 560)

        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("Enter an Anki search query…")
        self.status_label = QLabel("Enter a query to search for card B.", self)

        self.results = QTableWidget(0, 6, self)
        self.results.setHorizontalHeaderLabels(
            ["Card ID", "Note ID", "First field", "Second field", "Deck", "Note type"]
        )
        self.results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        vertical_header = self.results.verticalHeader()
        horizontal_header = self.results.horizontalHeader()
        assert vertical_header is not None
        assert horizontal_header is not None
        vertical_header.setVisible(False)
        horizontal_header.setStretchLastSection(True)

        self.previous_button = QPushButton("Previous", self)
        self.next_button = QPushButton("Next", self)
        self.page_label = QLabel("Page 1 of 1", self)
        navigation = QHBoxLayout()
        navigation.addWidget(self.previous_button)
        navigation.addWidget(self.page_label)
        navigation.addWidget(self.next_button)
        navigation.addStretch()

        left_panel = QWidget(self)
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(QLabel("Search:", left_panel))
        left_layout.addWidget(self.search_input)
        left_layout.addWidget(self.status_label)
        left_layout.addWidget(self.results)
        left_layout.addLayout(navigation)

        self.preview = AnkiWebView(self, kind=AnkiWebViewKind.PREVIEWER)
        self.preview.stdHtml(
            reviewer.revHtml(),
            css=["css/reviewer.css"],
            js=[
                "js/mathjax.js",
                "js/vendor/mathjax/tex-chtml-full.js",
                "js/reviewer.js",
            ],
            context=reviewer,
        )
        self.preview.eval("_showAnswer('Select a card to preview its back.', '');")

        right_panel = QWidget(self)
        right_layout = QVBoxLayout(right_panel)
        right_layout.addWidget(self.preview)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([560, 400])

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
            parent=self,
        )
        select_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert select_button is not None
        select_button.setText("Select")
        select_button.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter)
        layout.addWidget(self.buttons)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)

        self.search_input.textChanged.connect(self._schedule_search)
        self._debounce.timeout.connect(self._run_search)
        self.previous_button.clicked.connect(self._previous_page)
        self.next_button.clicked.connect(self._next_page)
        self.results.itemSelectionChanged.connect(self._selection_changed)
        self.results.itemDoubleClicked.connect(lambda _item: self._accept_selection())
        self.buttons.accepted.connect(self._accept_selection)
        self.buttons.rejected.connect(self.reject)
        self.finished.connect(self._cleanup_preview)

        self._update_navigation()
        self.search_input.setFocus()

    def _schedule_search(self) -> None:
        self._debounce.start()

    def _run_search(self) -> None:
        query = self.search_input.text().strip()
        self._page = 0
        self.selected_card_id = None

        if not query:
            self._matching_card_ids = []
            self.status_label.setText("Enter a query to search for card B.")
            self._render_page()
            return

        collection = mw.col
        if collection is None:
            self._matching_card_ids = []
            self.status_label.setText("No Anki collection is currently open.")
            self._render_page()
            return

        try:
            self._matching_card_ids = [
                card_id
                for card_id in collection.find_cards(query, order=True)
                if card_id != self.source_card_id
            ]
        except AnkiException as error:
            self._matching_card_ids = []
            self.status_label.setText(f"Search failed: {error}")
            self._render_page()
            return

        self.status_label.setText(f"{len(self._matching_card_ids):,} matching cards")
        self._render_page()

    def _render_page(self) -> None:
        self.results.setRowCount(0)
        collection = mw.col
        if collection is None:
            self._page_card_ids = []
            self.status_label.setText("No Anki collection is currently open.")
            self._update_navigation()
            self._selection_changed()
            return

        candidate_card_ids = page_slice(self._matching_card_ids, self._page)
        self._page_card_ids = []

        for card_id in candidate_card_ids:
            try:
                card = collection.get_card(card_id)
                note = card.note()
            except AnkiException:
                continue

            self._page_card_ids.append(card_id)

            fields = list(note.values())
            note_type = note.note_type()
            values = [
                str(card.id),
                str(card.nid),
                strip_html(fields[0]) if fields else "",
                strip_html(fields[1]) if len(fields) > 1 else "",
                collection.decks.name(card.did),
                str(note_type["name"]) if note_type else "",
            ]

            row = self.results.rowCount()
            self.results.insertRow(row)
            for column, value in enumerate(values):
                self.results.setItem(row, column, QTableWidgetItem(value))

        self.results.resizeColumnsToContents()
        self._update_navigation()
        self._selection_changed()

    def _update_navigation(self) -> None:
        pages = page_count(len(self._matching_card_ids))
        self.page_label.setText(f"Page {self._page + 1} of {pages}")
        self.previous_button.setEnabled(self._page > 0)
        self.next_button.setEnabled(self._page + 1 < pages)

    def _previous_page(self) -> None:
        if self._page > 0:
            self._page -= 1
            self._render_page()

    def _next_page(self) -> None:
        if self._page + 1 < page_count(len(self._matching_card_ids)):
            self._page += 1
            self._render_page()

    def _selection_changed(self) -> None:
        selection_model = self.results.selectionModel()
        select_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert selection_model is not None
        assert select_button is not None
        has_selection = bool(selection_model.selectedRows())
        select_button.setEnabled(has_selection)
        self._render_preview()

    def _render_preview(self) -> None:
        selection_model = self.results.selectionModel()
        assert selection_model is not None
        selected_rows = selection_model.selectedRows()
        if not selected_rows:
            self.preview.eval("_showAnswer('Select a card to preview its back.', '');")
            return

        row = selected_rows[0].row()
        if row >= len(self._page_card_ids):
            return


        collection = mw.col
        if collection is None:
            self.preview.eval("_showAnswer('No Anki collection is currently open.', '');")
            return

        try:
            card = collection.get_card(self._page_card_ids[row])
            answer = mw.prepare_card_text_for_display(card.answer())
        except AnkiException as error:
            message = json.dumps(f"Unable to render this card: {error}")
            self.preview.eval(f"_showAnswer({message}, '');")
            return

        answer = gui_hooks.card_will_show(answer, card, "interferencePreviewAnswer")
        body_class = theme_manager.body_classes_for_card_ord(card.ord)
        self.preview.eval(
            f"_showAnswer({json.dumps(answer)}, {json.dumps(body_class)});"
        )

    def _cleanup_preview(self, _result: int) -> None:
        self.preview.cleanup()

    def _accept_selection(self) -> None:
        selection_model = self.results.selectionModel()
        assert selection_model is not None
        selected_rows = selection_model.selectedRows()
        if not selected_rows:
            return

        row = selected_rows[0].row()
        if row >= len(self._page_card_ids):
            return

        self.selected_card_id = self._page_card_ids[row]
        self.accept()
