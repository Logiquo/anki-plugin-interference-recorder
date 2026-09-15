"""Maintenance operations for persisted interference events."""

from __future__ import annotations

import json
from dataclasses import dataclass

from anki.cards import CardId
from anki.collection import Collection, OpChanges
from anki.errors import AnkiException, DeletedError, NotFoundError
from anki.notes import Note, NoteId

from .storage import (
    DATA_NAME,
    EVENTS_FIELD,
    ID_FIELD,
    InterferenceEvent,
    StorageError,
    decode_events,
)

CLEAN_UNDO_NAME = "Clean Interference Records"


@dataclass(frozen=True)
class ShardCleanup:
    """An immutable snapshot of one shard that needs cleaning."""

    note_id: NoteId
    shard_id: str
    original_events: str
    retained_events: tuple[InterferenceEvent, ...]
    removed_count: int

    @property
    def delete_note(self) -> bool:
        return not self.retained_events


@dataclass(frozen=True)
class CleanupPlan:
    """Read-only scan result passed to the confirmation and write phases."""

    scanned_shards: int
    total_events: int
    missing_events: int
    affected_shards: int
    empty_shards: int
    changes: tuple[ShardCleanup, ...]


def scan_missing_card_records(collection: Collection) -> CleanupPlan:
    """Find events that reference a Card ID no longer in the collection."""
    card_exists: dict[int, bool] = {}
    changes: list[ShardCleanup] = []
    scanned_shards = 0
    total_events = 0

    def exists(card_id: int) -> bool:
        cached = card_exists.get(card_id)
        if cached is not None:
            return cached
        try:
            collection.get_card(CardId(card_id))
        except (NotFoundError, DeletedError):
            result = False
        else:
            result = True
        card_exists[card_id] = result
        return result

    for note_id in collection.find_notes(f'note:"{DATA_NAME}"'):
        note = collection.get_note(note_id)
        shard_id = note[ID_FIELD]
        raw_events = note[EVENTS_FIELD]
        events = decode_events(raw_events, shard_id)
        retained = tuple(
            event for event in events if exists(event.source) and exists(event.target)
        )
        removed_count = len(events) - len(retained)
        scanned_shards += 1
        total_events += len(events)
        if removed_count:
            changes.append(
                ShardCleanup(
                    note_id=note.id,
                    shard_id=shard_id,
                    original_events=raw_events,
                    retained_events=retained,
                    removed_count=removed_count,
                )
            )

    return CleanupPlan(
        scanned_shards=scanned_shards,
        total_events=total_events,
        missing_events=sum(change.removed_count for change in changes),
        affected_shards=len(changes),
        empty_shards=sum(change.delete_note for change in changes),
        changes=tuple(changes),
    )


def _rollback_cleanup(collection: Collection, undo_target: int) -> None:
    try:
        collection.merge_undo_entries(undo_target)
    except AnkiException:
        pass

    for _attempt in range(4):
        if not collection.undo_status().undo:
            break
        undone = collection.undo()
        if undone.operation == CLEAN_UNDO_NAME:
            return
    raise StorageError("The Clean Interference Records undo entry was not rolled back.")


def execute_cleanup(collection: Collection, plan: CleanupPlan) -> OpChanges:
    """Apply a confirmed plan atomically as one Anki undo entry."""
    if not plan.changes:
        raise StorageError("There are no missing-card records to clean.")

    notes_to_update: list[Note] = []
    note_ids_to_delete: list[NoteId] = []
    for change in plan.changes:
        try:
            note = collection.get_note(change.note_id)
        except (NotFoundError, DeletedError) as error:
            raise StorageError(
                f"Shard {change.shard_id} changed after the scan; please scan again."
            ) from error
        if note[ID_FIELD] != change.shard_id or note[EVENTS_FIELD] != change.original_events:
            raise StorageError(
                f"Shard {change.shard_id} changed after the scan; please scan again."
            )
        if change.delete_note:
            note_ids_to_delete.append(note.id)
        else:
            note[EVENTS_FIELD] = json.dumps(
                [event.to_dict() for event in change.retained_events],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            notes_to_update.append(note)

    undo_target = collection.add_custom_undo_entry(CLEAN_UNDO_NAME)
    try:
        changes: OpChanges | None = None
        if notes_to_update:
            collection.update_notes(notes_to_update)
            changes = collection.merge_undo_entries(undo_target)
        if note_ids_to_delete:
            collection.remove_notes(note_ids_to_delete)
            changes = collection.merge_undo_entries(undo_target)
        if changes is None:
            raise StorageError("The cleanup plan did not contain any collection changes.")
        return changes
    except Exception as error:
        try:
            _rollback_cleanup(collection, undo_target)
        except (AnkiException, RuntimeError) as rollback_error:
            raise StorageError(
                f"Cleanup failed and rollback also failed: {rollback_error}"
            ) from error
        raise
