# Interference Recorder

Interference Recorder is an Anki Desktop add-on for recording cases where one card is
mistaken for another.

The current implementation provides:

- **Tools → Interference Recorder** as a submenu with data cleanup and graph entries.
- **Record Interference** in the Reviewer context menu on the answer side.
- The configurable Reviewer shortcut `0` on the answer side.
- Anki-native card search with debounce and paginated, unlimited matching results.
- A split search view with results on the left and the selected card's rendered back on the right.
- A confirmation view showing the rendered backs of A and B side by side.
- One translated Again button, Anki's configured Again shortcut, and a Cancel button.
- One Anki `grade_now` operation that grades A and B Again with a single undo step.

Interference events are stored in Anki-native, synchronized log shards:

- Dedicated deck, deck preset, and note type: `_Interference_Link`.
- One local installation UUID identifies each writer.
- Each suspended data card stores up to 500 JSON events before a new numbered part is created.
- A single **Record Interference** undo entry covers both Again ratings and the appended event.

The **Show Graph** entry builds an interactive card graph from interference events and
successful normal reviews. Scores decay according to the configured `decay` value;
node and edge colors run from green at lower scores to red at higher scores. The graph
uses an automatic non-overlapping fCoSE layout, supports pan and zoom, and shows the
selected card's rendered back on the right.

From a selected node, choose an edge-score threshold to create a real Anki filtered
deck containing that card and its qualifying direct neighbors. Leaving the optional
name blank uses Anki's own default filtered-deck name.

## Data maintenance

Choose **Tools → Interference Recorder → Clean Missing Card Records…** to remove
events whose source or target Card ID no longer exists. The scan does not merge or
renumber shards; a shard with no remaining events is deleted. The entire cleanup is
one Anki undo step.

The dialog asks you to manually sync all devices before cleaning and manually sync
again afterward. The add-on never starts or controls Anki synchronization itself.

Graph assets are bundled locally, so graph display does not require network access.
The bundled Cytoscape.js, layout-base, cose-base, and cytoscape-fcose components are
distributed under their respective MIT licenses in `web/vendor/`.

## Development

The development environment targets Anki/`aqt` 26.8.1 and lives in `.venv`.

```powershell
uv sync
uv run ruff check .
uv run mypy src
uv run pytest
```

For local Anki testing, link `src/anki_plugin_interference_recorder` into the active
profile's `addons21` directory. Anki loads the `__init__.py` at the root of that linked
directory.
