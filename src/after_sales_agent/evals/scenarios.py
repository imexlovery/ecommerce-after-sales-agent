"""Scenario loading, validation, and deterministic synthetic fixture variants."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from after_sales_agent.domain.state import ActionState, ExecutionStatus, IssueType
from after_sales_agent.evals.contracts import ScenarioManifest
from after_sales_agent.fixtures.catalog import (
    ActionFixtureFault,
    FixtureFault,
    FixtureStore,
    default_fixture_store,
)
from after_sales_agent.tools.contracts import LogisticsTicket


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_scenarios(root: Path | None = None) -> list[ScenarioManifest]:
    scenario_root = (root or project_root()) / "evals" / "scenarios"
    manifests: list[ScenarioManifest] = []
    for path in sorted(scenario_root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"scenario file must contain a JSON array: {path}")
        manifests.extend(ScenarioManifest.model_validate(item) for item in payload)
    _validate_scenario_collection(manifests)
    return manifests


def _validate_scenario_collection(manifests: list[ScenarioManifest]) -> None:
    ids = [item.scenario_id for item in manifests]
    if len(ids) != len(set(ids)):
        raise ValueError("scenario_id values must be globally unique")
    triage_locked = [
        item
        for item in manifests
        if item.dataset_partition == "locked" and "triage" in item.applicable_layers
    ]
    investigation_locked = [
        item
        for item in manifests
        if item.dataset_partition == "locked" and "investigation" in item.applicable_layers
    ]
    if len(triage_locked) != 12:
        raise ValueError("the locked Triage set must contain exactly 12 scenarios")
    if len(investigation_locked) != 8:
        raise ValueError("the locked Investigation set must contain exactly 8 scenarios")
    if any("full_e2e" not in item.applicable_layers for item in investigation_locked):
        raise ValueError("all 8 locked Investigation scenarios must share Layer 3 IDs")


def fixture_for_scenario(scenario: ScenarioManifest) -> FixtureStore:
    store = default_fixture_store()
    if scenario.fixture_profile == "existing_ticket":
        normalized = scenario.normalized_case_input
        if normalized is None:
            raise ValueError("existing_ticket profile requires normalized_case_input")
        store.add_ticket(
            LogisticsTicket(
                ticket_id=f"ticket-{scenario.scenario_id}",
                order_id=normalized.order_id,
                issue_type=IssueType(normalized.issue_type),
                ticket_status="open",
                created_at=datetime(2026, 8, 23, 9, 0, tzinfo=UTC),
            )
        )
    elif scenario.fixture_profile in {"pod_timeout_once", "pod_timeout_persistent"}:
        attempts = 1 if scenario.fixture_profile == "pod_timeout_once" else 2
        store = store.with_faults(
            {
                (scenario.fault_seed, "get_delivery_proof", attempt): FixtureFault(
                    execution_status=ExecutionStatus.RETRYABLE_ERROR,
                    error_code="EVAL_SYNTHETIC_POD_TIMEOUT",
                )
                for attempt in range(1, attempts + 1)
            }
        )
    elif scenario.fixture_profile == "action_uncertain":
        store = store.with_action_faults(
            {
                scenario.fault_seed: [
                    ActionFixtureFault(
                        action_state=ActionState.UNCERTAIN,
                        error_code="EVAL_SYNTHETIC_WRITE_RESPONSE_LOST",
                    )
                ]
            }
        )
    return store
