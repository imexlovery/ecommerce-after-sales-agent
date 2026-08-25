"""Generate, bind, and verify a sanitized revision-bound Evidence Pack."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from _evidence import committed_revision, repository_root

from after_sales_agent.config import Settings
from after_sales_agent.evals.contracts import EvalReport, EvaluationFreeze
from after_sales_agent.evals.evidence_pack import (
    EVIDENCE_PACK_FILE_NAMES,
    EVIDENCE_PACK_ROOT,
    EvidencePackError,
    build_evidence_pack,
    canonical_json,
    payload_digest,
    validate_evidence_pack_additions,
    validate_safe_payload,
)
from after_sales_agent.evals.store import EvalArtifactStore
from after_sales_agent.policy.retrieval_eval import load_retrieval_locked_report

_TRUSTED_REPORT_PATHS = {
    "framework": Path("delivery/framework-integration-report.json"),
    "test_execution": Path("delivery/test-execution-report.json"),
    "release": Path("delivery/release-evidence.json"),
}


def _root_relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise EvidencePackError("path must remain inside the repository") from exc


def _pack_directory(root: Path, value: str) -> Path:
    candidate = Path(value)
    path = candidate if candidate.is_absolute() else root / candidate
    relative = _root_relative(root, path)
    if not relative.startswith(f"{EVIDENCE_PACK_ROOT.as_posix()}/"):
        raise EvidencePackError("Evidence Pack must remain under delivery/evidence-packs")
    return path


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise EvidencePackError(f"required evidence is absent: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise EvidencePackError(f"evidence must be a JSON object: {path}")
    return payload


def _load_report(store: EvalArtifactStore, evaluation_revision: str) -> EvalReport:
    if not store.reports_dir.exists():
        raise EvidencePackError("evaluation reports are absent")
    reports = [
        EvalReport.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(store.reports_dir.glob("*.json"))
        if evaluation_revision in path.read_text(encoding="utf-8")
    ]
    reports = [report for report in reports if report.evaluation_revision == evaluation_revision]
    if len(reports) != 1:
        raise EvidencePackError(
            f"expected exactly one report for evaluation revision; found={len(reports)}"
        )
    return reports[0]


def _git_changes(root: Path, older: str, newer: str) -> list[tuple[str, tuple[str, ...]]]:
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", older, newer],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if ancestor.returncode != 0:
        raise EvidencePackError("Evidence Pack revision does not descend from evaluated source")
    completed = subprocess.run(
        ["git", "diff", "--name-status", older, newer],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    changes: list[tuple[str, tuple[str, ...]]] = []
    for line in completed.stdout.splitlines():
        fields = line.split("\t")
        if not fields:
            continue
        changes.append((fields[0], tuple(fields[1:])))
    return changes


def _require_expected_files(pack_dir: Path, *, binding_required: bool) -> None:
    names = {path.name for path in pack_dir.iterdir() if path.is_file()}
    expected = {"evidence-pack.json", "content-sha256.txt"}
    if binding_required:
        expected.add("lineage-binding.json")
    if names != expected or not names.issubset(EVIDENCE_PACK_FILE_NAMES):
        raise EvidencePackError("Evidence Pack file set is not allowlisted and complete")


def _verify_payload_files(pack_dir: Path, *, binding_present: bool = False) -> dict[str, Any]:
    _require_expected_files(pack_dir, binding_required=binding_present)
    payload = _load_json(pack_dir / "evidence-pack.json")
    validate_safe_payload(payload)
    observed_digest = (pack_dir / "content-sha256.txt").read_text(encoding="utf-8").strip()
    if observed_digest != payload_digest(payload):
        raise EvidencePackError("Evidence Pack payload digest does not match")
    return payload


def _generate(args: argparse.Namespace) -> int:
    revision = committed_revision()
    root = repository_root()
    freeze_path = root / args.freeze
    freeze_relative = _root_relative(root, freeze_path)
    freeze = EvaluationFreeze.model_validate_json(freeze_path.read_text(encoding="utf-8"))
    if freeze.evaluation_revision != args.evaluation_revision:
        raise EvidencePackError("requested revision does not match the selected freeze")
    pack_dir = _pack_directory(root, args.output)
    if pack_dir.exists():
        raise EvidencePackError("Evidence Pack output must be a new immutable directory")
    store = EvalArtifactStore(Settings().eval_artifact_root)
    report = _load_report(store, args.evaluation_revision)
    records = store.load_runs(evaluation_revision=args.evaluation_revision)
    retrieval_locked_report = None
    if freeze.is_policy_rag_acceptance:
        locked_revision = freeze.retrieval_locked_evaluation_revision
        if locked_revision is None:
            raise EvidencePackError("Policy-RAG acceptance Freeze is incomplete")
        retrieval_locked_report = load_retrieval_locked_report(
            artifact_root=Settings().policy_retrieval_eval_artifact_root,
            evaluation_revision=locked_revision,
        )
    trusted_reports = {
        name: _load_json(root / path) for name, path in _TRUSTED_REPORT_PATHS.items()
    }
    payload = build_evidence_pack(
        evaluated_source_revision=revision,
        freeze_relative_path=freeze_relative,
        freeze=freeze,
        locked_report=report,
        locked_records=records,
        trusted_reports=trusted_reports,
        retrieval_locked_report=retrieval_locked_report,
    )
    pack_dir.mkdir(parents=True)
    (pack_dir / "evidence-pack.json").write_text(canonical_json(payload), encoding="utf-8")
    (pack_dir / "content-sha256.txt").write_text(payload_digest(payload) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "pack_path": _root_relative(root, pack_dir),
                "evaluated_source_revision": revision,
                "payload_sha256": payload_digest(payload),
            }
        )
    )
    return 0


def _bind(args: argparse.Namespace) -> int:
    evidence_pack_commit = committed_revision()
    root = repository_root()
    pack_dir = _pack_directory(root, args.pack)
    payload = _verify_payload_files(pack_dir)
    evaluated_source_revision = payload.get("evaluated_source_revision")
    if evaluated_source_revision != args.evaluated_source_revision:
        raise EvidencePackError("binding source revision does not match Evidence Pack payload")
    pack_relative = _root_relative(root, pack_dir)
    changes = _git_changes(root, args.evaluated_source_revision, evidence_pack_commit)
    validate_evidence_pack_additions(changes, pack_relative_path=pack_relative)
    binding = {
        "schema_version": 1,
        "evaluated_source_revision": args.evaluated_source_revision,
        "evidence_pack_commit": evidence_pack_commit,
        "evidence_pack_path": pack_relative,
        "payload_sha256": payload_digest(payload),
    }
    validate_safe_payload(binding)
    binding_path = pack_dir / "lineage-binding.json"
    if binding_path.exists():
        raise EvidencePackError("Evidence Pack lineage binding is immutable")
    binding_path.write_text(canonical_json(binding), encoding="utf-8")
    print(json.dumps({"binding_path": _root_relative(root, binding_path), **binding}))
    return 0


def _verify(args: argparse.Namespace) -> int:
    binding_commit = committed_revision()
    root = repository_root()
    pack_dir = _pack_directory(root, args.pack)
    _require_expected_files(pack_dir, binding_required=True)
    payload = _verify_payload_files(pack_dir, binding_present=True)
    binding = _load_json(pack_dir / "lineage-binding.json")
    validate_safe_payload(binding)
    evaluated_source_revision = binding.get("evaluated_source_revision")
    evidence_pack_commit = binding.get("evidence_pack_commit")
    pack_relative = _root_relative(root, pack_dir)
    if not isinstance(evaluated_source_revision, str) or not isinstance(evidence_pack_commit, str):
        raise EvidencePackError("Evidence Pack lineage binding is incomplete")
    if payload.get("evaluated_source_revision") != evaluated_source_revision:
        raise EvidencePackError("payload and lineage binding source revisions differ")
    if binding.get("evidence_pack_path") != pack_relative:
        raise EvidencePackError("lineage binding points to a different Evidence Pack path")
    if binding.get("payload_sha256") != payload_digest(payload):
        raise EvidencePackError("lineage binding payload digest does not match")
    validate_evidence_pack_additions(
        _git_changes(root, evaluated_source_revision, evidence_pack_commit),
        pack_relative_path=pack_relative,
    )
    validate_evidence_pack_additions(
        _git_changes(root, evidence_pack_commit, binding_commit),
        pack_relative_path=pack_relative,
    )
    print(
        json.dumps(
            {
                "evaluated_source_revision": evaluated_source_revision,
                "evidence_pack_commit": evidence_pack_commit,
                "lineage_binding_commit": binding_commit,
                "pack_path": pack_relative,
                "lineage_verified": True,
            }
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("--evaluation-revision", required=True)
    generate.add_argument("--freeze", required=True)
    generate.add_argument("--output", required=True)
    bind = commands.add_parser("bind")
    bind.add_argument("--evaluated-source-revision", required=True)
    bind.add_argument("--pack", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--pack", required=True)
    args = parser.parse_args()
    if args.command == "generate":
        return _generate(args)
    if args.command == "bind":
        return _bind(args)
    return _verify(args)


if __name__ == "__main__":
    raise SystemExit(main())
