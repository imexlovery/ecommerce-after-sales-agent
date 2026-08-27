"""Version-three paired Development Eval preparation contracts and harness."""

from .contracts import (
    V3A_EVAL_DEV_IDENTITY,
    V3B_EVAL_DEV_IDENTITY,
    V3CaseSpec,
    V3DevelopmentManifest,
    V3DevelopmentReport,
    V3RunRecord,
)
from .runner import FairnessViolation, V3PairedRunner, run_prep_dry_run
from .store import V3PrepStore, V3StoreError

__all__ = [
    "V3A_EVAL_DEV_IDENTITY",
    "V3B_EVAL_DEV_IDENTITY",
    "V3CaseSpec",
    "V3DevelopmentManifest",
    "V3DevelopmentReport",
    "V3RunRecord",
    "FairnessViolation",
    "V3PairedRunner",
    "V3PrepStore",
    "V3StoreError",
    "run_prep_dry_run",
]
