
---

# 第二部分：逐概念拆解课

这一部分故意从最基础的概念开始讲。假设读者知道机器人、软件和
大模型这几个词，但不知道它们在这套架构里如何拼在一起。

阅读时不要试图一次记住所有类名。先建立一个问题顺序：

```text
人想让机器人做什么？
    ↓
系统把目标写成了什么数据？
    ↓
运行时现在保存了什么状态？
    ↓
系统依据哪个世界快照做决定？
    ↓
哪个模块真正产生了物理副作用？
    ↓
动作之后用什么证据判断结果？
    ↓
失败后是谁提出修复，谁批准修复，谁执行修复？
```

只要这七个问题能顺着回答，整套系统就不会再显得神秘。

## 25. 先把五个最基础的词讲清楚

### 25.1 什么是“数据”

数据就是系统保存或传递的描述。

例子：

```text
杯子的名字是 cup-red
杯子的坐标是 (0.75, 0.40)
机器人夹爪现在是打开的
路线有 12 个路径点
```

数据本身不会自动做任何事情。

```text
{"held_object_id": "cup-red"}
```

只是一个描述。它不会真的让夹爪抓住杯子。

### 25.2 什么是“状态”

状态是“在某个时刻，系统认为世界或程序是什么样”。

例如机器人有两个不同的状态：

```text
物理世界状态：
    杯子在桌子上
    夹爪打开
    底盘位于 x=0.2, y=0.3

任务程序状态：
    Place 节点正在执行
    PlanPick 已经完成
    ExecuteGrasp 尚未验证
```

这两个状态不是同一个东西。

```text
世界状态回答：现实是什么样？
程序状态回答：程序做到哪了？
```

如果只保存程序状态，不保存世界状态，系统会出现：

```text
程序认为已经抓住杯子
现实中杯子其实掉在地上
```

如果只保存世界状态，不保存程序状态，系统会出现：

```text
系统知道杯子在哪里
但不知道下一步要做什么
```

所以这套系统至少需要把二者分开保存。

### 25.3 什么是“动作”

动作是会改变世界的操作。

```text
底盘沿路线移动
机械臂改变关节角
夹爪闭合
夹爪打开
移动一个箱子
```

动作有一个非常重要的特征：

> 动作不是“描述”，而是会产生副作用。

副作用就是“动作对外部世界造成的改变”。

```text
读取杯子坐标：通常没有物理副作用
计算一条路线：通常没有物理副作用
让底盘移动：有物理副作用
让夹爪闭合：有物理副作用
```

架构要特别防止把有副作用的事情伪装成没有副作用的数据计算。

### 25.4 什么是“程序”

程序不一定非要写成 Python。只要它具备以下几类内容，就有程序的特征：

```text
目标：
    要得到什么结果？

步骤：
    先做什么，后做什么？

状态：
    当前做到哪一步？

输入：
    依赖什么外部信息？

输出：
    产生什么结果？

条件：
    什么情况下可以继续？

异常：
    失败后怎么办？
```

任务递归树具备这些内容，所以它可以被称为“领域程序”。

### 25.5 什么是“运行时”

运行时不是一句“程序正在跑”这么简单。它至少包括：

```text
解释规则：
    一个节点进入后先做什么？

内存：
    当前节点状态放在哪里？

调用栈：
    当前嵌套到哪一层？

输入环境：
    当前世界状态是什么？

副作用边界：
    谁有资格调用设备？

结果处理：
    动作完成后如何判断成功？

错误处理：
    失败后重试、修复还是终止？
```

`TaskTreeKernel`、`TaskTreeStore`、`WorldModel`、`ArtifactStore` 和
`HarnessRuntime` 合在一起，才构成完整的机器人任务运行时。

## 26. Agent、脚本、工作流和运行时程序的区别

### 26.1 普通脚本

```text
第一行：移动到 A
第二行：抓取
第三行：移动到 B
第四行：释放
```

脚本通常假设环境已经知道，并且流程大致固定。

### 26.2 工作流

工作流比脚本更明确地表达：

```text
步骤 1 完成后才能进入步骤 2
失败后走备用分支
某个审批通过后才能继续
```

### 26.3 Agent

Agent 通常还能根据输入和环境决定：

```text
下一步做什么
调用哪个能力
遇到失败如何换方案
```

但是，如果 Agent 只是不断生成文字并调用工具，它的中间状态往往藏在：

```text
提示词
对话历史
临时变量
模型内部的隐式推理
```

这会让过程难以审计。

### 26.4 这套系统的组合

这套架构把四者组合起来：

```text
自然语言提供目标
Agent 提供理解和高层分解
任务树提供显式程序结构
运行时提供确定的执行和恢复规则
```

所以它不是“没有 Agent 的工作流”，也不是“把工作流交给模型随便决定”。

更准确地说：

> Agent 负责提出和解释程序，运行时负责把程序变成受控行为。

## 27. 编译器类比：为什么这个类比有用

### 27.1 编译器做什么

编译器把人更容易写的形式，转换成机器更容易执行的形式。

```text
源代码：
    x = 1 + 2

中间表示：
    load 1
    load 2
    add
    store x
```

机器人任务也存在类似转换：

```text
自然语言：
    把红杯子放到收纳区

结构化意图：
    place(object_selector, destination_selector)

任务程序：
    resolve -> plan -> pick -> transfer -> release -> verify

物理请求：
    具体路线、关节路径、夹爪命令
```

### 27.2 为什么不能跳过中间表示

如果自然语言直接变成低层动作，中间缺少：

```text
类型检查
对象绑定
前置条件
资源要求
证据引用
版本依赖
失败处理
```

这相当于让编译器把一句模糊的英文直接变成机器码，而且不允许检查变量、
内存和权限。

### 27.3 `TaskProgram` 是什么类型的中间表示

`TaskProgram` 不是最终执行图。它更像一个很小的抽象语法树：

```text
动作名称：
    place

参数：
    object_selector
    destination_selector
```

它的作用是把输入从“人话”变成“结构化意图”。

### 27.4 `TaskNodeSpec` 是什么

编译器把 `TaskProgram` 编译成根节点 `TaskNodeSpec`。

此时的根节点像一条高层指令：

```text
Place
```

它还没有包含每一次具体抓取的路径点。因为路径点要依赖当时的世界状态。

### 27.5 任务树为什么不是普通 AST

普通 AST 通常描述一段静态程序。任务树还需要描述：

```text
当前执行状态
现实反馈
物理副作用
动态修复
```

所以它更接近：

```text
AST
    + 状态机
    + 调用栈
    + 事件日志
    + 现实世界反馈
```

## 28. `TaskProgram`：从一句话变成一个小对象

### 28.1 人话定义

`TaskProgram` 就是：

> “用户想做哪类任务，以及这个任务的结构化参数是什么。”

### 28.2 当前核心字段

```python
TaskProgram(
    program_id="task-0001",
    action="place",
    arguments={
        "object_selector": ...,
        "destination_selector": ...,
    },
)
```

逐项解释：

#### `program_id`

任务的身份证。

```text
task-0001
```

它不是机器人对象的 ID，也不是节点 ID。它标识的是“一次用户提交的任务程序”。

#### `action`

要执行的高层动作类型。

当前核心实现要求：

