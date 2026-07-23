from decimal import Decimal

import pytest

from app.media.asr import ASRResult, ASRSegment
from app.media.product_entities import (
    MULTI_PRODUCT,
    UNKNOWN_PRODUCT,
    CatalogProduct,
    build_label_evidence,
)
from app.media.segmentation import (
    atomic_utterances,
    segment_from_asr_segments,
    validate_semantic_output,
)


def seg(start: int, end: int, text: str) -> ASRSegment:
    return ASRSegment(start, end, text, Decimal("0.8"))


def test_spoken_sentences_and_prices_do_not_become_product_labels() -> None:
    rows = segment_from_asr_segments(
        [
            seg(0, 900, "明白吗 是的"),
            seg(1_000, 2_000, "好不好家人们"),
            seg(2_100, 3_000, "500块钱以内没有对手"),
        ],
        3_000,
    )
    assert rows == []


def test_no_product_evidence_returns_unknown_product() -> None:
    evidence = build_label_evidence("ignored", [])
    assert evidence.canonical_name == UNKNOWN_PRODUCT
    assert evidence.source == "unknown"
    assert evidence.confidence == 0
    assert evidence.evidence_spans == []


def test_multiple_strong_products_return_structured_conflict() -> None:
    rows = segment_from_asr_segments(
        [
            seg(0, 1_500, "香薰和煎锅今天都是29元"),
            seg(1_500, 3_000, "香薰煎锅各拍一单"),
        ],
        3_000,
    )
    assert len(rows) == 1
    assert rows[0].predicted_product_label == MULTI_PRODUCT
    assert rows[0].product_evidence.conflicting_products == ["煎锅", "香薰"]
    assert rows[0].boundary_evidence["is_multi_product"] is True


def test_catalog_alias_maps_to_canonical_name_and_sku() -> None:
    catalog = (CatalogProduct("高压锅", ("压力锅", "红色高压锅"), matched_sku="SKU-POT-RED"),)
    rows = segment_from_asr_segments(
        [seg(0, 1_200, "这个压力锅"), seg(1_200, 3_000, "煮饭五分钟只要99元")],
        3_000,
        catalog=catalog,
    )
    assert len(rows) == 1
    assert rows[0].predicted_product_label == "高压锅"
    assert rows[0].product_evidence.source == "catalog"
    assert rows[0].product_evidence.matched_sku == "SKU-POT-RED"
    assert rows[0].product_evidence.evidence_spans[0].start == 0


def test_product_switch_needs_consensus_and_preserves_sentence_boundaries() -> None:
    rows = segment_from_asr_segments(
        [
            seg(0, 1_000, "高压锅今天99元"),
            seg(1_000, 3_000, "煮饭只要五分钟"),
            seg(3_000, 4_000, "有人问香薰"),
            seg(4_000, 6_000, "高压锅可以炖肉"),
            seg(6_000, 7_000, "香薰29元"),
            seg(7_000, 9_000, "香薰可以用一年"),
        ],
        9_000,
        min_seconds=3,
        max_seconds=20,
    )
    assert [row.predicted_product_label for row in rows] == ["高压锅", "香薰"]
    assert rows[0].end_ms <= rows[1].start_ms
    assert all(row.boundary_evidence["complete_sentence_start"] for row in rows)
    assert all(row.boundary_evidence["complete_sentence_end"] for row in rows)


def test_long_product_splits_at_repeated_entity_not_fixed_timestamp() -> None:
    rows = segment_from_asr_segments(
        [
            seg(0, 2_000, "拖把19元"),
            seg(2_200, 9_000, "免手洗而且方便收纳"),
            seg(10_500, 12_000, "这款拖把"),
            seg(12_000, 24_000, "棉头柔软拖地没有水渍"),
        ],
        24_000,
        min_seconds=6,
        max_seconds=15,
    )
    assert len(rows) == 2
    assert rows[1].predicted_start_ms == 10_500
    assert all(row.predicted_product_label == "拖把" for row in rows)


def test_repeated_segmentation_is_deterministic() -> None:
    segments = [
        seg(0, 1_000, "香薰29元"),
        seg(1_000, 3_000, "香薰可以用一年"),
        seg(3_000, 4_000, "煎锅只要49元"),
        seg(4_000, 6_000, "煎锅没有涂层"),
    ]
    first = segment_from_asr_segments(segments, 6_000)
    second = segment_from_asr_segments(segments, 6_000)
    assert first == second


def test_different_candidates_cannot_copy_a_label_without_evidence() -> None:
    rows = segment_from_asr_segments(
        [
            seg(0, 1_000, "香薰29元"),
            seg(1_000, 2_000, "香薰回购很多"),
            seg(2_000, 4_000, "明白吗 是的 保证品质"),
        ],
        4_000,
    )
    assert len(rows) == 1
    assert rows[0].product_evidence.evidence_spans
    assert all("香薰" in span.text for span in rows[0].product_evidence.evidence_spans)


def test_invalid_semantic_json_is_rejected() -> None:
    utterances = atomic_utterances(ASRResult("zh", [seg(0, 1_000, "商品A")]))
    with pytest.raises(ValueError):
        validate_semantic_output({"invalid": []}, utterances)


def test_forged_time_or_non_contiguous_evidence_is_rejected() -> None:
    utterances = atomic_utterances(
        ASRResult("zh", [seg(0, 1_000, "商品A"), seg(1_000, 2_000, "价格十块")])
    )
    payload = {
        "product_intervals": [
            {
                "predicted_product_label": "商品A",
                "start_utterance_id": "utterance-0001",
                "end_utterance_id": "utterance-0002",
                "evidence_utterance_ids": ["utterance-0001"],
                "confidence": "0.8",
                "boundary_reason": "semantic",
            }
        ]
    }
    with pytest.raises(ValueError):
        validate_semantic_output(payload, utterances)


def test_invented_product_label_is_rejected_unless_official() -> None:
    utterances = atomic_utterances(ASRResult("zh", [seg(0, 1_000, "商品A")]))
    row = {
        "predicted_product_label": "商品B",
        "start_utterance_id": "utterance-0001",
        "end_utterance_id": "utterance-0001",
        "evidence_utterance_ids": ["utterance-0001"],
        "confidence": "0.8",
        "boundary_reason": "semantic",
    }
    with pytest.raises(ValueError):
        validate_semantic_output({"product_intervals": [row]}, utterances)
    assert validate_semantic_output({"product_intervals": [row]}, utterances, {"商品B"})
