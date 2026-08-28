"""Write-once authorization and append-only state for the formal V3 run.

The committed V3 manifests describe a reserved matrix and deliberately remain
closed.  A formal Development run is opened by a separate, runtime-created
package after the implementation source has been committed.  The package
binds that evaluated source revision to the committed manifest digests, plan,
case-input digest, and the Owner-approved budget semantics.  It never stores a
credential value.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from os import fsync
from pathlib import Path
from typing import Any, Final, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from after_sales_agent.evals.v3.contracts import V3Contract, sha256_json

EXECUTION_PACKAGE_SCHEMA_VERSION: Final = "v3.development-execution-package.v1"
EXECUTION_PACKAGE_ENVELOPE_SCHEMA_VERSION: Final = (
    "v3.development-execution-package-envelope.v1"
)
EXECUTION_STATE_EVENT_SCHEMA_VERSION: Final = "v3.development-execution-state-event.v1"
EXECUTION_IDENTITY_PATTERN: Final = r"^V3-DEV-EXEC-[A-Z0-9][A-Z0-9-]{2,79}$"

# These constants are the one Owner-authorized Development measurement in this
# task.  They are intentionally not CLI options and are not inferred from an
# arbitrary caller-provided flag.
FORMAL_DEVELOPMENT_EXECUTION_IDENTITY: Final = "V3-DEV-EXEC-20260828-03"
FORMAL_MODEL_NAME: Final = "deepseek-v4-flash"
FORMAL_PROVIDER_CALL_CEILING: Final = 256
FORMAL_PROVIDER_CALL_CEILING_PER_RUN: Final = 8
FORMAL_TOKEN_THRESHOLD: Final = 1_000_000
FORMAL_OUTPUT_TOKEN_CAP: Final = 512
FORMAL_TIMEOUT_SECONDS: Final = 30.0
FORMAL_REPEAT: Final = 1


class ExecutionPackageError(RuntimeError):
    """Raised when a formal package cannot be created or replayed safely."""


class V3DevelopmentExecutionPackage(V3Contract):
    """The complete immutable authorization binding for one identity."""

    schema_version: Literal["v3.development-execution-package.v1"] = (
        EXECUTION_PACKAGE_SCHEMA_VERSION
    )
    package_id: str = Field(min_length=1)
    execution_identity: str = Field(pattern=EXECUTION_IDENTITY_PATTERN)
    scope: Literal["development_measurement_only"] = "development_measurement_only"
    evaluated_source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    manifest_source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    manifest_digests: Mapping[str, str]
    plan_version: str = Field(min_length=1)
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    production_case_inputs_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    matrix_case_count: Literal[32] = 32
    planned_run_count: Literal[64] = 64
    paired_run_count: Literal[64] = 64
    repeat: Literal[1] = FORMAL_REPEAT
    timeout_seconds: float = FORMAL_TIMEOUT_SECONDS
    selector_turn_ceiling_per_run: Literal[8] = FORMAL_PROVIDER_CALL_CEILING_PER_RUN
    selector_turn_ceiling_per_case: Literal[16] = 16
    authorized_provider_call_ceiling: Literal[256] = FORMAL_PROVIDER_CALL_CEILING
    authorized_provider_call_ceiling_per_run: Literal[8] = FORMAL_PROVIDER_CALL_CEILING_PER_RUN
    provider_hard_ceiling: Literal[True] = True
    provider_calls_per_selector_turn: Mapping[str, int]
    provider_call_ceiling_by_architecture: Mapping[str, int]
    token_threshold: Literal[1_000_000] = FORMAL_TOKEN_THRESHOLD
    token_threshold_config: Literal["V3_TOKEN_CEILING"] = "V3_TOKEN_CEILING"
    token_threshold_semantics: Literal[
        "cumulative_observed_total_tokens_post_response_stop"
    ] = "cumulative_observed_total_tokens_post_response_stop"
    hard_token_ceiling: Literal[False] = False
    overshoot_bound_provable: Literal[False] = False
    output_token_cap_per_invocation: Literal[512] = FORMAL_OUTPUT_TOKEN_CAP
    provider_call_semantics: Literal["pre_call_admitted_outer_ainvoke_attempt"] = (
        "pre_call_admitted_outer_ainvoke_attempt"
    )
    provider_retry_policy: Literal[
        "sdk_retries_disabled_internal_transport_attempts_not_observable"
    ] = "sdk_retries_disabled_internal_transport_attempts_not_observable"
    credential_name: Literal["DEEPSEEK_API_KEY"] = "DEEPSEEK_API_KEY"
    credential_present: Literal[True] = True
    llm_mode: Literal["live"] = "live"
    model_name: Literal["deepseek-v4-flash"] = FORMAL_MODEL_NAME
    owner_token_threshold_semantics_accepted: Literal[True] = True
    freeze_authorized: Literal[False] = False
    locked_eval_authorized: Literal[False] = False
    release_evidence_authorized: Literal[False] = False
    architecture_conclusion_authorized: Literal[False] = False
    created_at: datetime

    @model_validator(mode="after")
    def validate_package(self) -> V3DevelopmentExecutionPackage:
        if self.evaluated_source_revision == self.manifest_source_revision:
            raise ValueError(
                "evaluated source revision must remain distinct from manifest source revision"
            )
        if not self.manifest_digests:
            raise ValueError("formal package requires committed manifest digests")
        if any(
            not name
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            for name, digest in self.manifest_digests.items()
        ):
            raise ValueError("formal package contains an invalid manifest digest")
        if dict(self.provider_calls_per_selector_turn) != {"agent": 1, "workflow": 0}:
            raise ValueError("formal package provider calls per selector turn are asymmetric")
        if dict(self.provider_call_ceiling_by_architecture) != {"agent": 256, "workflow": 0}:
            raise ValueError("formal package provider ceilings are asymmetric")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("formal package created_at must be timezone-aware")
        return self

    @property
    def package_digest(self) -> str:
        """Digest the package body, excluding the envelope digest itself."""

        return sha256_json(self.model_dump(mode="json"))


class V3DevelopmentExecutionPackageEnvelope(V3Contract):
    """Serialized package with an independent tamper-detection digest."""

    schema_version: Literal["v3.development-execution-package-envelope.v1"] = (
        EXECUTION_PACKAGE_ENVELOPE_SCHEMA_VERSION
    )
    package: V3DevelopmentExecutionPackage
    package_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_digest(self) -> V3DevelopmentExecutionPackageEnvelope:
        if self.package_digest != self.package.package_digest:
            raise ValueError("execution package digest mismatch")
        return self


class V3DevelopmentExecutionStateEvent(V3Contract):
    """One append-only runtime state event bound to the package digest."""

    schema_version: Literal["v3.development-execution-state-event.v1"] = (
        EXECUTION_STATE_EVENT_SCHEMA_VERSION
    )
    event_id: str = Field(min_length=1)
    event_type: Literal[
        "initialized", "run_recorded", "report_recorded", "measurement_completed"
    ]
    package_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_at: datetime
    payload: Mapping[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timestamp(self) -> V3DevelopmentExecutionStateEvent:
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ValueError("execution state event recorded_at must be timezone-aware")
        return self


def execution_package_path(project_root: Path, execution_identity: str) -> Path:
    """Return the only valid package path for an identity."""

    if not re.fullmatch(EXECUTION_IDENTITY_PATTERN, execution_identity):
        raise ExecutionPackageError("execution identity has an invalid format")
    return (
        project_root.expanduser().resolve()
        / "var"
        / "v3"
        / "development"
        / execution_identity
        / "authorization-package.json"
    )


def execution_state_path(project_root: Path, execution_identity: str) -> Path:
    """Return the append-only state path paired with the authorization package."""

    return execution_package_path(project_root, execution_identity).with_name(
        "execution-state.jsonl"
    )


def _serialized_envelope(envelope: V3DevelopmentExecutionPackageEnvelope) -> str:
    return json.dumps(
        envelope.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"


def load_execution_package(
    path: Path,
    *,
    project_root: Path,
    execution_identity: str,
) -> V3DevelopmentExecutionPackage:
    """Load only the identity-scoped package and verify its digest/path."""

    expected = execution_package_path(project_root, execution_identity)
    try:
        resolved = path.expanduser().resolve()
    except OSError as exc:
        raise ExecutionPackageError("execution package path cannot be resolved") from exc
    if resolved != expected or path.is_symlink():
        raise ExecutionPackageError("execution package path is outside the identity binding")
    if not path.exists():
        raise ExecutionPackageError("formal execution package is missing")
    try:
        envelope = V3DevelopmentExecutionPackageEnvelope.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise ExecutionPackageError("formal execution package is malformed or tampered") from exc
    package = envelope.package
    if package.execution_identity != execution_identity:
        raise ExecutionPackageError("execution package identity differs from requested identity")
    return package


def write_once_execution_package(
    package: V3DevelopmentExecutionPackage,
    *,
    project_root: Path,
) -> Path:
    """Create the package once; identical replay is allowed, divergence is not."""

    path = execution_package_path(project_root, package.execution_identity)
    envelope = V3DevelopmentExecutionPackageEnvelope(
        package=package,
        package_digest=package.package_digest,
    )
    payload = _serialized_envelope(envelope)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            fsync(handle.fileno())
    except FileExistsError:
        try:
            existing = V3DevelopmentExecutionPackageEnvelope.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            raise ExecutionPackageError("existing execution package is malformed") from exc
        if existing != envelope:
            raise ExecutionPackageError(
                "execution package is write-once and already differs"
            ) from None
    return path


def _validate_environment_for_package() -> bool:
    """Check only non-secret settings needed to open the authorized package."""

    return (
        os.environ.get("LLM_MODE") == "live"
        and bool(os.environ.get("DEEPSEEK_API_KEY"))
        and os.environ.get("DEEPSEEK_MODEL", FORMAL_MODEL_NAME) == FORMAL_MODEL_NAME
    )


def validate_execution_package_binding(
    package: V3DevelopmentExecutionPackage,
    *,
    plan: Any,
    manifests: Any,
    evaluated_source_revision: str,
    source_tree_clean: bool,
    production_case_inputs_digest: str,
) -> None:
    """Compare every package binding with freshly observed committed inputs."""

    errors: list[str] = []
    if not source_tree_clean:
        errors.append("source tree is not clean")
    if evaluated_source_revision != package.evaluated_source_revision:
        errors.append("evaluated source revision differs")
    if package.manifest_source_revision != plan.manifest_source_revision:
        errors.append("manifest source revision differs")
    expected_manifest_digests = {
        item.manifest_id: sha256_json(item.model_dump(mode="json")) for item in manifests
    }
    if dict(package.manifest_digests) != expected_manifest_digests:
        errors.append("manifest digest binding differs")
    if package.plan_version != plan.plan_version:
        errors.append("plan version differs")
    if package.plan_digest != sha256_json(plan.model_dump(mode="json")):
        errors.append("plan digest differs")
    if package.production_case_inputs_digest != production_case_inputs_digest:
        errors.append("production case-input digest differs")
    if (
        package.matrix_case_count != plan.matrix_case_count
        or package.planned_run_count != plan.planned_run_count
    ):
        errors.append("matrix/run count differs")
    if package.repeat != plan.repeat or package.timeout_seconds != plan.timeout_seconds:
        errors.append("repeat/timeout differs")
    if package.selector_turn_ceiling_per_run != plan.selector_turn_ceiling_per_run:
        errors.append("selector turn ceiling differs")
    if package.selector_turn_ceiling_per_case != plan.selector_turn_ceiling_per_case:
        errors.append("case selector turn ceiling differs")
    if (
        package.authorized_provider_call_ceiling != plan.authorized_provider_call_ceiling
        or package.authorized_provider_call_ceiling_per_run
        != plan.authorized_provider_call_ceiling_per_run
    ):
        errors.append("provider ceiling differs")
    if dict(package.provider_calls_per_selector_turn) != dict(
        plan.provider_calls_per_selector_turn
    ):
        errors.append("provider calls per selector turn differs")
    if dict(package.provider_call_ceiling_by_architecture) != dict(
        plan.provider_call_ceiling_by_architecture
    ):
        errors.append("provider architecture ceiling differs")
    if (
        package.provider_hard_ceiling != plan.provider_hard_ceiling
        or package.token_threshold_config != plan.token_ceiling_config
        or package.token_threshold_semantics != plan.token_threshold_semantics
        or package.hard_token_ceiling != plan.hard_token_ceiling
        or package.overshoot_bound_provable != plan.overshoot_bound_provable
    ):
        errors.append("token/provider budget semantics differ")
    if package.output_token_cap_per_invocation != plan.output_token_cap_per_invocation:
        errors.append("output token cap differs")
    if package.provider_call_semantics != plan.provider_call_semantics:
        errors.append("provider call semantics differs")
    if package.provider_retry_policy != plan.provider_retry_policy:
        errors.append("provider retry policy differs")
    if not _validate_environment_for_package():
        errors.append("live mode, model, or named credential presence is invalid")
    if errors:
        raise ExecutionPackageError("; ".join(errors))


def create_formal_execution_package(
    project_root: Path,
    *,
    execution_identity: str = FORMAL_DEVELOPMENT_EXECUTION_IDENTITY,
) -> tuple[V3DevelopmentExecutionPackage, Path]:
    """Derive and write the one Owner-authorized package without provider I/O."""

    if execution_identity != FORMAL_DEVELOPMENT_EXECUTION_IDENTITY:
        raise ExecutionPackageError("this task permits only the authorized execution identity")
    if not _validate_environment_for_package():
        raise ExecutionPackageError(
            "LLM_MODE=live, DEEPSEEK_MODEL=deepseek-v4-flash, and "
            "DEEPSEEK_API_KEY presence are required"
        )

    # Local imports keep the source-bound production runner independent from
    # this persistence module during package/model import.
    from after_sales_agent.evals.v3.matrix import load_manifests
    from after_sales_agent.evals.v3.real_runner import (
        build_development_plan,
        current_source_revision,
        load_production_case_inputs,
        production_case_inputs_digest,
        source_tree_is_clean,
    )

    project = project_root.expanduser().resolve()
    current = current_source_revision(project)
    clean = source_tree_is_clean(project)
    if not clean:
        raise ExecutionPackageError("source tree must be clean before package creation")
    plan = build_development_plan(project)
    manifests = load_manifests(project)
    inputs = load_production_case_inputs(project)
    input_digest = production_case_inputs_digest(inputs)
    path = execution_package_path(project, execution_identity)
    if path.exists():
        package = load_execution_package(
            path,
            project_root=project,
            execution_identity=execution_identity,
        )
        validate_execution_package_binding(
            package,
            plan=plan,
            manifests=manifests,
            evaluated_source_revision=current,
            source_tree_clean=clean,
            production_case_inputs_digest=input_digest,
        )
        return package, path

    if plan.formal_measurement_authorized or any(
        item.formal_measurement_authorized for item in manifests
    ):
        raise ExecutionPackageError(
            "reserved plan/manifests must remain closed; authorization belongs to this package"
        )
    if plan.token_ceiling is not None:
        raise ExecutionPackageError("reserved plan token ceiling must remain unconfigured")
    package = V3DevelopmentExecutionPackage(
        package_id=f"V3-DEV-PACKAGE-{execution_identity}",
        execution_identity=execution_identity,
        evaluated_source_revision=current,
        manifest_source_revision=plan.manifest_source_revision,
        manifest_digests=dict(plan.manifest_digests),
        plan_version=plan.plan_version,
        plan_digest=sha256_json(plan.model_dump(mode="json")),
        production_case_inputs_digest=input_digest,
        provider_calls_per_selector_turn={"agent": 1, "workflow": 0},
        provider_call_ceiling_by_architecture={"agent": 256, "workflow": 0},
        created_at=datetime.now(UTC),
    )
    validate_execution_package_binding(
        package,
        plan=plan,
        manifests=manifests,
        evaluated_source_revision=current,
        source_tree_clean=clean,
        production_case_inputs_digest=input_digest,
    )
    return package, write_once_execution_package(package, project_root=project)


class V3DevelopmentExecutionStateLedger:
    """Append-only state ledger that prevents package/run identity drift."""

    def __init__(
        self,
        path: Path,
        *,
        package: V3DevelopmentExecutionPackage,
    ) -> None:
        self.path = path.expanduser().resolve()
        self.package = package
        self.path.parent.mkdir(parents=True, exist_ok=True)
        events = self._load()
        if not events:
            self._append(
                "initialized",
                {
                    "execution_identity": package.execution_identity,
                    "evaluated_source_revision": package.evaluated_source_revision,
                    "manifest_source_revision": package.manifest_source_revision,
                },
            )
        else:
            if events[0].event_type != "initialized":
                raise ExecutionPackageError("execution state ledger must start initialized")
            if events[0].payload != {
                "execution_identity": package.execution_identity,
                "evaluated_source_revision": package.evaluated_source_revision,
                "manifest_source_revision": package.manifest_source_revision,
            }:
                raise ExecutionPackageError("execution state initialization binding differs")

    def _load(self) -> tuple[V3DevelopmentExecutionStateEvent, ...]:
        if not self.path.exists():
            return ()
        events: list[V3DevelopmentExecutionStateEvent] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = V3DevelopmentExecutionStateEvent.model_validate_json(line)
            except Exception as exc:
                raise ExecutionPackageError(
                    f"invalid execution state event at line {line_number}"
                ) from exc
            if event.package_digest != self.package.package_digest:
                raise ExecutionPackageError("execution state event package digest differs")
            events.append(event)
        return tuple(events)

    def _append(
        self,
        event_type: Literal[
            "initialized", "run_recorded", "report_recorded", "measurement_completed"
        ],
        payload: Mapping[str, Any],
    ) -> None:
        event = V3DevelopmentExecutionStateEvent(
            event_id=uuid4().hex,
            event_type=event_type,
            package_digest=self.package.package_digest,
            recorded_at=datetime.now(UTC),
            payload=dict(payload),
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")
            handle.flush()
            fsync(handle.fileno())

    def _has_payload(self, event_type: str, key: str, value: str) -> bool:
        return any(
            event.event_type == event_type and event.payload.get(key) == value
            for event in self._load()
        )

    def record_run(self, eval_run_id: str) -> None:
        if not eval_run_id:
            raise ExecutionPackageError("recorded run identity is required")
        if not self._has_payload("run_recorded", "eval_run_id", eval_run_id):
            self._append("run_recorded", {"eval_run_id": eval_run_id})

    def record_report(self, report_id: str) -> None:
        if not report_id:
            raise ExecutionPackageError("recorded report identity is required")
        if not self._has_payload("report_recorded", "report_id", report_id):
            self._append("report_recorded", {"report_id": report_id})

    def record_measurement_completed(self, report_id: str) -> None:
        if not report_id:
            raise ExecutionPackageError("completed report identity is required")
        if not self._has_payload("measurement_completed", "report_id", report_id):
            self._append("measurement_completed", {"report_id": report_id})


__all__ = [
    "EXECUTION_IDENTITY_PATTERN",
    "EXECUTION_PACKAGE_ENVELOPE_SCHEMA_VERSION",
    "EXECUTION_PACKAGE_SCHEMA_VERSION",
    "EXECUTION_STATE_EVENT_SCHEMA_VERSION",
    "FORMAL_DEVELOPMENT_EXECUTION_IDENTITY",
    "FORMAL_MODEL_NAME",
    "FORMAL_OUTPUT_TOKEN_CAP",
    "FORMAL_PROVIDER_CALL_CEILING",
    "FORMAL_PROVIDER_CALL_CEILING_PER_RUN",
    "FORMAL_REPEAT",
    "FORMAL_TIMEOUT_SECONDS",
    "FORMAL_TOKEN_THRESHOLD",
    "ExecutionPackageError",
    "V3DevelopmentExecutionPackage",
    "V3DevelopmentExecutionPackageEnvelope",
    "V3DevelopmentExecutionStateEvent",
    "V3DevelopmentExecutionStateLedger",
    "create_formal_execution_package",
    "execution_package_path",
    "execution_state_path",
    "load_execution_package",
    "validate_execution_package_binding",
    "write_once_execution_package",
]