```text
action == "place"
```

这意味着参考实现目前主要演示“把一个对象放到一个目标区域”。

#### `arguments`

任务参数。当前要求至少有：

```text
object_selector
destination_selector
```

注意这里保存的不是已经解析出的实体对象，而是“如何找到对象”的规则。

### 28.3 为什么参数里保存选择器，而不是直接保存对象

如果直接保存：

```text
object_id = cup-red
```

系统失去了对下面这句话的记录：

```text
为什么选择了 cup-red？
```

而保存选择器可以记录：

```text
用户说的是“红色杯子”
选择器要求 kind=object
选择器要求 tags={red,cup}
如果多个候选，使用 stable_id 平局策略
```

这让“理解”和“绑定”分成了两步。

### 28.4 `TaskProgram` 的边界

它可以表达：

```text
要放置什么对象
放到什么目标
```

它不应该直接表达：

```text
每个电机的电流
控制器周期
传感器驱动细节
底层通信包
```

这些信息属于后面的能力和物理运行时。

## 29. `SpatialSelector`：系统怎样理解“那个东西”

### 29.1 人话定义

`SpatialSelector` 是一个“找东西的规则”。

它不是对象本身，而是一份查询条件。

```text
找一个 kind=object、带 red 和 cup 标签的实体
```

### 29.2 从“找红杯子”逐项拆开

```python
SpatialSelector(
    entity_kind="object",
    relation=SelectionRelation.FIRST,
    required_tags=frozenset({"red", "cup"}),
    reference="robot",
    frame="world",
    metric="euclidean",
    cardinality="one",
    tie_policy="stable_id",
)
```

#### `entity_kind`

先按大类过滤。

```text
object = 可被操作的物体
region = 区域或目标位置
```

如果目标是 `region`，就不应该绑定到一个普通杯子。

#### `relation`

规定候选之间怎样选。

当前可以有：

```text
exact
first
leftmost
rightmost
nearest
farthest
corner
```

例如：

```text
nearest:
    选离参考点最近的候选

leftmost:
    选 x 坐标最小的候选

corner:
    选最接近目标区域某个角的候选
```

#### `entity_id`

精确选择时使用。

```python
SpatialSelector.exact("cup-red", "object")
```

它表达的是：

```text
不要猜，必须是 ID 为 cup-red 的实体
```

#### `required_tags`

语义标签集合。

```text
{red, cup}
```

表示候选必须同时拥有这两个标签。

#### `region_id`

限制候选必须位于某个区域内。

```text
在桌面区域内，选择最近的红色杯子
```

这比只说“最近的红色杯子”更精确。

#### `reference`

距离或空间关系相对于谁计算。

```text
robot
origin
另一个指定实体
```

“最近”不是绝对概念，必须有参考点。

#### `frame`

坐标系。

```text
world
robot_base
camera
```

如果不说明坐标系，同一个 `(x, y)` 可能代表完全不同的位置。

当前参考选择器主要按世界坐标使用，但字段被保留下来，是为了让语义不被隐藏。

#### `metric`

距离怎么算。

当前参考实现使用欧氏距离：

```text
distance = sqrt((x1-x2)^2 + (y1-y2)^2)
```

未来也可能有：

```text
曼哈顿距离
路径代价
时间代价
风险加权距离
```

#### `cardinality`

要选几个。

当前参考实现只接受：

```text
one
```

这是一种故意收紧的约束，避免一个本来只支持单物体的动作收到一组对象。

#### `tie_policy`

如果两个候选完全一样，怎么办？

当前使用：

```text
stable_id
```

也就是按稳定 ID 排序，保证同样输入下结果可重复。

### 29.3 选择过程的完整例子

世界中有：

```text
cup-blue   tags={cup,blue}  position=(0.5,0.4)
cup-red-1  tags={cup,red}   position=(0.8,0.4)
cup-red-2  tags={cup,red}   position=(0.9,0.4)
```

选择器：

```text
kind=object
required_tags={cup,red}
relation=nearest
reference=robot
```

选择过程：

```text
第一步：按 kind 过滤
    保留三个 object

第二步：按 tags 过滤
    删除 cup-blue
    保留 cup-red-1、cup-red-2

第三步：计算到机器人的距离
    cup-red-1 更近

第四步：如果距离相同
    按 stable_id 选

输出：
    selected = cup-red-1
    candidates = [cup-red-1, cup-red-2]
    rationale = 选择规则说明
```

### 29.4 选择器不能做什么

选择器只负责“找谁”，不能负责：

```text
怎么走过去
用哪个抓取姿态
是否需要移动障碍物
机械臂如何避障
```

如果选择器开始调用 A* 或机器人控制，职责就混乱了。

## 30. `BindingArtifact`：把“我认为那个对象是谁”固定下来

### 30.1 为什么需要绑定结果

选择器是规则，绑定结果是这次执行的事实：

```text
object_selector -> cup-red
destination_selector -> drop-zone
```

### 30.2 绑定工件包含什么

```python
BindingArtifact(
    artifact_id="binding-...",
    metadata=...,
    object_id="cup-red",
    destination_id="drop-zone",
    object_candidates=("cup-red", "cup-red-2"),
    destination_candidates=("drop-zone",),
    grounding_evidence=(
        "nearest in frame=world, metric=euclidean, tie=stable_id",
        "first in frame=world, metric=euclidean, tie=stable_id",
    ),
)
```

逐项理解：

```text
object_id：
    最终选中的对象

destination_id：
    最终选中的目标

object_candidates：
    当时所有符合条件的对象

destination_candidates：
    当时所有符合条件的目标

grounding_evidence：
    为什么选它的文字化依据
```

### 30.3 绑定也可能过期

如果对象消失、对象 ID 复用、目标区域版本改变，绑定工件依赖可能失效。

所以绑定不是永远正确，而是：

```text
在某个世界快照下做出的选择结果
```

## 31. `TaskNodeSpec`：一个任务节点的“说明书”

### 31.1 节点不是一句名字

下面两个节点名字虽然不同，但如果没有结构化字段，内核仍不知道如何处理：

```text
PlanPick
ExecuteGrasp
```

真正重要的是节点的属性：

```text
它是系统操作还是物理操作？
它是叶子还是复合节点？
它来自用户程序还是修复？
它依赖哪些参数？
成功前需要满足什么？
成功后需要满足什么？
```

### 31.2 字段逐项解释

```python
TaskNodeSpec(
    node_id="...",
    task_type="ExecuteGrasp",
    operation_kind=OperationKind.PHYSICAL,
    control_kind=ControlKind.LEAF,
    origin=NodeOrigin.DECOMPOSER,
    parameters={"scope": "task-0001"},
    preconditions=(...),
    postconditions=(...),
    max_attempts=2,
    max_repairs=1,
    execution_policy=ExecutionPolicy.REQUIRE_EXECUTION,
)
```

#### `node_id`

节点的唯一 ID。

它需要稳定到足以支持：

```text
事件引用
执行栈引用
动作请求 ID
父子边
故障定位
```

#### `task_type`

业务语义名称。

```text
PlanPick
ExecuteGrasp
VerifyHeld
```

这不是算法名称。`A*` 不应该成为 `task_type`。

#### `operation_kind`

告诉内核应该把节点交给哪类处理器：

