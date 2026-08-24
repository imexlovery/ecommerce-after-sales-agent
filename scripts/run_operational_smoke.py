"""Verify clean install, migration, restart persistence, and Demo reset boundaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
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
) -> tuple[int, Any]:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(  # noqa: S310
        f"{base_url}{path}",
        data=body,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            raw = response.read().decode()
            content_type = response.headers.get("content-type", "")
            return response.status, json.loads(raw) if "json" in content_type and raw else raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        return exc.code, json.loads(raw) if raw else None


def _wait_ready(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("API exited before readiness")
        try:
            status, _ = _request(base_url, "/readyz")
            if status == 200:
                return
        except Exception:
            time.sleep(0.2)
    raise TimeoutError("API readiness timeout")


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    revision = committed_revision()
    source_root = repository_root()
    assertions: list[Assertion] = []
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
        if not all(assertion.passed for assertion in assertions):
            write_report(
                args.report,
                stage="operational",
                evidence_label="operational",
                revision=revision,
                assertions=assertions,
            )
            return report_exit(assertions)

        api_port = _free_port()
        base_url = f"http://127.0.0.1:{api_port}"
        environment["API_PORT"] = str(api_port)
        process: subprocess.Popen[str] | None = None
        log_path = temp / "api.log"

        def start() -> subprocess.Popen[str]:
            log = log_path.open("a", encoding="utf-8")
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
            _wait_ready(base_url, created)
            return created

        try:
            process = start()
            status, created = _request(
                base_url,
                "/v1/conversations",
                method="POST",
                payload={"fixture_customer_key": "customer_a"},
            )
            if status != 201:
                raise RuntimeError("conversation creation failed")
            conversation_id = str(created["conversation_id"])
            status, submitted = _request(
                base_url,
                f"/v1/conversations/{conversation_id}/messages",
                method="POST",
                payload={"content": "ORD-001 显示签收，但我没有收到。"},
            )
            if status != 202:
                raise RuntimeError("investigation submission failed")
            case_id = str(submitted["case_id"])
            status, case = _request(base_url, f"/v1/investigation-cases/{case_id}")
            if status != 200 or not case.get("active_proposal_id"):
                raise RuntimeError("proposal was not persisted")
            status, _ = _request(
                base_url,
                f"/v1/action-proposals/{case['active_proposal_id']}/confirm",
                method="POST",
                payload={"proposal_version": 1},
            )
            if status != 202:
                raise RuntimeError("exact proposal confirmation failed")
            status, closed = _request(base_url, f"/v1/investigation-cases/{case_id}")
            before_restart_ok = (
                status == 200
                and closed.get("case_state") == "closed"
                and closed.get("case_outcome") == "ticket_created"
            )
            _stop(process)
            process = start()
            status, restored = _request(base_url, f"/v1/investigation-cases/{case_id}")
            events_status, events = _request(
                base_url,
                f"/v1/conversations/{conversation_id}/events?follow=false",
            )
            restart_ok = (
                before_restart_ok
                and status == 200
                and restored.get("case_outcome") == "ticket_created"
                and events_status == 200
                and isinstance(events, str)
                and "event: action_verified" in events
            )
            assertions.append(
                Assertion(
                    assertion_id="restart_persistence_and_replay",
                    passed=restart_ok,
                    detail=(
                        "verified ticket Case and persisted event stream survived process restart"
                    ),
                )
            )

            protected_paths = [checkout / ".env.example"]
            freeze_path = checkout / "evals" / "config" / "acceptance-freeze.json"
            if freeze_path.exists():
                protected_paths.append(freeze_path)
            protected_before = {str(path): _digest(path) for path in protected_paths}
            sentinel_before = _digest(sentinel)
            reset_status, _ = _request(base_url, "/v1/demo/reset", method="POST")
            missing_status, _ = _request(base_url, f"/v1/conversations/{conversation_id}")
            protected_after = {str(path): _digest(path) for path in protected_paths}
            reset_ok = (
                reset_status == 204
                and missing_status == 404
                and sentinel.exists()
                and _digest(sentinel) == sentinel_before
                and protected_before == protected_after
            )
            assertions.append(
                Assertion(
                    assertion_id="demo_reset_boundary",
                    passed=reset_ok,
                    detail=(
                        "Demo reset removed runtime state and preserved config and Eval assets"
                    ),
                )
            )
        except Exception as exc:
            assertions.append(
                Assertion(
                    assertion_id="operational_runtime_journey",
                    passed=False,
                    detail=f"operational journey failed with {type(exc).__name__}",
                )
            )
        finally:
            _stop(process)
    write_report(
        args.report,
        stage="operational",
        evidence_label="operational",
        revision=revision,
        assertions=assertions,
        metadata={"clean_archive": True, "llm_mode": "mock"},
    )
    return report_exit(assertions)


if __name__ == "__main__":
    raise SystemExit(main())
