# Task Recursive Tree

A runnable reference architecture for mobile-manipulation tasks. It separates
task decisions, spatial grounding, planning, physical execution, observation,
and verification so that recovery remains explicit in the task tree.

## GeminiER2 Runtime

The default Windows launcher now runs the GeminiER2 MuJoCo experience through
the new kernel:

```text
Harness natural-language planner
  -> HarnessTaskProgramPlannerAdapter
       -> canonical TaskProgram artifact
  -> KernelCompilerBridge
  -> TaskTreeStore
  -> TaskTreeKernel
  -> GeminiER2 system operations / physical gateway
  -> MuJoCo
```

`TaskTreeStore` is the only live tree state. Harness task trees are accepted as
compile-time or recovery DTOs and are translated before execution. The Harness
runtime remains responsible for the world model, perception, tools, physical
transactions, MuJoCo, recording, and the browser console.

The main executor factory and the continuous-session recovery executor factory
both use `KernelExecutorBridge`. The legacy Harness executor is not
instantiated or called.

## Run

The GeminiER2 integration requires Python 3.11 or newer plus a working
GeminiER2Harness installation. The Windows launcher selects the first Python
from this project's `.venv`, `D:\GeminiER2Harness\.venv`, or the system
launcher that also provides Faster-Whisper and OpenCC.

On Windows, double-click `启动任务递归树.cmd`. It starts a persistent local
server, opens the browser control console, and enables local realtime voice
control by default. Click `Start Listening` once to grant microphone access;
the browser prefers a Realtek input when available. Spoken tasks are
transcribed locally with Faster-Whisper, while stop, pause, continue,
clarification, text input, and scene controls use the same persistent session.
Each submitted task gets a fresh explicit tree while the observed world,
robot backend, and transaction ledger continue from their current state.

```powershell
.\启动任务递归树.cmd
```

The console defaults to `http://127.0.0.1:8766/`. The launcher cleans up its
previous server instance, forwards server arguments, and accepts options such
as `--no-browser`, `--port`, `--out`, `--scene`, `--scene-seed`,
`--template-seed`, `--voice-model`, and `--harness-root`.

Run the persistent server without the launcher:

```powershell
$env:PYTHONPATH = "src"
python -m task_recursive_tree.integrations.gemini_er2.server --no-browser
```

Run the original one-shot CLI demo:

```powershell
$env:PYTHONPATH = "src"
python -m task_recursive_tree --tree-output .artifacts\demo.json
python -m task_recursive_tree --blocked --tree-output .artifacts\blocked.json
```

Run the test suite:

```powershell
$env:PYTHONPATH = "src"
python -m pytest
```

## Architecture

```text
TaskProgram (LLM-safe intent IR)
    |
    v
SpatialSelectorEngine ---> BindingArtifact
    |
    v
TaskCompiler ---> TaskTreeStore <--- TaskTreeInspector
                     ^
                     | sole writer
               TaskTreeKernel
                 /       \
        Decomposers       Operations
                            |
             +--------------+--------------+
             |                             |
      Domain capabilities             Physical skills
       A* / IK / RRT                        |
             |                         ActionRequest
             v                             |
      immutable plan artifacts         HarnessRuntime
                                           ^
                               canonical PhysicalEffect
                              + serialized EffectRunner
                              + SQLite WAL journal
                              + artifacts directory lock
                                           |
                        freshness + guards + leases + transaction
                                           |
                                      RobotBackend
                                           |
                                       Observation
                                           |
                                       WorldModel
                                           |
                                   independent Verifier
```

The persistent operator path wraps, but does not bypass, this flow:

```text
Browser Console -> HTTP Adapter -> ContinuousTaskSession -> TaskProgram
                                      |
                                       +-> Planner Adapter / Canonicalizer
                                       +-> fresh Kernel / Store per task
                                       +-> shared World / Harness / Backend
                                       `-> shared EffectRunner / journal
