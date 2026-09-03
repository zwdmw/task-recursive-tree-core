from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


NORMALIZATION_METADATA_KEY = "task_recursive_tree_normalizations"
PROGRAM_NORMALIZATION_MODEL_VERSION = (
    "gemini_er2_task_program_normalization/1.0"
)

_UNIVERSAL_QUANTIFIERS = frozenset(
    {
        "all",
        "all_matching",
        "each",
        "every",
        "universal",
        "\u5168\u90e8",
        "\u6240\u6709",
        "\u6bcf\u4e2a",
        "\u6bcf\u4e00",
        "\u90fd",
    }
)
_VALID_AGGREGATES = frozenset(
    {"all", "any", "at_least_n", "exactly_n"}
)


class ProgramNormalizationError(ValueError):
    """Raised when a quantifier cannot be represented without ambiguity."""


@dataclass(frozen=True)
class ProgramNormalizationResult:
    program: Any
    changed_paths: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    applied: bool = False


def normalize_model_task_program(
    program: Any,
) -> ProgramNormalizationResult:
    """Canonicalize quantifiers before Harness validates or grounds the IR.

    Grounded model output expands universal instructions into one concrete
    task per selected entity. A redundant universal marker on those concrete
    source selections is safe to remove. Set-valued, destination, conflicting,
    and unknown quantifiers remain explicit errors.
    """

    raw = _program_dict(program)
    if raw is None:
        return ProgramNormalizationResult(program=program)

    normalized = copy.deepcopy(raw)
    changed_paths: list[str] = []
    _normalize_node(
        normalized.get("root"),
        path="root",
        verified_grounded_model=_is_verified_grounded_model(normalized),
        changed_paths=changed_paths,
    )
    if changed_paths:
        _record_normalization(normalized, changed_paths)

    audit_paths = _recorded_paths(normalized)
    if not changed_paths:
        return ProgramNormalizationResult(
            program=program,
            changed_paths=audit_paths,
            warnings=_warnings(audit_paths),
            applied=False,
        )

    return ProgramNormalizationResult(
        program=_rebuild_program(program, normalized),
        changed_paths=audit_paths,
        warnings=_warnings(audit_paths),
        applied=True,
    )


def _normalize_node(
    node: Any,
    *,
    path: str,
    verified_grounded_model: bool,
    changed_paths: list[str],
) -> None:
    if not isinstance(node, dict):
        return

    kind = str(node.get("kind") or "")
    if kind == "task":
        for role in ("object", "source"):
            _normalize_task_source(
                node.get(role),
                path=f"{path}.{role}",
                verified_grounded_model=verified_grounded_model,
                changed_paths=changed_paths,
            )
        _reject_query_quantifiers(
            node.get("destination"),
            path=f"{path}.destination",
            reason=(
                "destination quantifiers need explicit broadcast or pairing "
                "semantics"
            ),
        )
        return

    if kind in {"sequence", "parallel"}:
        for index, child in enumerate(node.get("steps") or ()):
            _normalize_node(
                child,
                path=f"{path}.steps[{index}]",
                verified_grounded_model=verified_grounded_model,
                changed_paths=changed_paths,
            )
        return

    if kind == "for_each":
        aggregate = _normalize_aggregate(
            node,
            path=f"{path}.aggregate",
            changed_paths=changed_paths,
        )
        _normalize_collection(
            node.get("collection"),
            path=f"{path}.collection",
            aggregate=aggregate,
            changed_paths=changed_paths,
        )
        _normalize_node(
            node.get("body"),
            path=f"{path}.body",
            verified_grounded_model=verified_grounded_model,
            changed_paths=changed_paths,
        )
        return

    if kind == "selector":
        for index, child in enumerate(node.get("candidates") or ()):
            _normalize_node(
                child,
                path=f"{path}.candidates[{index}]",
                verified_grounded_model=verified_grounded_model,
                changed_paths=changed_paths,
            )
        return

    if kind == "condition":
        _normalize_node(
            node.get("then_branch"),
            path=f"{path}.then_branch",
            verified_grounded_model=verified_grounded_model,
            changed_paths=changed_paths,
        )
        _normalize_node(
            node.get("else_branch"),
            path=f"{path}.else_branch",
            verified_grounded_model=verified_grounded_model,
            changed_paths=changed_paths,
        )
        return

    if kind == "ensure":
        _normalize_node(
            node.get("body"),
            path=f"{path}.body",
            verified_grounded_model=verified_grounded_model,
            changed_paths=changed_paths,
        )
        _normalize_node(
            node.get("repair_program"),
            path=f"{path}.repair_program",
            verified_grounded_model=verified_grounded_model,
            changed_paths=changed_paths,
        )


