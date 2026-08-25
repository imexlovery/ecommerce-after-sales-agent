from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))

import run_operational_smoke  # noqa: E402


class _Response:
    status = 200
    headers = {"content-type": "application/json"}

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self) -> bytes:
        return b'{"status":"ready"}'


def test_first_investigation_timeout_is_bounded_by_measured_cold_start(
    monkeypatch: Any,
) -> None:
    observed: dict[str, float] = {}

    def fake_urlopen(_request: Any, *, timeout: float) -> _Response:
        observed["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(run_operational_smoke.urllib.request, "urlopen", fake_urlopen)

    status, body = run_operational_smoke._request(
        "http://127.0.0.1:1",
        "/readyz",
        timeout_seconds=run_operational_smoke.OPERATIONAL_FIRST_INVESTIGATION_TIMEOUT_SECONDS,
    )

    assert status == 200
    assert body == {"status": "ready"}
    assert observed["timeout"] == 120.0
    assert observed["timeout"] > 41.739


def test_operational_failure_tail_excludes_sensitive_diagnostics(tmp_path: Any) -> None:
    log_path = tmp_path / "api.log"
    log_path.write_text(
        "INFO: 127.0.0.1 - request completed\n"
        "Traceback (most recent call last):\n"
        "Authorization: secret-value\n"
        "WARNING: safe lifecycle warning\n",
        encoding="utf-8",
    )

    tail = run_operational_smoke._safe_log_tail(log_path)

    assert tail == (
        "INFO: 127.0.0.1 - request completed\n"
        "WARNING: safe lifecycle warning"
    )
