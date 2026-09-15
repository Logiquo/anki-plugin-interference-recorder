# Interference Recorder

Interference Recorder is an Anki Desktop add-on for recording cases where one card is
mistaken for another.

The current implementation provides:

- **Tools → Interference Recorder** as an add-on loading check.
- **Record Interference** in the Reviewer context menu on the answer side.
- The configurable Reviewer shortcut `0` on the answer side.
- Anki-native card search with debounce and paginated, unlimited matching results.
- A split search view with results on the left and the selected card's rendered back on the right.
- A temporary confirmation showing the selected A/B Card IDs and Note IDs.

Scheduling and interference persistence are not implemented yet.

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
