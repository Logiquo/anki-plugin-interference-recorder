"""Interactive interference graph and filtered-deck creation UI."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from anki.cards import CardId
from anki.errors import AnkiException
from aqt import colors, gui_hooks, mw
from aqt.operations import CollectionOp, QueryOp
from aqt.qt import (
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    Qt,
    QVBoxLayout,
    QWidget,
)
from aqt.theme import theme_manager
from aqt.utils import askUser, showInfo, showWarning
from aqt.webview import AnkiWebView, AnkiWebViewKind

from .filtered_deck import FilteredDeckResult, create_filtered_deck
from .graph_model import GraphData, cards_for_threshold, load_graph_data, validate_decay


class InterferenceGraphDialog(QDialog):
    """Single graph window with automatic layout and a card-back panel."""

    def __init__(self, addon_module: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._addon_module = addon_module
        self._graph_data: GraphData | None = None
        self._selected_card_id: int | None = None
        self._loading = False

        self.setWindowTitle("Interference Graph")
        self.resize(1240, 720)

        self.graph_status = QLabel("Loading interference graph…", self)
        self.refresh_button = QPushButton("Refresh", self)
        graph_toolbar = QHBoxLayout()
        graph_toolbar.addWidget(self.graph_status)
        graph_toolbar.addStretch()
        graph_toolbar.addWidget(self.refresh_button)

        self.graph_web = AnkiWebView(self, kind=AnkiWebViewKind.DEFAULT)
        self.graph_web.set_bridge_command(self._on_graph_command, self)
        self.graph_web.setHtml(self._graph_html())

        graph_panel = QWidget(self)
        graph_layout = QVBoxLayout(graph_panel)
        graph_layout.addLayout(graph_toolbar)
        graph_layout.addWidget(self.graph_web)

        self.selection_label = QLabel("Select a node to preview its card back.", self)
        self.preview = AnkiWebView(self, kind=AnkiWebViewKind.PREVIEWER)
        reviewer = getattr(mw, "reviewer", None)
        if reviewer is not None:
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
        self.preview.eval("_showAnswer('Select a node to preview its card back.', '');")

        self.threshold = QDoubleSpinBox(self)
        self.threshold.setRange(0, 1_000_000_000)
        self.threshold.setDecimals(4)
        self.threshold.setSingleStep(0.1)
        self.deck_name = QLineEdit(self)
        self.deck_name.setPlaceholderText("Use Anki's default filtered-deck name")
        self.create_deck_button = QPushButton("Create Filtered Deck", self)
        self.create_deck_button.setEnabled(False)

        deck_form = QFormLayout()
        deck_form.addRow("Minimum edge score:", self.threshold)
        deck_form.addRow("Optional deck name:", self.deck_name)

        preview_panel = QWidget(self)
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.addWidget(self.selection_label)
        preview_layout.addWidget(self.preview, stretch=1)
        preview_layout.addLayout(deck_form)
        preview_layout.addWidget(self.create_deck_button)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(graph_panel)
        splitter.addWidget(preview_panel)
        splitter.setSizes([790, 450])

        close_button = QPushButton("Close", self)
        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter)
        layout.addLayout(button_row)

        self.refresh_button.clicked.connect(self.refresh)
        self.create_deck_button.clicked.connect(self._create_filtered_deck)
        close_button.clicked.connect(self.close)
        self.finished.connect(self._cleanup)

        self.refresh()

    def _graph_html(self) -> str:
        addon = mw.addonManager.addonFromModule(self._addon_module)
        base = f"/_addons/{addon}/web"
        canvas = theme_manager.var(colors.CANVAS)
        foreground = theme_manager.var(colors.FG)
        elevated = theme_manager.var(colors.CANVAS_ELEVATED)
        border = theme_manager.var(colors.BORDER)
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
html, body, #graph {{ width:100%; height:100%; margin:0; overflow:hidden; }}
body {{ background:{canvas}; color:{foreground}; font-family:sans-serif; }}
#graph-status {{ position:absolute; inset:0; display:flex; align-items:center;
  justify-content:center; z-index:2; color:{foreground}; pointer-events:none; }}
#graph-status[hidden] {{ display:none; }}
#tooltip {{ position:absolute; z-index:3; white-space:pre; pointer-events:none;
  padding:6px 8px; border:1px solid {border}; border-radius:4px;
  color:{foreground}; background:{elevated}; font-size:12px; }}
#legend {{ position:absolute; left:10px; bottom:10px; z-index:2; padding:5px 8px;
  border:1px solid {border}; border-radius:4px; background:{elevated};
  color:{foreground}; font-size:11px; }}
.gradient {{ display:inline-block; width:90px; height:9px; margin:0 5px;
  background:linear-gradient(90deg,rgb(48,176,72),rgb(255,48,48)); }}
</style></head><body>
<div id="graph"></div><div id="graph-status">Loading…</div>
<div id="tooltip" hidden></div>
<div id="legend">Low <span class="gradient"></span> High</div>
<script src="{base}/vendor/cytoscape.min.js"></script>
<script src="{base}/vendor/layout-base.js"></script>
<script src="{base}/vendor/cose-base.js"></script>
<script src="{base}/vendor/cytoscape-fcose.js"></script>
<script src="{base}/graph.js"></script>
</body></html>"""

    def refresh(self) -> None:
        if self._loading:
            return
        config = mw.addonManager.getConfig(self._addon_module) or {}
        try:
            decay = validate_decay(config.get("decay", 0.9))
        except RuntimeError as error:
            showWarning(f"Could not build graph: {error}", parent=self)
            return

        self._loading = True
        self.refresh_button.setEnabled(False)
        self.graph_status.setText("Loading interference graph…")
        QueryOp(
            parent=self,
            op=lambda col: load_graph_data(col, decay),
            success=self._graph_loaded,
        ).failure(self._graph_failed).run_in_background()

    def _graph_loaded(self, data: GraphData) -> None:
        self._loading = False
        self.refresh_button.setEnabled(True)
        self._graph_data = data
        self._clear_selection()
        self.graph_status.setText(
            f"{len(data.nodes)} node(s), {len(data.edges)} edge(s)"
            + (
                f" — {data.missing_event_count} missing-card record(s) hidden"
                if data.missing_event_count
                else ""
            )
        )
        payload = json.dumps(asdict(data), ensure_ascii=False)
        self.graph_web.eval(f"renderInterferenceGraph({payload});")

    def _graph_failed(self, error: Exception) -> None:
        self._loading = False
        self.refresh_button.setEnabled(True)
        self.graph_status.setText("Unable to load graph.")
        showWarning(f"Could not build graph: {error}", parent=self)

    def _on_graph_command(self, command: str) -> None:
        if command.startswith("select-node:"):
            try:
                card_id = int(command.removeprefix("select-node:"))
            except ValueError:
                return
            self._select_card(card_id)
        elif command == "clear-selection":
            self._clear_selection()
        elif command == "layout-overlap-warning":
            showWarning(
                "The automatic layout could not fully separate every node label.",
                parent=self,
            )

    def _select_card(self, card_id: int) -> None:
        data = self._graph_data
        if data is None:
            return
        node = next((node for node in data.nodes if node.card_id == card_id), None)
        if node is None:
            return

        self._selected_card_id = card_id
        self.selection_label.setText(f"Card {card_id} — node score {node.score:.4f}")
        incident_scores = [
            edge.score
            for edge in data.edges
            if edge.source == card_id or edge.target == card_id
        ]
        self.threshold.setValue(min(incident_scores, default=0.0))
        self.create_deck_button.setEnabled(True)
        self._render_preview(CardId(card_id))

    def _render_preview(self, card_id: CardId) -> None:
        collection = mw.col
        if collection is None:
            return
        try:
            card = collection.get_card(card_id)
            answer = mw.prepare_card_text_for_display(card.answer())
        except AnkiException as error:
            self.preview.eval(
                f"_showAnswer({json.dumps(f'Unable to render this card: {error}')}, '');"
            )
            return
        answer = gui_hooks.card_will_show(answer, card, "interferenceGraphAnswer")
        body_class = theme_manager.body_classes_for_card_ord(card.ord)
        self.preview.eval(
            f"_showAnswer({json.dumps(answer)}, {json.dumps(body_class)});"
        )

    def _clear_selection(self) -> None:
        self._selected_card_id = None
        self.selection_label.setText("Select a node to preview its card back.")
        self.create_deck_button.setEnabled(False)
        self.preview.eval("_showAnswer('Select a node to preview its card back.', '');")

    def _create_filtered_deck(self) -> None:
        data = self._graph_data
        card_id = self._selected_card_id
        if data is None or card_id is None:
            return
        threshold = self.threshold.value()
        card_ids = cards_for_threshold(data, card_id, threshold)
        custom_name = self.deck_name.text().strip()
        shown_name = custom_name or "Anki's default filtered-deck name"
        if not askUser(
            f"Create filtered deck?\n\n"
            f"Name: {shown_name}\n"
            f"Minimum edge score: {threshold:.4f}\n"
            f"Requested cards: {len(card_ids)}",
            parent=self,
            defaultno=True,
            title="Interference Recorder",
        ):
            return

        self.create_deck_button.setEnabled(False)

        def succeeded(result: FilteredDeckResult) -> None:
            self.create_deck_button.setEnabled(True)
            skipped = result.requested - result.added
            showInfo(
                f'Created filtered deck "{result.deck_name}".\n\n'
                f"Requested: {result.requested}\n"
                f"Added: {result.added}\n"
                f"Skipped: {skipped}",
                parent=self,
            )

        def failed(error: Exception) -> None:
            self.create_deck_button.setEnabled(True)
            showWarning(f"Could not create filtered deck: {error}", parent=self)

        CollectionOp[Any](
            self,
            lambda col: create_filtered_deck(col, card_ids, custom_name),
        ).success(succeeded).failure(failed).run_in_background()

    def _cleanup(self, _result: int) -> None:
        # close() hides this singleton; the webviews remain reusable on reopen.
        pass
