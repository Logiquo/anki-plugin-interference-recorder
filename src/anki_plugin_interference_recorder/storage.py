"""Synced, sharded storage for interference events."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from anki.cards import CardId
from anki.collection import Collection, OpChanges
from anki.decks import DeckConfigDict, DeckId
from anki.errors import AnkiException
from anki.models import NotetypeDict
from anki.scheduler.v3 import CardAnswer

DATA_NAME = "_Interference_Link"
ID_FIELD = "Id"
EVENTS_FIELD = "Events"
EVENTS_PER_PART = 500
MAX_PART = 9999
PART_SUFFIX = "-part-"
UNDO_NAME = "Record Interference"


class StorageError(RuntimeError):
    """Raised when interference storage is missing or incompatible."""


@dataclass(frozen=True)
class InterferenceEvent:
    event_id: str
    source: int
    target: int
    time: str

    def validate(self) -> None:
        try:
            uuid.UUID(self.event_id)
        except (ValueError, AttributeError) as error:
            raise StorageError("event_id is not a valid UUID") from error
        if isinstance(self.source, bool) or not isinstance(self.source, int) or self.source <= 0:
            raise StorageError("source must be a positive Card ID")
        if isinstance(self.target, bool) or not isinstance(self.target, int) or self.target <= 0:
            raise StorageError("target must be a positive Card ID")
        if self.source == self.target:
            raise StorageError("source and target must be different cards")
        try:
            parsed = datetime.fromisoformat(self.time)
        except (TypeError, ValueError) as error:
            raise StorageError("time is not valid ISO 8601") from error
        if parsed.tzinfo is None:
            raise StorageError("time must include a UTC offset")

    @classmethod
    def from_dict(cls, value: object) -> InterferenceEvent:
        if not isinstance(value, dict):
            raise StorageError("event must be a JSON object")
        try:
            event = cls(
                event_id=value["event_id"],
                source=value["source"],
                target=value["target"],
                time=value["time"],
            )
        except KeyError as error:
            raise StorageError(f"event is missing {error.args[0]}") from error
        event.validate()
        return event

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return asdict(self)


def new_event(source: CardId, target: CardId) -> InterferenceEvent:
    event = InterferenceEvent(
        event_id=str(uuid.uuid4()),
        source=int(source),
        target=int(target),
        time=datetime.now().astimezone().isoformat(timespec="milliseconds"),
    )
    event.validate()
    return event


def _user_files_dir() -> Path:
    return Path(__file__).resolve().parent / "user_files"


def get_or_create_writer_id(user_files_dir: Path | None = None) -> str:
    directory = user_files_dir or _user_files_dir()
    path = directory / "writer_id"
    directory.mkdir(parents=True, exist_ok=True)

    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        try:
            return str(uuid.UUID(value))
        except ValueError as error:
            raise StorageError(f"Invalid writer ID in {path}") from error

    value = str(uuid.uuid4())
    temporary = directory / f".writer_id-{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(value, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return value


def _find_config(collection: Collection) -> DeckConfigDict | None:
    matches = [config for config in collection.decks.all_config() if config["name"] == DATA_NAME]
    if len(matches) > 1:
        raise StorageError(f"Multiple deck presets are named {DATA_NAME}.")
    return matches[0] if matches else None


def _validate_notetype(notetype: NotetypeDict) -> None:
    field_names = [field["name"] for field in notetype["flds"]]
    if field_names != [ID_FIELD, EVENTS_FIELD]:
        raise StorageError(
            f"Existing note type {DATA_NAME} has incompatible fields: {field_names!r}."
        )
    if len(notetype["tmpls"]) != 1:
        raise StorageError(f"Existing note type {DATA_NAME} must have exactly one template.")
    template = notetype["tmpls"][0]
    if template["qfmt"] != "{{Id}}" or template["afmt"] != "{{FrontSide}}":
        raise StorageError(f"Existing note type {DATA_NAME} has an incompatible template.")


def ensure_storage(collection: Collection) -> tuple[DeckId, NotetypeDict]:
    """Create or validate the dedicated deck, preset, and note type."""
    existing_deck_id = collection.decks.id_for_name(DATA_NAME)
    existing_notetype = collection.models.by_name(DATA_NAME)
    existing_config = _find_config(collection)

    if existing_notetype is not None:
        _validate_notetype(existing_notetype)

    if existing_deck_id is not None:
        deck = collection.decks.get(existing_deck_id, default=False)
        if deck is None or bool(deck.get("dyn")):
            raise StorageError(f"Existing deck {DATA_NAME} is not a normal deck.")
        if existing_config is None or int(deck["conf"]) != int(existing_config["id"]):
            raise StorageError(
                f"Existing deck {DATA_NAME} is not bound to its dedicated preset."
            )

    config = existing_config or collection.decks.add_config(DATA_NAME)

    if existing_deck_id is None:
        deck_id = DeckId(collection.decks.add_normal_deck_with_name(DATA_NAME).id)
        deck = collection.decks.get(deck_id, default=False)
        if deck is None:
            raise StorageError(f"Anki did not create deck {DATA_NAME}.")
        collection.decks.set_config_id_for_deck_dict(deck, config["id"])
    else:
        deck_id = existing_deck_id

    if existing_notetype is None:
        notetype = collection.models.new(DATA_NAME)
        collection.models.add_field(notetype, collection.models.new_field(ID_FIELD))
        collection.models.add_field(notetype, collection.models.new_field(EVENTS_FIELD))
        template = collection.models.new_template(DATA_NAME)
        template["qfmt"] = "{{Id}}"
        template["afmt"] = "{{FrontSide}}"
        collection.models.add_template(notetype, template)
        collection.models.add(notetype)
        existing_notetype = collection.models.by_name(DATA_NAME)
        if existing_notetype is None:
            raise StorageError(f"Anki did not create note type {DATA_NAME}.")

    return deck_id, existing_notetype


def _decode_events(raw: str, shard_id: str) -> list[InterferenceEvent]:
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as error:
        raise StorageError(f"Shard {shard_id} contains invalid JSON.") from error
    if not isinstance(values, list):
        raise StorageError(f"Shard {shard_id} Events must be a JSON list.")
    return [InterferenceEvent.from_dict(value) for value in values]


def _writer_shards(collection: Collection, writer_id: str) -> list[tuple[int, Any]]:
    pattern = re.compile(rf"^{re.escape(writer_id)}{re.escape(PART_SUFFIX)}(\d{{4}})$")
    shards: list[tuple[int, Any]] = []
    for note_id in collection.find_notes(f'note:"{DATA_NAME}"'):
        note = collection.get_note(note_id)
        match = pattern.fullmatch(note[ID_FIELD])
        if match:
            part = int(match.group(1))
            if part < 1:
                raise StorageError(f"Shard {note[ID_FIELD]} has an invalid part number.")
            shards.append((part, note))
    shards.sort(key=lambda item: item[0])
    parts = [part for part, _note in shards]
    if len(parts) != len(set(parts)):
        raise StorageError(f"Writer {writer_id} contains duplicate part numbers.")
    return shards


def _merge_latest(collection: Collection, undo_target: int) -> OpChanges:
    return collection.merge_undo_entries(undo_target)


def append_event(
    collection: Collection,
    event: InterferenceEvent,
    writer_id: str,
    undo_target: int,
) -> OpChanges:
    """Append an event and merge every storage mutation into undo_target."""
    event.validate()
    shards = _writer_shards(collection, writer_id)

    latest_events: list[InterferenceEvent] = []
    if shards:
        latest_events = _decode_events(shards[-1][1][EVENTS_FIELD], shards[-1][1][ID_FIELD])

    for _, note in shards:
        if any(item.event_id == event.event_id for item in _decode_events(note[EVENTS_FIELD], note[ID_FIELD])):
            return _merge_latest(collection, undo_target)

    if shards and len(latest_events) < EVENTS_PER_PART:
        note = shards[-1][1]
        latest_events.append(event)
        note[EVENTS_FIELD] = json.dumps(
            [item.to_dict() for item in latest_events], ensure_ascii=False, separators=(",", ":")
        )
        collection.update_note(note)
        return _merge_latest(collection, undo_target)

    next_part = shards[-1][0] + 1 if shards else 1
    if next_part > MAX_PART:
        raise StorageError(f"Writer {writer_id} has reached part-{MAX_PART:04d}.")

    deck_id, notetype = ensure_storage(collection)
    note = collection.new_note(notetype)
    note[ID_FIELD] = f"{writer_id}{PART_SUFFIX}{next_part:04d}"
    note[EVENTS_FIELD] = json.dumps([event.to_dict()], ensure_ascii=False, separators=(",", ":"))
    collection.add_note(note, deck_id)
    changes = _merge_latest(collection, undo_target)

    card_ids = collection.card_ids_of_note(note.id)
    if len(card_ids) != 1:
        raise StorageError(f"Shard {note[ID_FIELD]} did not generate exactly one card.")
    collection.sched.suspend_cards(card_ids)
    changes = _merge_latest(collection, undo_target)
    return changes


def grade_and_record(
    collection: Collection,
    event: InterferenceEvent,
    writer_id: str,
) -> OpChanges:
    """Grade both cards and append the event as one compensating undo group."""
    event.validate()
    undo_target = collection.add_custom_undo_entry(UNDO_NAME)
    try:
        collection._backend.grade_now(
            card_ids=[event.source, event.target],
            rating=CardAnswer.AGAIN,
        )
        changes = _merge_latest(collection, undo_target)
        changes = append_event(collection, event, writer_id, undo_target)
        return changes
    except Exception as error:
        try:
            try:
                collection.merge_undo_entries(undo_target)
            except AnkiException:
                pass

            rolled_back = False
            for _attempt in range(5):
                if not collection.undo_status().undo:
                    break
                undone = collection.undo()
                if undone.operation == UNDO_NAME:
                    rolled_back = True
                    break

            if not rolled_back:
                raise StorageError("The Record Interference undo entry was not rolled back.")
        except (AnkiException, RuntimeError) as rollback_error:
            raise StorageError(
                f"Record Interference failed and rollback also failed: {rollback_error}"
            ) from error
        raise


def iter_interference_events(collection: Collection) -> list[InterferenceEvent]:
    """Read valid events from every shard, deduplicated by event ID."""
    events: list[InterferenceEvent] = []
    seen: set[str] = set()
    for note_id in collection.find_notes(f'note:"{DATA_NAME}"'):
        note = collection.get_note(note_id)
        shard_id = note[ID_FIELD]
        for event in _decode_events(note[EVENTS_FIELD], shard_id):
            if event.event_id not in seen:
                seen.add(event.event_id)
                events.append(event)
    return events