```text
decomposer：
    展开出子节点

system：
    执行选择、规划、验证等非物理操作

physical：
    生成并执行物理请求
```

#### `control_kind`

告诉内核节点的控制结构：

```text
leaf：
    没有业务子节点，执行一个操作

sequence：
    按顺序完成所有子节点

selector：
    在候选分支中选择可行分支
```

当前 Place 参考流程主要使用 sequence。

#### `origin`

节点从哪里来：

```text
program：
    用户程序直接产生

compiler：
    编译器产生

decomposer：
    运行时分解产生

repair：
    故障修复产生
```

这个字段是理解“任务是怎么长出来的”的关键。

#### `parameters`

节点自己的参数。

例如：

```text
scope = task-0001
object_selector = ...
destination_selector = ...
repair_depth = 0
```

参数不是全局变量。每个节点应该尽量只依赖自己声明的参数和契约。

#### `preconditions`

执行前必须为真的条件。

例如：

```text
机器人已经在抓取站位
夹爪没有占用其他物体
对象仍然存在
计划仍然新鲜
```

#### `postconditions`

执行后必须为真的条件。

例如：

```text
ExecuteGrasp:
    object_held(cup-red)

ExecuteRelease:
    object_released(cup-red)

Place:
    object_at(cup-red, drop-zone)
```

#### `max_attempts`

节点最多尝试多少次。

它不是“失败就无限重来”的许可。

#### `max_repairs`

该节点最多允许挂载多少次 repair。

#### `execution_policy`

决定目标已满足时能否跳过执行。

```text
SKIP_IF_GOAL_SATISFIED：
    如果实时世界已经满足目标，可以跳过某些动作

REQUIRE_EXECUTION：
    即使目标看起来满足，也必须执行或展开
```

### 31.3 节点说明书和节点实例的区别

`TaskNodeSpec` 像：

```text
“抓取操作”的函数定义
```

但它还不是本次抓取的进度。

## 32. `TaskNodeRuntime`：同一个节点这一次跑到哪了

### 32.1 人话定义

`TaskNodeRuntime` 是：

> “这个节点在当前这次任务里，实际已经走到了哪里。”

### 32.2 字段逐项解释

```python
TaskNodeRuntime(
    status=NodeStatus.RUNNING,
    phase="execute",
    attempts=1,
    repairs=0,
    expanded=False,
    output_artifacts=(),
    last_diagnostic=None,
    started_at=...,
    finished_at=None,
    adapter_state={},
    active_obligations=(),
)
```

#### `status`

整个节点的粗粒度状态：

```text
pending
running
succeeded
failed
blocked
cancelled
```

#### `phase`

更细的进度。

```text
enter
goal_check
preconditions
execute
verify
children
repair
reconciliation
```

例如：

```text
status=running, phase=verify
```

表示节点仍在运行，但已经执行完动作，正在核实结果。

#### `attempts`

执行尝试计数。

第一次进入执行可能是 1，失败重试后变成 2。

#### `repairs`

这个节点已经经历了几次修复。

注意：

```text
attempts != repairs
```

重试是重新尝试同一个操作；修复是增加一棵处理障碍或前置问题的子树。

#### `expanded`

复合节点是否已经请求过分解。

如果没有这个字段，内核可能每次 tick 都重复添加同一批子节点。

#### `output_artifacts`

该节点产出的工件 ID。

例如：

```text
PlanPick -> pick_navigation-xxx, pick_plan-xxx
```

#### `last_diagnostic`

最近一次结构化失败信息。

它不是简单的异常字符串，而是可用于决定下一步的对象。

#### `adapter_state`

适配层需要保存的运行信息。

例如：

```text
active_request_id
dispatch_attempt
外部 Harness 的关联信息
```

#### `active_obligations`

需要在后续阶段持续保持或检查的义务。

例如：

```text
拿着杯子移动时，杯子必须持续被持有
```

### 32.3 为什么 Runtime 不能由 LLM 自己维护

如果模型自己写：

```json
{"status": "succeeded"}
```

它可以在没有证据时宣称成功。

所以 Runtime 必须由 Kernel 根据真实执行和验证结果更新。

## 33. `TaskEdge` 和 `GraphDelta`：程序怎样长出新的分支

### 33.1 `TaskEdge` 是什么

边是两个节点之间的关系。

```text
parent_id -> child_id
```

当前最重要的两种边：

```text
child：
    正常任务分解关系

repair：
    失败之后挂载的修复关系
```

### 33.2 为什么 repair 不是普通 child

如果所有边都叫 child，看到下面的树时：

```text
PlanTransfer
  └── RouteBlockedRepair
```

你无法知道：

```text
RouteBlockedRepair 是原本就计划的步骤？
还是失败后临时添加的？
```

使用不同边类型后，历史和意图都清晰了。

### 33.3 `GraphDelta` 是什么

`GraphDelta` 是“我想向现有任务图增加哪些东西”的申请。

```text
新增节点：
    AddNode(spec)

新增边：
    AddEdge(edge)

可选根 ID：
    root_id
```

### 33.4 为什么不让分解器直接调用 Store

如果分解器直接写 Store：

```python
store.add_node(...)
store.add_edge(...)
```

它可能：

- 把节点挂到别的任务下；
- 添加越界边；
- 创建环；
- 把另一个根替换掉；
- 添加不可达节点；
- 和另一个写入者产生冲突。

现在的方式是：

```text
分解器只返回 GraphDelta
Kernel 验证 GraphDelta
Kernel 原子挂载 GraphDelta
Kernel 写事件
```

### 33.5 GraphDelta 的验证像什么

它像数据库事务提交前的约束检查：

```text
外键是否存在？
是否违反唯一性？
是否形成非法循环？
是否越过权限边界？
```

## 34. `ExecutionFrame`：为什么需要执行栈

### 34.1 没有栈会发生什么

假设任务结构是：

```text
Place
  └── Pick
      └── ExecuteGrasp
```

如果没有执行栈，系统很难记录：

```text
Place 正在等待 Pick
Pick 正在等待 ExecuteGrasp
ExecuteGrasp 正在 verify
```

### 34.2 栈的直观图

```text
栈底
┌────────────────────────────┐
│ Place       / children     │
├────────────────────────────┤
│ Pick        / children     │
├────────────────────────────┤
│ ExecuteGrasp / verify      │ <- 当前栈顶
└────────────────────────────┘
栈顶
```

当前系统不是靠 Python 调用栈临时记住一切，而是把 `ExecutionFrame` 保存为 Store
的一部分。这对于暂停、观察和恢复很重要。

### 34.3 Frame 字段逐项解释

```python
ExecutionFrame(
    node_id="ExecuteGrasp",
    phase=FramePhase.VERIFY,
    next_child_index=0,
    active_child_id=None,
    original_diagnostic=None,
    last_child_diagnostic=None,
)
```

#### `node_id`

这个栈帧属于哪个节点。

#### `phase`

这个节点当前处于生命周期的哪一段。

#### `next_child_index`

如果是 sequence，下一次要进入第几个子节点。

#### `active_child_id`

当前正在等待哪个子节点返回。

#### `original_diagnostic`

这个帧是否是由某次失败或修复进入的，以及原始诊断是什么。

#### `last_child_diagnostic`

