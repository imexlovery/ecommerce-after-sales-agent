"""Generate the three protected delivery reports from trusted evidence only."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from _evidence import committed_revision, repository_root

from after_sales_agent.config import Settings
from after_sales_agent.evals.contracts import EvaluationFreeze
from after_sales_agent.evals.evidence_pack import (
    locked_evaluation_release_gate,
    retrieval_locked_release_gates,
)
from after_sales_agent.evals.store import EvalArtifactStore
from after_sales_agent.policy.retrieval_eval import load_retrieval_locked_report

EVIDENCE_PATHS = {
    "unit": Path("delivery/evidence/unit/assertions.json"),
    "component": Path("delivery/evidence/component/assertions.json"),
    "framework_integration": Path(
        "delivery/evidence/framework_integration/assertions.json"
    ),
    "surface_e2e": Path("delivery/evidence/surface_e2e/assertions.json"),
    "real_external": Path("delivery/evidence/real_external/assertions.json"),
    "operational": Path("delivery/evidence/operational/assertions.json"),
    "release_checks": Path("delivery/evidence/release_checks/assertions.json"),
    "provenance_domain": Path("delivery/evidence/provenance/domain-workflow.json"),
    "provenance_tools": Path("delivery/evidence/provenance/tools-mcp.json"),
    "provenance_models": Path(
        "delivery/evidence/provenance/prompt-inference-models.json"
    ),
}


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"required trusted evidence is absent: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"trusted evidence must be a JSON object: {path}")
    return payload


def _write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _retrieval_locked_gates(
    *,
    root: Path,
    settings: Settings,
    report: Any,
    revision: str,
) -> dict[str, bool]:
    """Load only a matching V3 Freeze/report pair; absence is a failed gate."""

    if report is None:
        return {"quality": False, "safety": False, "exact_revision": False}
    freeze_path = root / "evals" / "config" / "freezes" / f"{report.evaluation_revision}.json"
    if not freeze_path.exists():
        return {"quality": False, "safety": False, "exact_revision": False}
    try:
        freeze = EvaluationFreeze.model_validate_json(freeze_path.read_text(encoding="utf-8"))
        locked_revision = freeze.retrieval_locked_evaluation_revision
        if locked_revision is None:
            return {"quality": False, "safety": False, "exact_revision": False}
        retrieval_report = load_retrieval_locked_report(
            artifact_root=settings.policy_retrieval_eval_artifact_root,
            evaluation_revision=locked_revision,
        )
    except (OSError, ValueError):
        return {"quality": False, "safety": False, "exact_revision": False}
    return retrieval_locked_release_gates(
        freeze=freeze,
        report=retrieval_report,
        evaluated_source_revision=revision,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-failed", action="store_true")
    args = parser.parse_args()
    revision = committed_revision()
    root = repository_root()
    evidence = {name: _load(root / path) for name, path in EVIDENCE_PATHS.items()}
    bound_to_revision = all(
        item.get("source_revision") == revision for item in evidence.values()
    )
    evidence_pass = all(item.get("passed") is True for item in evidence.values())
    surface_live = (
        evidence["surface_e2e"].get("evidence_label") == "live_browser"
        and evidence["surface_e2e"].get("metadata", {}).get("llm_mode") == "live"
    )
    real_external = evidence["real_external"].get("evidence_label") == "real_external"

    settings = Settings()
    report = EvalArtifactStore(settings.eval_artifact_root).load_latest_report()
    retrieval_gates = _retrieval_locked_gates(
        root=root,
        settings=settings,
        report=report,
        revision=revision,
    )
    locked_eval_pass = locked_evaluation_release_gate(
        report=report,
        evaluated_source_revision=revision,
        retrieval_gates=retrieval_gates,
    )
    generated_at = datetime.now(UTC).isoformat()
    framework_names = (
        "framework_integration",
        "provenance_domain",
        "provenance_tools",
        "provenance_models",
    )
    framework_pass = bound_to_revision and all(
        evidence[name].get("passed") is True for name in framework_names
    )
    framework_report = {
        "schema_version": 1,
        "generated_at": generated_at,
        "source_revision": revision,
        "strategy": "OTHER_FRAMEWORK",
        "passed": framework_pass,
        "evidence": {name: evidence[name] for name in framework_names},
    }
    test_report = {
        "schema_version": 1,
        "generated_at": generated_at,
        "source_revision": revision,
        "passed": bound_to_revision and evidence_pass and locked_eval_pass,
        "stages": evidence,
        "locked_evaluation": (
            report.model_dump(mode="json") if report is not None else {"status": "absent"}
        ),
    }
    release_pass = bool(
        framework_pass
        and test_report["passed"]
        and surface_live
        and real_external
        and locked_eval_pass
    )
    release_report = {
        "schema_version": 1,
        "generated_at": generated_at,
        "source_revision": revision,
        "release_candidate_verified": release_pass,
        "gates": {
            "clean_committed_revision": bound_to_revision,
            "framework_integration": framework_pass,
            "registered_tests": evidence_pass,
            "locked_evaluation": locked_eval_pass,
            "retrieval_locked_quality": retrieval_gates["quality"],
            "retrieval_locked_safety": retrieval_gates["safety"],
            "retrieval_locked_exact_revision": retrieval_gates["exact_revision"],
            "live_provider": real_external and evidence["real_external"].get("passed") is True,
            "live_browser": surface_live and evidence["surface_e2e"].get("passed") is True,
            "operational_clean_start": evidence["operational"].get("passed") is True,
        },
        "architecture_conclusion": (
            report.architecture_conclusion if report is not None else None
        ),
        "evaluation_revision": report.evaluation_revision if report is not None else None,
    }
    if not release_pass and not args.allow_failed:
        raise RuntimeError("release reports refuse to publish because one or more gates failed")
    _write(root / "delivery/framework-integration-report.json", framework_report)
    _write(root / "delivery/test-execution-report.json", test_report)
    _write(root / "delivery/release-evidence.json", release_report)
    print(
        json.dumps(
            {
                "release_candidate_verified": release_pass,
                "source_revision": revision,
                "evaluation_revision": release_report["evaluation_revision"],
            }
        )
    )
    return 0 if release_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
