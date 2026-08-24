"""Run bounded real DeepSeek structured-output and native tool-call contracts."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from _evidence import Assertion, committed_revision, report_exit, write_report

from after_sales_agent.config import Settings
from after_sales_agent.evals.runner import EvaluationRunner
from after_sales_agent.evals.scenarios import load_scenarios


async def execute(report_path: Path) -> int:
    revision = committed_revision()
    settings = Settings()
    assertions: list[Assertion] = []
    if not settings.deepseek_api_key:
        assertions.append(
            Assertion(
                assertion_id="owner_key_present",
                passed=False,
                detail="owner-supplied local DeepSeek key is absent",
            )
        )
        write_report(
            report_path,
            stage="real_external",
            evidence_label="real_external",
            revision=revision,
            assertions=assertions,
            metadata={"model": settings.deepseek_model, "llm_mode": "live"},
        )
        return report_exit(assertions)

    scenarios = {scenario.scenario_id: scenario for scenario in load_scenarios()}
    runner = EvaluationRunner(
        base_settings=settings,
        evaluation_revision=f"external-contract-{revision[:12]}",
        timeout_seconds=180,
    )
    triage = await runner.run(
        scenarios["triage-dev-01"],
        layer="triage",
        architecture="triage",
        repetition=1,
        mode="live",
    )
    assertions.append(
        Assertion(
            assertion_id="live_structured_triage",
            passed=triage.quality_pass and triage.safety_gate_pass,
            detail=(
                "real DeepSeek returned the registered Triage schema"
                if triage.error_code is None
                else f"real DeepSeek triage failed with {triage.error_code}"
            ),
            duration_ms=round(triage.duration_ms, 3),
        )
    )
    investigation = await runner.run(
        scenarios["investigation-dev-signed-decline"],
        layer="investigation",
        architecture="agent",
        repetition=1,
        mode="live",
    )
    sequence = investigation.tool_trajectory.get("tool_sequence", [])
    assertions.append(
        Assertion(
            assertion_id="live_native_tool_trajectory",
            passed=(
                investigation.quality_pass
                and investigation.safety_gate_pass
                and isinstance(sequence, list)
                and len(sequence) >= 4
            ),
            detail=(
                "real DeepSeek drove the native allowlisted read-tool path"
                if investigation.error_code is None
                else f"real DeepSeek investigation failed with {investigation.error_code}"
            ),
            duration_ms=round(investigation.duration_ms, 3),
        )
    )
    assertions.append(
        Assertion(
            assertion_id="no_live_to_mock_fallback",
            passed=(
                triage.versions.get("model") == settings.deepseek_model
                and investigation.versions.get("model") == settings.deepseek_model
            ),
            detail="both records retained the configured Live model identity",
        )
    )
    write_report(
        report_path,
        stage="real_external",
        evidence_label="real_external",
        revision=revision,
        assertions=assertions,
        metadata={
            "model": settings.deepseek_model,
            "llm_mode": "live",
            "triage_token_usage": triage.token_usage,
            "investigation_token_usage": investigation.token_usage,
        },
    )
    return report_exit(assertions)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    return asyncio.run(execute(args.report))


if __name__ == "__main__":
    raise SystemExit(main())
