from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from after_sales_agent.evals.v3.contracts import (
    V3Metrics,
    V3Predicate,
    validate_manifest_cases,
    validate_paired_cases,
)
from after_sales_agent.evals.v3.matrix import CASES_BY_ID, V3A_CASES, V3B_CASES, manifests
from after_sales_agent.evals.v3.store import V3PrepStore, V3StoreError


def test_v3_predicates_fail_closed_for_unknown_paths_and_operators() -> None:
    with pytest.raises(ValidationError):
        V3Predicate(field_path="prompt.text", operator="equals", value="x")
    with pytest.raises(ValidationError):
        V3Predicate(field_path="tool_name", operator="regex", value="x")


def test_manifest_rejects_duplicate_case_ids_and_matrix_has_all_pairs() -> None:
    manifest = manifests()[0]
    payload = manifest.model_dump(mode="json")
    payload["case_ids"] = [manifest.case_ids[0], manifest.case_ids[0]]
    from after_sales_agent.evals.v3.contracts import V3DevelopmentManifest

    with pytest.raises(ValidationError):
        V3DevelopmentManifest.model_validate(payload)
    extra = manifest.model_dump(mode="json") | {"unexpected": True}
    with pytest.raises(ValidationError):
        V3DevelopmentManifest.model_validate(extra)
    assert len(V3A_CASES) == 24
    assert len(V3B_CASES) == 8


def test_missing_pair_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing or extra pair"):
        validate_paired_cases((V3A_CASES[0],), ())


def test_manifest_binding_rejects_revision_and_unregistered_grader() -> None:
    manifest = manifests()[0]
    altered_payload = manifest.model_dump(mode="json") | {"source_revision": "0" * 40}
    from after_sales_agent.evals.v3.contracts import V3DevelopmentManifest

    altered = V3DevelopmentManifest.model_validate(altered_payload)
    with pytest.raises(ValueError, match="source revision mismatch"):
        validate_manifest_cases(altered, CASES_BY_ID, {"GR-V3A-01"})
    altered_case = V3A_CASES[0].model_copy(update={"expected_grader_ids": ("GR-UNKNOWN",)})
    altered_cases = dict(CASES_BY_ID)
    altered_cases[altered_case.scenario_id] = altered_case
    with pytest.raises(ValueError, match="unregistered V3 grader"):
        validate_manifest_cases(manifest, altered_cases, {"GR-V3A-01"})


def test_cost_never_defaults_to_zero_without_price_basis() -> None:
    with pytest.raises(ValidationError):
        V3Metrics(
            actual_reads=0,
            cache_hits=0,
            unnecessary_reads=0,
            retry_attempts=0,
            rebuild_parity=True,
            clarification_questions=0,
            repeated_questions=0,
            latency_ms=0,
            model_calls=0,
            provider_calls=0,
            cost=0.0,
        )
    assert V3Metrics(
        actual_reads=0,
        cache_hits=0,
        unnecessary_reads=0,
        retry_attempts=0,
        rebuild_parity=True,
        clarification_questions=0,
        repeated_questions=0,
        latency_ms=0,
        model_calls=0,
        provider_calls=0,
    ).cost == "unavailable"


def test_store_rejects_v2_roots_and_requires_var_v3(tmp_path: Path) -> None:
    with pytest.raises(V3StoreError):
        V3PrepStore(tmp_path / "evals" / "v3")
    with pytest.raises(V3StoreError):
        V3PrepStore(tmp_path / "generated")
