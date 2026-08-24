"""Case and Run counters for governed read-tool use."""

from __future__ import annotations

from dataclasses import dataclass


class ToolBudgetExceeded(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ToolBudgetSnapshot:
    case_planning_turns: int
    run_planning_turns: int
    actual_read_tool_executions: int


class ToolBudget:
    """Mutable counters scoped to one Case and its active Run.

    The LangGraph ``on_agent_turn`` callback records planning turns. The
    governed executor records only actual source calls, so denied calls and
    cache hits cannot consume the six-execution allowance.
    """

    max_case_planning_turns = 16
    max_run_planning_turns = 8
    max_actual_read_tool_executions = 6

    def __init__(
        self,
        *,
        case_planning_turns: int = 0,
        run_planning_turns: int = 0,
        actual_read_tool_executions: int = 0,
    ) -> None:
        if not 0 <= case_planning_turns <= self.max_case_planning_turns:
            raise ValueError("invalid initial Case planning-turn count")
        if not 0 <= run_planning_turns <= self.max_run_planning_turns:
            raise ValueError("invalid initial Run planning-turn count")
        if not 0 <= actual_read_tool_executions <= self.max_actual_read_tool_executions:
            raise ValueError("invalid initial read-tool execution count")
        self._case_planning_turns = case_planning_turns
        self._run_planning_turns = run_planning_turns
        self._actual_read_tool_executions = actual_read_tool_executions

    def record_planning_turn(self) -> None:
        if self._case_planning_turns >= self.max_case_planning_turns:
            raise ToolBudgetExceeded("CASE_PLANNING_TURN_BUDGET_EXCEEDED")
        if self._run_planning_turns >= self.max_run_planning_turns:
            raise ToolBudgetExceeded("RUN_PLANNING_TURN_BUDGET_EXCEEDED")
        self._case_planning_turns += 1
        self._run_planning_turns += 1

    def record_actual_execution(self) -> None:
        if self._actual_read_tool_executions >= self.max_actual_read_tool_executions:
            raise ToolBudgetExceeded("READ_TOOL_EXECUTION_BUDGET_EXCEEDED")
        self._actual_read_tool_executions += 1

    def reset_run(self) -> None:
        self._run_planning_turns = 0

    @property
    def snapshot(self) -> ToolBudgetSnapshot:
        return ToolBudgetSnapshot(
            case_planning_turns=self._case_planning_turns,
            run_planning_turns=self._run_planning_turns,
            actual_read_tool_executions=self._actual_read_tool_executions,
        )
