"""GeminiER2Harness integration backed by TaskTreeKernel."""

from .compiler import KernelCompiledTree, KernelCompilerBridge
from .continuous_recovery import adapt_continuous_session_class
from .executor import KernelExecutorBridge
from .interaction_routes import (
    HarnessInteractionRoutePlanner,
    InteractionRouteFailure,
    InteractionRoutePlan,
)
from .operations import HarnessSystemOperationAdapter
from .physical_runtime import HarnessPhysicalGateway
from .planner import HarnessTaskProgramPlannerAdapter
from .program_normalization import (
    ProgramNormalizationError,
    ProgramNormalizationResult,
    normalize_model_task_program,
)
from .repair import HarnessRepairResolver
from .skill import HarnessMacroActionSkill, SUPPORTED_ACTIONS
from .translation import (
    GEMINI_ER2_METADATA_KEY,
    HarnessTreeTranslationError,
    translate_harness_subtree,
    translate_harness_tree,
)
from .tree_view import KernelTreeProjection, to_harness_status

__all__ = [
    "GEMINI_ER2_METADATA_KEY",
    "HarnessMacroActionSkill",
    "HarnessInteractionRoutePlanner",
    "HarnessPhysicalGateway",
    "HarnessRepairResolver",
    "HarnessSystemOperationAdapter",
    "HarnessTaskProgramPlannerAdapter",
    "HarnessTreeTranslationError",
    "KernelCompiledTree",
    "KernelCompilerBridge",
    "KernelExecutorBridge",
    "KernelTreeProjection",
    "InteractionRouteFailure",
    "InteractionRoutePlan",
    "ProgramNormalizationError",
    "ProgramNormalizationResult",
    "SUPPORTED_ACTIONS",
    "adapt_continuous_session_class",
    "normalize_model_task_program",
    "to_harness_status",
    "translate_harness_subtree",
    "translate_harness_tree",
]
