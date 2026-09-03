from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from task_recursive_tree.core.model import Diagnostic
from task_recursive_tree.task.model import NodeOutcome, TaskNodeSpec

from .artifacts import ArtifactPolicyError, HarnessArtifactBridge
from .interaction_routes import (
    HarnessInteractionRoutePlanner,
    InteractionRouteFailure,
)
from .paths import import_harness_module
from .region_space import (
    RELOCATION_CONTINUATION_CONTEXT_KEYS,
    RELOCATION_LAYOUT_CONSTRAINT_KEYS,
    HarnessRegionSpaceAdapter,
    RegionLayoutSelection,
    layout_targets_artifact,
)
from .translation import GEMINI_ER2_METADATA_KEY


PlanningHandler = Callable[[TaskNodeSpec, dict[str, Any]], Mapping[str, Any]]


class HarnessSystemOperationAdapter:
    """Execute Harness system nodes without owning task-tree state."""

    _PLANNING_OPERATIONS = {
        "assess_placeability",
        "assess_interaction",
        "plan_transport_posture",
        "inspect_region_state",
        "plan_region_layout",
        "resolve_region",
        "select_staging",
        "select_staging_region",
        "plan_detour",
        "reconcile",
        "reconcile_all",
        "reconcile_pending_transactions",
    }
    _ALIASES = {
        "verify": "verify_predicates",
        "select_staging_region": "select_staging",
        "reconcile_pending_transactions": "reconcile_all",
    }

    def __init__(
        self,
        *,
        runtime: Any,
        artifacts: HarnessArtifactBridge,
        harness_root: str | None = None,
        planning_handlers: Mapping[str, PlanningHandler] | None = None,
        route_planner: HarnessInteractionRoutePlanner | None = None,
        region_space: HarnessRegionSpaceAdapter | None = None,
    ) -> None:
        self.runtime = runtime
        self.artifacts = artifacts
        self.harness_root = harness_root
        self._planning_handlers = dict(planning_handlers or {})
        self.route_planner = (
            route_planner
            or HarnessInteractionRoutePlanner(
                runtime=runtime,
                harness_root=harness_root,
            )
        )
        self.region_space = (
            region_space
            or HarnessRegionSpaceAdapter(
                runtime=runtime,
                harness_root=harness_root,
            )
        )

    def run(
        self,
        node: TaskNodeSpec,
        context: Any | None = None,
    ) -> NodeOutcome:
        del context
        operation = self.operation_for(node)
        if not operation:
            return NodeOutcome.failure(
                "UNKNOWN_SYSTEM_OPERATION",
                f"System node {node.node_id!r} has no operation reference",
            )
        operation = self._ALIASES.get(operation, operation)
        params = self._parameters(node)

        try:
            if operation in self._planning_handlers:
                result = dict(self._planning_handlers[operation](node, params))
                return self._normalize_mapping(node, operation, result)
            if self._is_registered_tool(operation):
                return self._run_registered_tool(node, operation, params)
            if operation in self._PLANNING_OPERATIONS:
                method = getattr(self, f"_run_{operation}")
                return self._normalize_mapping(
                    node,
                    operation,
                    dict(method(node, params)),
                )
            return NodeOutcome.failure(
                "UNKNOWN_SYSTEM_OPERATION",
                f"Unsupported Harness system operation {operation!r}",
                details={"operation": operation, "node_id": node.node_id},
            )
        except Exception as exc:
            code = str(getattr(exc, "code", "") or "SYSTEM_OPERATION_ERROR")
            message = str(
                getattr(exc, "message", "")
                or f"{operation} failed: {exc}"
            )
            details = _plain(getattr(exc, "details", {}))
            if not isinstance(details, dict):
                details = {}
            details.update(
                {
                    "operation": operation,
                    "node_id": node.node_id,
                    "exception_type": type(exc).__name__,
                }
            )
            return NodeOutcome(
                succeeded=False,
                diagnostic=Diagnostic(
                    code=code,
                    message=message,
                    details=details,
                    retryable=bool(getattr(exc, "retryable", False)),
                    repairable=bool(
                        getattr(exc, "repairable", False)
                    ),
                ),
                result={
                    "name": operation,
                    "termination": "failed",
                    "failure_code": code,
                    "message": message,
                    "details": details,
                },
            )

    def operation_for(self, node: TaskNodeSpec) -> str:
        source = node.parameters.get(GEMINI_ER2_METADATA_KEY, {})
        source = source if isinstance(source, Mapping) else {}
        candidates = (
            node.parameters.get("system_check"),
            source.get("tool_ref"),
            source.get("task_type"),
            node.task_type,
        )
        return next(
            (
                str(value).strip()
                for value in candidates
                if value is not None and str(value).strip()
            ),
            "",
        )

    def _parameters(self, node: TaskNodeSpec) -> dict[str, Any]:
        params = {
            str(key): copy.deepcopy(value)
            for key, value in node.parameters.items()
            if key not in {GEMINI_ER2_METADATA_KEY, "system_check"}
        }
        resolved = self.operation_for(node)
        operation = self._ALIASES.get(resolved, resolved)
        if operation == "verify_predicates" and "predicates" not in params:
            source = node.parameters.get(GEMINI_ER2_METADATA_KEY, {})
            goal = source.get("goal") if isinstance(source, Mapping) else None
            params["predicates"] = _predicate_specs(goal)
        return params

    def _is_registered_tool(self, operation: str) -> bool:
        tools = getattr(self.runtime, "tools", None)
        names = getattr(tools, "names", None)
        return callable(names) and operation in names()

    def _run_registered_tool(
        self,
        node: TaskNodeSpec,
        operation: str,
        params: dict[str, Any],
    ) -> NodeOutcome:
        actions = import_harness_module(
            "er2sim.macro_actions",
            harness_root=self.harness_root,
        )
        contracts = import_harness_module(
            "er2sim.contracts",
            harness_root=self.harness_root,
        )
        readonly = getattr(actions, "READONLY_HANDLERS", {})
        task_control = getattr(actions, "TASK_CONTROL_HANDLERS", {})
        if operation in readonly:
            execute = self.runtime.execute_readonly
            handler = readonly[operation]
        elif operation in task_control:
            execute = self.runtime.execute_task_control
            handler = task_control[operation]
        else:
            return NodeOutcome.failure(
                "UNSUPPORTED_SYSTEM_TOOL_CLASS",
                f"Registered tool {operation!r} is not a system operation",
                details={"operation": operation},
            )

        request = contracts.MacroActionRequest(
            name=operation,
            request_id=f"system:{node.node_id}",
            arguments=params,
        )
        allowed = self._allowed_tools()
        result = execute(
            request,
            allowed,
            lambda current: handler(self.runtime, current, {}),
        )
        return self._normalize_mapping(
            node,
            operation,
            _plain(result),
        )

    def _allowed_tools(self) -> list[str]:
        policy = getattr(self.runtime, "policy", None)
        resolver = getattr(policy, "allowed_decisions", None)
        task_id = self._task_id()
        if callable(resolver) and task_id is not None:
            return list(
                resolver(task_id, include_system_tools=True)
            )
        names = getattr(getattr(self.runtime, "tools", None), "names", None)
        return sorted(names()) if callable(names) else []

    def _task_id(self) -> str | None:
        resolver = getattr(self.runtime, "_task_id", None)
        if callable(resolver):
            value = resolver(required=False)
            return str(value) if value is not None else None
        value = getattr(self.runtime, "_current_task_id", None)
        return str(value) if value is not None else None

    def _normalize_mapping(
        self,
        node: TaskNodeSpec,
        operation: str,
        raw: Mapping[str, Any],
    ) -> NodeOutcome:
        result = _plain(raw)
        if not isinstance(result, dict):
            result = {"result": result}
        result.setdefault("name", operation)
        termination = str(result.get("termination") or "succeeded").lower()
        succeeded = bool(result.get("ok", termination == "succeeded"))
        if succeeded:
            refs = self.artifacts.register_result(node, result)
            if operation in {"plan_region_layout", "select_staging"}:
                target_ref = _optional_text(result.get("target_ref"))
                artifact_reader = getattr(self.artifacts, "artifact", None)
                artifact = (
                    artifact_reader(target_ref)
                    if target_ref is not None and callable(artifact_reader)
                    else None
                )
                if isinstance(artifact, Mapping):
                    reconciliation = dict(
                        result.get("reservation_reconciliation") or {}
                    )
                    reconciliation["released"] = copy.deepcopy(
                        list(artifact.get("superseded_reservations") or ())
                    )
                    result["reservation_reconciliation"] = reconciliation
            return NodeOutcome.success(*refs, result=result)

        code = str(
            result.get("failure_code")
            or result.get("code")
            or "SYSTEM_OPERATION_FAILED"
        )
        message = str(
            result.get("message")
            or result.get("reason")
            or f"{operation} failed"
        )
        retryable = bool(
            result.get("retryable")
            or termination in {"timed_out", "outcome_unknown"}
        )
        raw_details = result.get("details")
        details = (
            dict(_plain(raw_details))
            if isinstance(raw_details, Mapping)
            else {}
        )
        details.setdefault("operation", operation)
        details.setdefault("termination", termination)
        details.setdefault("result", copy.deepcopy(result))
        return NodeOutcome(
            succeeded=False,
            diagnostic=Diagnostic(
                code=code,
                message=message,
                details=details,
                retryable=retryable,
                repairable=bool(result.get("repairable", False)),
            ),
            result=result,
        )

    def _run_assess_placeability(
        self,
        node: TaskNodeSpec,
        params: dict[str, Any],
    ) -> Mapping[str, Any]:
        simulator = import_harness_module(
            "er2sim.placement_simulator",
            harness_root=self.harness_root,
        )
        object_id = _required_entity(params, "object")
        destination_id = _required_entity(params, "destination")
        target_ref = None
        candidate_point = None
        explicit_target_ref = _optional_text(params.get("target_ref"))
        try:
            target_ref = self.artifacts.resolve_for(
                node,
                artifact_kind="layout_targets",
                ref_key="target_ref",
            )
        except ArtifactPolicyError as exc:
            return {
                "termination": "failed",
                "failure_code": "STALE_ARTIFACT",
                "message": (
                    "assess_placeability cannot use its declared "
                    f"{exc.artifact_kind} artifact: {exc.reason}"
                ),
                "retryable": True,
                "repairable": True,
                "details": {
                    "artifact_kind": exc.artifact_kind,
                    "artifact_ref": exc.artifact_ref,
                    "affected_refs": [exc.artifact_ref],
                    "policy_reason": exc.reason,
                    "phase": "before_assessment",
                    "exception_type": type(exc).__name__,
                },
            }
        except Exception as exc:
            return {
                "termination": "failed",
                "failure_code": "INVALID_ARTIFACT_CONTRACT",
                "message": (
                    "assess_placeability could not resolve its declared "
                    f"layout target: {exc}"
                ),
                "repairable": False,
                "details": {
                    "artifact_kind": "layout_targets",
                    "artifact_ref": explicit_target_ref,
                    "affected_refs": (
                        [explicit_target_ref] if explicit_target_ref else []
                    ),
                    "phase": "before_assessment",
                    "exception_type": type(exc).__name__,
                },
            }
        if explicit_target_ref is not None and target_ref is None:
            return {
                "termination": "failed",
                "failure_code": "INVALID_ARTIFACT_CONTRACT",
                "message": (
                    f"Explicit target_ref for {node.node_id!r} does not "
                    "resolve through its artifact contract"
                ),
                "repairable": False,
                "details": {
                    "artifact_kind": "layout_targets",
                    "artifact_ref": explicit_target_ref,
                    "affected_refs": [explicit_target_ref],
                    "phase": "before_assessment",
                },
            }
        if target_ref is not None:
            targets = import_harness_module(
                "er2sim.interaction_targets",
                harness_root=self.harness_root,
            )
            candidate_point = targets.resolve_layout_target_point(
                self.runtime,
                target_ref,
                object_id,
                destination_id,
            )
            if candidate_point is None:
                return {
                    "termination": "failed",
                    "failure_code": "INVALID_ARTIFACT_CONTRACT",
                    "message": (
                        f"Layout target {target_ref!r} cannot be resolved "
                        f"for {object_id!r} on {destination_id!r}"
                    ),
                    "repairable": False,
                    "details": {
                        "artifact_kind": "layout_targets",
                        "artifact_ref": target_ref,
                        "affected_refs": [target_ref],
                        "object_id": object_id,
                        "destination_id": destination_id,
                        "phase": "before_assessment",
                    },
                }
        assessment = simulator.assess_placement(
            self.runtime,
            object_id,
            destination_id,
            relation=str(params.get("relation", "inside_support_region")),
            corner=_optional_text(params.get("corner") or params.get("slot")),
            requires_empty=bool(params.get("requires_empty", False)),
            require_reachable=not bool(params.get("defer_reachability", False)),
            simulation_mode=str(params.get("simulation_mode", "conservative")),
            selector=_optional_text(params.get("target_selector")),
            candidate_point=candidate_point,
        )
        data = _plain(assessment)
        artifact = simulator.placement_assessment_artifact(assessment)
        if target_ref is not None:
            artifact["layout_target_ref"] = target_ref
        publication: dict[str, Any] = {}
        self.artifacts.publish(node, artifact, publication)
        succeeded = bool(getattr(assessment, "ok", False))
        failure_code = _optional_text(data.get("failure_code"))
        occupant_ids = list(
            dict.fromkeys(
                str(value)
                for value in (data.get("occupant_ids") or ())
                if str(value)
            )
        )
        blocking_entity_ids = [
            entity_id
            for entity_id in occupant_ids
            if entity_id
            not in {object_id, destination_id, "robot_1"}
        ]
        local_layout_failure = str(failure_code or "").upper() in {
            "REGION_OCCUPIED",
            "PLACEMENT_COLLISION",
        }
        details = {
            "object_id": object_id,
            "placement_object_id": object_id,
            "destination_id": destination_id,
            "placement_destination_id": destination_id,
            "target_ref": target_ref,
            "layout_target_ref": target_ref,
            "occupant_ids": occupant_ids,
            "blocking_entity_ids": blocking_entity_ids,
            "assessment": copy.deepcopy(data),
        }
        if local_layout_failure and blocking_entity_ids:
            details.update(
                {
                    "failed_predicate": "occupies_support_region",
                    "expected_value": "false",
                    "actual_value": "true",
                }
            )
        return {
            "termination": "succeeded" if succeeded else "failed",
            "failure_code": failure_code,
            "message": data.get("reason"),
            "retryable": data.get("verdict") == "unknown",
            "repairable": (
                not succeeded
                and (
                    local_layout_failure
                    or str(failure_code or "").upper()
                    == "SELF_OCCUPANCY_BLOCKS_PLACEMENT"
                )
            ),
            "details": details,
            "assessment": data,
            "layout_target_ref": target_ref,
            **publication,
        }

    def _run_assess_interaction(
        self,
        node: TaskNodeSpec,
        params: dict[str, Any],
    ) -> Mapping[str, Any]:
        targets = import_harness_module(
            "er2sim.interaction_targets",
            harness_root=self.harness_root,
        )
        reference_id = _required_entity(params, "reference", "object")
        target = targets.resolve_interaction_target(
            self.runtime,
            reference_id,
            params=params,
        )
        if target is None:
            return {
                "termination": "failed",
                "failure_code": "TARGET_NOT_FOUND",
                "message": f"Cannot resolve interaction target for {reference_id}",
            }
        placement_target = targets.is_placement_target(params)
        grasp_config = None
        reachability_point = tuple(target.point)
        if not placement_target:
            grasp_config = targets.resolve_grasp_execution_config(
                self.runtime.perception,
                reference_id,
                params,
            )
            control = import_harness_module(
                "er2sim.control",
                harness_root=self.harness_root,
            )
            geometry = (
                self.runtime.perception.catalog.get(reference_id, {})
                .get("geometry", {})
            ) or {}
            reachability_point = tuple(
                control.gripper_target_for_grasp(
                    self.runtime.scene,
                    tuple(target.point),
                    str(geometry.get("grasp_mode", "suction")),
                    suction_standoff=float(
                        grasp_config.suction_standoff
                    ),
                )
            )
        descriptor = _plain(target.descriptor())
        reachability = None
        reader = getattr(self.runtime.scene, "point_reachability", None)
        if callable(reader):
            reachability = _plain(reader(reachability_point))
        reachable = (
            bool(reachability.get("reachable"))
            if isinstance(reachability, Mapping)
            else True
        )
        artifact = {
            "kind": "interaction_assessment",
            "artifact_ref": self._artifact_ref(node, "interaction"),
            "reference_id": reference_id,
            "target": descriptor,
            "reachability_point": [
                float(value) for value in reachability_point
            ],
            "reachability_model": (
                "placement_interaction_point"
                if placement_target
                else "gripper_target_for_grasp"
            ),
            "reachability": reachability,
            "world_revision": self._world_revision(),
        }
        if grasp_config is not None:
            artifact["grasp_execution"] = {
                "policy": str(grasp_config.policy),
                "source_region_id": grasp_config.source_region_id,
                "source_contact_entity_ids": list(
                    grasp_config.source_contact_entity_ids
                ),
                "suction_standoff": float(
                    grasp_config.suction_standoff
                ),
            }
        publication: dict[str, Any] = {}
        self.artifacts.publish(node, artifact, publication)
        return {
            "termination": "succeeded" if reachable else "failed",
            "failure_code": None if reachable else "NO_REACHABLE_POSE",
            "message": (
                "interaction target is reachable"
                if reachable
                else "interaction target is outside the current workspace"
            ),
            "assessment": artifact,
            **publication,
        }

    def _run_plan_transport_posture(
        self,
        node: TaskNodeSpec,
        params: dict[str, Any],
    ) -> Mapping[str, Any]:
        capabilities = import_harness_module(
            "er2sim.transport_capabilities",
            harness_root=self.harness_root,
        )
        motion_planning = import_harness_module(
            "er2sim.motion_planning",
            harness_root=self.harness_root,
        )
        object_id = _required_entity(params, "object")
        destination_id = _first_entity(params, "destination")
        candidates = capabilities.transport_posture_candidates(
            self.runtime.scene,
            self.runtime.perception,
            object_id,
            destination_id,
        )
        if not candidates:
            return {
                "termination": "failed",
                "failure_code": "NO_REACHABLE_POSE",
                "message": "No transport posture candidate is available",
            }
        planner = getattr(
            self.runtime.scene,
            "_end_effector_planner",
            None,
        )
        if planner is None:
            planner = motion_planning.EndEffectorPlanner(
                self.runtime.scene
            )
            self.runtime.scene._end_effector_planner = planner

        selected: Mapping[str, Any] | None = None
        selected_index: int | None = None
        planning_summary: dict[str, Any] | None = None
        candidate_rejections: list[dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            try:
                plan = planner.plan_to_configuration(
                    candidate,
                    held_entity_id=object_id,
                )
            except motion_planning.PlanningError as exc:
                collision_pair = list(
                    getattr(planner.snapshot, "last_collision", None)
                    or ()
                )
                candidate_rejections.append({
                    "candidate_index": index,
                    "configuration": _plain(candidate),
                    "failure_code": str(exc.code),
                    "message": str(exc.message),
                    "collision_pair": collision_pair,
                })
                continue
            selected = candidate
            selected_index = index
            planning_summary = dict(_plain(plan.to_dict()))
            break

        if selected is None or planning_summary is None:
            return {
                "termination": "failed",
                "failure_code": "NO_REACHABLE_POSE",
                "message": (
                    "No collision-free transport posture path is available"
                ),
                "repairable": True,
                "details": {
                    "object_id": object_id,
                    "destination_id": destination_id,
                    "candidate_configurations": _plain(candidates),
                    "candidate_rejections": candidate_rejections,
                },
            }
        artifact = {
            "kind": "transport_posture",
            "artifact_ref": self._artifact_ref(node, "posture"),
            "object_id": object_id,
            "destination_id": destination_id,
            "profile": capabilities.transport_profile(
                self.runtime.perception,
                object_id,
            ),
            "target_configuration": _plain(selected),
            "candidate_configurations": _plain(candidates),
            "selected_candidate_index": selected_index,
            "candidate_rejections": candidate_rejections,
            "planning_summary": planning_summary,
            "read_set": self._read_set(object_id, destination_id),
            "world_revision": self._world_revision(),
            "model_version": getattr(
                capabilities,
                "TRANSPORT_MODEL_VERSION",
                "",
            ),
        }
        result: dict[str, Any] = {
            "termination": "succeeded",
            "posture": artifact,
            "planning_summary": copy.deepcopy(planning_summary),
            "candidate_rejections": copy.deepcopy(candidate_rejections),
        }
        self.artifacts.publish(node, artifact, result)
        return result

    def _run_resolve_region(
        self,
        node: TaskNodeSpec,
        params: dict[str, Any],
    ) -> Mapping[str, Any]:
        grounding = import_harness_module(
            "er2sim.grounding",
            harness_root=self.harness_root,
        )
        owner_id = _required_entity(
            params,
            "destination",
            "region_owner",
            "object",
        )
        grounder = grounding.SemanticGrounder(
            world=self.runtime.world,
            perception=self.runtime.perception,
            task=self.artifacts.task(),
        )
        region = grounder.resolve_region(
            str(params.get("owner_slot_id") or "region_owner"),
            owner_id,
            str(params.get("selector") or "support"),
        )
        artifact = {
            "kind": "resolved_region",
            "artifact_ref": self._artifact_ref(node, "region"),
            **_plain(region),
        }
        result: dict[str, Any] = {"termination": "succeeded", "region": artifact}
        self.artifacts.publish(node, artifact, result)
        return result

    def _run_inspect_region_state(
        self,
        node: TaskNodeSpec,
        params: dict[str, Any],
    ) -> Mapping[str, Any]:
        region_ref = _optional_text(
            params.get("region_ref")
            or params.get("staging_region_ref")
        )
        if region_ref is None:
            refs = self.region_space.candidate_refs(params)
            region_ref = refs[0] if refs else None
        if region_ref is None:
            return {
                "termination": "failed",
                "failure_code": "NO_STAGING_REGION",
                "message": "No support region is available for inspection",
                "repairable": False,
            }
        snapshot = self.region_space.inspect(region_ref)
        artifact = {
            **snapshot.to_dict(),
            "artifact_kind": "region_occupancy_snapshot",
            "artifact_ref": self._artifact_ref(node, "region-state"),
        }
        return {
            "termination": "succeeded",
            "region_state": artifact,
            "published_artifacts": [artifact],
        }

    def _run_plan_region_layout(
        self,
        node: TaskNodeSpec,
        params: dict[str, Any],
    ) -> Mapping[str, Any]:
        region_ref = _optional_text(
            params.get("region_ref")
            or params.get("staging_region_ref")
        )
        if region_ref is None:
            return {
                "termination": "failed",
                "failure_code": "NO_STAGING_REGION",
                "message": "plan_region_layout requires a region_ref",
                "repairable": False,
            }
        batch_ids = _batch_object_ids(params)
        reservation_group = str(
            params.get("reservation_group")
            or f"region-layout:{node.node_id}"
        )
        reservation_reconciliation = self._prepare_layout_allocation(
            node,
            batch_ids,
        )
        if reservation_reconciliation.get("blocked"):
            return self._layout_allocation_blocked_result(
                reservation_reconciliation
            )
        decision = self.region_space.plan_layout(
            region_ref,
            batch_ids,
            reservation_group=reservation_group,
            relation=_optional_text(params.get("relation")),
            destination_id=_first_entity(params, "destination"),
            preserve_entity_ids=_text_values(
                params.get("preserve_entity_ids")
            ),
            excluded_local_xy=_local_xy_values(
                params.get("excluded_local_xy")
            ),
            placement_policy=_optional_text(
                params.get("placement_policy")
            ),
            robust_clearance_margin_m=params.get(
                "robust_clearance_margin_m"
            ),
            placement_constraints={
                key: copy.deepcopy(params[key])
                for key in (
                    *RELOCATION_LAYOUT_CONSTRAINT_KEYS,
                    *RELOCATION_CONTINUATION_CONTEXT_KEYS,
                )
                if params.get(key) is not None
            },
            max_search_states=int(
                params.get("max_search_states", 25000)
            ),
        )
        return self._region_layout_result(
            node,
            params,
            batch_ids,
            decision,
            reservation_reconciliation=reservation_reconciliation,
        )

    def _run_select_staging(
        self,
        node: TaskNodeSpec,
        params: dict[str, Any],
    ) -> Mapping[str, Any]:
        batch_ids = _batch_object_ids(params)
        reservation_group = str(
            params.get("reservation_group")
            or f"staging-layout:{node.node_id}"
        )
        reservation_reconciliation = self._prepare_layout_allocation(
            node,
            batch_ids,
        )
        if reservation_reconciliation.get("blocked"):
            return self._layout_allocation_blocked_result(
                reservation_reconciliation
            )
        decision = self.region_space.select_layout(
            batch_ids,
            params,
            reservation_group=reservation_group,
            preserve_entity_ids=_text_values(
                params.get("preserve_entity_ids")
            ),
            excluded_local_xy=_local_xy_values(
                params.get("excluded_local_xy")
            ),
            max_search_states=int(
                params.get("max_search_states", 25000)
            ),
        )
        return self._region_layout_result(
            node,
            params,
            batch_ids,
            decision,
            reservation_reconciliation=reservation_reconciliation,
        )

    def _region_layout_result(
        self,
        node: TaskNodeSpec,
        params: Mapping[str, Any],
        batch_ids: tuple[str, ...],
        decision: RegionLayoutSelection,
        *,
        reservation_reconciliation: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        reservation_reconciliation = dict(
            reservation_reconciliation or {}
        )
        if not decision.feasible:
            failure = decision.failure
            return {
                "termination": "failed",
                "failure_code": (
                    failure.code
                    if failure is not None
                    else "NO_FEASIBLE_LAYOUT"
                ),
                "message": (
                    failure.message
                    if failure is not None
                    else "No feasible staging layout is available"
                ),
                "retryable": bool(
                    failure is not None
                    and failure.verdict.value
                    == "perception_insufficient"
                ),
                "repairable": False,
                "details": {
                    **(
                        dict(failure.details)
                        if failure is not None
                        else {}
                    ),
                    "layout_failure": (
                        failure.to_dict()
                        if failure is not None
                        else None
                    ),
                    "candidate_audit": [
                        copy.deepcopy(dict(value))
                        for value in decision.candidate_audit
                    ],
                    "batch_object_ids": list(batch_ids),
                    "reservation_reconciliation": (
                        reservation_reconciliation
                    ),
                },
            }
        assert decision.snapshot is not None
        assert decision.plan is not None
        definition = decision.snapshot.definition
        relation = (
            definition.allowed_relations[0]
            if definition.allowed_relations
            else "on_support"
        )
        occupancy_artifact = {
            **decision.snapshot.to_dict(),
            "artifact_kind": "region_occupancy_snapshot",
            "artifact_ref": self._artifact_ref(node, "region-state"),
        }
        layout_plan_artifact = {
            **decision.plan.to_dict(),
            "artifact_kind": "region_layout_plan",
            "artifact_ref": self._artifact_ref(node, "region-layout"),
            "anchor_id": definition.owner_id,
        }
        target_ref = str(
            params.get("target_ref")
            or self._artifact_ref(node, "layout-targets")
        )
        targets_artifact = layout_targets_artifact(
            target_ref=target_ref,
            selection=decision,
            subject_ids=batch_ids,
        )
        selection_artifact = {
            "kind": "staging_selection",
            "artifact_kind": "staging_selection",
            "artifact_ref": self._artifact_ref(node, "staging"),
            "staging_id": definition.owner_id,
            "staging_region_ref": definition.region_ref,
            "relation": relation,
            "layout_target_ref": target_ref,
            "reservation_group": decision.plan.reservation_group,
            "world_revision": decision.plan.world_revision,
            "state_fingerprint": decision.plan.state_fingerprint,
            "candidate_audit": [
                copy.deepcopy(dict(value))
                for value in decision.candidate_audit
            ],
        }
        return {
            "termination": "succeeded",
            "selection": selection_artifact,
            "region_state": occupancy_artifact,
            "layout_plan": layout_plan_artifact,
            "layout_targets": targets_artifact,
            "target_ref": target_ref,
            "batch_object_ids": list(batch_ids),
            "reservation_reconciliation": reservation_reconciliation,
            "published_artifacts": [
                occupancy_artifact,
                layout_plan_artifact,
                targets_artifact,
                selection_artifact,
            ],
        }

    def _prepare_layout_allocation(
        self,
        node: TaskNodeSpec,
        batch_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        pending_reader = getattr(
            self.artifacts,
            "pending_reconciliation_transaction_ids",
            None,
        )
        pending = (
            tuple(str(value) for value in pending_reader())
            if callable(pending_reader)
            else ()
        )
        if pending:
            return {
                "blocked": True,
                "pending_transaction_ids": list(dict.fromkeys(pending)),
                "fulfilled": [],
                "released": [],
            }
        reconcile = getattr(
            self.artifacts,
            "reconcile_layout_reservations",
            None,
        )
        fulfilled = (
            reconcile(entity_ids=batch_ids)
            if callable(reconcile)
            else ()
        )
        return {
            "blocked": False,
            "pending_transaction_ids": [],
            "fulfilled": _plain(fulfilled),
            "released": [],
        }

    @staticmethod
    def _layout_allocation_blocked_result(
        reconciliation: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        pending = [
            str(value)
            for value in (
                reconciliation.get("pending_transaction_ids") or ()
            )
        ]
        return {
            "termination": "outcome_unknown",
            "failure_code": "OUTCOME_UNKNOWN",
            "message": (
                "Layout allocation is blocked until pending physical "
                "transactions are reconciled"
            ),
            "retryable": True,
            "repairable": True,
            "details": {
                "pending_transaction_ids": pending,
                "reservation_reconciliation": copy.deepcopy(
                    dict(reconciliation)
                ),
            },
        }

    def _run_plan_detour(
        self,
        node: TaskNodeSpec,
        params: dict[str, Any],
    ) -> Mapping[str, Any]:
        reference_id = _required_entity(
            params,
            "reference",
            "destination",
            "object",
        )
        decision = self.route_planner.plan(reference_id, params)
        if isinstance(decision, InteractionRouteFailure):
            return {
                "termination": "failed",
                "failure_code": decision.code,
                "message": decision.message,
                "retryable": decision.retryable,
                "repairable": (
                    str(
                        decision.details.get("recovery_kind") or ""
                    ).casefold()
                    in {
                        "relocate_interaction_blocker",
                        "relocate_interaction_blocker_set",
                        "replan_placement_layout",
                    }
                ),
                "details": copy.deepcopy(decision.details),
            }
        artifact = {
            "kind": "detour_path",
            "artifact_kind": "detour_path",
            **copy.deepcopy(decision.artifact),
            "artifact_ref": self._artifact_ref(node, "path"),
            "reference_id": str(reference_id),
            "read_set": self._read_set(
                *decision.dependency_entity_ids
            ),
            "world_revision": self._world_revision(),
        }
        result: dict[str, Any] = {
            "termination": "succeeded",
            **copy.deepcopy(decision.result),
            "path": artifact,
        }
        self.artifacts.publish(node, artifact, result)
        return result

    def _run_reconcile(
        self,
        node: TaskNodeSpec,
        params: dict[str, Any],
    ) -> Mapping[str, Any]:
        transaction_id = params.get("transaction_id")
        reconciled = self.runtime.reconcile_pending_transactions(
            str(params.get("trigger") or node.node_id),
            transaction_id=(
                str(transaction_id) if transaction_id is not None else None
            ),
        )
        pending = [
            item for item in reconciled
            if str(item.get("status")) in {"pending", "unresolvable", "missing"}
        ]
        reconcile_layout = getattr(
            self.artifacts,
            "reconcile_layout_reservations",
            None,
        )
        layout_reconciled = (
            reconcile_layout()
            if not pending and callable(reconcile_layout)
            else ()
        )
        return {
            "termination": "outcome_unknown" if pending else "succeeded",
            "failure_code": "OUTCOME_UNKNOWN" if pending else None,
            "message": (
                "Reconciliation still has unresolved transactions"
                if pending
                else "Reconciliation completed"
            ),
            "retryable": bool(pending),
            "reconciled": _plain(reconciled),
            "layout_reservations_reconciled": _plain(
                layout_reconciled
            ),
        }

    def _run_reconcile_all(
        self,
        node: TaskNodeSpec,
        params: dict[str, Any],
    ) -> Mapping[str, Any]:
        return self._run_reconcile(node, {**params, "transaction_id": None})

    def _artifact_ref(self, node: TaskNodeSpec, label: str) -> str:
        digest = hashlib.sha256(
            f"{node.node_id}:{label}:{self._world_revision()}".encode("utf-8")
        ).hexdigest()[:16]
        return f"{label}_{digest}"

    def _world_revision(self) -> int:
        return int(getattr(self.runtime.world, "revision", 0))

    def _read_set(self, *entity_ids: str | None) -> dict[str, int]:
        reader = getattr(self.runtime.world, "read_set_for", None)
        if not callable(reader):
            return {}
        return dict(
            reader(
                entities=[
                    str(value) for value in entity_ids if value is not None
                ],
                relations=[],
                tasks=[],
                interaction=True,
                safety=True,
                frame_graph=True,
            )
        )


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if is_dataclass(value):
        return _plain(asdict(value))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _plain(to_dict())
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return copy.deepcopy(value)


def _predicate_specs(formula: Any) -> list[dict[str, Any]]:
    if not isinstance(formula, Mapping):
        return []
    if formula.get("predicate"):
        return [copy.deepcopy(dict(formula))]
    values: list[dict[str, Any]] = []
    for child in formula.get("args", ()) or ():
        values.extend(_predicate_specs(child))
    return values


def _role_values(params: Mapping[str, Any], role: str) -> list[str]:
    participants = params.get("participants")
    if not isinstance(participants, Mapping):
        return []
    value = participants.get(role)
    if isinstance(value, Mapping):
        value = value.get("entity_ids")
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)] if value is not None else []


def _first_entity(params: Mapping[str, Any], *roles: str) -> str | None:
    key_aliases = {
        "object": ("object_ids", "placement_object_ids"),
        "reference": ("reference_ids",),
        "destination": ("destination_ids",),
        "region_owner": ("region_owner_ids", "region_owner_id"),
    }
    role_aliases = {
        "object": ("object", "manipuland", "placement_object", "subject"),
        "reference": ("reference", "object", "destination"),
        "destination": ("destination", "reference", "region_owner"),
        "region_owner": ("region_owner", "destination", "reference"),
    }
    for role in roles:
        for key in key_aliases.get(role, ()):
            value = params.get(key)
            if isinstance(value, (list, tuple)) and value:
                return str(value[0])
            if value is not None and not isinstance(value, (list, tuple)):
                return str(value)
        for alias in role_aliases.get(role, (role,)):
            values = _role_values(params, alias)
            if values:
                return values[0]
    return None


def _required_entity(params: Mapping[str, Any], *roles: str) -> str:
    value = _first_entity(params, *roles)
    if value is None:
        raise ValueError(
            f"Missing entity reference for one of: {', '.join(roles)}"
        )
    return value


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _batch_object_ids(params: Mapping[str, Any]) -> tuple[str, ...]:
    for key in ("batch_object_ids", "object_ids", "placement_object_ids"):
        values = _text_values(params.get(key))
        if values:
            return values
    for role in ("object", "manipuland", "placement_object", "subject"):
        values = tuple(_role_values(params, role))
        if values:
            return values
    return ()


def _text_values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, Mapping):
        return _text_values(
            value.get("entity_ids") or value.get("entity_id")
        )
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(dict.fromkeys(str(item) for item in value))


def _local_xy_values(value: Any) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[tuple[float, float]] = []
    for raw in value:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            continue
        try:
            point = (float(raw[0]), float(raw[1]))
        except (TypeError, ValueError):
            continue
        result.append(point)
    return tuple(dict.fromkeys(result))


__all__ = ["HarnessSystemOperationAdapter"]