最近一个子节点失败时带回来的诊断。

### 34.4 为什么不用一个 `current_node_id`

因为嵌套任务需要同时知道父节点上下文。

```text
只记录当前节点：
    只知道 ExecuteGrasp

记录完整栈：
    知道它属于 Pick，Pick 又属于 Place
```

修复和返回时尤其需要父节点上下文。

## 35. 事件日志：把“发生过什么”从状态中分离出来

### 35.1 状态和事件的区别

状态回答：

```text
现在是什么样？
```

事件回答：

```text
过去发生过什么？
```

例如当前状态：

```text
PlanTransfer = succeeded
```

但这不能告诉你它是否：

```text
第一次失败后重试成功
先经历过一次修复
使用过两个不同的路线工件
```

事件日志可以保留这些过程。

### 35.2 一个事件的结构

```python
TaskEvent(
    sequence=42,
    event_type="physical_completed",
    node_id=".../NavigateHeld",
    data={
        "request_id": "...",
        "artifact_refs": ["..."],
        "transaction_id": "...",
        "snapshot_ref": "world:6",
    },
)
```

#### `sequence`

事件的顺序号。

#### `event_type`

发生了什么类型的事。

#### `node_id`

哪个任务节点相关。

#### `data`

附加信息，例如工件、事务、诊断、快照引用。

### 35.3 为什么事件必须追加而不是覆盖

如果只保留最后状态：

```text
节点最终 succeeded
```

你无法区分：

```text
一次顺利成功
```

还是：

```text
失败 -> 维修 -> 重试 -> 成功
```

机器人系统需要后者，因为故障和副作用会影响安全、维护和改进。

## 36. `WorldSnapshot`：把“现实”冻结成一张照片

### 36.1 为什么需要快照

现实世界一直在变化。规划器不能在一个没有时间边界的“活对象”上做计算：

```text
规划计算第一半时，杯子在 A
规划计算第二半时，杯子被移动到 B
```

如果输入对象一直变化，规划结果无法解释。

`WorldSnapshot` 的作用是：

> 把某一时刻系统观察到的世界状态冻结下来，供选择、规划和验证读取。

### 36.2 快照的核心字段

```python
WorldSnapshot(
    revision=7,
    frame_graph_revision=2,
    grid=...,
    robot=...,
    entities=...,
)
```

#### `revision`

整个世界快照的版本。

```text
world:7
```

它表示第几次世界状态提交，不一定等于物理命令数。

#### `frame_graph_revision`

坐标系关系的版本。

如果相机、底盘或地图坐标关系变了，即使物体 ID 不变，原计划也可能失效。

#### `grid`

导航使用的栅格地图。

#### `robot`

机器人的观察状态。

#### `entities`

世界中已知的实体集合。

### 36.3 “快照不可变”是什么意思

得到 `world:7` 后，不能直接把它改成：

```text
cup-red 的位置从 A 改成 B
```

正确做法是生成新的：

```text
world:8
```

旧的 `world:7` 仍然保留，方便解释当时的决策。

### 36.4 快照不是传感器原始数据

要区分：

```text
传感器原始数据：
    图像、点云、编码器读数

Observation：
    后端整理后的观察结果

WorldSnapshot：
    WorldModel 接收观察后形成的系统状态快照
```

快照是已经被世界模型组织过的可供业务读取的状态，不一定包含所有原始传感器数据。

## 37. `EntityState`：一个“物体”到底要描述什么

### 37.1 人话定义

`EntityState` 是系统对一个实体当前状态的描述。

实体可以是：

```text
杯子
箱子
目标区域
障碍物
工具
```

### 37.2 字段逐项解释

```python
EntityState(
    entity_id="movable-crate",
    kind="object",
    pose=Pose(...),
    radius=0.25,
    tags=frozenset({"crate"}),
    properties={
        "movable": True,
        "nav_obstacle": True,
        "arm_obstacle": True,
    },
    version=2,
)
```

#### `entity_id`

稳定标识。

稳定很重要，因为：

```text
“这个物体”不能只靠颜色和位置识别
```

任务、工件、诊断和事件都需要引用它。

#### `kind`

实体类别。

```text
object
region
```

类型错误会导致危险操作：

```text
把目标区域当成可抓取对象
把杯子当成放置区域
```

#### `pose`

位姿，不只是二维坐标。

通常包含：

```text
x
y
z
yaw
frame
```

对放置任务而言，朝向和高度也可能影响结果。

#### `radius`

参考几何尺寸。

当前参考模型使用半径进行简化碰撞和距离判断。生产模型通常需要更完整的几何体。

#### `tags`

语义标签。

```text
red
cup
fragile
parking
```

标签适合做语义筛选，但不应该单独承担几何碰撞判断。

#### `properties`

领域属性。

示例：

```text
movable：
    是否允许作为可移动对象

nav_obstacle：
    是否应该进入底盘导航占用图

arm_obstacle：
    是否应该进入机械臂碰撞检查

bounds：
    区域的边界

acceptance_radius：
    目标接受物体的距离容差

yaw_tolerance：
    目标允许的朝向误差
```

#### `version`

这个实体自身的变化版本。

如果只有别的实体移动，`cup-red` 的版本可能不变；如果杯子自己移动，版本增加。

### 37.3 为什么“位置”和“属性”都要有版本

计划可能依赖的不只是位置：

```text
对象是否可移动
区域容差是多少
障碍物是否参与导航
```

属性变了，也应该让依赖这个属性的工件重新检查。

## 38. `RobotState`：机器人是一个复合实体

### 38.1 不能只保存底盘位置

对于移动操作臂，至少要知道：

```text
底盘位置
机械臂关节
夹爪开闭状态
当前持有物
机器人状态代数
```

否则系统可能生成：

```text
从错误的机械臂起始姿态开始的路径
```

### 38.2 字段逐项解释

#### `base_pose`

移动底盘的位置和朝向。

#### `joints`

当前关节配置。

它是机械臂路径连续性的起点。

#### `gripper_open`

夹爪是否打开。

它影响：

```text
是否能抓取
是否正在持物
是否能释放
```

#### `held_object_id`

当前被认为夹持的对象 ID。

它是状态模型中的记录，不应该只靠“上一个抓取命令成功”推断。

#### `state_epoch`

机器人的状态变化代数。

可以把它想成：

```text
机器人状态的版本号
```

如果机械臂移动过，基于旧关节状态生成的 `PickPlan` 可能过期。

## 39. `Observation` 和 `WorldModel`：现实怎样回到程序

### 39.1 `Observation` 是什么

`Observation` 是一次对现实的观察结果，通常由后端在动作后产生：

```python
Observation(
    robot=RobotState(...),
    entities=(...),
    frame_graph_revision=...,
)
```

它回答：

```text
动作后机器人现在在哪？
夹爪是什么状态？
手里是否有物体？
已知实体现在在哪里？
坐标系关系有没有变化？
```

### 39.2 `WorldModel.ingest()` 做什么

可以把它简化成：

```text
收到 Observation
    ↓
比较机器人旧状态和新状态
    ↓
必要时增加 state_epoch
    ↓
比较每个实体旧状态和新状态
    ↓
必要时增加 entity.version
    ↓
保存新实体集合
    ↓
增加 WorldSnapshot.revision
    ↓
生成新的快照
```

