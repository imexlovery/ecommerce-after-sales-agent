"""Loader and deterministic validator for the canonical business demo dataset.

The files under data/business-demo-v1 are immutable, fictional source
records. This module validates their shape and relationships before the
composition root converts them into the read-only local source adapter.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from after_sales_agent.domain.state import IssueType, OrderStatus

DATASET_ID = "business-demo-v1"
DATASET_ROOT = Path(__file__).resolve().parents[3] / "data" / DATASET_ID
EXPECTED_FAULT_PROFILE_IDS = frozenset(
    {
        "pod-timeout-once",
        "timeline-retry",
        "pod-persistent-unavailable",
        "timeline-persistent-unavailable",
        "policy-unavailable",
        "ticket-uncertain",
    }
)


class DatasetModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None or value == "":
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"dataset timestamp must be timezone-aware: {value}")
    return parsed


def _manifest_evaluated_at(manifest: dict[str, Any]) -> datetime:
    raw_value = manifest.get("evaluated_at")
    if not isinstance(raw_value, str) or raw_value == "":
        raise ValueError("manifest evaluated_at must be a timezone-aware ISO timestamp")
    try:
        parsed = _parse_datetime(raw_value)
    except ValueError as exc:
        raise ValueError("manifest evaluated_at must be a timezone-aware ISO timestamp") from exc
    if parsed is None:
        raise ValueError("manifest evaluated_at must be a timezone-aware ISO timestamp")
    return parsed


def _reject_future_fact(value: datetime, label: str, evaluated_at: datetime) -> None:
    if value > evaluated_at:
        raise ValueError(f"{label} cannot be later than manifest evaluated_at")


class CustomerRecord(DatasetModel):
    customer_id: str = Field(min_length=1)
    customer_key: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    region: str = Field(min_length=1)
    default_service_level: str = Field(min_length=1)


class OrderRecord(DatasetModel):
    order_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    order_status: OrderStatus
    tracking_number: str = Field(min_length=1)
    service_level: str = Field(min_length=1)
    region: str = Field(min_length=1)
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    source_revision: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dates(self) -> OrderRecord:
        for name in ("shipped_at", "delivered_at"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must be timezone-aware")
        return self


class ShipmentRecord(DatasetModel):
    shipment_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    package_sequence: int = Field(ge=1)
    package_count: int = Field(ge=1)
    tracking_number: str = Field(min_length=1)
    shipment_status: str = Field(min_length=1)
    carrier_code: str = Field(min_length=1)


class TrackingEventRecord(DatasetModel):
    event_id: str = Field(min_length=1)
    shipment_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    occurred_at: datetime
    location: str = Field(min_length=1)
    note: str | None = None

    @model_validator(mode="after")
    def validate_occurred_at(self) -> TrackingEventRecord:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return self


class DeliveryProofRecord(DatasetModel):
    proof_id: str = Field(min_length=1)
    shipment_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    recipient_type: str = Field(min_length=1)
    signed_at: datetime
    note: str | None = None

    @model_validator(mode="after")
    def validate_signed_at(self) -> DeliveryProofRecord:
        if self.signed_at.tzinfo is None or self.signed_at.utcoffset() is None:
            raise ValueError("signed_at must be timezone-aware")
        return self


class CarrierAlertRecord(DatasetModel):
    alert_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    description: str = Field(min_length=1)
    active_from: datetime
    active_until: datetime | None = None
    impact_area: str = "regional"
    status: str = "active"
    expected_recovery_at: datetime | None = None

    @model_validator(mode="after")
    def validate_window(self) -> CarrierAlertRecord:
        for name in ("active_from", "active_until", "expected_recovery_at"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must be timezone-aware")
        if self.active_until is not None and self.active_until < self.active_from:
            raise ValueError("active_until cannot precede active_from")
        if self.expected_recovery_at is not None and self.expected_recovery_at < self.active_from:
            raise ValueError("expected_recovery_at cannot precede active_from")
        if self.status not in {"active", "resolved"}:
            raise ValueError("carrier alert status must be active or resolved")
        return self


class InvestigationCaseRecord(DatasetModel):
    case_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    issue_type: IssueType
    case_state: str = Field(min_length=1)
    case_outcome: str | None = None
    reason_code: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    target_shipment_id: str | None = None
    stage: str = "investigating"
    next_update_at: datetime | None = None

    @model_validator(mode="after")
    def validate_timestamps(self) -> InvestigationCaseRecord:
        for name in ("created_at", "updated_at", "next_update_at"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self


class FaultProfileRecord(DatasetModel):
    fault_profile_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    mode: str = Field(min_length=1)
    description: str = Field(min_length=1)


class PolicyClauseRecord(DatasetModel):
    clause_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    issue_type: IssueType
    service_level: str = Field(min_length=1)
    region: str = Field(min_length=1)
    eligible: bool
    stalled_after_hours: int = Field(ge=0)
    effective_from: datetime
    effective_to: datetime | None = None
    required_evidence_codes: list[str] = Field(min_length=1)
    customer_summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dates(self) -> PolicyClauseRecord:
        for name in ("effective_from", "effective_to"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must be timezone-aware")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot precede effective_from")
        return self


class ScenarioRecord(DatasetModel):
    scenario_id: str = Field(min_length=1)
    customer_key: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    issue_type: IssueType
    expected_disposition: str = Field(
        pattern=r"^(ANSWER|WAIT|CLARIFY|INVESTIGATE|ESCALATE)$"
    )
    note: str = Field(min_length=1)
    target_shipment_id: str | None = None
    customer_message: str | None = None
    expected_tool_sequence: list[str] = Field(default_factory=list)


class BusinessDemoDataset(DatasetModel):
    manifest: dict[str, Any]
    customers: tuple[CustomerRecord, ...]
    orders: tuple[OrderRecord, ...]
    shipments: tuple[ShipmentRecord, ...]
    tracking_events: tuple[TrackingEventRecord, ...]
    delivery_proofs: tuple[DeliveryProofRecord, ...]
    carrier_alerts: tuple[CarrierAlertRecord, ...]
    investigation_cases: tuple[InvestigationCaseRecord, ...]
    fault_profiles: tuple[FaultProfileRecord, ...]
    scenario_catalog: tuple[ScenarioRecord, ...]
    policy_clauses: tuple[PolicyClauseRecord, ...]

    @property
    def customer_by_key(self) -> dict[str, CustomerRecord]:
        return {item.customer_key: item for item in self.customers}

    @property
    def order_by_id(self) -> dict[str, OrderRecord]:
        return {item.order_id: item for item in self.orders}

    @property
    def shipment_by_id(self) -> dict[str, ShipmentRecord]:
        return {item.shipment_id: item for item in self.shipments}


def _coerce_row(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for field in (
        "shipped_at",
        "delivered_at",
        "occurred_at",
        "signed_at",
        "active_from",
        "active_until",
        "expected_recovery_at",
        "created_at",
        "updated_at",
        "next_update_at",
        "effective_from",
        "effective_to",
    ):
        if field in result:
            result[field] = _parse_datetime(result[field])
    for field in ("package_sequence", "package_count"):
        if field in result:
            result[field] = int(result[field])
    if "eligible" in result:
        result["eligible"] = result["eligible"].strip().lower() == "true"
    if "required_evidence_codes" in result:
        result["required_evidence_codes"] = json.loads(result["required_evidence_codes"])
    return result


def _read_csv[T: DatasetModel](root: Path, filename: str, model: type[T]) -> tuple[T, ...]:
    with (root / filename).open("r", encoding="utf-8", newline="") as handle:
        rows = csv.DictReader(handle)
        if rows.fieldnames is None:
            raise ValueError(f"{filename} has no header")
        return tuple(model.model_validate(_coerce_row(dict(row))) for row in rows)


def _read_json(root: Path, filename: str) -> Any:
    with (root / filename).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_dataset(root: Path) -> BusinessDemoDataset:
    policy_rows = tuple(
        PolicyClauseRecord.model_validate(
            {
                **row,
                "effective_from": _parse_datetime(row["effective_from"]),
                "effective_to": _parse_datetime(row.get("effective_to")),
            }
        )
        for row in _read_json(root, "policies/clauses.json")
    )
    return BusinessDemoDataset(
        manifest=_read_json(root, "manifest.json"),
        customers=_read_csv(root, "customers.csv", CustomerRecord),
        orders=_read_csv(root, "orders.csv", OrderRecord),
        shipments=_read_csv(root, "shipments.csv", ShipmentRecord),
        tracking_events=_read_csv(root, "tracking_events.csv", TrackingEventRecord),
        delivery_proofs=_read_csv(root, "delivery_proofs.csv", DeliveryProofRecord),
        carrier_alerts=_read_csv(root, "carrier_alerts.csv", CarrierAlertRecord),
        investigation_cases=_read_csv(root, "investigation_cases.csv", InvestigationCaseRecord),
        fault_profiles=tuple(
            FaultProfileRecord.model_validate(row)
            for row in _read_json(root, "fault_profiles.json")
        ),
        scenario_catalog=tuple(
            ScenarioRecord.model_validate(row)
            for row in _read_json(root, "scenario_catalog.json")
        ),
        policy_clauses=policy_rows,
    )


def _unique(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} contains duplicate identifiers")


def _validate_dataset(dataset: BusinessDemoDataset) -> dict[str, int]:
    if dataset.manifest.get("dataset_id") != DATASET_ID:
        raise ValueError("manifest dataset_id must be business-demo-v1")
    if dataset.manifest.get("schema_version") != DATASET_ID:
        raise ValueError("manifest schema_version must be business-demo-v1")
    if dataset.manifest.get("timezone") != "UTC":
        raise ValueError("manifest timezone must be UTC")
    if dataset.manifest.get("synthetic_only") is not True:
        raise ValueError("dataset must explicitly be synthetic_only")
    evaluated_at = _manifest_evaluated_at(dataset.manifest)

    _unique(tuple(item.customer_id for item in dataset.customers), "customers")
    _unique(tuple(item.customer_key for item in dataset.customers), "customer keys")
    _unique(tuple(item.order_id for item in dataset.orders), "orders")
    _unique(tuple(item.shipment_id for item in dataset.shipments), "shipments")
    _unique(tuple(item.event_id for item in dataset.tracking_events), "tracking events")
    _unique(tuple(item.proof_id for item in dataset.delivery_proofs), "delivery proofs")
    _unique(tuple(item.alert_id for item in dataset.carrier_alerts), "carrier alerts")
    _unique(tuple(item.case_id for item in dataset.investigation_cases), "investigation cases")
    _unique(tuple(item.clause_id for item in dataset.policy_clauses), "policy clauses")
    fault_profile_ids = {item.fault_profile_id for item in dataset.fault_profiles}
    if fault_profile_ids != EXPECTED_FAULT_PROFILE_IDS:
        raise ValueError(
            "fault profiles must be exactly: "
            + ", ".join(sorted(EXPECTED_FAULT_PROFILE_IDS))
        )

    customer_keys = {item.customer_key for item in dataset.customers}
    orders = dataset.order_by_id
    order_ids = set(orders)
    if {item.customer_id for item in dataset.orders} - customer_keys:
        raise ValueError("every order must reference a known customer_key")
    for order in orders.values():
        for name in ("shipped_at", "delivered_at"):
            value = getattr(order, name)
            if value is not None:
                _reject_future_fact(value, f"order {order.order_id} {name}", evaluated_at)
        if order.shipped_at is not None and order.delivered_at is not None:
            if order.delivered_at < order.shipped_at:
                raise ValueError("delivered_at cannot precede shipped_at")
        if order.order_status is OrderStatus.DELIVERED:
            if order.shipped_at is None or order.delivered_at is None:
                raise ValueError("delivered orders must have shipped_at and delivered_at")
        elif order.delivered_at is not None:
            raise ValueError("non-delivered orders cannot have delivered_at")
    shipment_by_id = dataset.shipment_by_id
    shipment_order_ids = {item.order_id for item in dataset.shipments}
    if shipment_order_ids - order_ids:
        raise ValueError("every shipment must reference a known order")
    if shipment_order_ids != order_ids:
        raise ValueError("every order must have at least one shipment")
    package_groups: dict[str, list[ShipmentRecord]] = {}
    for shipment in dataset.shipments:
        package_groups.setdefault(shipment.order_id, []).append(shipment)
        if shipment.package_sequence > shipment.package_count:
            raise ValueError("package_sequence cannot exceed package_count")
    package_distribution = {
        1: sum(
            len(items) == 1 and items[0].package_count == 1
            for items in package_groups.values()
        ),
        2: sum(
            len(items) == 2 and all(item.package_count == 2 for item in items)
            for items in package_groups.values()
        ),
        3: sum(
            len(items) == 3 and all(item.package_count == 3 for item in items)
            for items in package_groups.values()
        ),
    }
    if package_distribution != {1: 34, 2: 4, 3: 2}:
        raise ValueError(f"unexpected package distribution: {package_distribution}")
    if any(
        sorted(item.package_sequence for item in items)
        != list(range(1, items[0].package_count + 1))
        for items in package_groups.values()
    ):
        raise ValueError("package sequences must be complete and ordered")

    for event in dataset.tracking_events:
        _reject_future_fact(
            event.occurred_at,
            f"tracking event {event.event_id} occurred_at",
            evaluated_at,
        )
        event_shipment = shipment_by_id.get(event.shipment_id)
        if event_shipment is None or event_shipment.order_id != event.order_id:
            raise ValueError("tracking event must reference its shipment and order")
        order = orders[event.order_id]
        if order.shipped_at is not None and event.occurred_at < order.shipped_at:
            raise ValueError("tracking event cannot precede its order shipment time")
        if order.delivered_at is not None and event.occurred_at > order.delivered_at:
            raise ValueError("tracking event cannot follow its order delivery time")
    delivered_shipments = {
        item.shipment_id for item in dataset.shipments if item.shipment_status == "delivered"
    }
    proof_shipments = {item.shipment_id for item in dataset.delivery_proofs}
    if any(item.shipment_id not in delivered_shipments for item in dataset.delivery_proofs):
        raise ValueError("a delivery proof must reference a delivered shipment")
    for proof in dataset.delivery_proofs:
        _reject_future_fact(
            proof.signed_at,
            f"delivery proof {proof.proof_id} signed_at",
            evaluated_at,
        )
        shipment = shipment_by_id[proof.shipment_id]
        if shipment.order_id != proof.order_id:
            raise ValueError("delivery proof must match its shipment order")
        order = orders[proof.order_id]
        if order.shipped_at is not None and proof.signed_at < order.shipped_at:
            raise ValueError("delivery proof cannot precede its order shipment time")
        if order.delivered_at is not None and proof.signed_at > order.delivered_at:
            raise ValueError("delivery proof cannot follow its order delivery time")
    if {item.order_id for item in dataset.delivery_proofs} - order_ids:
        raise ValueError("delivery proof must reference a known order")
    for alert in dataset.carrier_alerts:
        if alert.order_id not in order_ids:
            raise ValueError("carrier alert must reference a known order")
    for case in dataset.investigation_cases:
        for name in ("created_at", "updated_at"):
            _reject_future_fact(
                getattr(case, name),
                f"investigation case {case.case_id} {name}",
                evaluated_at,
            )
        if case.order_id not in order_ids or case.customer_id != orders[case.order_id].customer_id:
            raise ValueError("investigation case must match its order owner")
    if any(
        item.customer_key not in customer_keys or item.order_id not in order_ids
        for item in dataset.scenario_catalog
    ):
        raise ValueError("scenario catalog must reference known synthetic records")
    if len(dataset.scenario_catalog) < 5:
        raise ValueError("scenario catalog must contain at least five demo combinations")
    shipment_ids = set(shipment_by_id)
    for case in dataset.investigation_cases:
        if case.target_shipment_id is not None and case.target_shipment_id not in shipment_ids:
            raise ValueError("investigation case target shipment must be known")
    for scenario in dataset.scenario_catalog:
        if (
            scenario.target_shipment_id is not None
            and scenario.target_shipment_id not in shipment_ids
        ):
            raise ValueError("scenario target shipment must be known")

    counts = {
        "customers": len(dataset.customers),
        "orders": len(dataset.orders),
        "shipments": len(dataset.shipments),
        "tracking_events": len(dataset.tracking_events),
        "delivered_shipments": len(delivered_shipments),
        "delivery_proofs": len(dataset.delivery_proofs),
        "missing_delivery_proofs": len(delivered_shipments - proof_shipments),
        "carrier_alerts": len(dataset.carrier_alerts),
        "investigation_cases": len(dataset.investigation_cases),
        "active_investigation_cases": sum(
            item.case_state == "investigating" for item in dataset.investigation_cases
        ),
        "closed_investigation_cases": sum(
            item.case_state == "closed" for item in dataset.investigation_cases
        ),
        "fault_profiles": len(dataset.fault_profiles),
        "policy_clauses": len(dataset.policy_clauses),
    }
    expected = dataset.manifest.get("counts", {})
    if any(expected.get(key) != value for key, value in counts.items()):
        raise ValueError(f"manifest counts do not match dataset: {counts}")
    if counts["active_investigation_cases"] != 6 or counts["closed_investigation_cases"] != 2:
        raise ValueError("investigation case lifecycle counts must be 6 active and 2 closed")
    return counts


def load_business_demo_dataset(root: Path | None = None) -> BusinessDemoDataset:
    """Load and validate the canonical business demo source records."""

    dataset = _read_dataset(root or DATASET_ROOT)
    _validate_dataset(dataset)
    return dataset


def validate_business_demo_dataset(root: Path | None = None) -> dict[str, Any]:
    """Return a stable validation summary or raise on the first invalid contract."""

    dataset = load_business_demo_dataset(root)
    return {
        "dataset_id": DATASET_ID,
        "schema_version": dataset.manifest["schema_version"],
        "valid": True,
        "counts": _validate_dataset(dataset),
    }


def main() -> int:
    try:
        summary = validate_business_demo_dataset()
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {"dataset_id": DATASET_ID, "valid": False, "error": str(exc)},
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
