# Interference Recorder

Interference Recorder is an Anki Desktop add-on for recording cases where one card is
mistaken for another.

The current dummy implementation adds **Tools → Interference Recorder**. Selecting it
shows a confirmation message that the add-on has loaded successfully.

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