### 39.3 为什么观察后不能直接改任务状态

观察只告诉系统现实是什么样。

它不应该自己决定：

```text
所以任务成功
```

任务成功还需要把观察状态与节点的目标谓词比较。这个比较由语义和验证阶段完成。

这叫：

```text
观察负责提供事实
验证负责解释事实
内核负责根据验证结果推进程序
```

## 40. 版本：为什么要同时有 world revision、entity version 和 state epoch

### 40.1 三种版本解决三个粒度的问题

```text
WorldSnapshot.revision：
    整个世界快照变了几次

EntityState.version：
    某个实体自己变了几次

RobotState.state_epoch：
    机器人状态变了几次
```

### 40.2 举例

初始：

```text
world:0
cup-red.version = 0
robot.state_epoch = 0
```

机器人移动底盘：

```text
world:1
cup-red.version = 0
robot.state_epoch = 1
```

杯子被人挪动：

```text
world:2
cup-red.version = 1
robot.state_epoch = 1
```

### 40.3 为什么不只用 world revision

如果只知道 `world:2` 和 `world:0` 不同，却不知道谁变了，系统只能粗暴地让所有计划重算。

实体和机器人粒度版本允许更精确地判断：

```text
只依赖 cup-red 的工件要重算
只依赖 robot joints 的工件也要重算
与这两者无关的某些数据可以继续复用
```

## 41. `Artifact`：计划不是一句话，而是一件有出处的东西

### 41.1 人话定义

工件就是：

> “某个能力根据某个世界快照算出来、可以被后续节点消费的计划结果。”

### 41.2 为什么叫工件

因为它不是最终目标，也不是原始输入，而是中间产物：

```text
世界快照 + 规划算法
    -> NavigationPlan

绑定结果 + 抓取能力
    -> PickPlan
```

### 41.3 工件必须有出处

一个裸路径：

```text
[(0.2,0.3), (0.3,0.4), (0.4,0.5)]
```

不够安全。它至少还要知道：

```text
基于哪个世界？
地图是什么版本？
障碍物有哪些？
机器人当时在哪个状态？
碰撞模型是什么？
载荷是什么？
```

### 41.4 工件是不可变的

旧计划不能原地改成新计划：

```text
错误：
    transfer_plan.path = new_path
```

正确：

```text
保留旧 transfer_plan
发布新 transfer_plan
让 alias 指向新版本
```

### 41.5 为什么不可变很重要

不可变让系统能回答：

```text
这次动作到底使用了哪一版路线？
```

如果旧工件被覆盖，历史就无法解释。

## 42. Freshness：如何判断工件还能不能用

### 42.1 人话定义

Freshness 就是“这个计划仍然适合当前现实吗？”

### 42.2 依赖比较

工件记录：

```text
source_snapshot = world:3
entity:cup-red = 0
map = 1
robot = 2
```

当前快照：

```text
world:4
entity:cup-red = 1
map = 1
robot = 2
```

结论：

```text
cup-red 变了
所以依赖它的工件过期
```

### 42.3 为什么不只比较快照编号

`world:4` 比 `world:3` 新，不代表所有内容都变了。

系统真正关心的是：

```text
我依赖的内容有没有变？
```

### 42.4 导航占用指纹

路线还依赖一组障碍物的整体占用情况。即使某个障碍物没有明显的单体字段变化，
新增障碍物也应该让路线失效。

因此导航计划可以记录：

```text
nav_occupancy fingerprint
```

这个指纹把地图静态占用和参与导航的实体组合起来。

### 42.5 忽略集合

机器人运输一个物体时，那个物体的位置预期会随着机器人一起变化。

如果计划把被运输物体自己当作固定障碍物，就会错误地判定路线过期。

因此计划可以声明：

```text
ignored_entity_ids = {cup-red}
```

但“忽略”不是完全不检查载荷。载荷的几何变化仍通过
`payload_transform_hash` 等字段单独检查。

## 43. `ActionRequest`：动作请求不是后端调用

### 43.1 人话定义

`ActionRequest` 是：

> “物理技能希望运行时替它执行的一份规范化请求。”

它还不是最终的 SDK 调用。

### 43.2 请求示例

```python
ActionRequest(
    request_id="tree:task-0001:ExecuteGrasp:attempt:1",
    action_name="ExecuteGrasp",
    commands=(
        ArmPathCommand(path=(...)),
        GripperCommand(close=True, object_id="cup-red"),
    ),
    resources=frozenset({"arm", "gripper"}),
    artifact_refs=("pick-plan-...",),
    preconditions=(
        PredicateFormula("base_near", {...}),
        PredicateFormula("joints_near", {...}),
    ),
)
```

### 43.3 `request_id`

这是一次物理请求的身份证。

如果网络超时，上层可能不确定请求是否已经派发。稳定 ID 可以让 Gateway 识别：

```text
这是同一个请求，不要再执行一次
```

### 43.4 `action_name`

动作的业务名称。

```text
NavigateToPickStance
ExecuteGrasp
NavigateHeld
ExecuteRelease
```

### 43.5 `commands`

真正需要后端执行的命令集合。

参考实现有：

```text
NavigateCommand
ArmPathCommand
GripperCommand
```

注意：这些仍是规范化命令对象，不是厂商 SDK 对象。

### 43.6 `resources`

动作要占用哪些资源：

```text
base
arm
gripper
```

如果一个动作需要 `arm` 和 `gripper`，运行时可以防止另一个动作同时抢用它们。

### 43.7 `artifact_refs`

请求明确声明自己消费了哪些工件。

这样运行时可以：

```text
检查工件是否存在
检查工件类型
检查工件是否新鲜
记录动作与计划的关系
```

### 43.8 `preconditions`

请求级前置条件是最后一道快照保护。

即使任务节点之前检查过，动作真正发送前仍应重新检查，因为现实可能已经变化。

## 44. `HarnessRuntime`：为什么它像操作系统内核

### 44.1 类比

普通程序不能直接把磁盘控制器的电压打出去，而是通过操作系统系统调用。

同样，任务节点不能直接把任意命令打给机器人，而要经过：

```text
HarnessRuntime
```

### 44.2 Runtime 负责哪些门禁

```text
工件新鲜度
    ↓
当前快照谓词
    ↓
资源租约
    ↓
checkpoint
    ↓
后端执行
    ↓
逐命令观察
    ↓
提交或对账
```

### 44.3 为什么物理技能不能直接拿 Backend

如果技能拥有后端：

```python
backend.execute(...)
```

它可以绕过：

```text
freshness
guards
leases
transaction
observation
```

这会让系统出现不同的物理入口：

```text
有些动作走安全链
有些动作偷偷绕过安全链
```

唯一入口的价值是让所有副作用都经过同样的审查。

## 45. `Verifier`：为什么“判断”必须和“执行”分开

### 45.1 判断和改变是两种完全不同的能力

```text
执行：
    让杯子移动

判断：
    杯子现在是否在目标区？
```

如果同一个函数既移动又判断，调用者很难知道：

```text
判断结果来自观察？
还是函数自己假定动作成功？
```

### 45.2 谓词是什么

谓词就是一个可以被判断真假的条件：