def _normalize_task_source(
    query: Any,
    *,
    path: str,
    verified_grounded_model: bool,
    changed_paths: list[str],
) -> None:
    if not isinstance(query, dict):
        return
    scopes = _query_quantifier_scopes(query, path=path)
    if not scopes:
        _reject_unhandled_quantifier(query, path=path)
        return

    for scope, quantifier_path in scopes:
        value = scope.get("quantifier")
        if not _is_universal_quantifier(value):
            raise ProgramNormalizationError(
                f"{quantifier_path}: unsupported quantifier {value!r}"
            )
    if not verified_grounded_model:
        raise ProgramNormalizationError(
            f"{scopes[0][1]}: a universal task source must use for_each "
            "unless trusted grounded-model coverage was verified"
        )
    if not _is_concrete_query(query):
        raise ProgramNormalizationError(
            f"{scopes[0][1]}: a non-concrete universal task source must "
            "use for_each"
        )

    for scope, quantifier_path in scopes:
        scope.pop("quantifier")
        changed_paths.append(quantifier_path)
        _remove_empty_scope(query, scope)
    _reject_unhandled_quantifier(query, path=path)


def _normalize_collection(
    query: Any,
    *,
    path: str,
    aggregate: str,
    changed_paths: list[str],
) -> None:
    if not isinstance(query, dict):
        return
    scopes = _query_quantifier_scopes(query, path=path)
    for scope, quantifier_path in scopes:
        value = scope.get("quantifier")
        if not _is_universal_quantifier(value):
            raise ProgramNormalizationError(
                f"{quantifier_path}: unsupported collection quantifier "
                f"{value!r}"
            )
        if aggregate != "all":
            raise ProgramNormalizationError(
                f"{quantifier_path}: universal collection quantifier "
                f"conflicts with aggregate={aggregate!r}"
            )
        scope.pop("quantifier")
        changed_paths.append(quantifier_path)
        _remove_empty_scope(query, scope)
    _reject_unhandled_quantifier(query, path=path)


def _normalize_aggregate(
    node: dict[str, Any],
    *,
    path: str,
    changed_paths: list[str],
) -> str:
    raw = node.get("aggregate", "all")
    if not isinstance(raw, str):
        raise ProgramNormalizationError(
            f"{path}: aggregate must be a string"
        )
    normalized = _normalize_token(raw)
    if normalized in _UNIVERSAL_QUANTIFIERS:
        canonical = "all"
    elif normalized in _VALID_AGGREGATES:
        canonical = normalized
    else:
        raise ProgramNormalizationError(
            f"{path}: unsupported aggregate {raw!r}"
        )
    if raw != canonical:
        node["aggregate"] = canonical
        changed_paths.append(path)
    return canonical


def _query_quantifier_scopes(
    query: dict[str, Any],
    *,
    path: str,
) -> list[tuple[dict[str, Any], str]]:
    scopes: list[tuple[dict[str, Any], str]] = []
    _reject_misplaced_quantifier(query, path=path)
    scope = query.get("scope")
    if isinstance(scope, dict) and "quantifier" in scope:
        scopes.append((scope, f"{path}.scope.quantifier"))

    basis = query.get("selection_basis")
    if isinstance(basis, dict):
        _reject_misplaced_quantifier(
            basis,
            path=f"{path}.selection_basis",
        )
        basis_scope = basis.get("scope")
        if (
            isinstance(basis_scope, dict)
            and "quantifier" in basis_scope
        ):
            scopes.append(
                (
                    basis_scope,
                    f"{path}.selection_basis.scope.quantifier",
                )
            )
    return scopes


def _reject_misplaced_quantifier(
    value: Mapping[str, Any],
    *,
    path: str,
) -> None:
    if "quantifier" in value:
        raise ProgramNormalizationError(
            f"{path}.quantifier: quantifier must not be a query field"
        )


def _reject_query_quantifiers(
    query: Any,
    *,
    path: str,
    reason: str,
) -> None:
    quantifier_path = _find_quantifier_path(query, path=path)
    if quantifier_path is not None:
        raise ProgramNormalizationError(
            f"{quantifier_path}: {reason}"
        )


