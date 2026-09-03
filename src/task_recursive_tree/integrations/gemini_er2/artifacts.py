from __future__ import annotations

import copy
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any, Iterator

from task_recursive_tree.task.model import TaskNodeSpec
from task_recursive_tree.task.store import TaskTreeStore

from .harness_safety import (
    DEFAULT_HARNESS_SAFETY_POLICY,
    HARNESS_SAFETY_MODEL_VERSION,
)
from .paths import import_harness_module
from .route_validation import ROUTE_VALIDATION_MODEL_VERSION
from .region_space import HarnessRegionSpaceAdapter
from .translation import GEMINI_ER2_METADATA_KEY


class ArtifactPolicyError(ValueError):
    """An artifact exists and matches its contract but is stale now."""

    def __init__(
        self,
        *,
        artifact_kind: str,
        artifact_ref: str,
        reason: str,
    ) -> None:
        self.artifact_kind = str(artifact_kind)
        self.artifact_ref = str(artifact_ref)
        self.reason = str(reason or "active artifact policy rejected it")
        super().__init__(
            f"{self.artifact_kind} artifact {self.artifact_ref!r} "
            f"is stale: {self.reason}"
        )


class HarnessArtifactBridge:
    """Bridge kernel node outputs to the Harness task artifact store."""

    def __init__(
        self,
        *,
        runtime: Any,
        store: TaskTreeStore,
        task_id: str | None,
        harness_root: str | None = None,
        region_space: HarnessRegionSpaceAdapter | None = None,
    ) -> None:
        self.runtime = runtime
        self.store = store
        self.task_id = task_id
        self.harness_root = harness_root
        self._region_space = region_space
        self._published: dict[str, dict[str, dict[str, Any]]] = {}
        self._consumed: dict[str, dict[str, dict[str, Any]]] = {}

    @contextmanager
    def mutation(self) -> Iterator[None]:
        task = self.task()
        artifacts = (
            getattr(task, "artifacts", None)
            if task is not None
            else None
        )
        artifact_snapshot = (
            copy.deepcopy(artifacts)
            if isinstance(artifacts, dict)
            else None
        )
        published_snapshot = copy.deepcopy(self._published)
        consumed_snapshot = copy.deepcopy(self._consumed)
        try:
            yield
        except BaseException:
            if artifact_snapshot is not None and task is not None:
                current = getattr(task, "artifacts", None)
                if isinstance(current, dict):
                    current.clear()
                    current.update(artifact_snapshot)
                else:
                    setattr(task, "artifacts", artifact_snapshot)
            self._published = published_snapshot
            self._consumed = consumed_snapshot
            raise

    def task(self) -> Any | None:
        tasks = getattr(self.runtime.world, "tasks", {})
        if not isinstance(tasks, Mapping):
            return None
        candidates = [
            self.task_id,
            getattr(self.runtime, "_current_task_id", None),
        ]
        for task_id in candidates:
            if task_id is not None and str(task_id) in tasks:
                return tasks[str(task_id)]
        if len(tasks) == 1:
            return next(iter(tasks.values()))
        return None

    def artifact(self, artifact_ref: str) -> dict[str, Any] | None:
        task = self.task()
        artifacts = getattr(task, "artifacts", {}) if task is not None else {}
        value = artifacts.get(str(artifact_ref)) if isinstance(
            artifacts, Mapping
        ) else None
        return copy.deepcopy(value) if isinstance(value, Mapping) else None

    def publish(
        self,
        node: TaskNodeSpec,
        artifact: Mapping[str, Any],
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        publication = copy.deepcopy(dict(artifact))
        publication.setdefault(
            "artifact_kind",
            publication.get("kind", ""),
        )
        publication.setdefault("publisher_node_id", node.node_id)
        artifact_kind = str(publication.get("artifact_kind") or "")
        if _is_managed_layout_publication(publication):
            return self._publish_managed_layout(
                node,
                publication,
                result,
            )
        contract = next(
            (
                item
                for item in self.contracts(node, "produces")
                if item.artifact_kind == artifact_kind
            ),
            None,
        )
        if contract is not None:
            publication.setdefault(
                "producer_node_id",
                contract.producer_node_id,
            )
            publication.setdefault(
                "continuation_node_id",
                contract.continuation_node_id,
            )
            if artifact_kind == "layout_targets":
                publication.setdefault(
                    "continuation_scope_id",
                    contract.continuation_node_id,
                )
                publication.setdefault("reservation_status", "active")
                targets = publication.get("targets")
                if isinstance(targets, dict):
                    for target in targets.values():
                        if isinstance(target, dict):
                            target.setdefault(
                                "reservation_status",
                                "active",
                            )
        else:
            publication.setdefault("producer_node_id", node.node_id)

        artifact_ref = publication.get("artifact_ref")
        if not isinstance(artifact_ref, str) or not artifact_ref:
            return publication
        task = self.task()
        artifacts = getattr(task, "artifacts", None) if task is not None else None
        if isinstance(artifacts, dict):
            artifacts[artifact_ref] = copy.deepcopy(publication)
        self._published.setdefault(node.node_id, {})[
            artifact_ref
        ] = copy.deepcopy(publication)
        if (
            artifact_kind == "layout_targets"
            and str(publication.get("source") or "")
            == "task_recursive_tree_batch_allocator/1.0"
            and str(
                publication.get("reservation_status") or "active"
            ).casefold()
            == "active"
        ):
            targets = publication.get("targets")
            entity_ids = tuple(
                str(entity_id)
                for entity_id, target in (
                    targets.items()
                    if isinstance(targets, Mapping)
                    else ()
                )
                if isinstance(target, Mapping)
                and str(
                    target.get("reservation_status") or "active"
                ).casefold()
                == "active"
            )
            superseded = self.release_layout_reservations_for_entities(
                entity_ids,
                reason=f"superseded_by_layout_artifact:{artifact_ref}",
                exclude_artifact_refs=(artifact_ref,),
                replacement_artifact_ref=artifact_ref,
            )
            publication[
                "reservation_committed_world_revision"
            ] = getattr(self.runtime.world, "revision", None)
            if superseded:
                publication["superseded_reservations"] = [
                    copy.deepcopy(dict(value)) for value in superseded
                ]
            if isinstance(artifacts, dict):
                artifacts[artifact_ref] = copy.deepcopy(publication)
            self._published[node.node_id][
                artifact_ref
            ] = copy.deepcopy(publication)
        if result is not None:
            result["artifact_ref"] = artifact_ref
            result["artifact_kind"] = publication.get("artifact_kind")
            result["publication"] = {
                key: publication.get(key)
                for key in (
                    "artifact_ref",
                    "artifact_kind",
                    "producer_node_id",
                    "publisher_node_id",
                    "continuation_node_id",
                )
                if publication.get(key) is not None
            }
        return publication

    def _publish_managed_layout(
        self,
        node: TaskNodeSpec,
        publication: dict[str, Any],
        result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        module = import_harness_module(
            "er2sim.task_artifacts",
            harness_root=self.harness_root,
        )
        contracts = [
            item
            for item in self.contracts(node, "produces")
            if str(getattr(item, "artifact_kind", "")) == "layout_targets"
        ]
        if len(contracts) != 1:
            raise module.ArtifactContractError(
                "managed layout publication requires exactly one "
                "layout_targets production contract"
            )
        contract = contracts[0]
        producer_node_id = str(
            getattr(contract, "producer_node_id", "") or ""
        )
        continuation_node_id = str(
            getattr(contract, "continuation_node_id", "") or ""
        )
        ref_key = str(getattr(contract, "ref_key", "") or "")
        if (
            not producer_node_id
            or not continuation_node_id
            or not ref_key
            or not bool(getattr(contract, "required", False))
        ):
            raise module.ArtifactContractError(
                "managed layout production contract is incomplete"
            )

        artifact_ref = publication.get("artifact_ref")
        if not isinstance(artifact_ref, str) or not artifact_ref:
            raise module.ArtifactContractError(
                "managed layout publication has no artifact_ref"
            )
        declared_ref = node.parameters.get(ref_key)
        if str(declared_ref or "") != artifact_ref:
            raise module.ArtifactContractError(
                f"managed layout artifact_ref {artifact_ref!r} does not "
                f"match node parameter {ref_key!r}"
            )
        declared_producer = node.parameters.get("layout_producer_node_id")
        if (
            declared_producer is not None
            and str(declared_producer) != producer_node_id
        ):
            raise module.ArtifactContractError(
                "managed layout node producer binding conflicts with its "
                "production contract"
            )
        declared_scope = node.parameters.get(
            "layout_continuation_scope_id"
        )
        if (
            declared_scope is not None
            and str(declared_scope) != continuation_node_id
        ):
            raise module.ArtifactContractError(
                "managed layout node continuation binding conflicts with "
                "its production contract"
            )

        expected_fields = {
            "producer_node_id": producer_node_id,
            "continuation_node_id": continuation_node_id,
            "continuation_scope_id": continuation_node_id,
            "publisher_node_id": node.node_id,
        }
        for key, expected in expected_fields.items():
            actual = publication.get(key)
            if actual is not None and str(actual) != str(expected):
                raise module.ArtifactContractError(
                    f"managed layout publication {key} conflicts with "
                    "its production contract"
                )
            publication[key] = expected
        if str(publication.get("kind") or "") != "layout_targets":
            raise module.ArtifactContractError(
                "managed layout publication has the wrong kind"
            )
        publication["artifact_kind"] = "layout_targets"
        publication.setdefault("reservation_status", "active")
        if str(publication["reservation_status"]).casefold() != "active":
            raise module.ArtifactContractError(
                "managed layout publication must start active"
            )
        targets = publication.get("targets")
        if isinstance(targets, dict):
            for target in targets.values():
                if isinstance(target, dict):
                    target.setdefault("reservation_status", "active")

        task = self.task()
        artifacts = getattr(task, "artifacts", None) if task is not None else None
        if not isinstance(artifacts, dict):
            raise module.ArtifactContractError(
                "managed layout publication requires a mutable task "
                "artifact store"
            )
        existing = artifacts.get(artifact_ref)
        try:
            publication = _merge_same_ref_layout(
                existing,
                publication,
            )
        except ValueError as exc:
            raise module.ArtifactContractError(str(exc)) from exc
        structural_reason = _layout_publication_structure_reason(publication)
        if structural_reason:
            raise module.ArtifactContractError(structural_reason)

        if self._region_space is None:
            self._region_space = HarnessRegionSpaceAdapter(
                runtime=self.runtime,
                harness_root=self.harness_root,
            )
        entity_ids = tuple(
            str(entity_id)
            for entity_id, target in publication["targets"].items()
            if isinstance(target, Mapping)
            and str(
                target.get("reservation_status") or "active"
            ).casefold()
            == "active"
        )
        revision = getattr(self.runtime.world, "revision", None)
        staged_updates, superseded = _stage_superseded_layouts(
            artifacts,
            entity_ids,
            replacement_artifact_ref=artifact_ref,
            revision=revision,
        )
        ignored_reservation_bindings = tuple(
            (
                str(value["artifact_ref"]),
                str(value["entity_id"]),
            )
            for value in superseded
        )
        policy_valid, policy_reason = (
            self._region_space.validate_layout_targets(
                publication,
                artifact_ref=artifact_ref,
                ignored_reservation_bindings=(
                    ignored_reservation_bindings
                ),
            )
        )
        if not policy_valid:
            raise module.ArtifactContractError(
                f"managed layout publication is not consumable: "
                f"{policy_reason}"
            )

        publication["reservation_committed_world_revision"] = revision
        if superseded:
            publication["superseded_reservations"] = [
                copy.deepcopy(dict(value)) for value in superseded
            ]
        else:
            publication.pop("superseded_reservations", None)

        for ref, staged in staged_updates.items():
            _commit_artifact_payload(artifacts, ref, staged)
            self._refresh_published_copy(ref, staged)
        _commit_artifact_payload(artifacts, artifact_ref, publication)
        self._published.setdefault(node.node_id, {})[
            artifact_ref
        ] = copy.deepcopy(publication)
        _record_publication_result(result, artifact_ref, publication)
        return publication

    def register_result(
        self,
        node: TaskNodeSpec,
        result: Mapping[str, Any],
    ) -> tuple[str, ...]:
        refs: list[str] = []
        for container in (result, result.get("data")):
            if not isinstance(container, Mapping):
                continue
            for key in ("artifact_ref", "assessment_ref"):
                value = container.get(key)
                if isinstance(value, str) and value:
                    refs.append(value)
            publications = container.get("published_artifacts")
            if isinstance(publications, list):
                for publication in publications:
                    if not isinstance(publication, Mapping):
                        continue
                    stored = self.publish(node, publication)
                    ref = stored.get("artifact_ref")
                    if isinstance(ref, str) and ref:
                        refs.append(ref)
        publication = result.get("publication")
        if isinstance(publication, Mapping):
            ref = publication.get("artifact_ref")
            if isinstance(ref, str) and ref:
                artifact = self.artifact(ref)
                if artifact is not None:
                    self._published.setdefault(node.node_id, {})[
                        ref
                    ] = artifact
                    refs.append(ref)
        for ref in tuple(refs):
            artifact = self.artifact(ref)
            if artifact is not None:
                self._published.setdefault(node.node_id, {})[
                    ref
                ] = artifact
        return tuple(dict.fromkeys(refs))

    def resolve_for(
        self,
        node: TaskNodeSpec,
        *,
        artifact_kind: str,
        ref_key: str,
    ) -> str | None:
        module = import_harness_module(
            "er2sim.task_artifacts",
            harness_root=self.harness_root,
        )
        contracts = [
            item
            for item in self.contracts(node, "consumes")
            if item.artifact_kind == artifact_kind
            and str(getattr(item, "ref_key", ref_key)) == ref_key
        ]
        contract = contracts[0] if contracts else None
        direct = node.parameters.get(ref_key)
        if isinstance(direct, str) and direct:
            artifact = self.artifact(direct)
            if contract is None:
                raise module.ArtifactContractError(
                    f"undeclared {artifact_kind} reference {direct!r} "
                    f"on {node.node_id!r}"
                )
            if artifact is not None and not artifact.get("invalidated"):
                valid, reason = self._publication_matches(
                    module,
                    artifact,
                    contract,
                    artifact_kind=artifact_kind,
                    consumer_node_id=node.node_id,
                )
                if not valid:
                    raise module.ArtifactContractError(reason)
                policy_valid, policy_reason = (
                    self._publication_policy_matches(
                        artifact_kind,
                        artifact,
                        artifact_ref=direct,
                        consumer_node=node,
                    )
                )
                if policy_valid:
                    self._record_consumption(
                        node,
                        direct,
                        artifact_kind,
                        artifact,
                    )
                    return direct
                if artifact_kind == "layout_targets":
                    raise ArtifactPolicyError(
                        artifact_kind=artifact_kind,
                        artifact_ref=direct,
                        reason=policy_reason,
                    )
                self.invalidate((direct,))

        if not contracts:
            return None
        assert contract is not None
        candidates: list[str] = []
        try:
            candidates.extend(
                self.store.runtime(
                    contract.producer_node_id
                ).output_artifacts
            )
        except KeyError:
            pass
        task = self.task()
        artifacts = getattr(task, "artifacts", {}) if task is not None else {}
        if isinstance(artifacts, Mapping):
            candidates.extend(
                str(ref)
                for ref, artifact in artifacts.items()
                if isinstance(artifact, Mapping)
                and str(
                    artifact.get("artifact_kind")
                    or artifact.get("kind")
                    or ""
                )
                == artifact_kind
                and str(
                    artifact.get("producer_node_id")
                    or artifact.get("owner_node_id")
                    or ""
                )
                == contract.producer_node_id
                and str(
                    artifact.get("continuation_node_id")
                    or contract.continuation_node_id
                )
                == node.node_id
            )

        stale_candidate: tuple[str, str] | None = None
        for ref in reversed(list(dict.fromkeys(candidates))):
            artifact = self.artifact(ref)
            if artifact is None or artifact.get("invalidated"):
                continue
            valid, _reason = self._publication_matches(
                module,
                artifact,
                contract,
                artifact_kind=artifact_kind,
                consumer_node_id=node.node_id,
            )
            if not valid:
                continue
            policy_valid, policy_reason = self._publication_policy_matches(
                artifact_kind,
                artifact,
                artifact_ref=ref,
                consumer_node=node,
            )
            if not policy_valid:
                if artifact_kind == "layout_targets":
                    stale_candidate = (ref, policy_reason)
                    continue
                self.invalidate((ref,))
                continue
            self._record_consumption(
                node,
                ref,
                artifact_kind,
                artifact,
            )
            return ref
        if stale_candidate is not None:
            ref, reason = stale_candidate
            raise ArtifactPolicyError(
                artifact_kind=artifact_kind,
                artifact_ref=ref,
                reason=reason,
            )
        if contract.required:
            raise module.ArtifactContractError(
                f"missing {artifact_kind} publication from "
                f"{contract.producer_node_id!r} for {node.node_id!r}"
            )
        return None

    def _publication_policy_matches(
        self,
        artifact_kind: str,
        artifact: Mapping[str, Any],
        *,
        artifact_ref: str | None = None,
        consumer_node: TaskNodeSpec | None = None,
    ) -> tuple[bool, str]:
        if artifact_kind == "layout_targets":
            source = str(artifact.get("source") or "")
            if source != "task_recursive_tree_batch_allocator/1.0":
                return True, ""
            artifact_status = str(
                artifact.get("reservation_status") or "active"
            ).casefold()
            if artifact_status != "active":
                return False, (
                    f"layout reservation is {artifact_status}, not active"
                )
            targets = artifact.get("targets")
            entity_ids = _layout_consumer_entity_ids(consumer_node)
            if (
                consumer_node is not None
                and _requires_explicit_layout_subject(consumer_node)
                and not entity_ids
            ):
                return False, (
                    "placement layout consumer has no placement object "
                    "binding"
                )
            if (
                consumer_node is not None
                and _requires_manipuland_layout_subject(consumer_node)
                and not entity_ids
            ):
                return False, (
                    "place_object layout consumer has no manipuland "
                    "binding"
                )
            canonical_refs = self._canonical_active_layout_refs()
            for entity_id in entity_ids:
                target = (
                    targets.get(entity_id)
                    if isinstance(targets, Mapping)
                    else None
                )
                if not isinstance(target, Mapping):
                    return False, (
                        f"layout reservation has no target for {entity_id}"
                    )
                target_status = str(
                    target.get("reservation_status") or "active"
                ).casefold()
                if target_status != "active":
                    return False, (
                        f"layout reservation for {entity_id} is "
                        f"{target_status}, not active"
                    )
                canonical_ref = canonical_refs.get(entity_id)
                if (
                    artifact_ref is not None
                    and canonical_ref is not None
                    and canonical_ref != str(artifact_ref)
                ):
                    return False, (
                        f"layout reservation for {entity_id} was "
                        f"superseded by {canonical_ref}"
                    )
            if self._region_space is None:
                self._region_space = HarnessRegionSpaceAdapter(
                    runtime=self.runtime,
                    harness_root=self.harness_root,
                )
            return self._region_space.validate_layout_targets(
                artifact,
                artifact_ref=artifact_ref,
            )

        if artifact_kind != "detour_path":
            return True, ""

        policy = DEFAULT_HARNESS_SAFETY_POLICY
        checks = (
            (
                artifact.get("collision_model_version"),
                HARNESS_SAFETY_MODEL_VERSION,
                "collision model",
            ),
            (
                artifact.get("route_validation_model_version"),
                ROUTE_VALIDATION_MODEL_VERSION,
                "route validation model",
            ),
            (
                artifact.get("route_validation_policy_fingerprint"),
                policy.fingerprint,
                "route validation policy",
            ),
        )
        for actual, expected, label in checks:
            if str(actual or "") != str(expected):
                return False, f"{label} changed"

        validation = artifact.get("route_validation")
        if not isinstance(validation, Mapping) or validation.get("ok") is not True:
            return False, "route validation evidence is missing or failed"
        if str(validation.get("safety_policy_fingerprint") or "") != (
            policy.fingerprint
        ):
            return False, "route validation evidence policy changed"

        samples = (
            (
                artifact.get("footprint_translation_sample"),
                policy.base_translation_sample,
                "translation sample",
            ),
            (
                artifact.get("footprint_rotation_sample"),
                policy.base_rotation_sample,
                "rotation sample",
            ),
            (
                validation.get("translation_sample"),
                policy.base_translation_sample,
                "validation translation sample",
            ),
            (
                validation.get("rotation_sample"),
                policy.base_rotation_sample,
                "validation rotation sample",
            ),
        )
        for raw, maximum, label in samples:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return False, f"{label} is missing or malformed"
            if value <= 0.0 or value > float(maximum) + 1e-12:
                return False, f"{label} is weaker than the active policy"

        control = import_harness_module(
            "er2sim.control",
            harness_root=self.harness_root,
        )
        expected_controller = str(
            getattr(
                control,
                "BASE_PATH_CONTROLLER_MODEL_VERSION",
                "continuous_pose_tracker/1.0",
            )
        )
        if str(
            artifact.get("base_path_controller_model_version") or ""
        ) != expected_controller:
            return False, "base path controller model changed"
        return True, ""

    def _publication_matches(
        self,
        module: Any,
        artifact: Mapping[str, Any],
        contract: Any,
        *,
        artifact_kind: str,
        consumer_node_id: str,
    ) -> tuple[bool, str]:
        valid, reason = module.publication_matches(
            artifact,
            contract,
            consumer_node_id=consumer_node_id,
        )
        if valid or artifact_kind != "layout_targets":
            return valid, reason

        kind = str(
            artifact.get("artifact_kind")
            or artifact.get("kind")
            or ""
        )
        producer = str(
            artifact.get("producer_node_id")
            or artifact.get("owner_node_id")
            or ""
        )
        scope_id = str(
            artifact.get("continuation_scope_id")
            or artifact.get("continuation_node_id")
            or ""
        )
        if kind != artifact_kind:
            return False, reason
        if producer != str(contract.producer_node_id):
            return False, reason
        if str(contract.continuation_node_id) != consumer_node_id:
            return False, reason
        if not scope_id or not self._within_subtree(
            consumer_node_id,
            scope_id,
        ):
            return False, reason
        return True, ""

    def _within_subtree(
        self,
        node_id: str,
        scope_id: str,
    ) -> bool:
        current = str(node_id)
        expected = str(scope_id)
        parents = {
            edge.child_id: edge.parent_id
            for edge in self.store.edges()
        }
        visited: set[str] = set()
        while current not in visited:
            if current == expected:
                return True
            visited.add(current)
            parent = parents.get(current)
            if parent is None:
                return False
            current = parent
        return False

    def contracts(self, node: TaskNodeSpec, direction: str) -> list[Any]:
        source = node.parameters.get(GEMINI_ER2_METADATA_KEY, {})
        metadata = (
            source.get("metadata", {})
            if isinstance(source, Mapping)
            else {}
        )
        module = import_harness_module(
            "er2sim.task_artifacts",
            harness_root=self.harness_root,
        )
        return list(module.contracts_from_metadata(metadata, direction))

    def invalidate(self, artifact_refs: list[str] | tuple[str, ...]) -> None:
        task = self.task()
        artifacts = getattr(task, "artifacts", {}) if task is not None else {}
        if not isinstance(artifacts, dict):
            return
        for ref in artifact_refs:
            artifact = artifacts.get(str(ref))
            if isinstance(artifact, dict):
                artifact["invalidated"] = True
                if artifact.get("kind") == "layout_targets":
                    artifact["reservation_status"] = "invalidated"
                    targets = artifact.get("targets")
                    if isinstance(targets, dict):
                        for target in targets.values():
                            if (
                                isinstance(target, dict)
                                and target.get("reservation_status")
                                == "active"
                            ):
                                target[
                                    "reservation_status"
                                ] = "invalidated"
                self._refresh_published_copy(str(ref), artifact)

    def pending_reconciliation_transaction_ids(self) -> tuple[str, ...]:
        reader = getattr(
            self.runtime,
            "pending_reconciliation_transaction_ids",
            None,
        )
        if not callable(reader):
            return ()
        try:
            values = reader()
        except Exception:
            return ("reconciliation_state_unavailable",)
        return tuple(
            dict.fromkeys(
                str(value)
                for value in (values or ())
                if value is not None and str(value)
            )
        )

    def fulfill_layout_reservation(
        self,
        artifact_ref: str,
        entity_id: str,
        *,
        source: str = "physical_action_succeeded",
        evidence: Mapping[str, Any] | None = None,
    ) -> bool:
        if self.pending_reconciliation_transaction_ids():
            return False
        artifact = self._mutable_artifact(artifact_ref)
        if (
            artifact is None
            or artifact.get("kind") != "layout_targets"
            or artifact.get("invalidated")
            or str(artifact.get("source") or "")
            != "task_recursive_tree_batch_allocator/1.0"
            or str(
                artifact.get("reservation_status") or "active"
            ).casefold()
            != "active"
        ):
            return False
        targets = artifact.get("targets")
        target = (
            targets.get(str(entity_id))
            if isinstance(targets, dict)
            else None
        )
        if not isinstance(target, dict):
            return False
        status = str(
            target.get("reservation_status") or "active"
        ).casefold()
        if status not in {"active", "fulfilled"}:
            return False
        if self._canonical_active_layout_refs().get(str(entity_id)) not in {
            None,
            str(artifact_ref),
        }:
            return False
        if self._region_space is None:
            self._region_space = HarnessRegionSpaceAdapter(
                runtime=self.runtime,
                harness_root=self.harness_root,
            )
        assessor = getattr(
            self._region_space,
            "assess_layout_target_fulfillment",
            None,
        )
        if not callable(assessor):
            return False
        verified, live_evidence = assessor(
            artifact,
            str(entity_id),
        )
        if not verified:
            return False
        if status == "fulfilled":
            return True
        target["reservation_status"] = "fulfilled"
        target["fulfilled_world_revision"] = getattr(
            self.runtime.world,
            "revision",
            None,
        )
        target["fulfillment_source"] = str(source)
        target["fulfillment_evidence"] = copy.deepcopy(
            dict(live_evidence)
        )
        if evidence is not None:
            target["fulfillment_action_evidence"] = copy.deepcopy(
                dict(evidence)
            )
        self._update_layout_reservation_status(artifact)
        self._refresh_published_copy(str(artifact_ref), artifact)
        return True

    def reconcile_layout_reservations(
        self,
        *,
        entity_ids: list[str] | tuple[str, ...] | None = None,
        artifact_refs: list[str] | tuple[str, ...] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Close active reservations already satisfied in the live scene."""

        if self.pending_reconciliation_transaction_ids():
            return ()
        task = self.task()
        artifacts = getattr(task, "artifacts", {}) if task is not None else {}
        if not isinstance(artifacts, dict):
            return ()
        selected_entities = (
            {
                str(value)
                for value in entity_ids
                if value is not None and str(value)
            }
            if entity_ids is not None
            else None
        )
        selected_refs = (
            tuple(str(value) for value in artifact_refs)
            if artifact_refs is not None
            else tuple(str(value) for value in artifacts)
        )
        if self._region_space is None:
            self._region_space = HarnessRegionSpaceAdapter(
                runtime=self.runtime,
                harness_root=self.harness_root,
            )

        canonical_refs = self._canonical_active_layout_refs()
        reconciled: list[dict[str, Any]] = []
        for ref in selected_refs:
            artifact = artifacts.get(ref)
            if (
                not isinstance(artifact, dict)
                or artifact.get("invalidated")
                or str(artifact.get("kind") or "") != "layout_targets"
                or str(artifact.get("source") or "")
                != "task_recursive_tree_batch_allocator/1.0"
                or str(
                    artifact.get("reservation_status") or "active"
                ).casefold()
                != "active"
            ):
                continue
            targets = artifact.get("targets")
            if not isinstance(targets, dict):
                continue
            changed = False
            for entity_id, target in targets.items():
                entity_id = str(entity_id)
                if (
                    selected_entities is not None
                    and entity_id not in selected_entities
                ):
                    continue
                if canonical_refs.get(entity_id) not in {None, ref}:
                    continue
                if (
                    not isinstance(target, dict)
                    or str(
                        target.get("reservation_status") or "active"
                    ).casefold()
                    != "active"
                ):
                    continue
                verified, evidence = (
                    self._region_space.assess_layout_target_fulfillment(
                        artifact,
                        entity_id,
                    )
                )
                if not verified:
                    continue
                target["reservation_status"] = "fulfilled"
                target["fulfilled_world_revision"] = getattr(
                    self.runtime.world,
                    "revision",
                    None,
                )
                target["fulfillment_source"] = (
                    "verified_physical_reconciliation"
                )
                target["fulfillment_evidence"] = copy.deepcopy(evidence)
                reconciled.append({
                    "artifact_ref": ref,
                    "entity_id": entity_id,
                    "status": "fulfilled",
                    "evidence": copy.deepcopy(evidence),
                })
                changed = True
            if changed:
                self._update_layout_reservation_status(artifact)
                self._refresh_published_copy(ref, artifact)
        return tuple(reconciled)

    def release_layout_reservations_for_entities(
        self,
        entity_ids: list[str] | tuple[str, ...],
        *,
        reason: str,
        exclude_artifact_refs: list[str] | tuple[str, ...] = (),
        replacement_artifact_ref: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Release obsolete active targets for entities being replanned."""

        selected_entities = {
            str(value)
            for value in entity_ids
            if value is not None and str(value)
        }
        if not selected_entities:
            return ()
        excluded = {
            str(value) for value in exclude_artifact_refs if str(value)
        }
        task = self.task()
        artifacts = getattr(task, "artifacts", {}) if task is not None else {}
        if not isinstance(artifacts, dict):
            return ()

        released: list[dict[str, Any]] = []
        revision = getattr(self.runtime.world, "revision", None)
        for raw_ref, artifact in artifacts.items():
            ref = str(raw_ref)
            if (
                ref in excluded
                or not isinstance(artifact, dict)
                or artifact.get("invalidated")
                or str(artifact.get("kind") or "") != "layout_targets"
                or str(artifact.get("source") or "")
                != "task_recursive_tree_batch_allocator/1.0"
            ):
                continue
            targets = artifact.get("targets")
            if not isinstance(targets, dict):
                continue
            changed = False
            for entity_id, target in targets.items():
                entity_id = str(entity_id)
                if (
                    entity_id not in selected_entities
                    or not isinstance(target, dict)
                    or str(
                        target.get("reservation_status") or "active"
                    ).casefold()
                    != "active"
                ):
                    continue
                target["reservation_status"] = "released"
                target["reservation_release_reason"] = str(reason)
                target["reservation_released_world_revision"] = revision
                if replacement_artifact_ref is not None:
                    target["superseded_by_artifact_ref"] = str(
                        replacement_artifact_ref
                    )
                released.append({
                    "artifact_ref": ref,
                    "entity_id": entity_id,
                    "status": "released",
                    "reason": str(reason),
                    **(
                        {
                            "replacement_artifact_ref": str(
                                replacement_artifact_ref
                            )
                        }
                        if replacement_artifact_ref is not None
                        else {}
                    ),
                })
                changed = True
            if changed:
                artifact["reservation_release_reason"] = str(reason)
                artifact[
                    "reservation_released_world_revision"
                ] = revision
                if replacement_artifact_ref is not None:
                    artifact["superseded_by_artifact_ref"] = str(
                        replacement_artifact_ref
                    )
                self._update_layout_reservation_status(artifact)
                self._refresh_published_copy(ref, artifact)
        return tuple(released)

    def release_layout_reservations(
        self,
        artifact_refs: list[str] | tuple[str, ...] | None = None,
        *,
        reason: str,
    ) -> tuple[str, ...]:
        task = self.task()
        artifacts = getattr(task, "artifacts", {}) if task is not None else {}
        if not isinstance(artifacts, dict):
            return ()
        selected = (
            tuple(str(value) for value in artifact_refs)
            if artifact_refs is not None
            else tuple(str(value) for value in artifacts)
        )
        released: list[str] = []
        for ref in selected:
            artifact = artifacts.get(ref)
            if (
                not isinstance(artifact, dict)
                or artifact.get("kind") != "layout_targets"
                or artifact.get("invalidated")
                or str(artifact.get("source") or "")
                != "task_recursive_tree_batch_allocator/1.0"
            ):
                continue
            targets = artifact.get("targets")
            changed = False
            if isinstance(targets, dict):
                for target in targets.values():
                    if (
                        isinstance(target, dict)
                        and str(
                            target.get("reservation_status") or "active"
                        )
                        == "active"
                    ):
                        target["reservation_status"] = "released"
                        target["reservation_release_reason"] = str(reason)
                        target[
                            "reservation_released_world_revision"
                        ] = getattr(
                            self.runtime.world,
                            "revision",
                            None,
                        )
                        changed = True
            if not changed:
                continue
            artifact["reservation_release_reason"] = str(reason)
            artifact["reservation_released_world_revision"] = getattr(
                self.runtime.world,
                "revision",
                None,
            )
            self._update_layout_reservation_status(artifact)
            self._refresh_published_copy(ref, artifact)
            released.append(ref)
        return tuple(released)

    def is_layout_target(self, artifact_ref: str) -> bool:
        artifact = self.artifact(artifact_ref)
        return (
            isinstance(artifact, dict)
            and str(
                artifact.get("artifact_kind")
                or artifact.get("kind")
                or ""
            )
            == "layout_targets"
        )

    def published(self, node_id: str) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(item)
            for item in self._published.get(node_id, {}).values()
        ]

    def consumed(self, node_id: str) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(item)
            for item in self._consumed.get(node_id, {}).values()
        ]

    def _record_consumption(
        self,
        node: TaskNodeSpec,
        artifact_ref: str,
        artifact_kind: str,
        artifact: Mapping[str, Any],
    ) -> None:
        self._consumed.setdefault(node.node_id, {})[
            artifact_ref
        ] = {
            "artifact_ref": artifact_ref,
            "artifact_kind": artifact_kind,
            "producer_node_id": artifact.get("producer_node_id"),
            "continuation_node_id": node.node_id,
            "world_revision": getattr(self.runtime.world, "revision", None),
        }

    def _mutable_artifact(
        self,
        artifact_ref: str,
    ) -> dict[str, Any] | None:
        task = self.task()
        artifacts = getattr(task, "artifacts", {}) if task is not None else {}
        if not isinstance(artifacts, dict):
            return None
        artifact = artifacts.get(str(artifact_ref))
        return artifact if isinstance(artifact, dict) else None

    def _refresh_published_copy(
        self,
        artifact_ref: str,
        artifact: Mapping[str, Any],
    ) -> None:
        for publications in self._published.values():
            if artifact_ref in publications:
                publications[artifact_ref] = copy.deepcopy(dict(artifact))

    def _canonical_active_layout_refs(self) -> dict[str, str]:
        task = self.task()
        artifacts = getattr(task, "artifacts", {}) if task is not None else {}
        if not isinstance(artifacts, Mapping):
            return {}
        canonical: dict[str, str] = {}
        for raw_ref, artifact in artifacts.items():
            if (
                not isinstance(artifact, Mapping)
                or artifact.get("invalidated")
                or str(artifact.get("kind") or "") != "layout_targets"
                or str(artifact.get("source") or "")
                != "task_recursive_tree_batch_allocator/1.0"
                or str(
                    artifact.get("reservation_status") or "active"
                ).casefold()
                != "active"
            ):
                continue
            targets = artifact.get("targets")
            if not isinstance(targets, Mapping):
                continue
            for entity_id, target in targets.items():
                if (
                    isinstance(target, Mapping)
                    and str(
                        target.get("reservation_status") or "active"
                    ).casefold()
                    == "active"
                ):
                    canonical[str(entity_id)] = str(raw_ref)
        return canonical

    @staticmethod
    def _update_layout_reservation_status(
        artifact: dict[str, Any],
    ) -> None:
        _update_layout_reservation_status_payload(artifact)


def _is_managed_layout_publication(
    publication: Mapping[str, Any],
) -> bool:
    return (
        str(
            publication.get("artifact_kind")
            or publication.get("kind")
            or ""
        )
        == "layout_targets"
        and str(publication.get("source") or "")
        == "task_recursive_tree_batch_allocator/1.0"
    )


def _merge_same_ref_layout(
    existing: Any,
    publication: Mapping[str, Any],
) -> dict[str, Any]:
    merged = copy.deepcopy(dict(publication))
    reason = _layout_publication_structure_reason(merged)
    if reason:
        raise ValueError(reason)
    if existing is None:
        merged["layout_generation"] = max(
            1,
            _integer_value(merged.get("layout_generation"), default=1),
        )
        return merged
    if not isinstance(existing, Mapping):
        raise ValueError(
            "managed layout artifact_ref conflicts with a non-artifact value"
        )
    if not _is_managed_layout_publication(existing):
        raise ValueError(
            "managed layout artifact_ref conflicts with an incompatible "
            "existing artifact"
        )
    existing_reason = _layout_publication_structure_reason(existing)
    if existing_reason:
        raise ValueError(
            f"existing managed layout cannot be patched: {existing_reason}"
        )

    existing_targets = copy.deepcopy(dict(existing["targets"]))
    replacement_targets = copy.deepcopy(dict(merged["targets"]))
    retained_ids = tuple(
        entity_id
        for entity_id in existing_targets
        if entity_id not in replacement_targets
    )
    if retained_ids:
        for key in ("anchor_id", "region_ref", "reservation_group"):
            if str(existing.get(key) or "") != str(merged.get(key) or ""):
                raise ValueError(
                    "partial same-ref layout replacement changed "
                    f"{key}; retained targets would lose their frame"
                )
    existing_targets.update(replacement_targets)
    existing_order = [
        str(value) for value in existing.get("subject_ids", ())
    ]
    replacement_order = [
        str(value) for value in merged.get("subject_ids", ())
    ]
    merged["targets"] = existing_targets
    merged["subject_ids"] = list(
        dict.fromkeys(
            [
                *existing_order,
                *replacement_order,
                *[str(value) for value in existing_targets],
            ]
        )
    )
    merged["replanned_subject_ids"] = replacement_order
    merged["layout_generation"] = max(
        1,
        _integer_value(existing.get("layout_generation"), default=1) + 1,
    )
    _update_layout_reservation_status_payload(merged)
    return merged


def _layout_publication_structure_reason(
    publication: Mapping[str, Any],
) -> str:
    if str(publication.get("kind") or "") != "layout_targets":
        return "managed layout publication has the wrong kind"
    for key in (
        "artifact_ref",
        "anchor_id",
        "region_ref",
        "reservation_group",
    ):
        if not str(publication.get(key) or ""):
            return f"managed layout publication is missing {key}"
    raw_subjects = publication.get("subject_ids")
    if not isinstance(raw_subjects, (list, tuple)) or not raw_subjects:
        return "managed layout publication has no subject set"
    subject_ids = tuple(
        dict.fromkeys(str(value) for value in raw_subjects if str(value))
    )
    if not subject_ids:
        return "managed layout publication has no subject set"
    targets = publication.get("targets")
    if not isinstance(targets, Mapping) or not targets:
        return "managed layout publication has no target map"
    target_ids = {str(value) for value in targets}
    if target_ids != set(subject_ids):
        return "managed layout publication subject and target sets differ"
    for entity_id in subject_ids:
        target = targets.get(entity_id)
        if not isinstance(target, Mapping):
            return f"managed layout target for {entity_id} is malformed"
        local_xy = target.get("local_xy")
        if not isinstance(local_xy, (list, tuple)) or len(local_xy) != 2:
            return (
                f"managed layout target for {entity_id} has no local_xy"
            )
    return ""


def _stage_superseded_layouts(
    artifacts: Mapping[str, Any],
    entity_ids: tuple[str, ...],
    *,
    replacement_artifact_ref: str,
    revision: Any,
) -> tuple[dict[str, dict[str, Any]], tuple[dict[str, Any], ...]]:
    selected = set(entity_ids)
    if not selected:
        return {}, ()
    reason = (
        f"superseded_by_layout_artifact:{replacement_artifact_ref}"
    )
    staged: dict[str, dict[str, Any]] = {}
    released: list[dict[str, Any]] = []
    for raw_ref, artifact in artifacts.items():
        ref = str(raw_ref)
        if (
            ref == replacement_artifact_ref
            or not isinstance(artifact, Mapping)
            or artifact.get("invalidated")
            or not _is_managed_layout_publication(artifact)
        ):
            continue
        targets = artifact.get("targets")
        if not isinstance(targets, Mapping):
            continue
        candidate = copy.deepcopy(dict(artifact))
        candidate_targets = candidate.get("targets")
        if not isinstance(candidate_targets, dict):
            continue
        changed = False
        for entity_id, target in candidate_targets.items():
            if (
                str(entity_id) not in selected
                or not isinstance(target, dict)
                or str(
                    target.get("reservation_status") or "active"
                ).casefold()
                != "active"
            ):
                continue
            target["reservation_status"] = "released"
            target["reservation_release_reason"] = reason
            target["reservation_released_world_revision"] = revision
            target["superseded_by_artifact_ref"] = (
                replacement_artifact_ref
            )
            released.append(
                {
                    "artifact_ref": ref,
                    "entity_id": str(entity_id),
                    "status": "released",
                    "reason": reason,
                    "replacement_artifact_ref": (
                        replacement_artifact_ref
                    ),
                }
            )
            changed = True
        if not changed:
            continue
        candidate["reservation_release_reason"] = reason
        candidate["reservation_released_world_revision"] = revision
        candidate["superseded_by_artifact_ref"] = (
            replacement_artifact_ref
        )
        _update_layout_reservation_status_payload(candidate)
        staged[ref] = candidate
    return staged, tuple(released)


def _commit_artifact_payload(
    artifacts: dict[str, Any],
    artifact_ref: str,
    payload: Mapping[str, Any],
) -> None:
    current = artifacts.get(str(artifact_ref))
    replacement = copy.deepcopy(dict(payload))
    if isinstance(current, dict):
        current.clear()
        current.update(replacement)
    else:
        artifacts[str(artifact_ref)] = replacement


def _record_publication_result(
    result: dict[str, Any] | None,
    artifact_ref: str,
    publication: Mapping[str, Any],
) -> None:
    if result is None:
        return
    result["artifact_ref"] = artifact_ref
    result["artifact_kind"] = publication.get("artifact_kind")
    result["publication"] = {
        key: publication.get(key)
        for key in (
            "artifact_ref",
            "artifact_kind",
            "producer_node_id",
            "publisher_node_id",
            "continuation_node_id",
        )
        if publication.get(key) is not None
    }


def _update_layout_reservation_status_payload(
    artifact: dict[str, Any],
) -> None:
    targets = artifact.get("targets")
    if not isinstance(targets, Mapping):
        return
    statuses = {
        str(value.get("reservation_status") or "active").casefold()
        for value in targets.values()
        if isinstance(value, Mapping)
    }
    if not statuses:
        return
    if "active" in statuses:
        artifact["reservation_status"] = "active"
    elif statuses <= {"fulfilled"}:
        artifact["reservation_status"] = "fulfilled"
    elif statuses <= {"invalidated"}:
        artifact["reservation_status"] = "invalidated"
    else:
        artifact["reservation_status"] = "released"


def _integer_value(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _requires_explicit_layout_subject(node: TaskNodeSpec) -> bool:
    params = node.parameters
    return (
        str(params.get("purpose") or "").casefold()
        == "placement_execution_preparation"
        or str(params.get("interaction_target_kind") or "").casefold()
        == "placement_pose"
    )


def _requires_manipuland_layout_subject(node: TaskNodeSpec) -> bool:
    return str(node.task_type or "").casefold() == "place_object"


def _layout_consumer_entity_ids(
    node: TaskNodeSpec | None,
) -> tuple[str, ...]:
    if node is None:
        return ()
    params = node.parameters
    participants = params.get("participants")
    placement_values: list[str] = []
    if isinstance(participants, Mapping):
        _extend_entity_ids(
            placement_values,
            participants.get("placement_object"),
        )
    for key in (
        "placement_object",
        "placement_object_id",
        "placement_object_ids",
    ):
        _extend_entity_ids(placement_values, params.get(key))
    if placement_values:
        return tuple(dict.fromkeys(placement_values))
    if _requires_explicit_layout_subject(node):
        return ()

    values: list[str] = []
    if isinstance(participants, Mapping):
        for role in ("manipuland", "object", "subject"):
            _extend_entity_ids(values, participants.get(role))
    for key in ("object_id", "object_ids"):
        _extend_entity_ids(values, params.get(key))
    if values:
        return tuple(
            dict.fromkeys(value for value in values if str(value))
        )
    _extend_entity_ids(values, params.get("batch_object_ids"))
    return tuple(
        dict.fromkeys(value for value in values if str(value))
    )


def _extend_entity_ids(values: list[str], raw: Any) -> None:
    if isinstance(raw, Mapping):
        raw = raw.get("entity_ids") or raw.get("entity_id")
    if isinstance(raw, str):
        if raw:
            values.append(raw)
        return
    if isinstance(raw, (list, tuple, set, frozenset)):
        values.extend(str(value) for value in raw if str(value))


__all__ = ["ArtifactPolicyError", "HarnessArtifactBridge"]