```text
object_held(cup-red)
object_released(cup-red)
object_at(cup-red, drop-zone)
base_near(target_pose)
joints_near(target_joints)
```

### 45.3 `object_at` 的实际判断

大致过程：

```text
读取对象位姿
读取目标位姿
计算三维距离
读取目标允许半径
检查距离是否在容差内
如果目标规定朝向，再检查 yaw 误差
返回真假和证据
```

它不是：

```text
看到 ExecuteRelease 返回成功
就直接返回 true
```

### 45.4 证据为什么要和结果一起返回

只有 `true` 不够解释。系统还需要：

```text
测得距离 = 0.07
容差 = 0.15
朝向误差 = 0.03
快照 = world:14
```

这样人和调试程序才能知道为什么成立。

## 46. `Diagnostic`：失败不是字符串

### 46.1 糟糕的错误

```text
失败了
```

这句话不能驱动下一步。

### 46.2 结构化诊断

```python
Diagnostic(
    code="ROUTE_BLOCKED",
    message="Transfer route is blocked",
    details={
        "blocker_id": "movable-crate",
        "snapshot_ref": "world:0",
    },
    retryable=True,
    repairable=True,
)
```

### 46.3 字段逐项解释

#### `code`

机器可识别的故障类别。

```text
ROUTE_BLOCKED
STALE_PLAN
HOLD_LOST
OUTCOME_UNKNOWN
GUARD_FAILED
```

#### `message`

给人看的概括。

#### `details`

下一步修复需要的具体事实。

```text
哪个障碍物？
哪一个请求？
哪一个工件？
哪一个快照？
```

#### `retryable`

同一个操作是否有意义地重试。

#### `repairable`

是否可能通过增加修复任务解决。

### 46.4 为什么两个布尔字段不能替代故障代码

```text
retryable=true
repairable=true
```

仍然不知道应该怎么修复。

`code` 和 `details` 才能把诊断传给正确的处理器。

## 47. `RepairProposal`：修复方案也必须是程序

### 47.1 人话定义

修复提案是：

> “针对某次失败，建议增加哪棵子树、从哪个节点进入，以及为什么这样修。”

### 47.2 典型结构

```python
RepairProposal(
    delta=GraphDelta(...),
    entry_node_id=".../repair-0/route-blocked",
    rationale="...",
    invalidates_artifacts=(),
    persistent_obligations=(),
)
```

### 47.3 为什么修复器不能直接执行

修复器只了解“如何修”，但不应该拥有：

```text
Store 写权限
机器人后端权限
最终状态裁决权
```

它提出建议，Kernel 审查和挂载。

### 47.4 为什么修复也要遵循同一套任务语义

如果主任务使用：

```text
节点
前置条件
后置条件
观察
验证
事件
```

而修复任务只是隐藏脚本，那么系统会出现两个世界：

```text
正常任务可追踪
修复任务不可追踪
```

当前设计让修复也成为任务树的一部分，因此修复可以继续递归、验证和审计。

## 48. `reconciliation`：物理世界不能假装支持回滚

### 48.1 软件回滚和物理回滚

软件中：

```text
x = 1
x = 2
rollback -> x = 1
```

真实机器人中：

```text
机械臂已经移动
通信突然断开
```

你不能仅靠内存把机械臂瞬移回去。

### 48.2 对账是什么意思

对账不是“把系统状态改成我希望的样子”，而是：

```text
重新观察真实设备
比较计划状态和真实状态
确认哪些副作用已经发生
确认哪些没有发生
决定下一步
```

### 48.3 三种可能结果

```text
确认成功：
    可以继续后续任务

确认失败：
    进入失败处理或修复

仍然不确定：
    暂停、人工介入或再次观察
```

### 48.4 为什么 `OUTCOME_UNKNOWN` 不能直接重试

如果上一条抓取请求其实已经成功，而系统因为超时再次发送抓取：

```text
可能重复夹取
可能撞击物体
可能破坏当前持物状态
```

所以结果不明时必须先对账。

## 49. 一次 `Place` 任务的极细执行顺序

下面把正常任务拆成尽量小的步骤。

### 49.1 第 0 步：用户提交意图

```text
把红杯子放到收纳区
```

此时系统只有意图，还没有：

```text
绑定对象
抓取姿态
导航路线
物理命令
```

### 49.2 第 1 步：规范化

LLM 或 Planner Adapter 将话语变成：

```text
action = place
object_selector = ...
destination_selector = ...
```

此时只回答：

```text
任务是什么
```

还没有回答：

```text
具体选中了哪个实体
```

### 49.3 第 2 步：编译根节点

编译器创建：

```text
program/task-0001
```

它的 `task_type` 是 `Place`，`operation_kind` 是 `decomposer`。

内核初始化：

```text
Store.root_id = program/task-0001
Runtime.status = pending
Stack = []
Events = []
```

### 49.4 第 3 步：进入根节点

Kernel push：

```text
Frame(node=Place, phase=ENTER)
```

更新运行态：

```text
Place.status = running
Place.attempts = 1
```

### 49.5 第 4 步：目标检查

内核先问：

```text
任务目标是不是已经满足？
```

如果目标已经满足且节点允许跳过，可能直接成功。

对 `Place` 这样的结构节点，通常还需要继续展开，以保留结构和执行语义。

### 49.6 第 5 步：前置条件

检查：

```text
输入参数是否存在
选择器是否合法
必要的世界信息是否存在
```

### 49.7 第 6 步：调用分解器

`Place` 是 decomposer 节点，分解器返回：

```text
ResolveAndInspect
PlanPick
PlanTransfer
Pick
TransferHeld
Release
VerifyPlaceGoal
```

Kernel 验证 `GraphDelta`，一次性挂载正常 child 边。

### 49.8 第 7 步：进入 `ResolveAndInspect`

系统操作读取 `WorldSnapshot`。

```text
object_selector -> cup-red
destination_selector -> drop-zone
```

发布 `BindingArtifact`。

节点成功的含义是：

```text
绑定结果已经产生并且已存入工件存储
```

不是：

```text
杯子已经移动
```

### 49.9 第 8 步：进入 `PlanPick`

读取绑定工件和当前世界。

调用：

```text
底盘站位规划
逆运动学
机械臂路径规划
碰撞检查
```

发布：

```text
pick_navigation
pick_plan
```

节点成功的含义是：

```text
计划已经生成
```

不是：

```text
机器人已经到达
```

### 49.10 第 9 步：进入 `PlanTransfer`

在空手或预期载荷模型下规划运输路线。

如果路线被挡：

```text
返回 ROUTE_BLOCKED
```

此时进入故障处理，而不是直接进入 `Pick`。

### 49.11 第 10 步：进入 `NavigateToPickStance`

PhysicalSkill 消费 `PickPlan` 中引用的导航工件，生成 `ActionRequest`。

Runtime 检查：

```text
导航工件是否新鲜
底盘资源是否可租
路线是否满足安全策略
```

通过后才交给后端。

### 49.12 第 11 步：动作后的观察

底盘移动后：

```text
RobotBackend.observe()
    -> Observation
    -> WorldModel.ingest()
    -> world:1
```

Kernel 不应该仅依据“后端函数没有抛异常”就把节点标成成功。

### 49.13 第 12 步：进入 `ExecuteGrasp`

