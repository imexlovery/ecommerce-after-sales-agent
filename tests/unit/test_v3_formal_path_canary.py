from pathlib import Path

import pytest

from after_sales_agent.evals.v3.formal_path_canary import (
    CANARY_HISTORICAL_IDENTITY,
    CANARY_IDENTITIES,
    CANARY_IDENTITY,
    FormalPathCanaryError,
    canary_root,
)


def test_formal_canary_uses_new_identity_and_preserves_historical_allowlist(
    tmp_path: Path,
) -> None:
    assert CANARY_IDENTITY == "V3-DEV-EXEC-CANARY-20260829-02"
    assert CANARY_HISTORICAL_IDENTITY == "V3-DEV-EXEC-CANARY-20260829-01"
    assert CANARY_IDENTITIES == {
        "V3-DEV-EXEC-CANARY-20260829-01",
        "V3-DEV-EXEC-CANARY-20260829-02",
    }
    assert canary_root(tmp_path, CANARY_HISTORICAL_IDENTITY).name == (
        "V3-DEV-EXEC-CANARY-20260829-01"
    )
    assert canary_root(tmp_path).name == CANARY_IDENTITY


def test_formal_canary_rejects_unbounded_identity_paths(tmp_path: Path) -> None:
    with pytest.raises(FormalPathCanaryError, match="not allowlisted"):
        canary_root(tmp_path, "V3-DEV-EXEC-CANARY-20260829-03")
