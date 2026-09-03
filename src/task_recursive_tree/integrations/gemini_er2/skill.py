from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from task_recursive_tree.core.model import Diagnostic
from task_recursive_tree.task.contracts import SkillContext
from task_recursive_tree.task.model import NodeOrigin, TaskNodeSpec

from .artifacts import ArtifactPolicyError, HarnessArtifactBridge
from .paths import import_harness_module
from .translation import GEMINI_ER2_METADATA_KEY


SUPPORTED_ACTIONS = frozenset(
    {
        "acquire_view",
        "follow_path",
        "move_to_transport_posture",
        "pick_object",
        "place_object",
        "prepare_base_motion_posture",
        "recover_workspace",
        "reposition_for_interaction",
    }
)

_ALLOWED_PARAMETERS: dict[str, tuple[str, ...]] = {
    "pick_object": (
        "source_region_id",
        "clear_region_owner_id",
        "grasp_policy",
    ),
    "place_object": (
        "side",
        "corner",
        "placement_policy",
        "staging_source_id",
        "transport_prepared",
        "placement_prepared",
        "base_prepositioned",
    ),
    "reposition_for_interaction": (
        "purpose",
        "relation",
        "side",
        "corner",
        "require_held_load",
        "expected_held_entity_id",
        "source_region_id",
        "clear_region_owner_id",
        "grasp_policy",
    ),
    "move_to_transport_posture": (),
    "follow_path": ("laps",),
    "acquire_view": ("purpose",),
    "prepare_base_motion_posture": (
        "purpose",
        "failure_mode",
        "failed_stage",
    ),
    "recover_workspace": (
        "configuration_id",
        "component_scope",
    ),
}

_ALLOWED_ROLES: dict[str, frozenset[str]] = {
    "pick_object": frozenset({"manipuland"}),
    "place_object": frozenset({"manipuland", "destination"}),
    "reposition_for_interaction": frozenset(
        {"reference", "placement_object"}
    ),
    "move_to_transport_posture": frozenset({"manipuland"}),
    "follow_path": frozenset({"anchor"}),
    "acquire_view": frozenset({"target"}),
    "prepare_base_motion_posture": frozenset(),
    "recover_workspace": frozenset(),
}

_ARTIFACT_INPUTS: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "place_object": (("layout_targets", "target_ref", False),),
    "reposition_for_interaction": (
        ("detour_path", "path_ref", True),
        ("layout_targets", "target_ref", False),
    ),
    "move_to_transport_posture": (
        ("transport_posture", "posture_ref", False),
    ),
    "follow_path": (("detour_path", "path_ref", True),),
}


class PhysicalRequestValidationError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        retryable: bool = False,
        repairable: bool = False,
    ) -> None:
        super().__init__(message)
        self.diagnostic = Diagnostic(
            code=code,
            message=message,
            details=dict(details or {}),
            retryable=retryable,
            repairable=repairable,
        )