def _reject_unhandled_quantifier(
    query: Any,
    *,
    path: str,
) -> None:
    quantifier_path = _find_quantifier_path(query, path=path)
    if quantifier_path is not None:
        raise ProgramNormalizationError(
            f"{quantifier_path}: unsupported quantifier placement"
        )


def _find_quantifier_path(
    value: Any,
    *,
    path: str,
) -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key) == "quantifier":
                return child_path
            nested = _find_quantifier_path(child, path=child_path)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for index, child in enumerate(value):
            nested = _find_quantifier_path(
                child,
                path=f"{path}[{index}]",
            )
            if nested is not None:
                return nested
    return None


def _remove_empty_scope(
    query: dict[str, Any],
    scope: dict[str, Any],
) -> None:
    if scope:
        return
    if query.get("scope") is scope:
        query.pop("scope", None)
        return
    basis = query.get("selection_basis")
    if isinstance(basis, dict) and basis.get("scope") is scope:
        basis.pop("scope", None)


def _record_normalization(
    raw: dict[str, Any],
    changed_paths: list[str],
) -> None:
    metadata = raw.get("translation_metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        raw["translation_metadata"] = metadata
    records = metadata.get(NORMALIZATION_METADATA_KEY)
    if not isinstance(records, list):
        records = []
        metadata[NORMALIZATION_METADATA_KEY] = records

    unique_paths = list(dict.fromkeys(changed_paths))
    for record in records:
        if (
            isinstance(record, dict)
            and record.get("model_version")
            == PROGRAM_NORMALIZATION_MODEL_VERSION
        ):
            record["paths"] = list(
                dict.fromkeys(
                    [
                        *list(record.get("paths") or ()),
                        *unique_paths,
                    ]
                )
            )
            return
    records.append(
        {
            "model_version": PROGRAM_NORMALIZATION_MODEL_VERSION,
            "kind": "quantifier_canonicalization",
            "paths": unique_paths,
        }
    )


def _recorded_paths(raw: Mapping[str, Any]) -> tuple[str, ...]:
    metadata = raw.get("translation_metadata")
    records = (
        metadata.get(NORMALIZATION_METADATA_KEY)
        if isinstance(metadata, Mapping)
        else None
    )
    paths: list[str] = []
    if isinstance(records, list):
        for record in records:
            if (
                isinstance(record, Mapping)
                and record.get("model_version")
                == PROGRAM_NORMALIZATION_MODEL_VERSION
            ):
                paths.extend(
                    str(path)
                    for path in (record.get("paths") or ())
                    if path is not None
                )
    return tuple(dict.fromkeys(paths))


def _warnings(paths: tuple[str, ...]) -> tuple[str, ...]:
    if not paths:
        return ()
    return (
        "Canonicalized task-program quantifiers at "
        + ", ".join(paths),
    )


def _is_concrete_query(value: Any) -> bool:
    return isinstance(value, dict) and any(
        value.get(key) is not None
        for key in ("entity_ref", "region_ref")
    )


def _is_verified_grounded_model(raw: Mapping[str, Any]) -> bool:
    metadata = raw.get("translation_metadata")
    if not isinstance(metadata, Mapping):
        return False
    if metadata.get("semantic_constraints_verified") is not True:
        return False
    translator = str(metadata.get("translator") or "").casefold()
    source = str(metadata.get("source") or "").casefold()
    return (
        translator.startswith("grounded_model_task_program/")
        or source == "native_model_translator"
    )


def _is_universal_quantifier(value: Any) -> bool:
    return isinstance(value, str) and (
        _normalize_token(value) in _UNIVERSAL_QUANTIFIERS
    )


def _normalize_token(value: str) -> str:
    return value.strip().casefold().replace("-", "_").replace(" ", "_")


def _program_dict(program: Any) -> dict[str, Any] | None:
    if isinstance(program, Mapping):
        return copy.deepcopy(dict(program))
    to_dict = getattr(program, "to_dict", None)
    if not callable(to_dict):
        return None
    raw = to_dict()
    if not isinstance(raw, Mapping):
        return None
    return copy.deepcopy(dict(raw))


def _rebuild_program(program: Any, raw: dict[str, Any]) -> Any:
    if isinstance(program, Mapping):
        return raw
    from_dict = getattr(type(program), "from_dict", None)
    if callable(from_dict):
        return from_dict(raw)
    return raw


__all__ = [
    "NORMALIZATION_METADATA_KEY",
    "PROGRAM_NORMALIZATION_MODEL_VERSION",
    "ProgramNormalizationError",
    "ProgramNormalizationResult",
    "normalize_model_task_program",
]
