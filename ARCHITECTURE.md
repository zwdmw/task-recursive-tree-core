# Architecture Contracts

## 1. Ownership

`TaskTreeStore` owns five forms of runtime state:

1. Immutable `TaskNodeSpec` values.
2. Separate `TaskNodeRuntime` values.
3. Typed `TaskEdge` values.
4. The persistent `ExecutionFrame` stack.
5. An append-only event log.

The store creates a private writer capability. A `TaskTreeKernel` claims that
capability during construction. Identity, rather than a copyable string, is
required for every mutation. Inspectors can read and serialize the tree but
cannot mutate it.

Kernel policy validation runs before Store invariants:

- decomposition deltas may only mount child nodes below the expanding node;
- repair deltas require exactly one repair edge from the failed node;
- repair entry and all declared nodes must belong to the repair subtree;
- the complete graph must remain acyclic and reachable from the root.

Expansion, repair mounting, success, and failure use in-process atomic Store
mutation blocks. A mutation snapshots all Store-owned state and restores it
when any nested operation raises. Repair invalidation is performed only after
the tree transition has validated; artifact adapters may expose their own
`mutation()` context so both sides roll back together. The reference Store is
not a durable restart implementation; production adapters must persist tree,
stack, events, aliases, artifacts, and the physical outbox in one storage
transaction.

## 2. Node Taxonomy

Each node keeps independent fields instead of encoding behavior in its name:

| Field | Meaning |
| --- | --- |
| `task_type` | Domain intent or operation identifier |
| `operation_kind` | `decomposer`, `system`, or `physical` |
| `control_kind` | `leaf`, `sequence`, or `selector` |
| `origin` | `program`, `compiler`, `decomposer`, or `repair` |
| edge `kind` | normal `child` or mounted `repair` |

Algorithm names such as A*, IK, and RRT must not be node types. They are private
implementation details of capabilities.

## 3. Spatial Selection

Semantic selection belongs in `selection/`. A selector fully states:

- entity kind and semantic tags;
- frame and reference;
- spatial relation;
- metric;
- cardinality;
- deterministic tie policy;
- optional region or corner scope.

Selection produces a `BindingArtifact`. It must not call navigation,
manipulation planning, IK, RRT, MPC, or robot control.

Grasp candidates, placement candidates, base stances, and transport posture are
geometric planning choices. They stay inside domain capabilities.

## 4. Artifacts

Only cross-node, recoverable operation packages are persisted:

- `BindingArtifact`
- `NavigationPlan`
- `PickPlan`
- `TransferPlan`

Artifacts are immutable. A scoped alias can point to a newer artifact after
replanning, but an artifact is never modified in place.

Every artifact records:

- source snapshot;
- exact dependency versions;
- frame graph revision;
- robot state epoch at creation;
- robot and collision model versions;
- payload transform hash when relevant;
- assumptions and random seed.

Freshness is derived by comparing declared dependency versions with the current
snapshot. There is no mutable `invalidated` flag. Navigation artifacts also
store an occupancy fingerprint, including obstacle membership, so a newly
appearing obstacle invalidates a plan. An ignore set allows a transfer plan to
exclude the payload whose pose is expected to change. Payload geometry is
checked independently through `payload_transform_hash`.

Entity freshness uses a combined `(generation, version)` token. An entity that
disappears from a complete observation and later reappears receives a new
generation even when its local version restarts at zero. Observations also
carry a source epoch, monotonic sequence, completeness flag, timestamp, and
confidence. Partial observations merge observed entities and do not imply that
unseen entities disappeared.

## 5. Physical Boundary

Physical code follows this path:

```text
PhysicalSkill.build_request
  -> ActionRequest
  -> PhysicalEffect(request_id, canonical_request_hash)
  -> EffectRunner
  -> HarnessRuntime.execute
  -> RobotBackend.execute
  -> Observation
  -> WorldModel.ingest
```

`HarnessRuntime` performs:

1. Artifact freshness checks.
2. Snapshot-only predicate guards.
3. Atomic resource leasing.
4. Backend checkpoint creation.
5. Command execution with observation ingestion after each command.
6. Commit, rollback plus observation reconciliation, or failure reconciliation.