class HarnessMacroActionSkill:
    """Compile one kernel physical node into a Harness macro request."""

    def __init__(
        self,
        *,
        artifact_bridge: HarnessArtifactBridge,
        harness_root: str | None = None,
    ) -> None:
        self.artifact_bridge = artifact_bridge
        self.harness_root = harness_root

    def build_request(
        self,
        node: TaskNodeSpec,
        context: SkillContext,
    ) -> object:
        source = node.parameters.get(GEMINI_ER2_METADATA_KEY)
        action_ref = (
            source.get("action_ref")
            if isinstance(source, Mapping)
            else None
        )
        action_name = str(action_ref or "").strip()
        if not action_name:
            raise ValueError(
                f"GeminiER2 physical node {node.node_id!r} has no action_ref"
            )
        if action_name not in SUPPORTED_ACTIONS:
            raise ValueError(
                f"Unsupported GeminiER2 macro action: {action_name}"
            )
        _validate_system_only_source(node, action_name, source)
        request_id = str(context.request_id or "").strip()
        if not request_id:
            raise ValueError(
                f"SkillContext.request_id is required for {node.node_id!r}"
            )

        parameters = {
            str(key): copy.deepcopy(value)
            for key, value in node.parameters.items()
            if key != GEMINI_ER2_METADATA_KEY
        }
        recovery_contract = _recovery_contract(
            action_name,
            parameters,
        )
        arguments: dict[str, Any] = {}
        participants = _participants_for_action(
            action_name,
            parameters,
            self.artifact_bridge,
        )
        if participants:
            arguments["participants"] = {
                role: {"entity_ids": list(entity_ids)}
                for role, entity_ids in participants.items()
            }

        self._copy_allowed_parameters(
            action_name,
            parameters,
            arguments,
        )
        artifact_refs = self._resolve_artifacts(
            node,
            action_name,
            parameters,
            arguments,
        )

        contracts = import_harness_module(
            "er2sim.contracts",
            harness_root=self.harness_root,
        )
        request = contracts.MacroActionRequest(
            name=action_name,
            request_id=request_id,
            arguments=copy.deepcopy(arguments),
        )
        self._attach_recovery_motion_permit(
            request,
            recovery_contract=recovery_contract,
        )
        if artifact_refs:
            setattr(request, "artifact_refs", tuple(artifact_refs))
        snapshot_id = _snapshot_id(self.artifact_bridge)
        path_ref = arguments.get("path_ref")
        if path_ref is not None and snapshot_id is None:
            raise PhysicalRequestValidationError(
                "STALE_RELEVANT_STATE",
                f"{action_name} cannot dispatch a route without a "
                "frozen input snapshot",
                details={
                    "action_name": action_name,
                    "artifact_ref": str(path_ref),
                    "affected_refs": [str(path_ref)],
                    "phase": "before_dispatch",
                },
                retryable=True,
            )
        if snapshot_id is not None:
            setattr(request, "input_snapshot_id", snapshot_id)
        return request

    @staticmethod
    def _copy_allowed_parameters(
        action_name: str,
        parameters: Mapping[str, Any],
        arguments: dict[str, Any],
    ) -> None:
        if action_name == "place_object":
            arguments["relation"] = str(
                parameters.get("relation", "inside_support_region")
            )
        for key in _ALLOWED_PARAMETERS[action_name]:
            if parameters.get(key) is None:
                continue
            value = parameters[key]
            if key in {
                "transport_prepared",
                "placement_prepared",
                "base_prepositioned",
                "require_held_load",
            }:
                arguments[key] = bool(value)
            elif key == "laps":
                arguments[key] = int(value)
            elif key in {
                "purpose",
                "relation",
                "expected_held_entity_id",
                "failure_mode",
                "failed_stage",
                "configuration_id",
                "component_scope",
            }:
                arguments[key] = str(value)
            else:
                arguments[key] = copy.deepcopy(value)
        if action_name == "follow_path":
            arguments.setdefault("laps", 1)
        if (
            action_name == "reposition_for_interaction"
            and _is_placement_target(parameters)
        ):
            arguments["interaction_target_kind"] = "placement_pose"
            arguments.setdefault(
                "require_held_load",
                bool(parameters.get("require_held_load", True)),
            )

    def _attach_recovery_motion_permit(
        self,
        request: object,
        *,
        recovery_contract: str | None,
    ) -> None:
        if recovery_contract is None:
            return
        issue_permit = getattr(
            self.artifact_bridge.runtime,
            "issue_recovery_motion_permit",
            None,
        )
        if not callable(issue_permit):
            return
        permit = issue_permit(
            request,
            contract=recovery_contract,
        )
        if permit is None:
            return
        expected_state = getattr(request, "expected_state", None)
        if not isinstance(expected_state, dict):
            expected_state = (
                dict(expected_state)
                if isinstance(expected_state, Mapping)
                else {}
            )
            setattr(request, "expected_state", expected_state)
        expected_state["recovery_motion_permit"] = str(permit)

    def _resolve_artifacts(
        self,
        node: TaskNodeSpec,
        action_name: str,
        parameters: Mapping[str, Any],
        arguments: dict[str, Any],
    ) -> tuple[str, ...]:
        refs: list[str] = []
        for artifact_kind, ref_key, required in _ARTIFACT_INPUTS.get(
            action_name, ()
        ):
            explicit = parameters.get(ref_key)
            try:
                resolved = self.artifact_bridge.resolve_for(
                    node,
                    artifact_kind=artifact_kind,
                    ref_key=ref_key,
                )
            except ArtifactPolicyError as exc:
                raise PhysicalRequestValidationError(
                    "STALE_ARTIFACT",
                    f"{action_name} cannot use its declared "
                    f"{artifact_kind} artifact: {exc.reason}",
                    details={
                        "action_name": action_name,
                        "artifact_kind": artifact_kind,
                        "artifact_ref": exc.artifact_ref,
                        "affected_refs": [exc.artifact_ref],
                        "policy_reason": exc.reason,
                        "phase": "before_dispatch",
                        "exception_type": type(exc).__name__,
                    },
                    retryable=True,
                    repairable=True,
                ) from exc
            except Exception as exc:
                code = (
                    "STALE_ARTIFACT"
                    if artifact_kind == "detour_path"
                    else "INVALID_ARTIFACT_CONTRACT"
                )
                raise PhysicalRequestValidationError(
                    code,
                    f"{action_name} could not resolve its declared "
                    f"{artifact_kind} artifact: {exc}",
                    details={
                        "action_name": action_name,
                        "artifact_kind": artifact_kind,
                        "artifact_ref": (
                            str(explicit)
                            if isinstance(explicit, str)
                            else None
                        ),
                        "affected_refs": (
                            [str(explicit)]
                            if isinstance(explicit, str) and explicit
                            else []
                        ),
                        "phase": "before_dispatch",
                        "exception_type": type(exc).__name__,
                    },
                ) from exc
            if explicit is not None and not resolved:
                raise PhysicalRequestValidationError(
                    (
                        "STALE_ARTIFACT"
                        if artifact_kind == "detour_path"
                        else "INVALID_ARTIFACT_CONTRACT"
                    ),
                    f"Explicit {ref_key} for {node.node_id!r} does not "
                    "resolve through its artifact contract",
                    details={
                        "action_name": action_name,
                        "artifact_kind": artifact_kind,
                        "artifact_ref": str(explicit),
                        "affected_refs": [str(explicit)],
                        "phase": "before_dispatch",
                    },
                )
            if required and not resolved:
                raise PhysicalRequestValidationError(
                    (
                        "STALE_ARTIFACT"
                        if artifact_kind == "detour_path"
                        else "INVALID_ARTIFACT_CONTRACT"
                    ),
                    f"{action_name} requires a declared {ref_key} artifact",
                    details={
                        "action_name": action_name,
                        "artifact_kind": artifact_kind,
                        "phase": "before_dispatch",
                    },
                )
            if resolved is None:
                continue
            if (
                artifact_kind == "layout_targets"
                and not self.artifact_bridge.is_layout_target(resolved)
            ):
                raise PhysicalRequestValidationError(
                    "INVALID_ARTIFACT_CONTRACT",
                    f"{resolved!r} is not a layout_targets artifact",
                    details={
                        "action_name": action_name,
                        "artifact_kind": artifact_kind,
                        "artifact_ref": resolved,
                        "affected_refs": [resolved],
                        "phase": "before_dispatch",
                    },
                )
            arguments[ref_key] = resolved
            refs.append(resolved)
        return tuple(dict.fromkeys(refs))


