"""Deterministic metric interfaces shared by fake and live memory evals."""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import mean
from typing import TypeVar
import re

from evals.memory_cortex.schema import FixtureObservation, RunMemoryFixture


T = TypeVar("T")


def ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


@dataclass(frozen=True)
class CaptureMetrics:
    coverage: float
    silent_drop_rate: float
    false_capture_rate: float


def capture_metrics(expected: Sequence[bool], observed: Sequence[bool]) -> CaptureMetrics:
    if len(expected) != len(observed):
        raise ValueError("capture arrays must have equal length")
    positives = sum(expected)
    negatives = len(expected) - positives
    missed = sum(want and not got for want, got in zip(expected, observed, strict=True))
    false = sum(not want and got for want, got in zip(expected, observed, strict=True))
    return CaptureMetrics(
        coverage=ratio(positives - missed, positives),
        silent_drop_rate=ratio(missed, positives),
        false_capture_rate=ratio(false, negatives),
    )


@dataclass(frozen=True)
class SetMetrics:
    precision: float
    recall: float
    f1: float


def set_metrics(expected: Iterable[T], observed: Iterable[T]) -> SetMetrics:
    gold, actual = set(expected), set(observed)
    overlap = len(gold & actual)
    precision = ratio(overlap, len(actual))
    recall = ratio(overlap, len(gold))
    return SetMetrics(
        precision=precision,
        recall=recall,
        f1=ratio(2 * precision * recall, precision + recall),
    )


def chunk_coverage(expected_chunk_ids: Iterable[str], processed_chunk_ids: Iterable[str]) -> float:
    expected = set(expected_chunk_ids)
    return ratio(len(expected & set(processed_chunk_ids)), len(expected))


def operation_accuracy(expected: Sequence[str], observed: Sequence[str]) -> float:
    if len(expected) != len(observed):
        raise ValueError("operation arrays must have equal length")
    return ratio(sum(a == b for a, b in zip(expected, observed, strict=True)), len(expected))


@dataclass(frozen=True)
class ExtractionMetrics:
    precision: float
    recall: float
    type_accuracy: float
    source_span_precision: float
    source_span_recall: float
    no_output_accuracy: float


def _subject(value: str) -> str:
    return " ".join(value.casefold().split())


def _variants(token: str) -> set[str]:
    aliases = {
        "one": "single",
        "cannot": "not",
        "bug": "failure",
    }
    variants = {token, aliases.get(token, token)}
    if len(token) > 3 and token.endswith("s"):
        variants.add(token[:-1])
    if len(token) > 4 and token.endswith("ed"):
        root = token[:-2]
        variants.update({root, token[:-1], f"{root}e"})
    if len(token) > 5 and token.endswith("ing"):
        root = token[:-3]
        variants.update({root, f"{root}e"})
        if len(root) > 2 and root[-1] == root[-2]:
            variants.add(root[:-1])
    return variants


def _raw_tokens(value: str) -> list[str]:
    normalized = re.sub(
        r"\btim(?:e|es|ed|ing)\s+out\b", "timeout", value.casefold()
    )
    return re.findall(r"[a-z0-9\u4e00-\u9fff]+", normalized)


def contains_concepts(required: str, actual: str) -> bool:
    required_groups: list[set[str]] = []
    for token in _raw_tokens(required):
        if token == "unreleased":
            required_groups.extend(({"not"}, _variants("released")))
        else:
            required_groups.append(_variants(token))
    actual_tokens = {
        variant
        for token in _raw_tokens(actual)
        for variant in _variants(token)
    }
    return all(group & actual_tokens for group in required_groups)


def extraction_metrics(
    fixtures: Sequence[RunMemoryFixture], observations: Sequence[FixtureObservation]
) -> ExtractionMetrics:
    observed_by_id = {item.fixture_id: item for item in observations}
    expected_count = 0
    observed_count = 0
    matched_items = 0
    matched_types = 0
    source_expected = 0
    source_matched = 0
    source_observed = 0
    no_output_expected = 0
    no_output_correct = 0
    for fixture in fixtures:
        observation = observed_by_id.get(fixture.id)
        candidates = observation.candidates if observation else []
        expected_count += len(fixture.gold_items)
        observed_count += len(candidates)
        if fixture.expected_no_output:
            no_output_expected += 1
            no_output_correct += int(not candidates)
        unmatched = set(range(len(candidates)))
        for gold in fixture.gold_items:
            source_expected += len(set(gold.evidence_refs))
            subject = _subject(gold.subject)
            matches = []
            for index in unmatched:
                actual = candidates[index]
                keyword_match = all(
                    contains_concepts(value, actual.statement)
                    for value in gold.statement_contains
                ) and all(
                    any(
                        contains_concepts(alternative, actual.statement)
                        for alternative in alternatives
                    )
                    for alternatives in gold.statement_contains_any
                ) and all(
                    contains_concepts(value, actual.applicability)
                    for value in gold.applicability_contains
                )
                subject_match = subject == _subject(actual.subject)
                if keyword_match:
                    matches.append((int(subject_match), index))
            if not matches:
                continue
            _, index = max(matches)
            unmatched.remove(index)
            actual = candidates[index]
            matched_items += 1
            matched_types += int(actual.memory_type == gold.memory_type)
            source_matched += len(set(gold.evidence_refs) & set(actual.evidence_refs))
            source_observed += len(set(actual.evidence_refs))
    return ExtractionMetrics(
        precision=ratio(matched_items, observed_count),
        recall=ratio(matched_items, expected_count),
        type_accuracy=ratio(matched_types, matched_items),
        source_span_precision=ratio(source_matched, source_observed),
        source_span_recall=ratio(source_matched, source_expected),
        no_output_accuracy=ratio(no_output_correct, no_output_expected),
    )


