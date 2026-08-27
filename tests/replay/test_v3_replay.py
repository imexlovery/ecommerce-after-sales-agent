# ruff: noqa: E501
from __future__ import annotations

from after_sales_agent.evals.v3.graders import V3GradingContext, grade_repeat_question
from after_sales_agent.evals.v3.matrix import CASES_BY_ID
from after_sales_agent.evals.v3.runner import V3PairedRunner


def test_question_and_message_replay_is_idempotent_and_bounded() -> None:
    case = CASES_BY_ID["v3b-question-replay"]
    record, _ = V3PairedRunner().run_case_pair(case)
    verdict = grade_repeat_question(V3GradingContext(case=case, trace=record.trace, final_outcome=record.final_outcome, safety_gate_pass=True))
    assert verdict.passed
    assert record.metrics.repeated_questions == 1
    assert len(record.trace.consumption_ledger) == 2
