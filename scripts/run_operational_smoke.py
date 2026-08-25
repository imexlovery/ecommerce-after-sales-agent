"""Verify clean install, migration, restart persistence, and Demo reset boundaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from _evidence import (
    Assertion,
    committed_revision,
    report_exit,
    repository_root,
    run_assertion,
    write_report,
)

OPERATIONAL_READINESS_TIMEOUT_SECONDS = 60.0
OPERATIONAL_HTTP_TIMEOUT_SECONDS = 15.0
# The F2 diagnostic measured 41.739s for the first Mock investigation while
# loading the real local embedding model and building its isolated index. Keep a
# finite margin for a clean machine, rather than allowing an unbounded request.
OPERATIONAL_FIRST_INVESTIGATION_TIMEOUT_SECONDS = 120.0


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout_seconds: float = OPERATIONAL_HTTP_TIMEOUT_SECONDS,
) -> tuple[int, Any]:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(  # noqa: S310
        f"{base_url}{path}",
        data=body,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            raw = response.read().decode()
            content_type = response.headers.get("content-type", "")
            return response.status, json.loads(raw) if "json" in content_type and raw else raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        return exc.code, json.loads(raw) if raw else None


def _wait_endpoint(
    base_url: str,
    path: str,
    process: subprocess.Popen[str],
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("API exited before readiness")
        try:
            status, _ = _request(base_url, path, timeout_seconds=2.0)
            if status == 200:
                return
        except Exception:
            time.sleep(0.2)
    raise TimeoutError(f"API {path} timeout")


def _stop(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_log_tail(path: Path) -> str | None:
    """Keep only bounded, non-sensitive server lifecycle lines in evidence."""

    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    safe_lines: list[str] = []
    forbidden_markers = (
        "api_key",
        "authorization",
        "bearer ",
        "traceback",
        "system prompt",
        "developer prompt",
        "provider payload",
    )
    allowed_prefixes = ("INFO:", "WARNING:", "ERROR:", "Loading weights:")
    for raw_line in raw_lines:
        line = " ".join(raw_line.split())
        lowered = line.casefold()
        if not line or any(marker in lowered for marker in forbidden_markers):
            continue
        if line.startswith(allowed_prefixes):
            safe_lines.append(line[:500])
    return "\n".join(safe_lines)[-4_000:] or None


def _policy_search_metrics(database_path: Path) -> dict[str, float] | None:
    """Read only numeric Policy RAG timings from the persisted tool envelope."""

    try:
        with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                """
                SELECT result_envelope
                FROM tool_calls
                WHERE tool_name = 'search_after_sales_policy'
                  AND actual_execution = 1
                ORDER BY requested_at DESC
                LIMIT 1
                """
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None or row[0] is None:
        return None
    try:
        envelope = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        payload = envelope["payload"]
        return {
            "retrieval_latency_ms": round(float(payload["retrieval_latency_ms"]), 3),
            "resolver_latency_ms": round(float(payload["resolver_latency_ms"]), 3),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    revision = committed_revision()
    source_root = repository_root()
    assertions: list[Assertion] = []
    stage_timings: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="after-sales-clean-start-") as temp_value:
        temp = Path(temp_value)
        archive = temp / "source.tar"
        checkout = temp / "checkout"
        checkout.mkdir()
        subprocess.run(
            ["git", "archive", "--format=tar", "HEAD", "-o", str(archive)],
            cwd=source_root,
            check=True,
        )
        with tarfile.open(archive) as bundle:
            bundle.extractall(checkout, filter="data")

        environment = os.environ.copy()
        environment.pop("VIRTUAL_ENV", None)
        database_path = temp / "business.db"
        checkpoint_path = temp / "checkpoints.db"
        eval_root = temp / "evals"
        eval_root.mkdir()
        sentinel = eval_root / "reset-must-preserve.json"
        sentinel.write_text('{"preserve":true}\n', encoding="utf-8")
        environment.update(
            {
                "LLM_MODE": "mock",
                "DATABASE_URL": f"sqlite:///{database_path}",
                "LANGGRAPH_CHECKPOINT_URL": str(checkpoint_path),
                "EVAL_ARTIFACT_ROOT": str(eval_root),
                "POLICY_INDEX_ROOT": str(temp / "policy-index"),
                "POLICY_RETRIEVAL_EVAL_ARTIFACT_ROOT": str(temp / "retrieval-evals"),
                "POLICY_RETRIEVAL_MODE": "real_local",
                "FRONTEND_ORIGIN": "http://127.0.0.1:5173",
                "MOCK_DEMO_STEP_DELAY_MS": "0",
            }
        )
        assertions.extend(
            [
                run_assertion(
                    "clean_python_sync",
                    ["uv", "sync", "--locked", "--python", "3.12"],
                    cwd=checkout,
                    environment=environment,
                    detail="empty archived checkout resolved exactly from uv.lock",
                    timeout_seconds=300,
                ),
                run_assertion(
                    "clean_frontend_install",
                    ["npm", "ci"],
                    cwd=checkout / "frontend",
                    environment=environment,
                    detail="empty archived frontend installed exactly from package-lock.json",
                    timeout_seconds=300,
                ),
                run_assertion(
                    "clean_migration",
                    ["uv", "run", "alembic", "upgrade", "head"],
                    cwd=checkout,
                    environment=environment,
                    detail="fresh business database migrated to Alembic head",
                    timeout_seconds=120,
                ),
            ]
        )
        stage_timings.extend(
            {
                "stage": assertion.assertion_id,
                "status": "passed" if assertion.passed else "failed",
                "duration_ms": assertion.duration_ms,
            }
            for assertion in assertions
        )
        if not all(assertion.passed for assertion in assertions):
            write_report(
                args.report,
                stage="operational",
                evidence_label="operational",
                revision=revision,
                assertions=assertions,
                metadata={
                    "clean_archive": True,
                    "llm_mode": "mock",
                    "policy_retrieval_mode": "real_local",
                    "virtual_env_sanitized": True,
                    "stage_timings": stage_timings,
                },
            )
            return report_exit(assertions)

        api_port = _free_port()
        base_url = f"http://127.0.0.1:{api_port}"
        environment["API_PORT"] = str(api_port)
        process: subprocess.Popen[str] | None = None
        log_path = temp / "api.log"
        log_handles: list[Any] = []
        current_stage = "api_process_spawn"

        def timed_stage(stage: str, operation: Callable[[], Any]) -> Any:
            nonlocal current_stage
            current_stage = stage
            started = time.perf_counter()
            try:
                result = operation()
            except Exception:
                stage_timings.append(
                    {
                        "stage": stage,
                        "status": "failed",
                        "duration_ms": round((time.perf_counter() - started) * 1_000, 3),
                    }
                )
                raise
            stage_timings.append(
                {
                    "stage": stage,
                    "status": "passed",
                    "duration_ms": round((time.perf_counter() - started) * 1_000, 3),
                }
            )
            return result

        def start() -> subprocess.Popen[str]:
            log = log_path.open("a", encoding="utf-8")
            log_handles.append(log)
            created = subprocess.Popen(
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
                cwd=checkout,
                env=environment,
                text=True,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            return created

        safe_failure_log_tail: str | None = None
        try:
            process = timed_stage("api_process_spawn", start)
            timed_stage(
                "healthz",
                lambda: _wait_endpoint(
                    base_url,
                    "/healthz",
                    process,
                    timeout_seconds=OPERATIONAL_READINESS_TIMEOUT_SECONDS,
                ),
            )
            timed_stage(
                "readyz",
                lambda: _wait_endpoint(
                    base_url,
                    "/readyz",
                    process,
                    timeout_seconds=OPERATIONAL_READINESS_TIMEOUT_SECONDS,
                ),
            )

            def create_conversation() -> dict[str, Any]:
                status, created = _request(
                    base_url,
                    "/v1/conversations",
                    method="POST",
                    payload={"fixture_customer_key": "customer_a"},
                )
                if status != 201 or not isinstance(created, dict):
                    raise RuntimeError("conversation creation failed")
                return created

            created = timed_stage("conversation_creation", create_conversation)
            conversation_id = str(created["conversation_id"])

            def submit_investigation() -> dict[str, Any]:
                status, submitted = _request(
                    base_url,
                    f"/v1/conversations/{conversation_id}/messages",
                    method="POST",
                    payload={"content": "ORD-001 显示签收，但我没有收到。"},
                    timeout_seconds=OPERATIONAL_FIRST_INVESTIGATION_TIMEOUT_SECONDS,
                )
                if status != 202 or not isinstance(submitted, dict):
                    raise RuntimeError("investigation submission failed")
                return submitted

            submitted = timed_stage("first_investigation_request", submit_investigation)
            case_id = str(submitted["case_id"])
            policy_metrics = _policy_search_metrics(database_path)
            assertions.append(
                Assertion(
                    assertion_id="policy_embedding_model_index_cold_start_observed",
                    passed=policy_metrics is not None,
                    detail=(
                        "persisted real-local Policy RAG retrieval timing observed"
                        if policy_metrics is not None
                        else "persisted Policy RAG retrieval timing was not available"
                    ),
                )
            )
            if policy_metrics is not None:
                stage_timings.append(
                    {
                        "stage": "policy_embedding_model_index_cold_start",
                        "status": "observed",
                        "retrieval_latency_ms": policy_metrics["retrieval_latency_ms"],
                        "resolver_latency_ms": policy_metrics["resolver_latency_ms"],
                    }
                )

            def read_proposal() -> dict[str, Any]:
                status, case = _request(
                    base_url,
                    f"/v1/investigation-cases/{case_id}",
                )
                if (
                    status != 200
                    or not isinstance(case, dict)
                    or not case.get("active_proposal_id")
                ):
                    raise RuntimeError("proposal was not persisted")
                return case

            case = timed_stage("proposal_persistence", read_proposal)
            proposal_id = str(case["active_proposal_id"])

            def confirm_proposal() -> None:
                status, _ = _request(
                    base_url,
                    f"/v1/action-proposals/{proposal_id}/confirm",
                    method="POST",
                    payload={"proposal_version": 1},
                )
                if status != 202:
                    raise RuntimeError("exact proposal confirmation failed")

            timed_stage("exact_confirmation", confirm_proposal)

            def read_closed_case() -> None:
                status, closed = _request(base_url, f"/v1/investigation-cases/{case_id}")
                if (
                    status != 200
                    or not isinstance(closed, dict)
                    or closed.get("case_state") != "closed"
                    or closed.get("case_outcome") != "ticket_created"
                ):
                    raise RuntimeError("ticket read-back failed")

            timed_stage("ticket_read_back", read_closed_case)
            timed_stage("process_stop_for_restart", lambda: _stop(process))
            process = timed_stage("process_restart_spawn", start)
            timed_stage(
                "restart_healthz",
                lambda: _wait_endpoint(
                    base_url,
                    "/healthz",
                    process,
                    timeout_seconds=OPERATIONAL_READINESS_TIMEOUT_SECONDS,
                ),
            )
            timed_stage(
                "restart_readyz",
                lambda: _wait_endpoint(
                    base_url,
                    "/readyz",
                    process,
                    timeout_seconds=OPERATIONAL_READINESS_TIMEOUT_SECONDS,
                ),
            )

            def verify_restart_replay() -> None:
                status, restored = _request(
                    base_url,
                    f"/v1/investigation-cases/{case_id}",
                )
                events_status, events = _request(
                    base_url,
                    f"/v1/conversations/{conversation_id}/events?follow=false",
                )
                if (
                    status != 200
                    or not isinstance(restored, dict)
                    or restored.get("case_outcome") != "ticket_created"
                    or events_status != 200
                    or not isinstance(events, str)
                    or "event: action_verified" not in events
                ):
                    raise RuntimeError("restart persistence or SSE replay failed")

            timed_stage("sse_recovery", verify_restart_replay)

            protected_paths = [checkout / ".env.example"]
            protected_paths.extend(
                sorted((checkout / "evals" / "config" / "freezes").glob("*.json"))
            )
            protected_before = {str(path): _digest(path) for path in protected_paths}
            sentinel_before = _digest(sentinel)

            def reset_boundary() -> None:
                reset_status, _ = _request(base_url, "/v1/demo/reset", method="POST")
                missing_status, _ = _request(base_url, f"/v1/conversations/{conversation_id}")
                protected_after = {str(path): _digest(path) for path in protected_paths}
                if not (
                    reset_status == 204
                    and missing_status == 404
                    and sentinel.exists()
                    and _digest(sentinel) == sentinel_before
                    and protected_before == protected_after
                ):
                    raise RuntimeError("Demo reset boundary failed")

            timed_stage("reset_boundary", reset_boundary)
        except Exception as exc:
            safe_failure_log_tail = _safe_log_tail(log_path)
            assertions.append(
                Assertion(
                    assertion_id="operational_runtime_journey",
                    passed=False,
                    detail=(
                        f"operational stage {current_stage} failed with {type(exc).__name__}"
                    ),
                    safe_output_tail=safe_failure_log_tail,
                )
            )
        finally:
            _stop(process)
            for handle in log_handles:
                handle.close()
    write_report(
        args.report,
        stage="operational",
        evidence_label="operational",
        revision=revision,
        assertions=assertions,
        metadata={
            "clean_archive": True,
            "llm_mode": "mock",
            "policy_retrieval_mode": "real_local",
            "virtual_env_sanitized": True,
            "first_investigation_timeout_seconds": OPERATIONAL_FIRST_INVESTIGATION_TIMEOUT_SECONDS,
            "stage_timings": stage_timings,
        },
    )
    return report_exit(assertions)


if __name__ == "__main__":
    raise SystemExit(main())
