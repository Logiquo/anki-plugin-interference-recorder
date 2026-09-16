from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aqt  # noqa: F401 - initializes Anki's generated Python modules
import pytest
from anki.cards import CardId
from anki.collection import Collection
from anki.stats_pb2 import RevlogEntry

from anki_plugin_interference_recorder.filtered_deck import create_filtered_deck
from anki_plugin_interference_recorder.graph_model import (
    build_graph_data,
    cards_for_threshold,
    fold_observations,
    successful_review_times,
    validate_decay,
)
from anki_plugin_interference_recorder.storage import InterferenceEvent, StorageError


@dataclass
class FakeReviewLog:
    time: int
    review_kind: int
    button_chosen: int


@pytest.fixture
def collection(tmp_path: Path) -> Iterator[Collection]:
    col = Collection(str(tmp_path / "collection.anki2"))
    try:
        yield col
    finally:
        col.close()


def _event(source: int, target: int, offset: int) -> InterferenceEvent:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=offset)
    return InterferenceEvent(
        event_id=str(uuid.uuid4()),
        source=source,
        target=target,
        time=timestamp.isoformat(),
    )


def _add_card(collection: Collection, front: str) -> CardId:
    notetype = collection.models.by_name("Basic")
    deck_id = collection.decks.id_for_name("Default")
    assert notetype is not None and deck_id is not None
    note = collection.new_note(notetype)
    note["Front"] = front
    note["Back"] = f"{front} back"
    collection.add_note(note, deck_id)
    return collection.card_ids_of_note(note.id)[0]


def test_observation_formula_matches_documented_decay() -> None:
    assert fold_observations([1, 1, 0, 0, 1], 0.9) == pytest.approx(
        1 + 0.9**3 + 0.9**4
    )


def test_graph_averages_directions_and_sums_incident_edges() -> None:
    events = [_event(1, 2, 0), _event(2, 1, 1), _event(1, 3, 2)]

    graph = build_graph_data(events, successful_reviews={}, decay=0.9)

    edges = {(edge.source, edge.target): edge for edge in graph.edges}
    assert edges[(1, 2)].score == pytest.approx(1.0)
    assert edges[(1, 3)].score == pytest.approx(0.5)
    scores = {node.card_id: node.score for node in graph.nodes}
    assert scores == pytest.approx({1: 1.5, 2: 1.0, 3: 0.5})


def test_successful_source_reviews_decay_one_direction() -> None:
    events = [_event(1, 2, 0), _event(1, 2, 1), _event(1, 2, 4)]
    base_ms = round(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    graph = build_graph_data(
        events,
        successful_reviews={1: [base_ms + 2_000, base_ms + 3_000]},
        decay=0.9,
    )

    edge = graph.edges[0]
    expected_directed = 1 + 0.9**3 + 0.9**4
    assert edge.source_to_target == pytest.approx(expected_directed)
    assert edge.target_to_source == 0
    assert edge.score == pytest.approx(expected_directed / 2)


def test_threshold_includes_selected_node_and_direct_neighbors_only() -> None:
    graph = build_graph_data(
        [_event(1, 2, 0), _event(1, 3, 1), _event(3, 4, 2)],
        successful_reviews={},
        decay=0.9,
    )

    assert cards_for_threshold(graph, 1, 0.5) == [1, 2, 3]
    assert cards_for_threshold(graph, 1, 0.5001) == [1]


def test_decay_validation_rejects_invalid_values() -> None:
    for value in (0, -0.1, 1.1, True, "0.9"):
        with pytest.raises(StorageError, match="decay"):
            validate_decay(value)


def test_only_successful_normal_reviews_are_decay_observations() -> None:
    entries = [
        FakeReviewLog(time=1, review_kind=RevlogEntry.REVIEW, button_chosen=1),
        FakeReviewLog(time=2, review_kind=RevlogEntry.REVIEW, button_chosen=2),
        FakeReviewLog(time=3, review_kind=RevlogEntry.REVIEW, button_chosen=3),
        FakeReviewLog(time=4, review_kind=RevlogEntry.REVIEW, button_chosen=4),
        FakeReviewLog(time=5, review_kind=RevlogEntry.LEARNING, button_chosen=3),
        FakeReviewLog(time=6, review_kind=RevlogEntry.RELEARNING, button_chosen=3),
        FakeReviewLog(time=7, review_kind=RevlogEntry.FILTERED, button_chosen=3),
        FakeReviewLog(time=8, review_kind=RevlogEntry.MANUAL, button_chosen=3),
    ]

    assert successful_review_times(entries) == [2, 3, 4]


def test_create_filtered_deck_uses_anki_default_name_and_exact_cards(
    collection: Collection,
) -> None:
    first = _add_card(collection, "A")
    second = _add_card(collection, "B")
    excluded = _add_card(collection, "C")

    result = create_filtered_deck(collection, [int(first), int(second)], "")

    assert result.deck_name.startswith("Filtered Deck ")
    assert collection.decks.is_filtered(result.deck_id)
    assert set(collection.find_cards(f'deck:"{result.deck_name}"')) == {first, second}
    assert excluded not in collection.find_cards(f'deck:"{result.deck_name}"')
    assert result.requested == 2
    assert result.added == 2
