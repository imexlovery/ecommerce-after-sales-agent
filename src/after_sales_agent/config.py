"""Validated application settings.

The runtime mode is intentionally explicit. A Live process with no key is invalid;
there is no provider fallback hidden in this module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMMode(StrEnum):
    MOCK = "mock"
    LIVE = "live"


class PolicyRetrievalMode(StrEnum):
    """The embedding path is explicit and never silently falls back."""

    REAL_LOCAL = "real_local"
    FAKE_TEST = "fake_test"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    llm_mode: LLMMode = Field(default=LLMMode.MOCK, alias="LLM_MODE")
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_model: str = Field(default="deepseek-v4-flash", alias="DEEPSEEK_MODEL")
    deepseek_api_base: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_API_BASE")
    deepseek_timeout_seconds: float = Field(
        default=30.0, ge=1.0, le=120.0, alias="DEEPSEEK_TIMEOUT_SECONDS"
    )
    mock_demo_step_delay_ms: int = Field(
        default=0,
        ge=0,
        le=1_000,
        alias="MOCK_DEMO_STEP_DELAY_MS",
    )
    synthetic_fault_profile: Literal["none", "pod_timeout_once", "policy_unavailable"] = Field(
        default="none",
        alias="SYNTHETIC_FAULT_PROFILE",
    )
    scenario_fault_seed: str = Field(
        default="demo-default",
        min_length=1,
        alias="SCENARIO_FAULT_SEED",
    )

    policy_retrieval_mode: PolicyRetrievalMode = Field(
        default=PolicyRetrievalMode.REAL_LOCAL,
        alias="POLICY_RETRIEVAL_MODE",
    )
    policy_embedding_model: str = Field(
        default="BAAI/bge-small-zh-v1.5",
        alias="POLICY_EMBEDDING_MODEL",
    )
    policy_embedding_revision: str = Field(
        default="7999e1d3359715c523056ef9478215996d62a620",
        alias="POLICY_EMBEDDING_REVISION",
    )
    policy_index_root: Path = Field(
        default=Path("./var/policy-rag-index"),
        alias="POLICY_INDEX_ROOT",
    )
    policy_retrieval_eval_artifact_root: Path = Field(
        default=Path("./var/retrieval-evals"),
        alias="POLICY_RETRIEVAL_EVAL_ARTIFACT_ROOT",
    )
    policy_retrieval_top_k: int = Field(default=3, ge=1, le=3, alias="POLICY_RETRIEVAL_TOP_K")
    policy_retrieval_min_similarity: float = Field(
        default=0.50,
        ge=-1.0,
        le=1.0,
        alias="POLICY_RETRIEVAL_MIN_SIMILARITY",
    )

    database_url: str = Field(default="sqlite:///./var/after-sales.db", alias="DATABASE_URL")
    langgraph_checkpoint_url: Path = Field(
        default=Path("./var/langgraph-checkpoints.db"), alias="LANGGRAPH_CHECKPOINT_URL"
    )
    eval_artifact_root: Path = Field(default=Path("./var/evals"), alias="EVAL_ARTIFACT_ROOT")
    fixture_version: str = Field(default="fixture-v1", alias="FIXTURE_VERSION")
    scenario_evaluated_at: datetime = Field(
        default=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
        alias="SCENARIO_EVALUATED_AT",
    )

    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=8000, ge=1024, le=65535, alias="API_PORT")
    frontend_origin: str = Field(default="http://127.0.0.1:5173", alias="FRONTEND_ORIGIN")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    max_message_chars: int = 2_000
    max_entry_clarifications: int = 1
    max_business_clarifications: int = 2
    max_run_planning_turns: int = 8
    max_case_planning_turns: int = 16
    max_case_read_executions: int = 6
    proposal_ttl_minutes: int = 15

    @model_validator(mode="after")
    def validate_live_configuration(self) -> Settings:
        if self.llm_mode is LLMMode.LIVE and not self.deepseek_api_key:
            raise ValueError(
                "LLM_MODE=live requires DEEPSEEK_API_KEY; Live never falls back to Mock"
            )
        if self.llm_mode is LLMMode.LIVE and self.synthetic_fault_profile != "none":
            raise ValueError("SYNTHETIC_FAULT_PROFILE is available only in explicit Mock mode")
        if self.deepseek_model in {"deepseek-chat", "deepseek-reasoner"}:
            raise ValueError("legacy DeepSeek model aliases are not supported by this project")
        if self.policy_embedding_model != "BAAI/bge-small-zh-v1.5":
            raise ValueError("Phase 2-A pins POLICY_EMBEDDING_MODEL to BAAI/bge-small-zh-v1.5")
        if self.policy_embedding_revision != "7999e1d3359715c523056ef9478215996d62a620":
            raise ValueError("Phase 2-A requires the pinned BGE model revision")
        if (
            self.scenario_evaluated_at.tzinfo is None
            or self.scenario_evaluated_at.utcoffset() is None
        ):
            raise ValueError("SCENARIO_EVALUATED_AT must include a timezone")
        return self

    def ensure_local_directories(self) -> None:
        if self.database_url.startswith("sqlite:///./"):
            Path(self.database_url.removeprefix("sqlite:///./")).parent.mkdir(
                parents=True, exist_ok=True
            )
        self.langgraph_checkpoint_url.parent.mkdir(parents=True, exist_ok=True)
        self.eval_artifact_root.mkdir(parents=True, exist_ok=True)
        self.policy_index_root.mkdir(parents=True, exist_ok=True)
        self.policy_retrieval_eval_artifact_root.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings()