@dataclass(frozen=True)
class RetrievalMetrics:
    precision_at_k: float
    recall_at_k: float
    abstention_accuracy: float


def retrieval_metrics(
    *,
    relevant: Sequence[set[str]],
    returned: Sequence[list[str]],
    expected_abstain: Sequence[bool],
    k: int = 5,
) -> RetrievalMetrics:
    if not (len(relevant) == len(returned) == len(expected_abstain)):
        raise ValueError("retrieval arrays must have equal length")
    precisions: list[float] = []
    recalls: list[float] = []
    abstentions: list[float] = []
    for gold, actual, abstain in zip(relevant, returned, expected_abstain, strict=True):
        top = actual[:k]
        hit_count = len(gold & set(top))
        precisions.append(ratio(hit_count, len(top)))
        recalls.append(ratio(hit_count, len(gold)))
        abstentions.append(float((not top) == abstain))
    return RetrievalMetrics(mean(precisions), mean(recalls), mean(abstentions))


@dataclass(frozen=True)
class CacheObservation:
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    uncached_input_tokens: int | None
    ttft_ms: float | None


@dataclass(frozen=True)
class CacheMetrics:
    availability: float
    cache_read_tokens: int
    cache_write_tokens: int
    uncached_input_tokens: int
    mean_ttft_ms: float | None


def cache_metrics(observations: Sequence[CacheObservation]) -> CacheMetrics:
    available = [
        item
        for item in observations
        if item.cache_read_tokens is not None
        and item.cache_write_tokens is not None
        and item.uncached_input_tokens is not None
    ]
    ttft = [item.ttft_ms for item in observations if item.ttft_ms is not None]
    return CacheMetrics(
        availability=ratio(len(available), len(observations)),
        cache_read_tokens=sum(item.cache_read_tokens or 0 for item in available),
        cache_write_tokens=sum(item.cache_write_tokens or 0 for item in available),
        uncached_input_tokens=sum(item.uncached_input_tokens or 0 for item in available),
        mean_ttft_ms=mean(ttft) if ttft else None,
    )


@dataclass(frozen=True)
class QueryObservation:
    latency_ms: float
    steps: int
    returned_spans: int
    input_tokens: int
    output_tokens: int
    bulletin_ids: tuple[str, ...] = ()
    relevant_ids: tuple[str, ...] = ()
    reader_error: bool = False


@dataclass(frozen=True)
class QueryMetrics:
    mean_latency_ms: float
    mean_steps: float
    mean_returned_spans: float
    mean_tokens: float
    bulletin_precision: float
    reader_error_rate: float


def query_metrics(observations: Sequence[QueryObservation]) -> QueryMetrics:
    if not observations:
        return QueryMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    bulletin_precisions = [
        ratio(len(set(item.bulletin_ids) & set(item.relevant_ids)), len(item.bulletin_ids))
        for item in observations
    ]
    return QueryMetrics(
        mean_latency_ms=mean(item.latency_ms for item in observations),
        mean_steps=mean(item.steps for item in observations),
        mean_returned_spans=mean(item.returned_spans for item in observations),
        mean_tokens=mean(item.input_tokens + item.output_tokens for item in observations),
        bulletin_precision=mean(bulletin_precisions),
        reader_error_rate=mean(float(item.reader_error) for item in observations),
    )


@dataclass(frozen=True)
class PairedDelta:
    mean_delta: float
    ci95_low: float
    ci95_high: float


def paired_delta(
    memory_off: Sequence[float],
    memory_on: Sequence[float],
    *,
    seed: int,
    bootstrap_samples: int = 2_000,
) -> PairedDelta:
    if len(memory_off) != len(memory_on) or not memory_off:
        raise ValueError("paired arrays must be non-empty and equal length")
    deltas = [on - off for off, on in zip(memory_off, memory_on, strict=True)]
    rng = random.Random(seed)
    sampled = sorted(
        mean(rng.choice(deltas) for _ in deltas) for _ in range(bootstrap_samples)
    )
    low = sampled[int(bootstrap_samples * 0.025)]
    high = sampled[min(bootstrap_samples - 1, int(bootstrap_samples * 0.975))]
    return PairedDelta(mean(deltas), low, high)


__all__ = [
    "CacheMetrics",
    "CacheObservation",
    "CaptureMetrics",
    "ExtractionMetrics",
    "PairedDelta",
    "QueryMetrics",
    "QueryObservation",
    "RetrievalMetrics",
    "SetMetrics",
    "cache_metrics",
    "capture_metrics",
    "chunk_coverage",
    "contains_concepts",
    "extraction_metrics",
    "operation_accuracy",
    "paired_delta",
    "query_metrics",
    "retrieval_metrics",
    "set_metrics",
]