请求包含：

```text
ArmPathCommand
GripperCommand(close=True)
```

前置条件可能包含：

```text
底盘接近抓取站位
机械臂关节在预期起点
```

动作后得到新的观察：

```text
gripper_open = false
held_object_id = cup-red
```

### 49.14 第 13 步：`VerifyHeld`

验证器判断：

```text
object_held(cup-red)
```

只有此时，系统才有证据认为已经抓住。

### 49.15 第 14 步：持物运输

依次完成：

```text
MoveToTransportPosture
NavigateHeld
VerifyPlacementReady
```

每个物理动作后都要更新世界并重新检查持物义务。

### 49.16 第 15 步：释放

`ExecuteRelease` 执行：

```text
移动到放置姿态
打开夹爪
```

然后 `VerifyReleased` 检查：

```text
held_object_id != cup-red
```

### 49.17 第 16 步：最终验证

`VerifyPlaceGoal` 检查：

```text
object_at(cup-red, drop-zone)
```

最终成功不是由某个动作节点单独决定，而是由目标谓词和现实快照共同决定。

## 50. 一个“计划过期”的极细例子

### 50.1 计划生成

```text
world:3
cup-red at (0.7, 0.4)
robot.state_epoch = 2
```

生成：

```text
PickPlan P1
metadata:
    snapshot_ref = world:3
    entity:cup-red = 0
    arm = fingerprint(...)
    robot_state_epoch = 2
```

### 50.2 现实变化

人在机器人执行前把杯子移到了：

```text
cup-red at (0.9, 0.5)
```

观察后：

```text
world:4
cup-red.version = 1
```

### 50.3 发送前检查

FreshnessChecker 比较：

```text
工件需要 entity:cup-red = 0
当前是 entity:cup-red = 1
```

返回：

```text
fresh = false
diagnostic = STALE_PLAN
```

### 50.4 正确处理

```text
不发送旧路径
旧计划保留用于审计
发布新的 PickPlan P2
让别名 pick_plan 指向 P2
重新进入物理节点
```

### 50.5 错误处理

```text
不检查 freshness
直接执行 P1
```

可能导致：

```text
机械臂伸向旧位置
撞击桌面
抓空
碰倒附近物体
```

这就是为什么“工件版本”不是文档字段，而是执行安全机制。

## 51. 一个“选择歧义”的极细例子

世界中有两个红杯子：

```text
cup-red-left
cup-red-right
```

用户说：

```text
把红杯子放到收纳区
```

### 51.1 系统不能直接猜

如果选择器只要求：

```text
tags={red,cup}
```

那么两个都符合。

### 51.2 三种可选政策

#### 政策 A：稳定规则自动选

```text
按 stable_id 选字典序较小者
```

优点：

```text
可重复
```

缺点：

```text
未必符合人真正指的那个
```

#### 政策 B：加入空间关系

```text
选择 leftmost
```

优点：

```text
更接近用户描述
```

缺点：

```text
“左”相对于谁仍需明确
```

#### 政策 C：请求澄清

```text
你是指左边的红杯子，还是右边的红杯子？
```

这通常是风险较高任务的更好选择。

### 51.3 重要结论

任务内核只能执行已经结构化的选择结果。

```text
语义歧义的解决属于意图层
```

内核负责保证：

```text
一旦选择规则被接受，绑定过程可解释、可重复、可验证
```

## 52. 一个“动作结果不明”的极细例子

### 52.1 事件

```text
ExecuteGrasp 已经向机器人发送
网络连接随后中断
```

系统现在不知道：

```text
夹爪是否闭合
杯子是否被抓住
动作执行到哪一个路径点
```

### 52.2 不能做的事

```text
直接标记 succeeded
```

或者：

```text
直接再次发送 ExecuteGrasp
```

### 52.3 正确流程

```text
记录 OUTCOME_UNKNOWN
保存 request_id 和 transaction_id
进入 reconciliation
重新调用观察能力
更新 WorldSnapshot
```

### 52.4 对账后的分支

```text
观察到 held_object_id = cup-red
    -> 原目标已经部分完成
    -> 跳过重复抓取，继续运输

观察到 held_object_id = null
    -> 抓取没有成立
    -> 重新检查工件和站位
    -> 允许有限重试

观察仍然无法确认
    -> 暂停或人工介入
```

## 53. 为什么“移动障碍物”必须是任务

### 53.1 把它藏在规划器里会发生什么

假设 A* 内部做了这样的事：

```text
发现箱子挡路
把箱子从占用图中删除
重新规划一条路线
```

数学上可能得到可行路径，但现实中箱子并没有移动。

### 53.2 正确的语义

```text
箱子挡路
    -> 产生 ROUTE_BLOCKED
    -> 诊断识别 movable-crate
    -> 生成 Place(movable-crate, parking-zone)
    -> 真实执行搬箱子
    -> 观察箱子新位置
    -> 生成新世界快照
    -> 重新规划杯子任务
```

### 53.3 这相当于什么

在普通程序里，它类似：

```python
try:
    transfer(cup)
except RouteBlocked:
    place(crate, parking_zone)
    transfer(cup)
```

区别是机器人系统把这个异常分支显式保存为任务树，并且每个物理动作都需要现实证据。

## 54. 读代码时的“谁读谁写”总表

### 54.1 主要对象所有权

| 对象 | 创建或发布者 | 主要读取者 | 主要写入者 |
| --- | --- | --- | --- |
| `TaskProgram` | LLM / Planner Adapter / 用户输入 | Compiler | 创建后不可变 |
| `TaskNodeSpec` | Compiler / Decomposer / Repair | Kernel / Inspector | Store 通过 Kernel |
| `TaskNodeRuntime` | Store 初始化 | Kernel / Inspector | Kernel |
| `TaskEdge` | GraphDelta 提案 | Kernel / Inspector | Kernel |
| `ExecutionFrame` | Kernel | Kernel / Inspector | Kernel |
| `WorldSnapshot` | WorldModel | Selector / Planner / Verifier | 由新观察生成新版本 |
| `EntityState` | 初始场景或 Observation | WorldModel / Planner | WorldModel 生成新状态 |
| `Artifact` | 规划或绑定操作 | Skills / Runtime / Inspector | 发布后不可变 |
| `ActionRequest` | PhysicalSkill | HarnessRuntime | 创建后不可变 |
| `Observation` | RobotBackend | WorldModel / Runtime | 新观察对象不可变 |
| `Diagnostic` | 操作、技能、语义或 Runtime | Kernel / RepairResolver | 创建后不可变 |
| `RepairProposal` | RepairResolver | Kernel | 创建后不可变 |

### 54.2 最关键的三条

```text
谁都可以提议，但只有 Kernel 可以改任务树。
谁都可以计算计划，但只有 Runtime 可以触碰后端。
谁都可以报告观察，但只有 WorldModel 可以生成新的世界快照。
```

## 55. 当前系统中几个最容易误解的地方

### 55.1 误解：LLM 是系统的大脑，所以它控制全部流程

更准确：

```text
LLM 提出高层意图和可能的策略
Kernel 按生命周期规则推进
Runtime 按权限和安全规则执行
Verifier 根据事实判定结果
```

LLM 不是唯一决策者。

### 55.2 误解：任务树就是待办清单