The kernel persists an `awaiting_physical` dispatch record and attempt ledger
before calling the serialized `SynchronousEffectRunner`. The runner
deduplicates an exact `(request_id, request_hash)`, rejects request-ID reuse
with another payload, and can persist its request state in a SQLite WAL journal.
The durable state machine is:

```text
pending -> dispatching -> completed
   ^          |
   |          `-> outcome_unknown
   `---------- dispatch rejected before physical execution
```

The `dispatching -> pending` transition is permitted only when one attempt
provides all four pieces of no-dispatch evidence:
`dispatch_stage` is `not_started` or `reservation`,
`physical_dispatch_started` is false, `physical_outcome_known` is true, and
`requires_reconciliation` is false. After restart, `pending` may be
dispatched. A recovered `dispatching` request is marked `outcome_unknown`;
`completed` and `outcome_unknown` requests are never dispatched again. A
persisted result is reconciliation evidence only. It is not replayed into a
new kernel as a successful action result because the in-memory tree state has
not been durably restored.

When external reconciliation establishes the result, the kernel closes the
matching attempt as `reconciled_succeeded` or `reconciled_refuted`, clears the
active unknown marker, and retains the reconciliation node and request history
for audit. The durable effect journal may remain `outcome_unknown`; it records
what the original dispatcher knew, while the kernel ledger records the later
evidence-based resolution.

If the physical runtime returns but the completion journal update fails, the
runner reports `OUTCOME_UNKNOWN`, even when the physical call itself returned
success. `PhysicalEffectError` evidence, including request hashes and journal
state, is preserved in the kernel diagnostic.

`CANCELLED` means a certified no-side-effect stop. A cancellation without a
closed transaction, quiescence evidence, a fresh evidence revision, and an
explicit no-side-effect verdict is normalized to `OUTCOME_UNKNOWN` and follows
the reconciliation path. Required effects reported as `partial` are also
outcome-unknown.

No decomposer, system operation, planner, predicate, repair resolver, or
inspector receives a runtime/backend capability. Role contexts expose only:

- decomposition: no services;
- system operation: world, artifacts, selector, capabilities, verifier, model;
- skill: artifact reads;
- repair: world and artifact reads.

## 6. Verification

`Verifier.evaluate(formula, snapshot)` is independent of action execution. It
only consumes a formula and immutable world snapshot, then returns a result plus
evidence tied to a snapshot reference.

Verification failure is not equivalent to planner failure. Examples:

- `HOLD_LOST`: observed object is no longer held.
- `OUTCOME_UNKNOWN`: the requested postcondition lacks confirming evidence.
- `GUARD_FAILED`: a physical request precondition is false before execution.

## 7. Recursive Repair

A failure is represented by a structured `Diagnostic`. The repair resolver may
return `RepairProposal(GraphDelta, entry_node_id, rationale,
invalidates_artifacts)`. It cannot edit the tree. After validating the repair
delta, the kernel atomically mounts the repair state and then applies declared
artifact invalidations as the final operation in the same rollback scope before
executing the repair subtree.

For `ROUTE_BLOCKED`:

1. Counterfactual A* must prove that ignoring the candidate restores a route.
2. The witness must identify a movable obstacle.
3. A parking region must exist.
4. The resolver proposes `RouteBlockedRepair`.
5. Its subtree runs `Place(blocker, parking)` and refreshes the dependent pick
   plan.
6. The kernel mounts it under a `repair` edge.
7. On repair success, the failed operation is retried.

Blocker relocation has an explicit recursion-depth limit. If the robot already
holds an object, route blockage proposes transfer replanning; it never attempts
to grasp a blocker with an occupied gripper.

Moving an obstacle inside A* or a controller is forbidden because that hides a
physical side effect from the task tree.

Plan repair follows artifact dependencies. Recomputing a `PickPlan` also
recomputes the dependent `TransferPlan`.

## 8. Extension Rules

Add a new high-level task:

1. Add a validated `TaskProgram` action.
2. Compile only its root node.
3. Register a decomposer that returns a typed `GraphDelta`.
4. Reuse system operations and physical skills where possible.
5. Add an end-to-end test and a tree-shape assertion.

Add a new robot:

1. Implement the `RobotBackend` protocol.
2. Keep vendor SDK types inside that adapter.
3. Convert backend telemetry into `Observation`.
4. Do not expose backend state to planners or verifiers.

Add MPC:

1. Implement `JointTrajectoryController` with the real MPC tracker.
2. Inject it into the robot backend/controller adapter.
3. Preserve the `ActionRequest` and observation contracts.
4. Do not turn MPC iterations into task nodes.

## 9. Continuous Session Boundary

`ContinuousTaskSession` is an application-level coordinator, not a second task
executor. It accepts one typed task submission at a time and always routes it
through:

```text
TaskProgram -> TaskCompiler -> TaskTreeKernel
```

Every submitted task receives a new `TaskTreeStore`, `TaskTreeKernel`, and
`TaskTreeInspector`. The execution reuses the same `WorldModel`,
`HarnessRuntime`, artifact store, transaction ledger, robot backend, and
`EffectRunner`. This keeps the physical scene continuous without mixing nodes
from separate root goals into one tree or losing in-process request
idempotency.

The HTTP adapter and browser console may:

- submit typed object and destination identifiers;
- read world, task-tree, stack, event, and history snapshots;
- request a complete scenario reset;
- shut down their own local server.

They cannot mutate nodes, mount repairs, invoke planners, or call the robot
backend. Reset replaces the complete physical runtime; ordinary task
submission does not reset world state. A reset closes the old runner and opens
a new runner over the same `<artifacts_dir>/physical-effects.sqlite3` journal,
so process-local result objects are discarded while durable dispatch evidence
is retained. Session close is explicit and idempotent.

Task snapshot IDs remain monotonic across reset and are initialized from
existing `task-NNNN.json` files, so reset or process restart does not overwrite
history. A nonterminal kernel return is exposed as `incomplete` with a stable
error code and requires an explicit reset before another submission.

The launcher PID record is only process-ownership metadata. It is validated
against the local health endpoint before requesting shutdown and is not a task
tree persistence mechanism.

## 10. Reference Limitations

- The bundled Store is in-memory. JSON snapshots are inspectable but are not a
  complete process-restart protocol. The durable physical journal prevents
  duplicate request dispatch; it does not restore the task tree or prove that
  the root goal completed.
- The EffectRunner is synchronous and therefore still blocks one
  kernel tick while the physical runtime call is in progress.
- One artifacts directory must have one active dispatcher. Multi-process
  ownership requires a process lock or renewable dispatcher lease before two
  live processes may share one physical journal.
- Request hashes are tied to the canonical request schema. Long-lived
  deployments need schema/version migration before changing request fields or
  normalization rules.
- The bundled arm model is planar with fixed height. It rejects targets outside
  the modeled vertical tolerance and supports optional release-yaw validation.
- The simulation backend can roll back memory. Real adapters must report
  `supports_rollback = False` and rely on observation reconciliation.
- Collision checking is conservative grid/circle geometry, intended to validate
  architecture contracts rather than replace a production collision library.
- The web console is a local single-operator adapter with no authentication.
  Keep the default loopback bind unless an authenticated deployment layer is
  added.
- GeminiER2 safety installation still patches modules within its host process.
  Process isolation remains required before unrelated Harness consumers may
  safely share that process.

The staged engine refactoring plan is documented in
[`docs/ENGINE_EVOLUTION_ROADMAP.zh-CN.md`](docs/ENGINE_EVOLUTION_ROADMAP.zh-CN.md).

## 11. GeminiER2 Integration Boundary

The GeminiER2 integration is an adapter layer, not a second architecture:

```text
ContinuousTaskSession
  -> HarnessTaskProgramPlannerAdapter
       -> canonical TaskProgram + normalization audit
  -> KernelCompilerBridge
  -> KernelExecutorBridge
       -> TaskTreeStore
       -> TaskTreeKernel
       -> GeminiER2NodeSemantics
       -> HarnessBuiltinTaskDecomposerAdapter
       -> HarnessSystemOperationAdapter
       -> HarnessMacroActionSkill
       -> HarnessPhysicalGateway
       -> HarnessRepairResolver
```

The ownership rules are:

- Harness `TaskTree` values are input DTOs only.
- Harness planner output is canonicalized before the continuous session writes
  `task_program.json`; `KernelCompilerBridge` reapplies the same canonicalizer
  as a compatibility boundary for older artifacts.
- Verified concrete universal selections remain explicit sequence items.
  `for_each` aggregate aliases canonicalize to `all`; ambiguous destination,
  conflicting, misplaced, and unknown quantifiers are rejected with paths.
- `TaskTreeStore` is the sole runtime tree state.
- `TaskTreeKernel` is the sole node lifecycle interpreter and tree writer.
- Planning nodes publish opaque artifacts through explicit producer/consumer
  contracts.
- Interaction stance generation may reject kinematically invalid candidates
  and candidates blocked by residual unmodeled obstacles. Structured base
  obstacles are validated only by SE(2) lattice A* using route geometry and
  the robot footprint, so coarse stance geometry cannot preempt navigation.
- Physical nodes consume only declared artifact references and use the stable
  request ID assigned by the kernel.
- Each node declares an execution policy. Goal-idempotent nodes may terminate
  at entry when their live goal is already satisfied; structural nodes must
  execute or expand first and are validated against the same goal only in the
  postcondition phase.
- Main and recovery trees use the same executor bridge.
- Harness owns MuJoCo, world evidence, registered tools, safety policy,
  transactions, and physical macro execution.

### 11.1 Unified Motion Safety Policy

The integration installs one process-local safety adapter before constructing
the Harness runtime. The adapter is the single policy source for planning,
artifact publication, dispatch, and execution:

- Base routes use no more than `5 mm` translation and `0.5 deg` rotation
  sampling. Adaptive conservative footprint inflation certifies the interval
  between samples, so a narrow corner or rotation collision cannot hide between
  two accepted poses.
- Every A* result is checked against the actual start pose, requested goal pose,
  structured route obstacles, residual navigation obstacles, and the complete
  workspace. Placement feasibility is evaluated at the route's actual endpoint.
- A detour artifact records the validation model, policy fingerprint, controller
  model, actual sample values, dense sample count, footprint identity, obstacle
  identity, and validation evidence.
- A physical route node must consume a declared `detour_path` producer contract.
  An explicit `path_ref` cannot bypass producer or continuation identity checks.
- Route dispatch requires a frozen input snapshot. Artifacts created under an
  older safety policy, controller model, or failed validation are marked stale
  and replanned instead of being executed.
- Harness macro execution revalidates `requires_revalidation` routes against the
  live world with the same hardened functions. `PATH_BLOCKED`,
  `STALE_ARTIFACT`, and `NO_SAFE_DETOUR` invalidate the consumed route before
  recursive repair mounts a new `plan_detour` subtree.
- The physical gateway delegates executor lookup and policy evaluation to the
  Harness `ToolRegistry`, then calls the canonical
  `execute_physical(request, allowed=None)` entrypoint. Signature detection
  keeps any legacy three-argument adapter explicit and separately tested.
- Harness `system_only` physical tools (`prepare_base_motion_posture`,
  `move_to_transport_posture`, and `recover_workspace`) are dispatched through
  `execute_system_physical(request)`. They are never made model-callable by
  widening the ordinary `allowed_decisions` set.
- Arm RRT and the reference joint controller share dense edge validation.
  Collision-free endpoints are insufficient; every interpolated edge must also
  be collision-free. Collision-sensitive Harness arm motion is forced through
  motion planning with unsafe direct-IK fallback disabled.

The core reference artifact store remains immutable and freshness-derived. The
GeminiER2 bridge also mirrors the Harness task store's explicit `invalidated`
marker because the external runtime uses that marker to redirect a continuation
to a newly published artifact.

### 11.2 Remaining Physical Model Boundary

This adapter hardens the existing Harness model; it does not invent geometry
that the Harness does not expose. A production robot integration must still
provide:

- the full attached-body transform `T_ee_object` and payload geometry throughout
  planning and execution;
- link- and geometry-specific allowed-contact policies instead of a broad
  robot/payload whitelist;
- identical planned and executed finger, attachment, and retreat states during
  release;
- controller-cycle collision monitoring using the same attached-body and
  contact policy as the planner.