def _validate_system_only_source(
    node: TaskNodeSpec,
    action_name: str,
    source: Any,
) -> None:
    if action_name != "recover_workspace":
        return
    source_mapping = source if isinstance(source, Mapping) else {}
    source_origin = str(source_mapping.get("origin") or "").strip().lower()
    trusted_repair = (
        node.origin is NodeOrigin.REPAIR
        and source_origin in {"", "repair_planner"}
    )
    trusted_system_recovery = (
        node.origin is NodeOrigin.REPAIR
        and source_origin in {
            "continuous_session_recovery",
            "system_recovery",
        }
        and _has_system_owned_recovery_marker(source_mapping)
    )
    if trusted_repair or trusted_system_recovery:
        return
    raise PhysicalRequestValidationError(
        "SYSTEM_ONLY_ACTION_NOT_ALLOWED",
        "System-only GeminiER2 macro action 'recover_workspace' "
        "requires a trusted repair or system-owned recovery source",
        details={
            "action_name": action_name,
            "node_origin": node.origin.value,
            "source_origin": source_origin or None,
        },
    )


def _has_system_owned_recovery_marker(
    source: Mapping[str, Any],
) -> bool:
    metadata = source.get("metadata")
    if (
        isinstance(metadata, Mapping)
        and metadata.get("system_owned_recovery") is True
    ):
        return True
    tree = source.get("tree")
    tree_metadata = tree.get("metadata") if isinstance(tree, Mapping) else None
    return bool(
        isinstance(tree_metadata, Mapping)
        and tree_metadata.get("system_owned_recovery") is True
    )


