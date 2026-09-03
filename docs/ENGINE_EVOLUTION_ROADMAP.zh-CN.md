# Task Recursive Tree 引擎演进路线

更新时间：2026-09-02

## 目标

本项目不应改造成“游戏王决斗引擎”，而应吸收成熟规则引擎、工作流引擎和机器人运行时中已经被验证的机制，形成：

```text
显式任务树
+ 确定性规则选择
+ 纯状态转换
+ 持久化副作用协议
+ 观测驱动协调
```

所有改造仅发生在本项目内部。外部 Harness、机器人 SDK 和其他项目只能通过适配器接入，不得被本项目安装脚本、运行时补丁或测试修改。

## 可借鉴的决斗引擎机制

1. **发动与结算分离**

   物理节点应明确经历“构造请求、持久化准备、派发、观测、验证、结算”，不能把调用后端等同于动作成功。

2. **确定性时点与冲突消解**

   修复规则、协调规则和安全规则可以使用 agenda，但候选顺序必须固定为：

   ```text
   priority -> specificity -> rule_id
   ```

   规则只能返回提案，不能直接修改任务树。

3. **实例身份与重新出现**

   决斗实体离场再入场后不再是原实例。当前世界模型的 `(generation, version)` 已实现同类语义，应继续作为 freshness 和 ABA 防护基础。

4. **次数和窗口限制**

   `dispatch_epoch`、attempt budget、repair budget 对应“一回合一次”和发动窗口。限制必须进入类型化状态和事件，不应散落为字符串键。

5. **持续效果**

   可借鉴为声明式 invariant monitor，例如持物状态、禁入区域、payload clearance 和控制器心跳。监控器只能报告事件和 Diagnostic，不能隐式执行修复。

## 不应照搬的部分

- 不引入完整连锁栈来替代主任务树。
- 不允许任意脚本回调直接改 Store、World 或 Backend。
- 不为所有事实建立全局 Rete 网络；规则系统只用于修复、协调和持续约束。
- 不把 A*、IK、RRT、MPC 内部迭代暴露为任务节点。

## 已完成的近期基础

- Store 唯一写者、原子 mutation 和失败回滚。
- artifact invalidation 纳入共享回滚范围。
- 显式非终态 `incomplete`、稳定错误码和单调任务快照编号。
- terminal completion 幂等化。
- 取消必须携带无副作用证书，否则归一化为 `OUTCOME_UNKNOWN`。
- 带完整类型信息的 canonical physical request hash、请求快照、attempt ledger
  和同步 EffectRunner。
- SQLite WAL 物理副作用 journal：
  `pending -> dispatching -> completed/outcome_unknown`；具备“backend 从未
  派发”证据的 runtime 拒绝可执行 `dispatching -> pending`。
- journal 持久化 request hash schema；缺失 schema 标签的 pending 行
  只有在摘要本身已经等于当前 v2 hash 时才可安全重标记，真正的 legacy
  hash 不根据有损 JSON payload 自动迁移。
- Session、Application 和新 execution 共享同一 runner；reset 重开同一 journal。
- artifacts 目录级进程锁；同一目录只允许一个活跃 Session。
- 只有持有目录独占锁的新 Session 才能显式恢复上一任遗留的
  `dispatching -> outcome_unknown`；runner 不自动恢复活跃状态。
- Kernel 区分派发前确定失败与派发后未知结果：
  `not_started/reservation -> FAILED`，
  `prior_dispatch/execution/completion -> OUTCOME_UNKNOWN`。
- freshness、guard、resource lease 等 runtime 内部派发前检查必须携带
  `physical_dispatch_started=False` 和 `physical_outcome_known=True`；
  缺少证据时保持保守协调语义。
- 观测 source epoch、sequence、completeness、confidence 和实体 generation。

## P1：收紧内核边界

### 1. 类型化物理节点状态

从开放的 `adapter_state` 中抽出：

```text
PhysicalNodeState
PhysicalAttemptRecord
ReconciliationRecord
CompletionRecord
```

每个结构带 schema version；适配器私有字段放入独立 `integration_state`。验收标准是 Kernel 不再依赖数十个隐式字符串键表达物理生命周期。

### 2. 分离 Repair 与 Reconciliation

- Repair：已知失败，目标是生成新的可执行方案。
- Reconciliation：结果未知，目标是通过新观测确认“已成功、已失败、仍未知”。

两者使用不同协议、预算、事件和规则注册表，避免把未知物理结果误当作普通规划失败。

### 3. 完整取消协议

```text
CancelRequested
-> EffectRunner.cancel(request_id)
-> backend quiescence
-> cancellation certificate
-> CANCELLED 或 OUTCOME_UNKNOWN
```

活动物理节点在证书产生前进入 `cancelling`，不能提前把整棵树终态化。

### 4. 单 dispatcher 所有权

当前已使用跨平台 advisory process lock，强制一个 artifacts 目录只有一个
活跃 Session。中断派发恢复只在新 Session 取得独占锁后显式执行，因此第二个
runner 不会把仍在执行的请求误判为崩溃。下一步是在需要分布式 dispatcher
时加入 owner token、CAS 和带过期时间的 lease。

## P2：从解释器重构为 Transition Engine

保留 `TaskTreeKernel` 作为唯一入口，内部拆分为：

```text
PhaseReducer
CompositeController
PhysicalDispatchCoordinator
FailureCoordinator
ReconciliationCoordinator
GraphDeltaValidator
```

组件返回 `TransitionResult(commands, events)`，由 Kernel 一次提交。目标是减少 Kernel 内分散的 `set_runtime`、stack 和 event 写入，并让每个阶段可以做纯函数测试。

## P3：完整 Durable Execution

将以下内容放入同一 SQLite/PostgreSQL 事务边界：

- TaskStore snapshot 和可重放事件；
- execution stack；
- artifact 与 alias；
- physical outbox/journal；
- completion record。

执行模型升级为：

```text
Command -> Reducer -> DomainEvents -> State
                         |
                         `-> Effect Dispatcher -> Observation -> Command
```

只有完成这一步，系统才能在进程重启后恢复任务结论，而不仅是阻止重复物理派发。

## P4：异步 Dispatcher 与 Mailbox

Kernel 只提交 effect command，不在 tick 内阻塞等待机器人。Dispatcher 独立执行，结果通过单机器人串行 mailbox 返回。显式阶段建议为：

```text
PREPARED
RESERVED
WAITING_EFFECT
OBSERVING
RESOLVING
```

这一步解决暂停、取消、超时、心跳和进程恢复对阻塞线程的依赖。

## 实施顺序

1. 类型化 `PhysicalNodeState`。
2. 拆分 Repair/Reconciliation。
3. 完成取消协议和 dispatcher 所有权。
4. 提取 transition reducers。
5. 建立可重放事件和统一事务存储。
6. 引入异步 dispatcher、mailbox 和持续 invariant monitor。

每一阶段都必须保留现有端到端工作流测试，并增加崩溃窗口、重复请求、状态迁移、取消和协调的故障注入测试。