待办清单通常只记录：

```text
未完成 / 已完成
```

任务树还记录：

```text
控制结构
节点类型
执行阶段
前后置条件
尝试次数
修复关系
运行栈
事件
```

它更像可执行程序。

### 55.3 误解：工件就是缓存

缓存通常只关心：

```text
有没有旧结果
```

工件还关心：

```text
结果基于什么世界
依赖哪些版本
是否仍然适用
谁产生
谁消费
```

它是带契约的中间产物，不是随便放在内存里的缓存。

### 55.4 误解：观察只是日志

观察不是打印：

```text
robot moved
```

它会改变 WorldModel 的事实版本，进而影响：

```text
工件新鲜度
谓词验证
后续规划
修复判断
```

### 55.5 误解：修复就是重试

重试：

```text
同一个节点，再做一次
```

修复：

```text
先增加另一棵处理原因的子树
再回到原节点或继续原目标
```

## 56. 这套架构的“程序语义”可以怎样写

### 56.1 节点的语义

可以把一个节点理解为一个带条件的函数：

```text
Node(node_spec, world, artifacts)
    -> success
    -> failure(diagnostic)
    -> graph_delta
    -> action_request
```

### 56.2 物理节点的特殊性

普通函数通常是：

```text
输入 -> 输出
```

物理节点更像：

```text
输入快照 + 计划工件
    -> ActionRequest
    -> 外部世界发生变化
    -> Observation
    -> 新快照
    -> 验证结果
```

所以它不是纯函数，必须被 Runtime 包裹。

### 56.3 任务树的状态转换

```text
PENDING
    -> RUNNING
    -> SUCCEEDED
```

失败路径：

```text
RUNNING
    -> FAILED
    -> RETRY
    -> RUNNING
```

修复路径：

```text
RUNNING
    -> Diagnostic
    -> repair subtree
    -> RUNNING
```

物理不确定路径：

```text
RUNNING
    -> OUTCOME_UNKNOWN
    -> RECONCILIATION
    -> SUCCEEDED / FAILED / BLOCKED
```

## 57. 为什么这不是“自然语言自修改代码”

它有一点像自修改程序：

```text
运行时可以增加任务节点
```

但有四个重要限制：

1. 增量必须符合 `GraphDelta` 结构；
2. Kernel 必须验证作用域、可达性和无环性；
3. 新节点仍然只能使用已注册的节点处理器；
4. 物理动作仍必须经过 `ActionRequest` 和 Runtime。

因此更准确的说法是：

> 这是受限的、类型化的、领域专用运行时扩展，不是任意代码注入。

## 58. 对“自然语言驱动运行时”的最终分层判断

### 58.1 语言驱动了什么

```text
任务目标
任务参数
语义选择
高层约束
失败解释和修复意图
```

### 58.2 语言没有直接驱动什么

```text
电机电流
控制器采样周期
关节底层伺服
碰撞检测每一帧的实现
后端通信协议
真实世界事实的最终裁决
```

### 58.3 中间真正起作用的东西

```text
TaskProgram
TaskNodeSpec
TaskNodeRuntime
GraphDelta
Artifact
ActionRequest
WorldSnapshot
Observation
Diagnostic
RepairProposal
```

所以“自然语言驱动”说的是入口和高层语义，不是说自然语言贯穿到底层每一个控制周期。

## 59. 你可以如何判断一个新模块是否放对了位置

遇到一个新需求时，问下面的问题。

### 问题一：它是在表达目标，还是在执行动作？

```text
表达目标：
    TaskProgram / 节点参数

执行动作：
    PhysicalSkill -> ActionRequest -> Runtime
```

### 问题二：它需要读现实吗？

```text
需要：
    通过 WorldModel / snapshot

不需要：
    不要偷偷读取 backend 内部状态
```

### 问题三：它会产生现实副作用吗？

```text
会：
    必须经过 physical node 和 Runtime

不会：
    可以是 system operation 或 capability
```

### 问题四：它是在算结果，还是在改变结果？

```text
算路径：
    capability -> artifact

改变世界：
    ActionRequest -> backend
```

### 问题五：失败后需要增加任务吗？

```text
需要：
    Diagnostic -> RepairProposal -> GraphDelta

不需要：
    由普通 retry 或终止处理
```

## 60. 生产化时最重要的补课

从参考架构走向真实生产系统，最需要补的不是再加一个更大的模型，而是以下基础设施。

### 60.1 持久化

需要可靠保存：

```text
任务树
运行态
执行栈
事件
工件
别名
事务
```

### 60.2 真实机器人对账

真实后端必须明确：

```text
哪些动作可取消
哪些动作不可回滚
怎样获取可靠观察
结果不明时怎样恢复
```

### 60.3 感知不确定性

需要处理：

```text
识别置信度
对象遮挡
多个候选
位置误差
传感器延迟
```

### 60.4 安全策略

需要把安全从参考几何模型扩展到：

```text
真实碰撞几何
接触规则
速度和力限制
急停
人机协作区域
硬件互锁
```

### 60.5 多任务和多机器人

需要增加：

```text
调度器
资源仲裁
任务优先级
抢占
冲突检测
跨树一致性
```

当前系统的串行任务边界是清晰的起点，但不是终点。

## 61. 作为学习者，建议按三个层次复习

### 第一层：会说人话

你能解释：

```text
TaskProgram 是用户目标的结构化表达
任务树是执行步骤和恢复路径
WorldSnapshot 是现实快照
Artifact 是带版本的计划
ActionRequest 是物理请求
Observation 是动作后的证据
```

### 第二层：会画数据流

你能画出：

```text
自然语言
    -> TaskProgram
    -> TaskNodeSpec
    -> Runtime
    -> Artifact
    -> ActionRequest
    -> Observation
    -> WorldSnapshot
    -> Verifier
```

### 第三层：会追一次故障

你能回答：

```text
路线为什么失败？
诊断里哪个字段说明了障碍物？
为什么这个障碍物可以被移动？
修复树挂在哪里？
哪些工件因此过期？
世界版本如何推进？
最终成功由哪个谓词证明？
```

达到第三层，才算真正理解，而不是背术语。

## 62. 最后再回答一次核心问题

问题：

```text
这套系统是不是把机器人的执行过程，
变成了一套自然语言驱动的运行时代码？
```

分句回答：

### “自然语言驱动”——部分是

自然语言影响：

```text
任务目标
对象选择
目标选择
约束
高层修复意图
```

### “运行时代码”——基本是

任务递归树拥有：

```text
结构
控制流
运行状态
调用栈
输入输出
前置条件
后置条件
异常路径
动态增量
事件轨迹
```

### “机器人执行过程”——是

物理动作被纳入：

```text
计划
请求
资源
事务
观察
验证
对账
```

### “自然语言直接变成机器控制”——不是

底层控制仍然由：

```text
规划器
技能
HarnessRuntime
RobotBackend
控制器
```

负责。

最终最严谨的结论是：

> **这套系统把自然语言目标编译成一个受约束的机器人领域运行时程序，并让这个程序在持续变化的物理世界中被解释、观察、验证和修复。它不是把自然语言直接翻译成电机指令，而是把自然语言放在了一个有类型、有权限、有证据、有恢复机制的执行系统入口。**

