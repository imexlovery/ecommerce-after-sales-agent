"""Run the registered journey in Chromium against the local API and Vite."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from _evidence import Assertion, committed_revision, report_exit, repository_root, write_report


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_for(url: str, process: subprocess.Popen[str], timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("local surface process exited before readiness")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:  # noqa: S310
                if response.status < 500:
                    return
        except Exception:
            time.sleep(0.2)
    raise TimeoutError("local surface did not become ready within the registered timeout")


def _stop(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--mode", choices=("mock", "live"), default="mock")
    parser.add_argument(
        "--fault-profile",
        choices=("none", "policy_unavailable"),
        default="none",
        help="Explicit Mock-only failure path for the second browser checkpoint.",
    )
    args = parser.parse_args()
    revision = committed_revision()
    root = repository_root()
    frontend = root / "frontend"
    assertions: list[Assertion] = []
    backend: subprocess.Popen[str] | None = None
    vite: subprocess.Popen[str] | None = None
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="after-sales-surface-") as temp_value:
        temp = Path(temp_value)
        api_port = _free_port()
        frontend_port = _free_port()
        frontend_url = f"http://127.0.0.1:{frontend_port}"
        api_url = f"http://127.0.0.1:{api_port}"
        environment = os.environ.copy()
        environment.update(
            {
                "LLM_MODE": args.mode,
                "SCENARIO_EVALUATED_AT": "2026-08-29T08:00:00Z",
                "DATABASE_URL": f"sqlite:///{temp / 'business.db'}",
                "LANGGRAPH_CHECKPOINT_URL": str(temp / "checkpoints.db"),
                "EVAL_ARTIFACT_ROOT": str(temp / "evals"),
                "POLICY_INDEX_ROOT": str(temp / "policy-rag-index"),
                "POLICY_RETRIEVAL_EVAL_ARTIFACT_ROOT": str(temp / "retrieval-evals"),
                "POLICY_RETRIEVAL_MODE": "real_local",
                "SYNTHETIC_FAULT_PROFILE": args.fault_profile,
                "SURFACE_EXPECT_POLICY_UNAVAILABLE": (
                    "1" if args.fault_profile == "policy_unavailable" else "0"
                ),
                "FRONTEND_ORIGIN": frontend_url,
                "API_HOST": "127.0.0.1",
                "API_PORT": str(api_port),
                "VITE_API_BASE_URL": api_url,
                "SURFACE_BASE_URL": frontend_url,
                "EXPECTED_LLM_MODE": args.mode,
                "SURFACE_E2E_TIMEOUT_MS": "240000" if args.mode == "live" else "90000",
            }
        )
        try:
            with (
                (temp / "backend.log").open("w", encoding="utf-8") as backend_log,
                (temp / "frontend.log").open("w", encoding="utf-8") as frontend_log,
            ):
                backend = subprocess.Popen(
                    [
                        "uv",
                        "run",
                        "uvicorn",
                        "after_sales_agent.api.app:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(api_port),
                    ],
                    cwd=root,
                    env=environment,
                    text=True,
                    stdout=backend_log,
                    stderr=subprocess.STDOUT,
                )
                vite = subprocess.Popen(
                    [
                        "npm",
                        "run",
                        "dev",
                        "--",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(frontend_port),
                    ],
                    cwd=frontend,
                    env=environment,
                    text=True,
                    stdout=frontend_log,
                    stderr=subprocess.STDOUT,
                )
                _wait_for(f"{api_url}/readyz", backend, 30)
                _wait_for(frontend_url, vite, 30)
                completed = subprocess.run(
                    ["npm", "run", "e2e:surface"],
                    cwd=frontend,
                    env=environment,
                    text=True,
                    capture_output=True,
                    timeout=300,
                    check=False,
                )
                safe_output = "\n".join(
                    part for part in (completed.stdout, completed.stderr) if part
                )[-4_000:]
                assertions.append(
                    Assertion(
                        assertion_id="chromium_customer_surface",
                        passed=completed.returncode == 0,
                        detail=(
                            "Chromium completed customer confirmation, read-back, "
                            "refresh, and Dashboard scroll checks"
                            if args.fault_profile == "none"
                            else (
                                "Chromium completed the policy-unavailable fail-closed path, "
                                "verified no Proposal, and refreshed without re-execution"
                            )
                        ),
                        command=["npm", "run", "e2e:surface"],
                        exit_code=completed.returncode,
                        duration_ms=round((time.perf_counter() - started) * 1_000, 3),
                        safe_output_tail=safe_output or None,
                    )
                )
        except Exception as exc:
            assertions.append(
                Assertion(
                    assertion_id="chromium_customer_surface",
                    passed=False,
                    detail=f"surface harness failed with {type(exc).__name__}",
                    duration_ms=round((time.perf_counter() - started) * 1_000, 3),
                )
            )
        finally:
            _stop(vite)
            _stop(backend)
    write_report(
        args.report,
        stage="surface_e2e",
        evidence_label=(
            "mock_llm + real_local_retrieval + surface_e2e"
            if args.mode == "mock"
            else "live_browser"
        ),
        revision=revision,
        assertions=assertions,
        metadata={
            "browser": "Chromium",
            "llm_mode": args.mode,
            "policy_retrieval_mode": "real_local",
            "synthetic_fault_profile": args.fault_profile,
        },
    )
    return report_exit(assertions)


if __name__ == "__main__":
    raise SystemExit(main())
