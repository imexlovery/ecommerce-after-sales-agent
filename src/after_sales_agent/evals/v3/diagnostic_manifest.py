"""Typed, committed input manifest for the V3 selector transport diagnostic."""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from after_sales_agent.application.adaptive_core import (
    EvidenceRequirementCode,
    RecoveryRoute,
)
from after_sales_agent.domain.state import IssueType
from after_sales_agent.evals.v3.contracts import sha256_json

DIAGNOSTIC_IDENTITY: Final = "V3-DEV-DIAG-20260828-04"
DIAGNOSTIC_MANIFEST_RELATIVE: Final = Path(
    "evals/v3/diagnostic-manifests/V3-DEV-DIAG-20260828-04.json"
)
DIAGNOSTIC_MANIFEST_SCHEMA_VERSION: Final = "v3.selector-transport-diagnostic-manifest.v1"

_TOOL_TO_REQUIREMENT = {
    "get_order_context": EvidenceRequirementCode.ORDER_STATUS,
    "get_logistics_timeline": EvidenceRequirementCode.TRACKING_TIMELINE,
    "get_delivery_proof": EvidenceRequirementCode.DELIVERY_PROOF,
    "search_after_sales_policy": EvidenceRequirementCode.POLICY_APPLICABILITY,
    "get_existing_logistics_tickets": EvidenceRequirementCode.ACTIVE_TICKET_STATUS,
    "get_carrier_service_alerts": EvidenceRequirementCode.CARRIER_ALERT_CONTEXT,
}
_STAGES = Literal[
    "first_observation",
    "intermediate_observation",
    "policy_parameter_path",
    "ticket_parameter_path",
    "legal_finish",
    "premature_finish_boundary",
    "guard_recovery_boundary",
]


class DiagnosticManifestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_id: str = Field(pattern=r"^V3-DIAG-[A-Z0-9-]{3,80}$")
    stage: _STAGES
    issue_type: IssueType
    completed_tools: tuple[str, ...] = Field(default_factory=tuple, max_length=6)
    expected_action: Literal["call_tool", "finish"]
    expected_tool_name: str | None = Field(default=None, max_length=96)
    expected_evidence_requirement: EvidenceRequirementCode | None = None
    expected_route: RecoveryRoute
    context_note: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_input(self) -> DiagnosticManifestInput:
        if len(self.completed_tools) != len(set(self.completed_tools)):
            raise ValueError("diagnostic completed_tools must be unique")
        if any(tool not in _TOOL_TO_REQUIREMENT for tool in self.completed_tools):
            raise ValueError("diagnostic completed_tools contains an unknown read tool")
        if self.expected_action == "finish":
            if (
                self.expected_tool_name is not None
                or self.expected_evidence_requirement is not None
            ):
                raise ValueError("finish diagnostic input cannot expect a tool or requirement")
            if self.expected_route is not RecoveryRoute.FINALIZE:
                raise ValueError("finish diagnostic input must expect finalize")
        else:
            if self.expected_tool_name not in _TOOL_TO_REQUIREMENT:
                raise ValueError("call diagnostic input must expect an allowlisted tool")
            expected_requirement = _TOOL_TO_REQUIREMENT[self.expected_tool_name]
            if self.expected_evidence_requirement is not expected_requirement:
                raise ValueError("diagnostic tool and evidence requirement do not match")
            if self.expected_route is not RecoveryRoute.REPLAN:
                raise ValueError("call diagnostic input must expect replan")
        if self.issue_type is IssueType.SIGNED_NOT_RECEIVED:
            if (
                "get_delivery_proof" in self.completed_tools
                and self.expected_tool_name == "get_delivery_proof"
            ):
                raise ValueError("signed diagnostic cannot repeat delivery proof")
        if self.issue_type is IssueType.STALLED_TRACKING:
            if self.expected_tool_name == "get_delivery_proof":
                raise ValueError("stalled diagnostic cannot select delivery proof")
        return self


class DiagnosticManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v3.selector-transport-diagnostic-manifest.v1"] = (
        DIAGNOSTIC_MANIFEST_SCHEMA_VERSION
    )
    diagnostic_identity: Literal["V3-DEV-DIAG-20260828-04"] = DIAGNOSTIC_IDENTITY
    manifest_id: str = Field(pattern=r"^V3-DEV-DIAG-MANIFEST-[A-Z0-9-]{3,64}$")
    model: Literal["deepseek-v4-flash"]
    timeout_seconds: float = 30.0
    max_provider_calls: Literal[24] = 24
    max_attempts_per_input: Literal[2] = 2
    inputs: tuple[DiagnosticManifestInput, ...] = Field(min_length=12, max_length=12)

    @model_validator(mode="after")
    def validate_manifest(self) -> DiagnosticManifest:
        if self.timeout_seconds != 30.0:
            raise ValueError("diagnostic timeout must remain 30 seconds")
        input_ids = [item.input_id for item in self.inputs]
        if len(input_ids) != len(set(input_ids)):
            raise ValueError("diagnostic input IDs must be unique")
        if {item.issue_type for item in self.inputs} != {
            IssueType.SIGNED_NOT_RECEIVED,
            IssueType.STALLED_TRACKING,
        }:
            raise ValueError("diagnostic manifest must cover both supported issues")
        stages = {item.stage for item in self.inputs}
        if not {"first_observation", "intermediate_observation", "legal_finish"}.issubset(stages):
            raise ValueError("diagnostic manifest is missing required progress stages")
        return self


def diagnostic_manifest_digest(manifest: DiagnosticManifest) -> str:
    """Hash the normalized manifest, excluding no mutable runtime fields."""

    return sha256_json(manifest.model_dump(mode="json"))


def diagnostic_manifest_path(project_root: Path) -> Path:
    return project_root.expanduser().resolve() / DIAGNOSTIC_MANIFEST_RELATIVE


def load_diagnostic_manifest(project_root: Path) -> tuple[DiagnosticManifest, str, Path]:
    path = diagnostic_manifest_path(project_root)
    manifest = DiagnosticManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if manifest.diagnostic_identity != DIAGNOSTIC_IDENTITY:
        raise ValueError("diagnostic manifest identity is not authorized")
    return manifest, diagnostic_manifest_digest(manifest), path


def diagnostic_manifest_payload(project_root: Path) -> dict[str, object]:
    """Return safe validation metadata without provider or customer data."""

    manifest, digest, path = load_diagnostic_manifest(project_root)
    return {
        "manifest_id": manifest.manifest_id,
        "diagnostic_identity": manifest.diagnostic_identity,
        "manifest_digest": digest,
        "manifest_path": str(path),
        "input_count": len(manifest.inputs),
        "max_provider_calls": manifest.max_provider_calls,
    }


__all__ = [
    "DIAGNOSTIC_IDENTITY",
    "DIAGNOSTIC_MANIFEST_RELATIVE",
    "DIAGNOSTIC_MANIFEST_SCHEMA_VERSION",
    "DiagnosticManifest",
    "DiagnosticManifestInput",
    "diagnostic_manifest_digest",
    "diagnostic_manifest_path",
    "diagnostic_manifest_payload",
    "load_diagnostic_manifest",
]
