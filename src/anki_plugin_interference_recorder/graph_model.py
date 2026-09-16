"""Read-only graph scoring derived from interference events and Anki revlogs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from anki.cards import CardId
from anki.collection import Collection
from anki.consts import BUTTON_ONE
from anki.errors import DeletedError, NotFoundError
from anki.stats_pb2 import RevlogEntry

from .storage import InterferenceEvent, StorageError, iter_interference_events


@dataclass(frozen=True)
class GraphNode:
    card_id: int
    score: float


@dataclass(frozen=True)
class GraphEdge:
    source: int
    target: int
    score: float
    source_to_target: float
    target_to_source: float


@dataclass(frozen=True)
class GraphData:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    missing_event_count: int = 0


class ReviewLogLike(Protocol):
    time: int
    review_kind: int
    button_chosen: int


def validate_decay(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StorageError("decay must be a number greater than 0 and at most 1")
    decay = float(value)
    if not 0 < decay <= 1:
        raise StorageError("decay must be greater than 0 and at most 1")
    return decay


def fold_observations(observations: list[int], decay: float) -> float:
    """Fold chronological 0/1 observations with exponential decay."""
    score = 0.0
    for observation in observations:
        score = decay * score + observation
    return score


def successful_review_times(entries: Sequence[ReviewLogLike]) -> list[int]:
    """Keep only successful answers made while a card was in normal review."""
    return [
        int(entry.time)
        for entry in entries
        if entry.review_kind == RevlogEntry.REVIEW
        and entry.button_chosen > BUTTON_ONE
    ]


def _event_time_ms(event: InterferenceEvent) -> int:
    parsed = datetime.fromisoformat(event.time)
    if parsed.tzinfo is None:
        raise StorageError(f"Event {event.event_id} time has no UTC offset.")
    return round(parsed.timestamp() * 1000)


def directed_score(
    events: list[InterferenceEvent], review_times_ms: list[int], decay: float
) -> float:
    """Score one direction, with reviews ordered before events on exact ties."""
    observations: list[tuple[int, int, str, int]] = [
        (time_ms, 0, f"{index:020d}", 0)
        for index, time_ms in enumerate(review_times_ms)
    ]
    observations.extend(
        (_event_time_ms(event), 1, event.event_id, 1) for event in events
    )
    observations.sort(key=lambda item: item[:3])
    return fold_observations([item[3] for item in observations], decay)


def build_graph_data(
    events: list[InterferenceEvent],
    successful_reviews: dict[int, list[int]],
    decay: float,
    *,
    missing_event_count: int = 0,
) -> GraphData:
    """Aggregate directed observations into undirected edges and nodes."""
    decay = validate_decay(decay)
    unique_events: dict[str, InterferenceEvent] = {}
    for event in events:
        event.validate()
        unique_events.setdefault(event.event_id, event)

    by_direction: dict[tuple[int, int], list[InterferenceEvent]] = {}
    pairs: set[tuple[int, int]] = set()
    for event in unique_events.values():
        by_direction.setdefault((event.source, event.target), []).append(event)
        pairs.add(
            (min(event.source, event.target), max(event.source, event.target))
        )

    edges: list[GraphEdge] = []
    node_scores: dict[int, float] = {}
    for first, second in sorted(pairs):
        first_to_second = directed_score(
            by_direction.get((first, second), []),
            successful_reviews.get(first, []),
            decay,
        )
        second_to_first = directed_score(
            by_direction.get((second, first), []),
            successful_reviews.get(second, []),
            decay,
        )
        score = (first_to_second + second_to_first) / 2
        edges.append(
            GraphEdge(
                source=first,
                target=second,
                score=score,
                source_to_target=first_to_second,
                target_to_source=second_to_first,
            )
        )
        node_scores[first] = node_scores.get(first, 0.0) + score
        node_scores[second] = node_scores.get(second, 0.0) + score

    nodes = tuple(
        GraphNode(card_id=card_id, score=score)
        for card_id, score in sorted(node_scores.items())
    )
    return GraphData(
        nodes=nodes,
        edges=tuple(edges),
        missing_event_count=missing_event_count,
    )


def load_graph_data(collection: Collection, decay: float) -> GraphData:
    """Load valid cards and successful normal-review timestamps via Anki APIs."""
    events = iter_interference_events(collection)
    card_exists: dict[int, bool] = {}

    def exists(card_id: int) -> bool:
        if card_id in card_exists:
            return card_exists[card_id]
        try:
            collection.get_card(CardId(card_id))
        except (NotFoundError, DeletedError):
            card_exists[card_id] = False
        else:
            card_exists[card_id] = True
        return card_exists[card_id]

    valid_events = [
        event for event in events if exists(event.source) and exists(event.target)
    ]
    card_ids = {event.source for event in valid_events} | {
        event.target for event in valid_events
    }
    successful_reviews: dict[int, list[int]] = {}
    for card_id in card_ids:
        successful_reviews[card_id] = successful_review_times(
            list(collection.get_review_logs(CardId(card_id)))
        )

    return build_graph_data(
        valid_events,
        successful_reviews,
        decay,
        missing_event_count=len(events) - len(valid_events),
    )


def cards_for_threshold(data: GraphData, card_id: int, threshold: float) -> list[int]:
    if threshold < 0:
        raise ValueError("threshold must not be negative")
    card_ids = {card_id}
    for edge in data.edges:
        if edge.score < threshold:
            continue
        if edge.source == card_id:
            card_ids.add(edge.target)
        elif edge.target == card_id:
            card_ids.add(edge.source)
    return sorted(card_ids)
