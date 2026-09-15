"""Confirmation dialog for a paired Again action."""

from __future__ import annotations

import json

from anki.cards import Card
from aqt import gui_hooks, mw
from aqt.qt import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QKeySequence,
    QPushButton,
    QShortcut,
    QVBoxLayout,
    QWidget,
)
from aqt.reviewer import Reviewer
from aqt.theme import theme_manager
from aqt.utils import tr
from aqt.webview import AnkiWebView, AnkiWebViewKind


class ConfirmInterferenceDialog(QDialog):
    """Show both card backs and request one paired Again confirmation."""

    def __init__(
        self,
        source_card: Card,
        target_card: Card,
        reviewer: Reviewer,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Record Interference")
        self.resize(1100, 620)

        source_panel, self.source_preview = self._card_panel(self)
        target_panel, self.target_preview = self._card_panel(self)

        divider = QFrame(self)
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)

        cards_layout = QHBoxLayout()
        cards_layout.addWidget(source_panel)
        cards_layout.addWidget(divider)
        cards_layout.addWidget(target_panel)

        again_button = QPushButton(tr.studying_again(), self)
        cancel_button = QPushButton(tr.actions_cancel(), self)

        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()
        buttons_layout.addWidget(again_button)
        buttons_layout.addWidget(cancel_button)
        buttons_layout.addStretch()

        layout = QVBoxLayout(self)
        layout.addLayout(cards_layout)
        layout.addLayout(buttons_layout)

        again_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        self.finished.connect(self._cleanup)

        again_key = mw.pm.get_answer_key(1)
        self.again_shortcut: QShortcut | None = None
        if again_key:
            self.again_shortcut = QShortcut(QKeySequence(again_key), self)
            self.again_shortcut.activated.connect(self.accept)
            again_button.setToolTip(f"{tr.actions_shortcut_key(val=again_key)}")

        self._initialize_preview(self.source_preview, reviewer)
        self._initialize_preview(self.target_preview, reviewer)
        self._render_answer(self.source_preview, source_card)
        self._render_answer(self.target_preview, target_card)

    @staticmethod
    def _card_panel(parent: QWidget) -> tuple[QWidget, AnkiWebView]:
        panel = QWidget(parent)
        layout = QVBoxLayout(panel)
        preview = AnkiWebView(panel, kind=AnkiWebViewKind.PREVIEWER)
        layout.addWidget(preview)
        return panel, preview

    @staticmethod
    def _initialize_preview(preview: AnkiWebView, reviewer: Reviewer) -> None:
        preview.stdHtml(
            reviewer.revHtml(),
            css=["css/reviewer.css"],
            js=[
                "js/mathjax.js",
                "js/vendor/mathjax/tex-chtml-full.js",
                "js/reviewer.js",
            ],
            context=reviewer,
        )

    @staticmethod
    def _render_answer(preview: AnkiWebView, card: Card) -> None:
        answer = mw.prepare_card_text_for_display(card.answer())
        answer = gui_hooks.card_will_show(answer, card, "interferenceConfirmAnswer")
        body_class = theme_manager.body_classes_for_card_ord(card.ord)
        preview.eval(
            f"_showAnswer({json.dumps(answer)}, {json.dumps(body_class)});"
        )

    def _cleanup(self, _result: int) -> None:
        self.source_preview.cleanup()
        self.target_preview.cleanup()
