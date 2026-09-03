from __future__ import annotations

import importlib
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Mapping

from task_recursive_tree.task.compiler import TaskTreeDefinition

from .paths import import_harness_module
from .placement_coordination import coordinate_sequence_placement_layouts
from .program_normalization import normalize_model_task_program


CompilerFactory = Callable[..., Any]
TreeTranslator = Callable[[Any], TaskTreeDefinition]


@dataclass(frozen=True)
class KernelCompiledTree:
    """Read-only compile-time view consumed by ContinuousTaskSession."""

    definition: TaskTreeDefinition
    task_id: str | None = None
    program_ref: str | None = None
    world_revision: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )

    @property
    def root_id(self) -> str:
        return self.definition.root_id

    @property
    def nodes(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {
                command.spec.node_id: command.spec
                for command in self.definition.delta.nodes
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "kernel_task_tree_definition/1.0",
            "root_id": self.root_id,
            "task_id": self.task_id,
            "program_ref": self.program_ref,
            "world_revision": self.world_revision,
            "nodes": {
                node_id: {"spec": _jsonable(spec)}
                for node_id, spec in self.nodes.items()
            },
            "edges": [
                _jsonable(command.edge)
                for command in self.definition.delta.edges
            ],
            "execution_stack": [],
            "metadata": _jsonable(self.metadata),
        }


@dataclass(frozen=True)
class KernelCompilationResult:
    program: Any
    task: Any
    tree: KernelCompiledTree
    collection_snapshots: Mapping[str, Any] = field(default_factory=dict)
    binding_artifacts: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "collection_snapshots",
            MappingProxyType(dict(self.collection_snapshots)),
        )
        object.__setattr__(
            self,
            "binding_artifacts",
            MappingProxyType(dict(self.binding_artifacts)),
        )
        object.__setattr__(self, "warnings", tuple(self.warnings))


class KernelCompilerBridge:
    """Use Harness grounding, then discard its executable tree representation."""

    def __init__(
        self,
        *,
        runtime: Any,
        harness_root: str | None = None,
        compiler_factory: CompilerFactory | None = None,
        tree_translator: TreeTranslator | None = None,
    ) -> None:
        self.runtime = runtime
        self.harness_root = harness_root
        self._compiler_factory = compiler_factory
        self._tree_translator = tree_translator

    def compile(
        self,
        program: Any,
        *,
        task_id: str,
        instruction: str,
    ) -> KernelCompilationResult:
        compiler_factory = (
            self._compiler_factory or self._load_compiler_factory()
        )
        harness_compiler = compiler_factory(runtime=self.runtime)
        normalization = normalize_model_task_program(program)
        compiled = harness_compiler.compile(
            normalization.program,
            task_id=task_id,
            instruction=instruction,
            commit=True,
        )

        translator = self._tree_translator or self._load_tree_translator()
        definition = translator(compiled.tree)
        if not isinstance(definition, TaskTreeDefinition):
            raise TypeError(
                "translate_harness_tree must return TaskTreeDefinition"
            )
        definition = coordinate_sequence_placement_layouts(definition)

        source_tree = compiled.tree
        tree = KernelCompiledTree(
            definition=definition,
            task_id=getattr(compiled.task, "id", task_id),
            program_ref=getattr(source_tree, "program_ref", None),
            world_revision=getattr(source_tree, "world_revision", None),
            metadata={
                "compiler_bridge": "gemini_er2/1.0",
                "source_tree_schema": (
                    getattr(source_tree, "metadata", {}) or {}
                ).get("schema"),
                "program_normalizations": list(
                    normalization.changed_paths
                ),
            },
        )
        return KernelCompilationResult(
            program=getattr(
                compiled,
                "program",
                normalization.program,
            ),
            task=compiled.task,
            tree=tree,
            collection_snapshots=getattr(
                compiled, "collection_snapshots", {}
            ),
            binding_artifacts=getattr(
                compiled, "binding_artifacts", {}
            ),
            warnings=(
                *normalization.warnings,
                *tuple(getattr(compiled, "warnings", ()) or ()),
            ),
        )

    def _load_compiler_factory(self) -> CompilerFactory:
        module = import_harness_module(
            "er2sim.task_compiler",
            harness_root=self.harness_root,
        )
        return module.TaskCompiler

    @staticmethod
    def _load_tree_translator() -> TreeTranslator:
        module = importlib.import_module(
            "task_recursive_tree.integrations.gemini_er2.translation"
        )
        return module.translate_harness_tree


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            item.name: _jsonable(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)
