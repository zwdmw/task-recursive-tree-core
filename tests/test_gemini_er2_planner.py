from __future__ import annotations

from task_recursive_tree.integrations.gemini_er2.planner import (
    HarnessTaskProgramPlannerAdapter,
)


def verified_program() -> dict:
    return {
        "schema": "task_program/1.0",
        "program_id": "planner-normalization",
        "root": {
            "kind": "task",
            "task_type": "grasp",
            "object": {
                "entity_ref": "can_1",
                "selection_basis": {
                    "category": "can",
                    "attributes": {"shape": "cylinder"},
                    "scope": {"quantifier": "all"},
                },
            },
            "params": {},
        },
        "translation_metadata": {
            "source": "native_model_translator",
            "translator": "grounded_model_task_program/1.0",
            "semantic_constraints_verified": True,
        },
    }


class FakePlanner:
    def __init__(self) -> None:
        self.parse_report = {"source": "fake"}
        self.calls = []

    def parse_task_program(self, instruction, perception):
        self.calls.append((instruction, perception))
        return verified_program()

    def delegated_method(self):
        return "delegated"


def test_planner_normalizes_before_returning_program_artifact() -> None:
    wrapped = FakePlanner()
    planner = HarnessTaskProgramPlannerAdapter(wrapped)
    perception = object()

    program = planner.parse_task_program(
        "把圆柱都放在篮子里",
        perception,
    )

    assert wrapped.calls == [("把圆柱都放在篮子里", perception)]
    assert "scope" not in (
        program["root"]["object"]["selection_basis"]
    )
    report = planner.parse_report
    audit = report["task_recursive_tree_normalization"]
    assert audit["applied"] is True
    assert audit["paths"] == [
        "root.object.selection_basis.scope.quantifier",
    ]
    assert wrapped.parse_report == {"source": "fake"}
    assert planner.delegated_method() == "delegated"


def test_planner_exposes_recorded_normalization_on_idempotent_pass() -> None:
    wrapped = FakePlanner()
    first = HarnessTaskProgramPlannerAdapter(wrapped)
    normalized = first.parse_task_program("instruction", object())

    class AlreadyNormalizedPlanner:
        parse_report = {}

        @staticmethod
        def parse_task_program(*_args, **_kwargs):
            return normalized

    second = HarnessTaskProgramPlannerAdapter(AlreadyNormalizedPlanner())
    returned = second.parse_task_program("instruction", object())

    assert returned is normalized
    assert second.last_normalization is not None
    assert second.last_normalization.applied is False
    assert second.parse_report[
        "task_recursive_tree_normalization"
    ]["paths"] == [
        "root.object.selection_basis.scope.quantifier",
    ]
