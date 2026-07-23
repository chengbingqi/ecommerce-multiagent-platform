from collections import defaultdict
from copy import deepcopy
from decimal import Decimal

from app.seed.generator import canonical_checksum, generate_dataset, scenario_evidence


def test_same_manifest_has_identical_checksum_and_counts(manifest: dict) -> None:
    first = generate_dataset(manifest)
    second = generate_dataset(manifest)
    assert first == second
    assert canonical_checksum(first) == canonical_checksum(second)
    for table, expected in manifest["counts"].items():
        assert len(first[table]) == expected


def test_different_seed_changes_checksum(manifest: dict) -> None:
    changed = deepcopy(manifest)
    changed["seed"] += 1
    assert canonical_checksum(generate_dataset(manifest)) != canonical_checksum(
        generate_dataset(changed)
    )


def test_all_five_scenarios_have_records(manifest: dict) -> None:
    evidence = scenario_evidence(generate_dataset(manifest))
    assert set(evidence) == set(manifest["enabled_scenarios"])
    assert all(evidence.values())


def test_sales_daily_is_traceable_to_orders(manifest: dict) -> None:
    dataset = generate_dataset(manifest)
    totals: dict[tuple[str, str], tuple[Decimal, int]] = defaultdict(lambda: (Decimal("0.00"), 0))
    for order in dataset["orders"]:
        key = (order["store_id"], order["ordered_at"][:10])
        current_amount, current_count = totals[key]
        totals[key] = (current_amount + Decimal(order["total_amount"]), current_count + 1)
    for row in dataset["sales_daily"]:
        amount, count = totals[(row["store_id"], row["sales_date"])]
        assert Decimal(row["gmv"]) == amount
        assert row["order_count"] == count