def _recovery_contract(
    action_name: str,
    parameters: Mapping[str, Any],
) -> str | None:
    if action_name != "recover_workspace":
        return None
    required = (
        "configuration_id",
        "component_scope",
        "recovery_contract",
    )
    missing = [
        key
        for key in required
        if parameters.get(key) is None
        or not str(parameters.get(key)).strip()
    ]
    if missing:
        raise PhysicalRequestValidationError(
            "INVALID_PHYSICAL_REQUEST",
            "recover_workspace requires configuration_id, "
            "component_scope, and recovery_contract",
            details={
                "action_name": action_name,
                "missing_parameters": missing,
            },
        )
    return str(parameters["recovery_contract"]).strip()


def _participants_for_action(
    action_name: str,
    parameters: Mapping[str, Any],
    artifact_bridge: HarnessArtifactBridge,
) -> dict[str, list[str]]:
    raw = parameters.get("participants")
    participants: dict[str, list[str]] = {}
    if isinstance(raw, Mapping):
        for role, values in raw.items():
            entity_ids = _entity_ids(values, artifact_bridge)
            if entity_ids:
                participants[str(role)] = entity_ids

    role_fields = {
        "manipuland",
        "object",
        "source",
        "destination",
        "reference",
        "placement_object",
        "anchor",
        "target",
    }
    for role in role_fields:
        if role in participants:
            continue
        values = parameters.get(f"{role}_ids")
        if values is None:
            values = parameters.get(role)
        entity_ids = _entity_ids(values, artifact_bridge)
        if entity_ids:
            participants[role] = entity_ids

    if action_name in {
        "pick_object",
        "place_object",
        "move_to_transport_posture",
    }:
        _move_alias(participants, "manipuland", "object", "source")
    elif action_name == "reposition_for_interaction":
        _move_alias(participants, "reference", "object", "manipuland")
    elif action_name == "follow_path":
        _move_alias(participants, "anchor", "destination")
    elif action_name == "acquire_view":
        _move_alias(participants, "target", "object", "reference")

    allowed = _ALLOWED_ROLES[action_name]
    return {
        role: list(dict.fromkeys(entity_ids))
        for role, entity_ids in participants.items()
        if role in allowed and entity_ids
    }


def _move_alias(
    participants: dict[str, list[str]],
    canonical: str,
    *aliases: str,
) -> None:
    if canonical not in participants:
        for alias in aliases:
            if participants.get(alias):
                participants[canonical] = participants[alias]
                break
    for alias in aliases:
        participants.pop(alias, None)


def _entity_ids(
    value: Any,
    artifact_bridge: HarnessArtifactBridge,
) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        if "entity_ids" in value:
            value = value["entity_ids"]
        elif "entity_id" in value:
            value = [value["entity_id"]]
        elif "slot_id" in value:
            value = [value["slot_id"]]
        else:
            return []
    elif isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []

    task = artifact_bridge.task()
    slots = getattr(task, "slots", {}) if task is not None else {}
    result: list[str] = []
    for item in value:
        text = str(item)
        slot_id = text[1:] if text.startswith("$") else text
        slot = slots.get(slot_id) if isinstance(slots, Mapping) else None
        if slot is None:
            result.append(text)
            continue
        bound = (
            slot.get("bound_entity_ids", ())
            if isinstance(slot, Mapping)
            else getattr(slot, "bound_entity_ids", ())
        )
        result.extend(str(entity_id) for entity_id in (bound or ()))
    return list(dict.fromkeys(result))


def _is_placement_target(parameters: Mapping[str, Any]) -> bool:
    participants = parameters.get("participants")
    return bool(
        parameters.get("target_ref")
        or parameters.get("placement_object")
        or parameters.get("placement_object_ids")
        or (
            isinstance(participants, Mapping)
            and participants.get("placement_object")
        )
    )


def _snapshot_id(artifact_bridge: HarnessArtifactBridge) -> str | None:
    world = getattr(artifact_bridge.runtime, "world", None)
    freeze = getattr(world, "freeze_snapshot", None)
    if not callable(freeze):
        return None
    try:
        value = freeze()
        snapshot = value.get("snapshot", {}) if isinstance(value, Mapping) else {}
        snapshot_id = snapshot.get("id") if isinstance(snapshot, Mapping) else None
        return str(snapshot_id) if snapshot_id is not None else None
    except Exception:
        return None


__all__ = [
    "HarnessMacroActionSkill",
    "PhysicalRequestValidationError",
    "SUPPORTED_ACTIONS",
]
