import hashlib
import json
import random
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

NAMESPACE = uuid.UUID("5d51b4de-98e9-4ef8-a2c8-c1e696bf3db2")
TABLE_ORDER = (
    "stores",
    "principals",
    "products",
    "skus",
    "inventories",
    "orders",
    "order_items",
    "logistics",
    "refunds",
    "sales_daily",
    "campaigns",
    "knowledge_documents",
    "knowledge_chunks",
    "sops",
)


def load_manifest(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as stream:
        return cast(dict[str, Any], json.load(stream))


def stable_id(seed: int, entity: str, index: int) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{seed}:{entity}:{index}"))


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def generate_dataset(manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    seed = int(manifest["seed"])
    rng = random.Random(seed)
    clock = datetime.fromisoformat(manifest["simulated_clock"].replace("Z", "+00:00"))
    scenarios = set(manifest["enabled_scenarios"])
    counts = manifest["counts"]
    data: dict[str, list[dict[str, Any]]] = {name: [] for name in TABLE_ORDER}

    for index in range(counts["stores"]):
        data["stores"].append(
            {
                "id": stable_id(seed, "store", index),
                "scope_key": f"store-{index + 1:03d}",
                "name": f"模拟店铺 {index + 1}",
                "created_at": _iso(clock - timedelta(days=90)),
            }
        )
    store_ids = [row["id"] for row in data["stores"]]
    data["principals"] = [
        {
            "id": "customer-001",
            "role": "customer:self",
            "store_id": store_ids[0],
            "customer_ref": "customer-001",
            "active": True,
        },
        {
            "id": "operator-store-001",
            "role": "store_operator",
            "store_id": store_ids[0],
            "customer_ref": None,
            "active": True,
        },
        {
            "id": "agent-customer-service",
            "role": "agent:customer_service",
            "store_id": store_ids[0],
            "customer_ref": None,
            "active": True,
        },
        {
            "id": "agent-operations",
            "role": "agent:operations",
            "store_id": store_ids[0],
            "customer_ref": None,
            "active": True,
        },
        {
            "id": "agent-media",
            "role": "agent:media",
            "store_id": store_ids[0],
            "customer_ref": None,
            "active": True,
        },
        {
            "id": "admin-001",
            "role": "admin",
            "store_id": None,
            "customer_ref": None,
            "active": True,
        },
    ]

    for index in range(counts["products"]):
        store_id = store_ids[index % len(store_ids)]
        data["products"].append(
            {
                "id": stable_id(seed, "product", index),
                "store_id": store_id,
                "name": f"模拟商品 {index + 1}",
                "category": ("家居", "数码")[index % 2],
            }
        )
    for index in range(counts["skus"]):
        product = data["products"][index % len(data["products"])]
        price = Decimal(39 + rng.randint(0, 260)) + Decimal(index) / Decimal(10)
        data["skus"].append(
            {
                "id": stable_id(seed, "sku", index),
                "product_id": product["id"],
                "store_id": product["store_id"],
                "code": f"SIM-{seed}-{index + 1:03d}",
                "price": _money(price),
            }
        )
    for index, sku in enumerate(data["skus"]):
        stockout = "stockout_spike" in scenarios and index in (0, 2)
        data["inventories"].append(
            {
                "id": stable_id(seed, "inventory", index),
                "store_id": sku["store_id"],
                "sku_id": sku["id"],
                "on_hand": 0 if stockout else 20 + rng.randint(0, 80),
                "reserved": 0 if stockout else rng.randint(0, 8),
                "scenario": "stockout_spike" if stockout else None,
                "updated_at": _iso(clock - timedelta(hours=index)),
            }
        )

    daily: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"gmv": Decimal("0.00"), "orders": 0}
    )
    for index in range(counts["orders"]):
        sku = data["skus"][index % len(data["skus"])]
        quantity = 1 + rng.randint(0, 2)
        amount = Decimal(sku["price"]) * quantity
        ordered_at = clock - timedelta(days=1 + index % 7, hours=index % 12)
        customer_ref = "customer-001" if index % 5 == 0 else f"sim-customer-{index % 9:03d}"
        order_id = stable_id(seed, "order", index)
        data["orders"].append(
            {
                "id": order_id,
                "store_id": sku["store_id"],
                "customer_ref": customer_ref,
                "status": "delivered" if index % 4 else "shipped",
                "total_amount": _money(amount),
                "currency": "CNY",
                "ordered_at": _iso(ordered_at),
            }
        )
        data["order_items"].append(
            {
                "id": stable_id(seed, "order_item", index),
                "order_id": order_id,
                "sku_id": sku["id"],
                "quantity": quantity,
                "unit_price": sku["price"],
                "line_total": _money(amount),
            }
        )
        delayed = "logistics_delay" in scenarios and index in (1, 7, 13)
        data["logistics"].append(
            {
                "id": stable_id(seed, "logistics", index),
                "order_id": order_id,
                "carrier": "模拟快递",
                "status": "delayed" if delayed else "in_transit",
                "last_event": "模拟转运延误" if delayed else "模拟运输途中",
                "estimated_delivery_at": _iso(
                    clock + timedelta(days=3 if delayed else 1, hours=index % 6)
                ),
                "updated_at": _iso(clock - timedelta(hours=index % 18)),
                "scenario": "logistics_delay" if delayed else None,
            }
        )
        key = (sku["store_id"], ordered_at.date().isoformat())
        daily[key]["gmv"] += amount
        daily[key]["orders"] += 1

    for index in range(counts["refunds"]):
        order = data["orders"][index * 2]
        spike = "refund_spike" in scenarios and index < 4
        data["refunds"].append(
            {
                "id": stable_id(seed, "refund", index),
                "store_id": order["store_id"],
                "order_id": order["id"],
                "status": "simulated_requested",
                "amount": order["total_amount"],
                "reason_code": "SIMULATED_QUALITY" if spike else "SIMULATED_OTHER",
                "scenario": "refund_spike" if spike else None,
                "created_at": _iso(clock - timedelta(hours=index + 2)),
            }
        )

    index = 0
    for store_index, store_id in enumerate(store_ids):
        for day_offset in range(1, 8):
            sales_date = (clock - timedelta(days=day_offset)).date().isoformat()
            aggregate = daily[(store_id, sales_date)]
            conversion_drop = (
                "conversion_drop" in scenarios and store_index == 0 and day_offset in (1, 2)
            )
            sessions = 1200 if conversion_drop else 160 + day_offset * 7
            data["sales_daily"].append(
                {
                    "id": stable_id(seed, "sales_daily", index),
                    "store_id": store_id,
                    "sales_date": sales_date,
                    "sessions": sessions,
                    "conversions": 4 if conversion_drop else int(aggregate["orders"]),
                    "order_count": int(aggregate["orders"]),
                    "gmv": _money(aggregate["gmv"]),
                    "scenario": "conversion_drop" if conversion_drop else None,
                }
            )
            index += 1

    for index, store_id in enumerate(store_ids):
        is_live = "live_campaign" in scenarios and index == 0
        data["campaigns"].append(
            {
                "id": stable_id(seed, "campaign", index),
                "store_id": store_id,
                "name": "模拟直播活动" if is_live else "模拟常规活动",
                "campaign_type": "live" if is_live else "standard",
                "status": "active",
                "starts_at": _iso(clock - timedelta(hours=2)),
                "ends_at": _iso(clock + timedelta(hours=4)),
                "scenario": "live_campaign" if is_live else None,
            }
        )

    current_content = "模拟退款政策：签收后七日内，未使用商品可提交退款申请。"
    expired_content = "旧版模拟退款政策：签收后三日内可申请。"
    faq_content = "模拟物流FAQ：延误状态以订单物流事件和预计送达时间为准。"
    documents = (
        ("current", "退款政策", "policy_v1", current_content, -30, None, "policy"),
        ("expired", "旧退款政策", "policy_v0", expired_content, -90, -40, "policy"),
        ("faq", "物流FAQ", "faq_v1", faq_content, -30, None, "faq"),
    )
    for index, (doc_key, title, version, content, effective, expires, source_type) in enumerate(
        documents
    ):
        doc_id = stable_id(seed, f"document-{doc_key}", index)
        data["knowledge_documents"].append(
            {
                "id": doc_id,
                "store_id": None,
                "title": title,
                "version": version,
                "effective_at": _iso(clock + timedelta(days=effective)),
                "expires_at": _iso(clock + timedelta(days=expires)) if expires else None,
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                "source_type": source_type,
            }
        )
        data["knowledge_chunks"].append(
            {
                "id": stable_id(seed, f"chunk-{doc_key}", index),
                "document_id": doc_id,
                "chunk_key": f"{doc_key}-001",
                "section": "适用规则",
                "content": content,
                "keywords": ["退款", "政策"] if source_type == "policy" else ["物流", "延误"],
                "ordinal": 1,
            }
        )
    current_doc = data["knowledge_documents"][0]
    data["knowledge_chunks"].append(
        {
            "id": stable_id(seed, "chunk-current", 99),
            "document_id": current_doc["id"],
            "chunk_key": "current-002",
            "section": "审批要求",
            "content": "模拟退款请求必须具备有效审批和幂等键。",
            "keywords": ["退款", "审批", "幂等"],
            "ordinal": 2,
        }
    )
    for index, expired in enumerate((False, True)):
        steps = [
            {"order": 1, "action": "核对模拟订单"},
            {"order": 2, "action": "检查退款资格"},
            {"order": 3, "action": "提交审批"},
        ]
        data["sops"].append(
            {
                "id": stable_id(seed, "sop", index),
                "store_id": None,
                "name": "模拟退款处理SOP" if not expired else "旧模拟退款SOP",
                "version": "sop_v1" if not expired else "sop_v0",
                "effective_at": _iso(clock - timedelta(days=30 if not expired else 90)),
                "expires_at": _iso(clock - timedelta(days=40)) if expired else None,
                "content_hash": hashlib.sha256(
                    json.dumps(steps, ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest(),
                "steps": steps,
            }
        )
    return data


def canonical_checksum(dataset: dict[str, list[dict[str, Any]]]) -> str:
    canonical = json.dumps(
        dataset,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def scenario_evidence(dataset: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    evidence: dict[str, list[str]] = {}
    for scenario, table in (
        ("stockout_spike", "inventories"),
        ("refund_spike", "refunds"),
        ("logistics_delay", "logistics"),
        ("conversion_drop", "sales_daily"),
        ("live_campaign", "campaigns"),
    ):
        evidence[scenario] = [row["id"] for row in dataset[table] if row["scenario"] == scenario]
    return evidence
