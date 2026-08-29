from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from after_sales_agent.config import Settings
from after_sales_agent.fixtures.business_demo import (
    DATASET_ROOT,
    load_business_demo_dataset,
    validate_business_demo_dataset,
)
from after_sales_agent.fixtures.catalog import default_fixture_store, legacy_fixture_store


def test_business_demo_dataset_has_canonical_counts_and_relationships() -> None:
    summary = validate_business_demo_dataset()

    assert summary == {
        "dataset_id": "business-demo-v1",
        "schema_version": "business-demo-v1",
        "valid": True,
        "counts": {
            "customers": 20,
            "orders": 40,
            "shipments": 48,
            "tracking_events": 132,
            "delivered_shipments": 20,
            "delivery_proofs": 14,
            "missing_delivery_proofs": 6,
            "carrier_alerts": 8,
            "investigation_cases": 8,
            "active_investigation_cases": 6,
            "closed_investigation_cases": 2,
            "fault_profiles": 6,
            "policy_clauses": 10,
        },
    }

    dataset = load_business_demo_dataset()
    assert all(
        record.shipped_at is None
        or (record.shipped_at.tzinfo is not None and record.shipped_at.utcoffset() is not None)
        for record in dataset.orders
    )
    assert dataset.customer_by_key["customer_a"].display_name == "虚拟客户 A"
    assert len(dataset.shipment_by_id) == 48


def test_business_demo_catalog_has_stable_package_and_issue_matrix_ids() -> None:
    scenario_ids = {item.scenario_id for item in load_business_demo_dataset().scenario_catalog}

    assert len(scenario_ids) == 21
    assert "partial-packages-target-c" in scenario_ids
    assert {
        "signed-pod-recipient-clarification",
        "signed-pod-location-explanation",
        "signed-pod-conflict",
        "signed-pod-absent",
        "signed-pod-transient-retry",
        "signed-pod-persistent-unavailable",
        "signed-active-investigation",
        "signed-foreign-order",
        "signed-active-carrier-recovery",
        "signed-front-desk-denial",
    } <= scenario_ids
    assert {
        "stalled-not-shipped-claim",
        "stalled-within-sla",
        "stalled-overdue-no-active-case",
        "stalled-carrier-recovery",
        "stalled-resolved-carrier-alert",
        "stalled-active-investigation",
        "stalled-timeline-transient-retry",
        "stalled-timeline-persistent-unavailable",
        "stalled-delivered-issue-revision",
        "stalled-structural-conflict",
    } <= scenario_ids


def test_default_store_is_business_demo_and_legacy_store_is_explicit() -> None:
    current = default_fixture_store()
    legacy = legacy_fixture_store()

    assert current.fixture_version == "business-demo-v1"
    assert len(current.customer_keys) == 20
    assert len(current.list_orders_for_customer("customer_a")) == 3
    assert len(current.get_shipments("ORD-001")) == 1
    assert current.get_delivery_proof("ORD-001") is None
    assert legacy.fixture_version == "fixture-v1"
    assert len(legacy.customer_keys) == 2


def test_default_settings_match_business_demo_manifest_evaluated_at() -> None:
    dataset = load_business_demo_dataset()
    expected = datetime.fromisoformat(dataset.manifest["evaluated_at"].replace("Z", "+00:00"))
    settings = Settings(_env_file=None)

    assert settings.fixture_version == dataset.manifest["dataset_id"]
    assert settings.scenario_evaluated_at == expected


def _copy_business_demo(tmp_path: Path) -> Path:
    root = tmp_path / "business-demo-v1"
    shutil.copytree(DATASET_ROOT, root)
    return root


def _replace_csv_values(
    root: Path,
    filename: str,
    identifier_field: str,
    identifier: str,
    replacements: dict[str, str],
) -> None:
    path = root / filename
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = reader.fieldnames
    assert fieldnames is not None
    row = next(item for item in rows if item[identifier_field] == identifier)
    row.update(replacements)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_manifest_evaluated_at_must_be_timezone_aware(tmp_path: Path) -> None:
    root = _copy_business_demo(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["evaluated_at"] = "2026-08-29T08:00:00"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest evaluated_at.*timezone-aware"):
        load_business_demo_dataset(root)


@pytest.mark.parametrize(
    ("filename", "identifier_field", "identifier", "replacements"),
    [
        ("orders.csv", "order_id", "ORD-002", {"shipped_at": "2026-08-29T08:00:01Z"}),
        ("orders.csv", "order_id", "ORD-001", {"delivered_at": "2026-08-29T08:00:01Z"}),
        (
            "tracking_events.csv",
            "event_id",
            "EVT-004",
            {"occurred_at": "2026-08-29T08:00:01Z"},
        ),
        (
            "delivery_proofs.csv",
            "proof_id",
            "POD-004",
            {"signed_at": "2026-08-29T08:00:01Z"},
        ),
        (
            "investigation_cases.csv",
            "case_id",
            "SEED-CASE-001",
            {
                "created_at": "2026-08-29T08:00:01Z",
                "updated_at": "2026-08-29T08:01:01Z",
            },
        ),
        (
            "investigation_cases.csv",
            "case_id",
            "SEED-CASE-001",
            {"updated_at": "2026-08-29T08:00:01Z"},
        ),
    ],
)
def test_future_facts_fail_closed(
    tmp_path: Path,
    filename: str,
    identifier_field: str,
    identifier: str,
    replacements: dict[str, str],
) -> None:
    root = _copy_business_demo(tmp_path)
    _replace_csv_values(root, filename, identifier_field, identifier, replacements)

    with pytest.raises(ValueError, match="cannot be later than manifest evaluated_at"):
        validate_business_demo_dataset(root)