```

The dependency direction is intentional:

- The tree decides what happens next.
- Domain capabilities generate operation-level plans.
- A*, IK, and RRT are implementation details, never task-tree nodes.
- Physical skills only translate artifacts into typed action requests.
- Only `HarnessRuntime` invokes the robot backend.
- Planners and predicates read `WorldSnapshot`; neither reads backend state.
- Verifiers do not call planners, skills, controllers, or the backend.
- An LLM may emit `TaskProgram`; it cannot emit or mutate runtime tree state.
- Planner output is canonicalized before `task_program.json` is persisted.
  The compiler repeats the same check for compatibility with older artifacts;
  unknown, conflicting, misplaced, and destination quantifiers fail closed.
- Decomposers and repair resolvers return typed `GraphDelta` values.
- `TaskTreeKernel` owns an unforgeable writer capability for `TaskTreeStore`.
- Failed Store mutations restore specs, runtimes, edges, stack, and events.
- Kernel validation rejects unmounted, unreachable, or escaping graph deltas.
- Physical requests carry a canonical payload hash and an explicit attempt
  ledger before dispatch.
- The continuous session persists request states in
  `<artifacts_dir>/physical-effects.sqlite3`; a completed or unresolved request
  is not physically dispatched again after restart.
- Persisted physical results are reconciliation evidence, not a substitute for
  restoring the task tree or re-verifying the observed goal.
- Each extension receives a role-specific context; skills cannot access runtime
  or a robot backend.

See [ARCHITECTURE.md](../ARCHITECTURE.md) for contracts and extension rules, and
[the engine evolution roadmap](ENGINE_EVOLUTION_ROADMAP.zh-CN.md) for the
staged refactoring plan.

## Reference Place Tree

```text
Place
|- ResolveAndInspect
|- PlanPick
|- PlanTransfer
|- Pick
|  |- NavigateToPickStance
|  |- ExecuteGrasp
|  `- VerifyHeld
|- TransferHeld
|  |- MoveToTransportPosture
|  |- NavigateHeld
|  `- VerifyPlacementReady
|- Release
|  |- ExecuteRelease
|  `- VerifyReleased
`- VerifyPlaceGoal
```

Planning the transfer before grasping detects route failures while the gripper
is still empty. A blocker is accepted only when counterfactual A* proves that
removing that exact obstacle restores a route. The kernel mounts a
`RouteBlockedRepair` subtree that recursively places the blocker in a parking
region and refreshes the dependent pick plan before retrying. If a new obstacle
appears while an object is already held, repair replans the transfer instead of
trying to grasp a second object.

## Package Map

```text
src/task_recursive_tree/
  core/          shared diagnostics and predicate contracts
  artifacts/     immutable plans, metadata, aliases, and artifact storage
  task/          IR, compiler, node model, store, kernel, decomposers, repair
  selection/     semantic spatial selectors and grounding
  world/         snapshots, geometry, observations, predicates
  capabilities/  A*, IK, RRT, navigation, pick, and transfer planning
  runtime/       immutable artifacts, freshness, leases, transactions, harness
  skills/        plan-to-ActionRequest translation
  robot/         model, controller/MPC port, backend port, simulation backend
  adapters/      compatibility boundary for imperative callers
  session/       serialized multi-task session over one live physical runtime
  web/           local HTTP adapter and browser operator console
```

## Safety Model

- Navigation plans carry their clearance radius and ignored-entity set through
  to backend execution.
- GeminiER2 detour paths are accepted only through declared producer/consumer
  contracts, a frozen dispatch snapshot, and the active route-safety policy
  fingerprint. Execution blockage invalidates the consumed path before repair.
- GeminiER2 base motion uses a shared `5 mm / 0.5 deg` continuous sweep policy
  for A* result validation and physical route revalidation.
- Physical dispatch uses the Harness-owned executor registry and canonical
  `execute_physical(request, allowed=None)` contract; an integration test checks
  the installed Harness signature to catch interface drift.
- Harness-marked `system_only` physical actions use the trusted
  `execute_system_physical(request)` entrypoint rather than the ordinary model
  action path.
- Pick plans depend on the observed arm configuration; every arm path must
  start continuously from the current joints.
- RRT and joint controllers validate interpolated edges, not only path
  waypoints.
- Transfer plans use a folded transport posture and a held-object envelope.
- Occupancy fingerprints invalidate plans when obstacles are added, removed, or
  moved, while allowing the explicitly ignored payload to move.
- Grasp and release use three-dimensional distance. Optional destination yaw
  tolerance is checked by planning, execution, and final verification.
- The harness observes after every physical command. Rollback-capable
  simulators restore and reconcile; real backends reconcile observed state
  without pretending physical motion can be undone.
- Cancellation is directly retryable only with certified no-side-effect,
  closed-transaction, and quiescence evidence; otherwise it is reconciled as
  `OUTCOME_UNKNOWN`.
- Physical requests are executed from a validated snapshot. Their durable
  identity uses type-aware canonical hashing rather than object `repr()`, and
  the journal persists the hash schema so pending rows can be migrated
  explicitly.
- Runtime-side freshness, guard, and lease rejection carries certified
  pre-dispatch evidence. The runner returns those rows to `pending`; exceptions
  without such evidence remain `OUTCOME_UNKNOWN`.
- Reconciliation closes the matching kernel attempt as
  `reconciled_succeeded` or `reconciled_refuted`, while preserving the original
  durable journal evidence and request history.
- One active `ContinuousTaskSession` owns an artifacts directory through an
  advisory process lock. Only a new exclusive owner may recover inherited
  `dispatching` rows as `outcome_unknown`; a live runner never does so.
- Entity freshness includes a generation so disappearance and reappearance
  cannot reuse the same dependency version.

## Reference Scope

This repository is a reference modular monolith. `TaskTreeStore` is an
in-memory authoritative store with an explicit serializable stack and atomic
in-process mutations with rollback. The SQLite WAL physical journal is durable
and prevents duplicate dispatch for the same request ID and hash, but JSON tree
exports are still inspection snapshots rather than a complete restart
protocol. One artifacts directory enforces one active dispatcher with a local
advisory process lock. A production deployment should retain that ownership
rule, add owner-token compare-and-swap or leases for distributed dispatchers,
implement the same store contract with SQLite/PostgreSQL/event-log durability,
and persist artifacts in the same transaction boundary as the physical outbox.

The included robot is a deterministic planar two-link simulation. A real robot
adapter should preserve `ActionRequest`, observation, guard, and reconciliation
contracts while delegating trajectory tracking to its IK/MPC/controller stack.
