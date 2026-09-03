const fs = require("fs");
const path = require("path");

const PptxGenJS = require(path.resolve(
  __dirname,
  "..",
  ".artifacts",
  "ppt-build",
  "node_modules",
  "pptxgenjs",
));

const ROOT = path.resolve(__dirname, "..");
const DOCS = path.join(ROOT, "docs");
const EXAMPLES = path.join(ROOT, ".artifacts", "ppt-examples");
const OUT_DIR = path.join(DOCS, "presentation");
const OUT_FILE = path.join(
  DOCS,
  "Task-Recursive-Tree-Agent-架构讲解-老板CTO版.pptx",
);
const OUT_NOTES = path.join(
  DOCS,
  "Task-Recursive-Tree-Agent-架构讲解-老板CTO版-讲稿.md",
);
const OUT_MANIFEST = path.join(
  DOCS,
  "Task-Recursive-Tree-Agent-架构讲解-老板CTO版-目录.json",
);

const pptx = new PptxGenJS();
pptx.layout = "LAYOUT_WIDE";
pptx.author = "Task Recursive Tree";
pptx.company = "Task Recursive Tree";
pptx.subject = "Task Recursive Tree Agent architecture briefing";
pptx.title = "Task Recursive Tree Agent 架构讲解";
pptx.lang = "zh-CN";
pptx.theme = {
  headFontFace: "Microsoft YaHei",
  bodyFontFace: "Microsoft YaHei",
  lang: "zh-CN",
};
pptx.defineSlideMaster({
  title: "TRT_MASTER",
  background: { color: "F4F6F3" },
  objects: [],
});
pptx.writeOptions = { compression: true };

const SW = 13.333;
const SH = 7.5;
const C = {
  bg: "F4F6F3",
  paper: "FFFFFF",
  ink: "18303A",
  muted: "61727A",
  faint: "8A989E",
  line: "D4DDDF",
  coral: "D96C4C",
  coralLight: "FFF0EA",
  teal: "187F75",
  tealLight: "E7F4F1",
  gold: "C48B24",
  goldLight: "FFF5D9",
  blue: "4B78A8",
  blueLight: "EAF1FA",
  purple: "74559A",
  purpleLight: "F1ECF8",
  green: "5A8C55",
  greenLight: "EDF6E9",
  repair: "B34A70",
  repairLight: "FBEAF0",
  dark: "202A30",
  white: "FFFFFF",
  red: "B84040",
};
const S = pptx.ShapeType;
const FONT = "Microsoft YaHei";
const MONO = "Consolas";
const slides = [];

// Speaker notes are deliberately written for two audiences. The slide
// remains concise; the notes carry the explanation, likely questions, and
// transition needed for a live architecture briefing.
const SPEAKER_GUIDE = {
  1: {
    oneLine: "这不是一个更会聊天的机器人，而是一套把 Agent 决策变成可控执行的运行时。",
    boss: "可以把它理解成给 Agent 加了一层“项目经理 + 现场总控”：模型负责理解目标，系统负责按规则推进、留证和恢复。",
    cto: "LLM 最终只提交受约束的 TaskProgram；TaskTreeKernel 解释生命周期并写入唯一的 TaskTreeStore；Harness 承担世界、工具和物理后端。",
    emphasize: [
      "演示重点不是模型回答得多漂亮，而是任务进入真实世界后仍然可观察、可验证、可恢复。",
      "整套架构围绕任务树、世界模型和规划工件三类对象展开。",
    ],
    questions: [
      ["是不是把 LLM 排除在系统之外？", "不是。LLM 负责自然语言理解、澄清和高层意图；它不直接修改运行时树，也不直接拥有物理后端。"],
    ],
    transition: "接下来先用四句话说明这套架构到底解决了什么。",
  },
  2: {
    oneLine: "四个关键词概括全部设计：分离、控制、证据、修复。",
    boss: "这四个词对应四种经营结果：降低误动作风险、看得懂过程、出了问题能恢复，而不是只能人工重来。",
    cto: "分离对应意图与副作用的边界；控制对应 Kernel 和 Store；证据对应快照、事件、工件和事务；修复对应 Diagnostic 到 repair subtree 的闭环。",
    emphasize: [
      "先记住“模型可能做什么”和“系统现在允许做什么”是两件事。",
      "后面的架构图和案例都在验证这四个判断，而不是增加模块数量。",
    ],
    questions: [
      ["为什么不直接把这些逻辑放进提示词？", "提示词不能成为唯一的权限、状态和事务边界；真正的副作用必须由代码契约和运行时闸门约束。"],
    ],
    transition: "为了让后面的术语不抽象，先把系统里最重要的三个对象讲清楚。",
  },
  3: {
    oneLine: "任务树回答“接下来做什么”，世界模型回答“现场现在是什么”，规划工件回答“基于这个现场怎么做”。",
    boss: "可以类比成三样东西：任务树是待办清单和过程记录，世界模型是现场账本，规划工件是带版本的施工图。",
    cto: "三者不能混成一个大上下文：Store 管树状态，WorldModel 提供不可变快照，Artifact 记录生产者、依赖版本、快照引用和消费契约。",
    emphasize: [
      "树决定顺序，但不凭空猜现实；世界提供证据，但不替任务决定顺序。",
      "工件是两者之间的连接层，世界变了就发布新工件，而不是偷偷改旧计划。",
      "LLM 输出的是意图，不是这三个对象的运行时权威状态。",
    ],
    questions: [
      ["为什么不能只保留一份 JSON？", "一份混合 JSON 会把意图、运行态、现实证据和计划版本混在一起，难以校验所有权，也无法精确判断哪个计划过期。"],
    ],
    transition: "有了这三个对象，再看为什么“直接工具调用”在真实场景里会失去控制。",
  },
  4: {
    oneLine: "直接让 LLM 调机器人 API，短期快，长期会把状态、权限和恢复逻辑变成黑盒。",
    boss: "低风险的一次性自动化可以这样做；但只要动作会改变现场、任务会连续执行，就必须知道每一步为什么做、做完后现场是否真的变了。",
    cto: "黑盒方案缺少统一的执行栈、快照边界、工件新鲜度、物理事务和恢复入口；失败时常见的“再试一次”还可能重复副作用。",
    emphasize: [
      "这里不是否定传统 Agent，而是明确它的适用边界。",
      "Task Recursive Tree 把黑盒调用拆成意图、树、请求、观测和诊断几个可检查阶段。",
    ],
    questions: [
      ["增加控制层会不会让系统更慢？", "会增加少量编排和验证开销，但换来可审计、可重放和局部恢复；真实物理系统通常更怕错误动作而不是多几毫秒。"],
    ],
    transition: "因此，架构的关键不是堆模块，而是先确定哪些边界必须由谁拥有。",
  },
  5: {
    oneLine: "六条设计原则把隐式行为变成显式、可校验的状态。",
    boss: "原则最终服务于三个结果：出了问题能定位，恢复时不乱动，换设备或算法时不用推倒重来。",
    cto: "单一所有权解决写冲突，类型化上下文解决权限扩散，任务与世界分层解决连续会话，工件不可变解决版本追踪，观测和递归修复解决闭环。",
    emphasize: [
      "这些不是代码风格，而是运行时不变量。",
      "原则之间是链条：没有所有权就没有控制，没有证据就无法判断恢复是否成功。",
    ],
    questions: [
      ["严格边界会不会降低开发效率？", "前期需要写清协议，但后续新增任务、算法或机器人只需实现局部契约，避免跨层联动修改。"],
    ],
    transition: "下面把这些原则放回一张总图，先建立全局方向感。",
  },
  6: {
    oneLine: "系统沿着“入口 → 编译 → 任务树内核 → 物理世界”主链运行，证据和修复沿反馈回路返回。",
    boss: "从左到右就是一条指令如何落地：人提出目标，系统翻译成任务，机器人执行，现场反馈结果。",
    cto: "入口层不直接写树；编译层生成受约束定义；Kernel 是唯一生命周期解释器和 Store 唯一写入者；物理动作只能从 HarnessRuntime 出口进入后端。",
    emphasize: [
      "实线看主执行链，虚线看世界证据和修复回路，粉色代表 repair。",
      "A*、IK、RRT 在图中是能力，不是任务节点，避免算法细节污染任务语义。",
    ],
    questions: [
      ["LLM 在图中的边界在哪里？", "它位于入口和规划适配层，只提交规范化的任务意图；运行时节点、事件和物理请求由内核及 Harness 管理。"],
    ],
    transition: "总图看清后，最容易被问到的问题是：连续下达多条指令时，状态到底怎么隔离？",
  },
  7: {
    oneLine: "每条任务拥有独立的控制平面，但所有任务继续面对同一个真实现场。",
    boss: "像不同的项目各有自己的项目档案，但都在同一个工厂里作业；项目档案不能混，工厂现场也不能凭空重置。",
    cto: "ContinuousTaskSession 串行接收一次一个 typed submission；每次新建 Store、Kernel、Inspector 和执行栈，同时复用 WorldModel、Runtime、事务账本和 Backend。",
    emphasize: [
      "隔离的是树、节点运行态、事件和根目标；共享的是观察到的世界与物理基础设施。",
      "当前参考实现是串行会话，不把它描述成已经完成的并行调度系统。",
    ],
    questions: [
      ["多个任务能不能并行？", "当前协调器按任务串行执行；如果生产需要并行，应在上层增加调度和资源仲裁，不能把多个根目标硬塞进一棵树。"],
    ],
    transition: "树和世界的边界确定后，再看谁负责推动一棵树从 pending 走到 succeeded。",
  },
  8: {
    oneLine: "Kernel 像任务树的操作系统内核：负责调度、状态、验证和恢复，不替规划器或控制器做专业算法。",
    boss: "它不是“更大的模型”，而是一套稳定的执行规则：什么时候能做、做完怎么算成功、失败后下一步是什么。",
    cto: "生命周期包含进入、目标检查、前置谓词、展开或执行、子节点、后置验证、修复或对账和终态；执行栈与事件日志让推进过程可回放。",
    emphasize: [
      "Goal already satisfied 可以在入口短路，但结构节点必须先展开或执行，再在后置阶段验证。",
      "GraphDelta 在挂载前要校验作用域、边类型、无环和可达性。",
    ],
    questions: [
      ["Kernel 会不会变成新的万能模块？", "不会。它只解释通用生命周期和策略；领域规划、物理请求和修复提案仍由角色化适配器提供。"],
    ],
    transition: "内核有了统一生命周期，下一步要看每类扩展到底能看到什么、不能看到什么。",
  },
  9: {
    oneLine: "三类节点、三种权限，把“能决定什么”和“能触碰什么”写进协议。",
    boss: "分解器负责拆任务，系统操作负责算方案，物理技能负责把方案翻译成动作请求；没有一个模块可以包办全部事情。",
    cto: "Decomposer 只返回 GraphDelta；System Operation 可读 WorldSnapshot 和发布工件但不能调用 Backend；Physical Skill 只能消费工件构造 ActionRequest。",
    emphasize: [
      "权限通过 role context 强制，而不是靠开发者自觉。",
      "算法迭代不应成为树节点，否则回放会充满无业务意义的内部步骤。",
    ],
    questions: [
      ["为什么物理技能不能直接调用机器人？", "这样才能保证所有副作用都经过 Runtime 的新鲜度、guards、租约、checkpoint、事务和观测闸门。"],
    ],
    transition: "下面用一个最简单的放置任务，逐层展开这套控制平面。",
  },
  10: {
    oneLine: "正常放置任务先创建一个根节点，再由 Kernel 按需展开成 16 个可追踪节点。",
    boss: "用户只说“把红色杯子放进投放区”，系统内部会把它变成解析、规划、抓取、持物移动、释放和最终确认。",
    cto: "编译期只落根节点 Place；复合节点执行时通过 GraphDelta 增加 child 节点，最终形成 16 个节点、15 条 child 边、0 条 repair 边。",
    emphasize: [
      "树记录的是业务动作和验证点，不记录 A* 或 IK 的每次迭代。",
      "每一个物理动作旁边都有相应的前置、持有或结果验证节点。",
    ],
    questions: [
      ["为什么不一开始生成完整树？", "按需展开可以依据实际世界和已绑定对象生成下一层，减少无效节点，并让修复子树在失败时动态挂载。"],
    ],
    transition: "树形结构只是骨架，下一页按时间顺序说明这 16 个节点怎样推动真实动作。",
  },
  11: {
    oneLine: "一次成功放置包含 5 次物理事务和多次系统验证，成功不等于一次 API 返回成功。",
    boss: "系统每走一步都要确认现场是否真的跟上了计划，最后看到的是杯子到位，而不只是“命令已发送”。",
    cto: "T1 到 T5 分别覆盖抓取位移动、抓取、运输姿态、持物导航和释放；每条命令后都产生 Observation，并由 WorldModel 和 Verifier 判断后续状态。",
    emphasize: [
      "world:0 到 world:7 表示证据驱动的世界版本推进。",
      "物理节点是副作用边界；系统节点负责规划、验证和条件判断。",
    ],
    questions: [
      ["为什么要提前规划持物运输？", "提前检查可以在空夹爪阶段发现路线不可行，避免已经拿住物体后才暴露无法转移的问题。"],
    ],
    transition: "计划要能被验证和重用，就不能只是某个函数里的临时变量，必须成为有版本的工件。",
  },
  12: {
    oneLine: "规划工件是带来源、依赖和消费契约的不可变施工图。",
    boss: "现场一变，系统不会悄悄改旧计划，而是保留旧证据、生成新版本，再让后续动作使用合法的新计划。",
    cto: "BindingArtifact、PickPlan、TransferPlan 和 NavigationPlan 都带 source snapshot、依赖版本、模型指纹和 payload 约束；新鲜度通过依赖比较计算。",
    emphasize: [
      "旧工件保留是审计能力，新工件发布是恢复能力，别名切换是执行能力。",
      "重算 PickPlan 时必须按依赖关系刷新 TransferPlan，不能只更新一半。",
    ],
    questions: [
      ["为什么不直接把工件标成 invalidated？", "参考核心用依赖版本计算 freshness，避免可变标志和真实状态脱节；外部桥接层如有兼容标记，也只能作为适配信息。"],
    ],
    transition: "工件准备好之后，最关键的 CTO 问题是：它究竟怎样安全地穿过物理边界？",
  },
  13: {
    oneLine: "一个物理动作必须经过快照、权限、资源、执行、观测和对账六个阶段。",
    boss: "把它看成物理动作的审批链：计划合法、现场满足条件、设备被独占、动作完成后还要拿证据确认。",
    cto: "PhysicalSkill 只构造 ActionRequest；HarnessRuntime 负责 freshness、snapshot predicates、leases、checkpoint、backend execute、observation ingest 和 commit/reconciliation。",
    emphasize: [
      "真实后端通常不能回滚，系统必须承认这一点，靠观测对账恢复事实，而不是假装动作撤销了。",
      "动作后验证是闭环的终点，也是下一次规划的起点。",
    ],
    questions: [
      ["checkpoint 在真实机器人上有什么用？", "模拟器可以回滚；真实设备的 checkpoint 更多是事务边界和对账基线，不能替代物理事实。"],
    ],
    transition: "有了这条物理闭环，再看路线被挡时为什么不能简单地“多试几次”。",
  },
  14: {
    oneLine: "路线失败后，系统先证明阻挡原因和修复条件，再决定是否挂载修复树。",
    boss: "不是看到失败就乱绕路或乱搬东西，而是先回答：真的是谁挡住了？它能不能移动？移到哪里安全？",
    cto: "ROUTE_BLOCKED 进入 Diagnostic；反事实 A* 识别具体 movable witness，检查 parking region 和可达性，RepairResolver 才能返回 RepairProposal。",
    emphasize: [
      "A* 只证明路线，不隐藏物理副作用。",
      "如果存在安全绕行路线，应由规划能力解决；只有满足修复证据链才进入 blocker relocation。",
    ],
    questions: [
      ["为什么不直接让规划器绕开箱子？", "可以绕行就应当重新规划；本案例的路线证据表明候选障碍是可移动且移走后路线恢复，因此用显式修复更合适。"],
    ],
    transition: "一旦修复被批准，它不是旁路函数，而是挂到失败节点下面的真实子树。",
  },
  15: {
    oneLine: "修复树与主任务使用同一套内核，因此修复也能展开、验证、失败和再次修复。",
    boss: "修复不是人工插入的一段魔法脚本，而是系统自己能解释的一项工作：先搬箱子，再刷新计划，最后回到原任务。",
    cto: "repair delta 必须恰好从失败节点挂一条 repair 边，入口和节点不能越界，完整图必须无环且可达；参考案例最终 34 个节点、1 条 repair 边。",
    emphasize: [
      "Place(movable-crate, parking-zone) 会递归展开成和普通 Place 相同的结构。",
      "递归深度和 gripper 占用等条件限制修复不能无限套娃或产生不安全动作。",
    ],
    questions: [
      ["如果修复任务本身也遇到阻塞怎么办？", "它可以沿同一协议继续提出修复，但受递归深度、资源占用和安全条件限制；无法收敛就明确进入 blocked，而不是无限重试。"],
    ],
    transition: "树形结构说明了“挂在哪里”，下一页按真实时间线走完一次恢复。",
  },
  16: {
    oneLine: "恢复过程是：诊断 → 挂载 → 搬障碍物 → 观测 → 重规划 → 重试原节点 → 验证。",
    boss: "修复改变的是阻碍目标的现场条件，不是偷偷换掉用户原来的目标；最后仍然是把杯子放到投放区。",
    cto: "修复完成后世界从 world:0 推进到 world:7，依赖旧现场的计划重新检查新鲜度并发布新工件，最终原 PlanTransfer 在新条件下成功，根任务到 world:14。",
    emphasize: [
      "事件日志能把每一步和具体节点、工件、世界版本对应起来。",
      "恢复成功的判定来自新观测和后置谓词，而不是 repair resolver 自己说成功。",
    ],
    questions: [
      ["为什么 PlanTransfer 看起来执行了两次？", "第一次是原始计划失败，第二次是在修复并刷新依赖工件后重试；两次 attempt 都属于同一个原目标节点。"],
    ],
    transition: "单个任务恢复后，再看连续会话如何把两个独立目标接在同一个现实世界上。",
  },
  17: {
    oneLine: "连续会话做到“每条指令一棵新树，现场状态不重置”。",
    boss: "用户可以先让机器人搬走箱子，再让它把杯子放到目标区，第二条指令不需要重新初始化整个场景。",
    cto: "task-0001 的根是 program/task-0001，task-0002 的根是 program/task-0002；世界版本从 0 到 7 再到 14，事务和后端连续复用。",
    emphasize: [
      "新树解决审计和取消边界，共享世界解决真实操作的连续性。",
      "当前证据是串行连续任务，不应误读成多任务并行执行。",
    ],
    questions: [
      ["第二条任务如何知道第一条任务搬走了箱子？", "它读取共享 WorldModel 的新快照和实体版本，而不是读取第一棵树的节点内部状态。"],
    ],
    transition: "连续性解决了体验问题，最后还要回答物理动作如何被系统性地约束。",
  },
  18: {
    oneLine: "安全不是提示词里的提醒，而是由工件、快照、租约、事务和观测共同构成的物理边界。",
    boss: "就像生产线上的多道闸门：任何一道不通过，动作都不能直接触碰机器人。",
    cto: "普通物理动作和 system_only 动作分流；Runtime 是唯一后端入口；路由、footprint、payload、执行时重验证和 reconciliation 使用统一策略。",
    emphasize: [
      "计划新鲜度和动作前 guards 防止拿旧地图执行新动作。",
      "动作后采样和提交/对账防止“命令发出即当成事实”。",
    ],
    questions: [
      ["模型被提示注入后能不能直接发危险动作？", "模型没有 Runtime 或 Backend 能力，且物理技能只能生成受限请求；最终仍需通过 Harness 的注册表和安全策略。"],
    ],
    transition: "这些边界最终要转化为经营价值和工程效率，下面用案例数字对齐两种视角。",
  },
  19: {
    oneLine: "架构优势不是模块更多，而是可靠性、解释性、安全性、连续性和扩展性可以被验证。",
    boss: "管理层看到的是风险可控、故障可恢复、现场可连续使用；这些不是口号，而是案例中可数的节点、事务和世界版本。",
    cto: "局部能力通过协议替换，故障定位落在具体节点和工件，安全副作用集中在 Runtime；参考数据是验证架构的证据，不应直接当成生产 SLA。",
    emphasize: [
      "把 16/34 节点、5/10 事务、127/275 事件等数字作为可追溯证据，而不是性能承诺。",
      "工程效率来自局部重规划和局部修复，而不是每次整条流程重做。",
    ],
    questions: [
      ["这些优势如何变成 KPI？", "可进一步定义任务成功率、恢复收敛率、人工介入率、过期计划拦截率、回放完整率和单任务平均重规划范围。"],
    ],
    transition: "最后把当前参考实现和真正生产部署之间的差距讲清楚，便于形成可执行决策。",
  },
  20: {
    oneLine: "参考实现已经验证架构契约，生产化要补的是持久化、真实设备、运维治理和评测体系。",
    boss: "当前适合做可观测、可回放、可注入故障的试点；不应把模拟器验证直接包装成生产完成。",
    cto: "Store 需要持久化树、栈、事件、别名和工件的同事务落盘；真实 Backend 需要 observation/reconciliation；同时补认证、多操作员、指标、追踪和策略评测。",
    emphasize: [
      "P0 先解决状态可靠保存和真实机器人适配，P1 再补运维与评测，P2 才扩展规模化编排。",
      "试点任务要能观测、能回放、能故障注入，并且副作用边界可明确验证。",
    ],
    questions: [
      ["第一阶段最合理的试点是什么？", "选择一个目标明确、动作可回放、故障可构造且能量化成功条件的放置或搬运任务。"],
    ],
    transition: "最后用三句话收束：谁管控制平面，谁承载现实，失败如何被重新纳入工作流。",
  },
  21: {
    oneLine: "Kernel 管控制平面，Harness 承载物理世界，Repair 把失败变成可治理的工作。",
    boss: "决策不是继续堆模型能力，而是选一个真实试点，把可控执行能力跑通并量化。",
    cto: "下一步按四个动作推进：确定任务边界，接入持久化 Store，接入真实 Backend，建立故障注入、回放和指标体系。",
    emphasize: [
      "重复核心判断：模型负责意图，系统负责状态和副作用。",
      "把架构优势落到可验证的试点结果，而不是停留在图示层面。",
    ],
    questions: [
      ["今天需要做什么决策？", "确认试点场景、验收指标和生产化优先级；架构契约已经明确，下一步是用真实任务验证收益。"],
    ],
    transition: "进入试点范围和验收指标讨论。",
  },
};

function formatSpeakerNotes(number, title, brief) {
  const guide = SPEAKER_GUIDE[number];
  if (!guide) {
    return brief || `本页按图讲解：${title}。`;
  }
  const lines = [
    `本页一句话：${guide.oneLine}`,
    "",
    `面向老板：${guide.boss}`,
    "",
    `面向 CTO：${guide.cto}`,
    "",
    "讲解重点：",
    ...guide.emphasize.map((item) => `- ${item}`),
    "",
    "可能追问与回答：",
  ];
  guide.questions.forEach(([question, answer]) => {
    lines.push(`- 问：${question}`);
    lines.push(`  答：${answer}`);
  });
  lines.push("");
  lines.push(`过渡：${guide.transition}`);
  lines.push("");
  lines.push(`原始备注：${brief || "本页按图讲解。"}`);
  return lines.join("\n");
}

function colorOf(value, fallback = C.ink, context = "") {
  if (typeof value === "string" && value.length > 0) {
    return value;
  }
  if (value && typeof value === "object" && typeof value.color === "string") {
    return value.color;
  }
  if (context) {
    console.warn(`Non-string color in ${context}; using ${fallback}`);
  }
  return fallback;
}

function loadJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return fallback;
  }
}

const evidence = loadJson(path.join(EXAMPLES, "execution-evidence.json"), {
  normal: {
    node_count: 16,
    event_count: 127,
    transactions: [{}, {}, {}, {}, {}],
    initial_snapshot: "world:0",
    final_snapshot: "world:7",
    repair_edge_count: 0,
  },
  blocked: {
    node_count: 34,
    event_count: 275,
    transactions: [{}, {}, {}, {}, {}, {}, {}, {}, {}, {}],
    initial_snapshot: "world:0",
    final_snapshot: "world:14",
    repair_edge_count: 1,
  },
});
const continuous = loadJson(
  path.join(EXAMPLES, "continuous-two-tasks.json"),
  {
    first_state: {
      world: { revision: 7 },
      tree: { root_id: "program/task-0001", nodes: new Array(16) },
      last_result: { transactions: 5 },
    },
    second_state: {
      world: { revision: 14 },
      tree: { root_id: "program/task-0002", nodes: new Array(16) },
      last_result: { transactions: 5 },
      transactions: 10,
    },
  },
);

function n(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function addText(slide, text, x, y, w, h, opts = {}) {
  const base = {
    x,
    y,
    w,
    h,
    fontFace: FONT,
    fontSize: 14,
    color: C.ink,
    margin: 0.04,
    breakLine: false,
    fit: "shrink",
    valign: "mid",
    paraSpaceAfterPt: 0,
    ...opts,
  };
  slide.addText(text, base);
}

function addRichText(slide, runs, x, y, w, h, opts = {}) {
  addText(slide, runs, x, y, w, h, opts);
}

function rect(slide, x, y, w, h, fill, line = fill, radius = true, opts = {}) {
  const context = opts.context || "rect";
  const fillColor = colorOf(fill, C.paper, `${context}.fill`);
  const lineColor = colorOf(line, fillColor, `${context}.line`);
  slide.addShape(radius ? S.roundRect : S.rect, {
    x,
    y,
    w,
    h,
    rectRadius: radius ? 0.08 : undefined,
    fill: { color: fillColor, transparency: opts.transparency || 0 },
    line: {
      color: lineColor,
      width: opts.lineWidth || 1,
      transparency: opts.lineTransparency || 0,
      dash: opts.dash,
    },
    shadow: opts.shadow
      ? { type: "outer", color: "77858B", opacity: 0.16, blur: 1, angle: 45, distance: 1 }
      : undefined,
  });
}

function line(slide, x1, y1, x2, y2, color = C.muted, width = 1.3, opts = {}) {
  const lineColor = colorOf(color, C.muted, "line.color");
  const dx = x2 - x1;
  const dy = y2 - y1;
  slide.addShape(S.line, {
    // DrawingML rejects negative extents; use flips to preserve direction.
    x: Math.min(x1, x2),
    y: Math.min(y1, y2),
    w: Math.max(Math.abs(dx), 0.001),
    h: Math.max(Math.abs(dy), 0.001),
    flipH: dx < 0,
    flipV: dy < 0,
    line: {
      color: lineColor,
      width,
      dashType: opts.dash,
      beginArrowType: opts.beginArrow ? "triangle" : "none",
      endArrowType: opts.arrow === false ? "none" : "triangle",
    },
  });
}

function circle(slide, x, y, d, fill, lineColor = fill, opts = {}) {
  const fillColor = colorOf(fill, C.paper, "circle.fill");
  const strokeColor = colorOf(lineColor, fillColor, "circle.line");
  slide.addShape(S.ellipse, {
    x,
    y,
    w: d,
    h: d,
    fill: { color: fillColor, transparency: opts.transparency || 0 },
    line: { color: strokeColor, width: opts.lineWidth || 1 },
  });
}

function badge(slide, number, x, y, color = C.coral) {
  circle(slide, x, y, 0.28, color, color);
  addText(slide, String(number), x, y + 0.005, 0.28, 0.25, {
    fontFace: "Aptos",
    fontSize: 11,
    bold: true,
    color: C.white,
    align: "center",
    margin: 0,
  });
}

function pill(slide, text, x, y, w, fill, color = C.ink, opts = {}) {
  rect(slide, x, y, w, opts.h || 0.3, fill, opts.line || fill, true, {
    lineWidth: opts.lineWidth || 1,
    context: `pill:${text}`,
  });
  addText(slide, text, x + 0.05, y + 0.01, w - 0.1, (opts.h || 0.3) - 0.02, {
    fontSize: opts.fontSize || 10,
    color,
    bold: opts.bold || false,
    align: opts.align || "center",
    margin: 0,
  });
}

function node(slide, title, subtitle, x, y, w, h, fill, stroke, opts = {}) {
  rect(slide, x, y, w, h, fill, stroke, true, {
    lineWidth: opts.lineWidth || 1.4,
    shadow: opts.shadow !== false,
    context: `node:${title}`,
  });
  if (opts.badge !== undefined) {
    badge(slide, opts.badge, x + 0.12, y + 0.12, opts.badgeColor || stroke);
  }
  const titleY = opts.badge === undefined ? y + 0.08 : y + 0.07;
  addText(slide, title, x + 0.12, titleY, w - 0.24, opts.titleH || 0.32, {
    fontSize: opts.titleSize || 15,
    bold: true,
    align: opts.align || "center",
  });
  if (subtitle) {
    addText(slide, subtitle, x + 0.12, y + (opts.subtitleY || 0.43), w - 0.24, h - (opts.subtitleY || 0.43) - 0.08, {
      fontSize: opts.subtitleSize || 10.5,
      color: opts.subtitleColor || C.muted,
      align: opts.align || "center",
      valign: opts.subtitleValign || "mid",
    });
  }
}

function sectionHeader(slide, section, title, kicker = "") {
  slide.background = { color: C.bg };
  rect(slide, 0, 0, SW, 0.1, C.teal, C.teal, false);
  addText(slide, section.toUpperCase(), 0.55, 0.24, 2.5, 0.23, {
    fontFace: "Aptos",
    fontSize: 9,
    bold: true,
    color: C.teal,
    charSpacing: 1.2,
  });
  addText(slide, title, 0.55, 0.5, 12.1, 0.48, {
    fontSize: 25,
    bold: true,
    color: C.ink,
  });
  if (kicker) {
    addText(slide, kicker, 0.58, 0.98, 12.1, 0.28, {
      fontSize: 11.5,
      color: C.muted,
    });
  }
}

function footer(slide, num, label = "Task Recursive Tree Agent") {
  line(slide, 0.55, 7.03, 12.75, 7.03, C.line, 0.7, { arrow: false });
  addText(slide, label, 0.58, 7.09, 4.5, 0.16, {
    fontFace: "Aptos",
    fontSize: 7.5,
    color: C.faint,
    margin: 0,
  });
  addText(slide, `2026-08-30  ·  ${String(num).padStart(2, "0")}`, 10.7, 7.09, 2, 0.16, {
    fontFace: "Aptos",
    fontSize: 7.5,
    color: C.faint,
    align: "right",
    margin: 0,
  });
}

function finish(slide, _requestedNum, title, notes) {
  // Derive the visible number from insertion order so adding explanatory
  // slides cannot leave footers, notes, and the manifest out of sync.
  const num = slides.length + 1;
  const detailedNotes = formatSpeakerNotes(num, title, notes);
  footer(slide, num);
  slides.push({ number: num, title, notes: detailedNotes });
  if (detailedNotes) {
    slide.addNotes(detailedNotes);
  }
}

function callout(slide, text, x, y, w, h, fill, stroke, opts = {}) {
  rect(slide, x, y, w, h, fill, stroke, true, {
    lineWidth: opts.lineWidth || 1.2,
    context: `callout:${String(text).slice(0, 24)}`,
  });
  addText(slide, text, x + 0.12, y + 0.04, w - 0.24, h - 0.08, {
    fontSize: opts.fontSize || 13,
    color: opts.color || C.ink,
    bold: opts.bold || false,
    align: opts.align || "left",
  });
}

function metric(slide, value, label, x, y, w, accent) {
  addText(slide, value, x, y, w, 0.38, {
    fontFace: "Aptos",
    fontSize: 26,
    bold: true,
    color: accent,
    align: "center",
    margin: 0,
  });
  addText(slide, label, x, y + 0.42, w, 0.3, {
    fontSize: 10.5,
    color: C.muted,
    align: "center",
    margin: 0,
  });
}

function bulletList(slide, items, x, y, w, lineH = 0.42, opts = {}) {
  items.forEach((item, index) => {
    const yy = y + index * lineH;
    circle(slide, x, yy + 0.11, 0.11, opts.bulletColor || C.teal, opts.bulletColor || C.teal);
    addText(slide, item, x + 0.2, yy, w - 0.2, lineH, {
      fontSize: opts.fontSize || 13,
      color: opts.color || C.ink,
      bold: opts.bold || false,
      valign: "top",
    });
  });
}

function treeText(slide, lines, x, y, w, h, opts = {}) {
  addText(slide, lines.join("\n"), x, y, w, h, {
    fontFace: opts.fontFace || MONO,
    fontSize: opts.fontSize || 11,
    color: opts.color || C.ink,
    breakLine: true,
    valign: "top",
    margin: opts.margin === undefined ? 0.08 : opts.margin,
    fit: "shrink",
  });
}

function drawTinyTree(slide, x, y, scale = 1, color = C.teal) {
  const rootX = x + 0.65 * scale;
  circle(slide, rootX, y, 0.26 * scale, color, color);
  line(slide, rootX + 0.13 * scale, y + 0.26 * scale, x + 0.22 * scale, y + 0.72 * scale, color, 1.4);
  line(slide, rootX + 0.13 * scale, y + 0.26 * scale, x + 1.2 * scale, y + 0.72 * scale, color, 1.4);
  line(slide, x + 0.22 * scale, y + 0.72 * scale, x + 0.02 * scale, y + 1.2 * scale, color, 1.4);
  line(slide, x + 0.22 * scale, y + 0.72 * scale, x + 0.43 * scale, y + 1.2 * scale, color, 1.4);
  line(slide, x + 1.2 * scale, y + 0.72 * scale, x + 0.98 * scale, y + 1.2 * scale, color, 1.4);
  line(slide, x + 1.2 * scale, y + 0.72 * scale, x + 1.42 * scale, y + 1.2 * scale, color, 1.4);
  [0.02, 0.43, 0.98, 1.42].forEach((dx) => circle(slide, x + dx * scale, y + 1.2 * scale, 0.2 * scale, C.paper, color, { lineWidth: 1.2 }));
}

function drawPipeline(slide, items, y, opts = {}) {
  const x0 = opts.x || 0.75;
  const gap = opts.gap || 0.18;
  const w = opts.w || 1.78;
  const h = opts.h || 0.8;
  items.forEach((item, index) => {
    const x = x0 + index * (w + gap);
    node(slide, item.title, item.subtitle || "", x, y, w, h, item.fill, item.stroke, {
      titleSize: item.titleSize || 12,
      subtitleSize: item.subtitleSize || 9.5,
      subtitleY: 0.42,
      shadow: false,
    });
    if (index < items.length - 1) {
      line(slide, x + w, y + h / 2, x + w + gap, y + h / 2, C.muted, 1.5);
    }
  });
}

function drawTreeNode(slide, label, x, y, w, h, fill, stroke, opts = {}) {
  node(slide, label, opts.sub || "", x, y, w, h, fill, stroke, {
    titleSize: opts.titleSize || 11,
    subtitleSize: opts.subSize || 8.5,
    subtitleY: opts.sub ? 0.28 : 0.32,
    shadow: false,
    badge: opts.badge,
    badgeColor: stroke,
  });
}

function addSectionBand(slide, title, text, y, fill = C.paper, stroke = C.line, h = 1.1) {
  rect(slide, 0.55, y, 12.23, h, fill, stroke, true, { lineWidth: 1 });
  addText(slide, title, 0.75, y + 0.17, 3.2, 0.28, {
    fontSize: 16,
    bold: true,
  });
  addText(slide, text, 0.75, y + 0.52, 11.55, h - 0.62, {
    fontSize: 11.5,
    color: C.muted,
    valign: "top",
  });
}

// 1. Cover
{
  const slide = pptx.addSlide("TRT_MASTER");
  slide.background = { color: C.bg };
  rect(slide, 0, 0, SW, SH, C.bg, C.bg, false);
  rect(slide, 0, 0, 0.18, SH, C.teal, C.teal, false);
  addText(slide, "架构讲解  /  管理层 + CTO", 0.72, 0.7, 4.5, 0.3, {
    fontFace: "Aptos",
    fontSize: 11,
    bold: true,
    color: C.teal,
    charSpacing: 1,
  });
  addText(slide, "Task Recursive Tree Agent", 0.72, 1.22, 7.1, 0.75, {
    fontSize: 31,
    bold: true,
  });
  addText(slide, "让机器人从“能执行”走向“可解释、可恢复、可扩展”", 0.75, 2.08, 6.7, 0.55, {
    fontSize: 19,
    color: C.muted,
  });
  addText(slide, "一套把自然语言意图、任务树、规划工件、物理动作、观测验证和故障修复串成闭环的 Agent 运行时。", 0.75, 2.88, 5.9, 0.8, {
    fontSize: 13.5,
    color: C.muted,
    valign: "top",
  });

  // Executive signals.
  rect(slide, 0.75, 4.3, 5.8, 1.28, C.paper, C.line, true, { lineWidth: 1.1, shadow: true });
  metric(slide, "1", "权威任务树状态", 0.98, 4.54, 1.2, C.gold);
  metric(slide, "3", "节点职责类型", 2.45, 4.54, 1.2, C.teal);
  metric(slide, "2", "状态边界：任务 / 世界", 3.92, 4.54, 1.4, C.blue);
  addText(slide, "失败不是异常分支，而是可追踪、可验证、可重试的任务。", 0.98, 5.22, 5.25, 0.23, {
    fontSize: 10.5,
    color: C.repair,
    bold: true,
    align: "center",
  });

  // Abstract architecture tree.
  addText(slide, "从意图到现实", 8.0, 0.95, 4.4, 0.3, {
    fontSize: 14,
    bold: true,
    color: C.muted,
    align: "center",
  });
  const cx = 9.95;
  circle(slide, cx, 1.58, 0.66, C.gold, C.gold);
  addText(slide, "任务树", cx - 0.07, 1.75, 0.8, 0.2, {
    fontSize: 11,
    bold: true,
    color: C.white,
    align: "center",
    margin: 0,
  });
  const branch = [
    { x: 8.0, y: 2.72, label: "规划", fill: C.blueLight, stroke: C.blue },
    { x: 9.48, y: 2.72, label: "执行", fill: C.tealLight, stroke: C.teal },
    { x: 10.96, y: 2.72, label: "验证", fill: C.greenLight, stroke: C.green },
    { x: 12.05, y: 4.2, label: "修复", fill: C.repairLight, stroke: C.repair },
  ];
  branch.forEach((item) => {
    line(slide, cx + 0.33, 2.24, item.x + 0.48, item.y, item.stroke, 2);
    node(slide, item.label, "", item.x, item.y, 0.96, 0.55, item.fill, item.stroke, {
      titleSize: 12,
      shadow: true,
    });
  });
  line(slide, 10.45, 2.42, 12.42, 4.2, C.repair, 2, { dash: "dash" });
  node(slide, "真实世界", "机器人 / 场景 / 观测", 8.35, 4.2, 2.1, 0.7, C.paper, C.ink, {
    titleSize: 15,
    subtitleSize: 9.5,
    subtitleY: 0.36,
  });
  line(slide, 9.4, 4.2, 9.95, 2.24, C.teal, 1.8, { dash: "dash" });
  addText(slide, "观测反馈", 9.57, 3.4, 0.95, 0.2, {
    fontSize: 9,
    color: C.teal,
    align: "center",
    margin: 0,
  });
  pill(slide, "GeminiER2 + Harness + MuJoCo", 8.25, 5.45, 3.55, C.tealLight, C.teal, {
    h: 0.36,
    fontSize: 10,
    line: C.teal,
  });
  addText(slide, "基于 D:\\Task Recursive Tree 当前参考实现", 0.75, 6.75, 5.8, 0.22, {
    fontSize: 9,
    color: C.faint,
  });
  finish(slide, 1, "封面", "这页只讲定位：我们不是再做一个直接调用工具的 LLM，而是在做一个可治理的 Agent 执行控制平面。");
}

// 2. Executive takeaway
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(slide, "01 / 先讲结论", "这是一个把 Agent 决策变成可治理执行系统的架构", "老板先看结果，CTO 再看实现：同一套设计同时回答可靠性、解释性、扩展性和安全性。");
  const takeaways = [
    ["01", "决策与动作分离", "LLM 只提交受约束的任务意图；机器人动作必须经过任务树和物理边界。", C.coral, C.coralLight],
    ["02", "任务树成为控制平面", "一个 Kernel 统一处理生命周期、顺序、重试、暂停、取消和修复。", C.gold, C.goldLight],
    ["03", "状态和工件可追溯", "世界快照、规划工件、事件日志和执行栈让每一步都有证据。", C.teal, C.tealLight],
    ["04", "失败显式递归", "路线被挡不是“再试一次”，而是挂载 repair 子树，完成修复后回到原节点。", C.repair, C.repairLight],
  ];
  takeaways.forEach((item, index) => {
    const x = 0.65 + index * 3.08;
    rect(slide, x, 1.52, 2.82, 2.05, item[4], item[3], true, { lineWidth: 1.3, shadow: true });
    addText(slide, item[0], x + 0.18, 1.74, 0.58, 0.35, {
      fontFace: "Aptos",
      fontSize: 22,
      bold: true,
      color: item[3],
      margin: 0,
    });
    addText(slide, item[1], x + 0.18, 2.25, 2.46, 0.34, {
      fontSize: 15,
      bold: true,
    });
    addText(slide, item[2], x + 0.18, 2.76, 2.46, 0.58, {
      fontSize: 10.5,
      color: C.muted,
      valign: "top",
    });
  });
  addText(slide, "一条任务如何穿过系统", 0.7, 4.05, 3.6, 0.3, {
    fontSize: 16,
    bold: true,
  });
  drawPipeline(slide, [
    { title: "自然语言", subtitle: "人的目标", fill: C.coralLight, stroke: C.coral },
    { title: "TaskProgram", subtitle: "受约束意图", fill: C.blueLight, stroke: C.blue },
    { title: "任务树", subtitle: "Kernel 控制", fill: C.goldLight, stroke: C.gold },
    { title: "ActionRequest", subtitle: "可执行请求", fill: C.tealLight, stroke: C.teal },
    { title: "观测验证", subtitle: "真实证据", fill: C.greenLight, stroke: C.green },
  ], 4.58, { x: 0.75, w: 2.15, gap: 0.23, h: 0.86 });
  callout(slide, "核心判断：把“模型可能做什么”与“系统允许现在做什么”分开，才能把 Agent 带进真实世界。", 1.05, 6.0, 11.2, 0.56, C.paper, C.teal, {
    fontSize: 15,
    color: C.teal,
    bold: true,
    align: "center",
  });
  finish(slide, 2, "执行摘要", "先让听众记住四个词：分离、控制、证据、修复。后面所有技术细节都是围绕这四个词展开。");
}

// 3. Three core objects
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(
    slide,
    "02 / 三个核心对象",
    "先把三个对象分开，整套架构就容易理解了",
    "可以把系统类比成：任务清单 + 现场账本 + 带版本的施工图。三者各自回答一个问题，不能混成一个大上下文。",
  );

  const objects = [
    {
      number: "01",
      title: "任务树",
      english: "Task Tree",
      question: "回答：接下来做什么？",
      analogy: "待办清单 + 过程记录",
      items: ["节点、边、状态、执行栈", "记录顺序、验证点和修复入口"],
      owner: "权威所有者：TaskTreeKernel / TaskTreeStore",
      fill: C.goldLight,
      stroke: C.gold,
    },
    {
      number: "02",
      title: "世界模型",
      english: "World Model",
      question: "回答：现场现在是什么？",
      analogy: "现场账本 + 最新地图",
      items: ["机器人、物体、障碍物和版本", "只接受 Observation 形成的新证据"],
      owner: "事实来源：Observation → WorldModel",
      fill: C.tealLight,
      stroke: C.teal,
    },
    {
      number: "03",
      title: "规划工件",
      english: "Artifact",
      question: "回答：基于这个现场怎么做？",
      analogy: "带版本的施工图",
      items: ["Binding / Pick / Transfer / Navigation", "携带快照、依赖和模型指纹"],
      owner: "不可变数据：变化时发布新版本",
      fill: C.purpleLight,
      stroke: C.purple,
    },
  ];
  objects.forEach((item, index) => {
    const x = 0.7 + index * 4.1;
    rect(slide, x, 1.55, 3.65, 3.62, item.fill, item.stroke, true, {
      lineWidth: 1.25,
      shadow: true,
    });
    circle(slide, x + 0.23, 1.82, 0.46, item.stroke, item.stroke);
    addText(slide, item.number, x + 0.23, 1.93, 0.46, 0.16, {
      fontFace: "Aptos",
      fontSize: 10,
      bold: true,
      color: C.white,
      align: "center",
      margin: 0,
    });
    addText(slide, item.title, x + 0.82, 1.8, 1.5, 0.28, {
      fontSize: 18,
      bold: true,
    });
    addText(slide, item.english, x + 0.82, 2.13, 2.35, 0.19, {
      fontFace: "Aptos",
      fontSize: 9.5,
      color: C.muted,
    });
    callout(slide, item.question, x + 0.23, 2.58, 3.18, 0.48, C.paper, item.stroke, {
      fontSize: 12,
      color: item.stroke,
      bold: true,
      align: "center",
    });
    addText(slide, item.analogy, x + 0.23, 3.22, 3.18, 0.27, {
      fontSize: 14,
      bold: true,
      color: C.ink,
      align: "center",
    });
    item.items.forEach((text, itemIndex) => {
      const yy = 3.72 + itemIndex * 0.38;
      circle(slide, x + 0.3, yy + 0.08, 0.1, item.stroke, item.stroke);
      addText(slide, text, x + 0.5, yy, 2.83, 0.25, {
        fontSize: 10.3,
        color: C.muted,
        valign: "top",
      });
    });
    addText(slide, item.owner, x + 0.23, 4.58, 3.18, 0.38, {
      fontSize: 9.2,
      color: item.stroke,
      bold: true,
      align: "center",
      valign: "top",
    });
  });

  addText(slide, "术语速译：TaskProgram = 任务意图  ·  GraphDelta = 增量挂载  ·  Diagnostic = 结构化失败  ·  Repair = 修复子树", 0.85, 5.25, 11.7, 0.18, {
    fontSize: 8.8,
    color: C.faint,
    align: "center",
    margin: 0,
  });
  addText(slide, "一条任务如何把三者串起来", 0.78, 5.48, 3.4, 0.25, {
    fontSize: 16,
    bold: true,
  });
  drawPipeline(
    slide,
    [
      { title: "人的目标", subtitle: "自然语言", fill: C.coralLight, stroke: C.coral },
      { title: "任务树", subtitle: "决定顺序", fill: C.goldLight, stroke: C.gold },
      { title: "规划工件", subtitle: "连接计划", fill: C.purpleLight, stroke: C.purple },
      { title: "物理动作", subtitle: "受限请求", fill: C.tealLight, stroke: C.teal },
      { title: "世界观测", subtitle: "真实证据", fill: C.greenLight, stroke: C.green },
      { title: "状态回流", subtitle: "继续 / 修复", fill: C.repairLight, stroke: C.repair },
    ],
    5.84,
    { x: 0.7, w: 1.78, gap: 0.2, h: 0.66 },
  );
  callout(slide, "一句话：树管顺序，世界给证据，工件把计划接到动作；三者分开，系统才可审计。", 1.1, 6.62, 11.1, 0.34, C.paper, C.ink, {
    fontSize: 12,
    color: C.ink,
    bold: true,
    align: "center",
  });
  finish(slide, 3, "三个核心对象", "先把任务树、世界模型和规划工件分开讲清楚：任务树决定顺序，世界模型提供事实，工件连接计划与动作。");
}

// 4. Problem / contrast
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(slide, "03 / 为什么要这样设计", "为什么不能让 LLM 直接调用机器人 API？", "直接调用在 Demo 阶段很快，但一旦进入连续场景、动态障碍和故障恢复，隐式状态会变成系统性风险。");
  rect(slide, 0.65, 1.48, 5.65, 4.98, C.paper, C.line, true, { lineWidth: 1.1, shadow: true });
  rect(slide, 0.65, 1.48, 5.65, 0.55, C.coralLight, C.coral, true, { lineWidth: 1.2 });
  addText(slide, "传统黑盒 Agent", 0.9, 1.64, 2.6, 0.25, { fontSize: 17, bold: true, color: C.coral });
  node(slide, "LLM", "自由生成工具调用", 1.05, 2.32, 1.25, 0.75, C.coralLight, C.coral, { titleSize: 15 });
  node(slide, "Tool API", "参数 + 副作用", 2.75, 2.32, 1.45, 0.75, C.coralLight, C.coral, { titleSize: 14 });
  node(slide, "Robot", "直接改变现实", 4.6, 2.32, 1.25, 0.75, C.coralLight, C.coral, { titleSize: 14 });
  line(slide, 2.3, 2.7, 2.75, 2.7, C.coral, 1.8);
  line(slide, 4.2, 2.7, 4.6, 2.7, C.coral, 1.8);
  bulletList(slide, [
    "计划藏在模型上下文里，外部无法审计",
    "工具执行后，模型未必知道真实世界发生了什么",
    "重试可能重复副作用，恢复逻辑散落在各个工具里",
    "计划一旦过期，系统没有统一的失效判定",
  ], 1.03, 3.55, 4.9, 0.54, { bulletColor: C.coral, fontSize: 12.3 });
  pill(slide, "风险：不可解释 · 难恢复 · 难扩展", 1.12, 5.88, 4.65, C.coralLight, C.coral, {
    h: 0.36,
    fontSize: 11.5,
    line: C.coral,
    bold: true,
  });

  rect(slide, 7.03, 1.48, 5.65, 4.98, C.paper, C.line, true, { lineWidth: 1.1, shadow: true });
  rect(slide, 7.03, 1.48, 5.65, 0.55, C.tealLight, C.teal, true, { lineWidth: 1.2 });
  addText(slide, "Task Recursive Tree Agent", 7.28, 1.64, 3.9, 0.25, { fontSize: 17, bold: true, color: C.teal });
  node(slide, "意图", "TaskProgram", 7.4, 2.2, 1.18, 0.7, C.blueLight, C.blue, { titleSize: 13 });
  node(slide, "Kernel", "树生命周期", 9.02, 2.2, 1.35, 0.7, C.goldLight, C.gold, { titleSize: 13 });
  node(slide, "请求", "ActionRequest", 10.82, 2.2, 1.35, 0.7, C.tealLight, C.teal, { titleSize: 13 });
  line(slide, 8.58, 2.55, 9.02, 2.55, C.muted, 1.6);
  line(slide, 10.37, 2.55, 10.82, 2.55, C.muted, 1.6);
  node(slide, "Observation", "每条物理命令后采样", 8.1, 3.55, 2.0, 0.7, C.greenLight, C.green, { titleSize: 13 });
  node(slide, "Verifier", "只读快照证据", 10.75, 3.55, 1.55, 0.7, C.greenLight, C.green, { titleSize: 13 });
  line(slide, 11.48, 2.9, 9.1, 3.55, C.teal, 1.7);
  line(slide, 10.1, 3.9, 10.75, 3.9, C.green, 1.6);
  node(slide, "Diagnostic", "失败结构化", 7.55, 4.8, 1.55, 0.7, C.repairLight, C.repair, { titleSize: 13 });
  node(slide, "Repair 子树", "修复后重试", 10.15, 4.8, 1.7, 0.7, C.repairLight, C.repair, { titleSize: 13 });
  line(slide, 8.3, 4.8, 9.1, 4.25, C.repair, 1.7, { dash: "dash" });
  line(slide, 9.1, 5.15, 10.15, 5.15, C.repair, 1.7);
  callout(slide, "收益：每一步都有“谁决定、用什么工件、执行了什么、观察到了什么、失败如何处理”的答案。", 7.45, 5.88, 4.82, 0.42, C.tealLight, C.teal, {
    fontSize: 10.5,
    color: C.teal,
    bold: true,
    align: "center",
  });
  finish(slide, 4, "问题与动机", "这页不要贬低传统 Agent，重点是说明它的适用边界：一旦有真实副作用和连续状态，就需要显式控制平面。");
}

// 4. High-level principles
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(slide, "04 / 高层设计原则", "六条原则，决定这套系统为什么可控", "这些原则不是代码风格，而是所有权、状态、数据契约和故障处理的约束。");
  const principles = [
    ["01", "单一所有权", "TaskTreeStore 是唯一运行时树；TaskTreeKernel 是唯一写入者。", C.gold, C.goldLight],
    ["02", "类型化边界", "分解器、系统操作、物理技能、修复器各有专属上下文。", C.blue, C.blueLight],
    ["03", "任务与世界分层", "每个任务新建树；物理世界、后端和事务账本持续复用。", C.teal, C.tealLight],
    ["04", "工件不可变", "计划携带快照、依赖版本和模型指纹；重规划发布新工件。", C.purple, C.purpleLight],
    ["05", "观测驱动闭环", "动作后必采样；验证只读 WorldSnapshot，不相信隐式内部状态。", C.green, C.greenLight],
    ["06", "失败显式递归", "Diagnostic → RepairProposal → repair 子树 → 原节点重试。", C.repair, C.repairLight],
  ];
  principles.forEach((item, index) => {
    const col = index % 3;
    const row = Math.floor(index / 3);
    const x = 0.7 + col * 4.1;
    const y = 1.55 + row * 1.68;
    rect(slide, x, y, 3.65, 1.35, item[4], item[3], true, { lineWidth: 1.1, shadow: true });
    circle(slide, x + 0.18, y + 0.2, 0.43, item[3], item[3]);
    addText(slide, item[0], x + 0.18, y + 0.29, 0.43, 0.18, {
      fontFace: "Aptos",
      fontSize: 11,
      bold: true,
      color: C.white,
      align: "center",
      margin: 0,
    });
    addText(slide, item[1], x + 0.78, y + 0.2, 2.55, 0.28, {
      fontSize: 15,
      bold: true,
    });
    addText(slide, item[2], x + 0.78, y + 0.61, 2.55, 0.52, {
      fontSize: 10.5,
      color: C.muted,
      valign: "top",
    });
  });
  callout(slide, "设计目标：把不可控的隐式行为，变成可审计的显式状态。", 1.5, 5.35, 10.3, 0.72, C.paper, C.ink, {
    fontSize: 20,
    color: C.ink,
    bold: true,
    align: "center",
  });
  addText(slide, "因此，算法可以替换，机器人可以替换，任务树的控制逻辑和安全边界不需要被一起重写。", 1.75, 6.18, 9.8, 0.32, {
    fontSize: 12.5,
    color: C.muted,
    align: "center",
  });
  finish(slide, 5, "高层设计原则", "重点讲原则之间的关系：所有权保证可控，边界保证隔离，工件保证可追溯，观测保证真实，修复保证闭环。");
}

// 5. Overall architecture
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(slide, "05 / 总体架构", "四层结构：入口、编译、任务树内核、物理世界", "读图方向从左到右、从上到下；实线是主执行链，虚线是状态/证据反馈，粉色是修复回路。");

  // Layer labels.
  const bands = [
    [1.38, 0.85, "A 入口与 Agent", C.coralLight, C.coral],
    [2.35, 0.85, "B 编译与边界翻译", C.blueLight, C.blue],
    [3.32, 1.55, "C 任务树控制平面", C.goldLight, C.gold],
    [5.03, 1.35, "D 物理执行与世界反馈", C.tealLight, C.teal],
  ];
  bands.forEach(([y, h, label, fill, stroke]) => {
    rect(slide, 0.55, y, 1.55, h, fill, stroke, true, { lineWidth: 1 });
    addText(slide, label, 0.67, y + 0.12, 1.31, h - 0.2, {
      fontSize: 11,
      bold: true,
      color: stroke,
      align: "center",
    });
  });

  node(slide, "操作员", "语音 / 文本", 2.42, 1.48, 1.55, 0.6, C.coralLight, C.coral, { titleSize: 12 });
  node(slide, "浏览器 + HTTP", "只读观察 / 提交任务", 4.25, 1.48, 2.1, 0.6, C.coralLight, C.coral, { titleSize: 12 });
  node(slide, "GeminiER2", "LLM 规划与目标解析", 6.65, 1.48, 2.0, 0.6, C.tealLight, C.teal, { titleSize: 12 });
  node(slide, "ContinuousTaskSession", "串行任务；共享物理世界", 8.95, 1.48, 2.6, 0.6, C.tealLight, C.teal, { titleSize: 12 });
  line(slide, 3.97, 1.78, 4.25, 1.78, C.muted, 1.4);
  line(slide, 6.35, 1.78, 6.65, 1.78, C.muted, 1.4);
  line(slide, 8.65, 1.78, 8.95, 1.78, C.muted, 1.4);

  node(slide, "Harness TaskCompiler", "编译期 TaskTree DTO", 2.42, 2.46, 2.15, 0.6, C.tealLight, C.teal, { titleSize: 12 });
  node(slide, "KernelCompilerBridge", "translate_harness_tree()", 4.93, 2.46, 2.25, 0.6, C.blueLight, C.blue, { titleSize: 12 });
  node(slide, "TaskTreeDefinition", "根节点 + GraphDelta", 7.55, 2.46, 2.1, 0.6, C.goldLight, C.gold, { titleSize: 12 });
  node(slide, "KernelExecutorBridge", "装配每个任务的执行环境", 9.95, 2.46, 2.45, 0.6, C.blueLight, C.blue, { titleSize: 12 });
  line(slide, 4.57, 2.76, 4.93, 2.76, C.muted, 1.4);
  line(slide, 7.18, 2.76, 7.55, 2.76, C.muted, 1.4);
  line(slide, 9.65, 2.76, 9.95, 2.76, C.muted, 1.4);

  node(slide, "TaskTreeKernel", "唯一生命周期解释器 / Store 唯一写入者", 2.42, 3.55, 3.18, 0.9, C.goldLight, C.gold, {
    titleSize: 16,
    subtitleSize: 10.5,
    subtitleY: 0.47,
    badge: 1,
    badgeColor: C.gold,
  });
  node(slide, "TaskTreeStore", "唯一权威运行时树状态", 6.0, 3.55, 2.45, 0.9, C.purpleLight, C.purple, {
    titleSize: 15,
    subtitleSize: 10.5,
    subtitleY: 0.47,
  });
  node(slide, "适配器集合", "分解 / 系统操作 / 物理技能 / 修复", 8.85, 3.55, 3.55, 0.9, C.blueLight, C.blue, {
    titleSize: 14,
    subtitleSize: 10.2,
    subtitleY: 0.47,
  });
  line(slide, 5.6, 4.0, 6.0, 4.0, C.gold, 1.7);
  line(slide, 8.45, 4.0, 8.85, 4.0, C.gold, 1.7);
  addText(slide, "只读投影 / Inspector", 6.25, 4.57, 1.95, 0.2, {
    fontSize: 9,
    color: C.purple,
    align: "center",
    margin: 0,
  });
  line(slide, 7.2, 4.45, 7.2, 4.78, C.purple, 1.2, { dash: "dash" });

  node(slide, "PhysicalSkill", "节点 + 工件 → ActionRequest", 2.42, 5.28, 2.15, 0.72, C.blueLight, C.blue, { titleSize: 12 });
  node(slide, "HarnessRuntime", "新鲜度 / guards / leases / transaction", 4.93, 5.28, 2.45, 0.72, C.tealLight, C.teal, { titleSize: 12 });
  node(slide, "MuJoCo / Backend", "机器人执行", 7.78, 5.28, 1.82, 0.72, C.paper, C.ink, { titleSize: 12 });
  node(slide, "Observation", "每条命令后采样", 9.95, 5.28, 1.72, 0.72, C.greenLight, C.green, { titleSize: 12 });
  node(slide, "World Model", "更新真实状态", 12.0, 5.28, 0.85, 0.72, C.greenLight, C.green, { titleSize: 10 });
  line(slide, 4.57, 5.64, 4.93, 5.64, C.muted, 1.5);
  line(slide, 7.38, 5.64, 7.78, 5.64, C.muted, 1.5);
  line(slide, 9.6, 5.64, 9.95, 5.64, C.muted, 1.5);
  line(slide, 11.67, 5.64, 12.0, 5.64, C.muted, 1.5);

  // Feedback and repair.
  line(slide, 12.42, 5.28, 12.42, 4.58, C.green, 1.5, { dash: "dash" });
  line(slide, 12.42, 4.58, 10.6, 4.58, C.green, 1.5, { dash: "dash" });
  addText(slide, "世界快照 / 证据", 10.85, 4.43, 1.3, 0.2, {
    fontSize: 9,
    color: C.green,
    margin: 0,
    align: "center",
  });
  line(slide, 9.5, 4.45, 9.5, 4.98, C.repair, 1.6, { dash: "dash" });
  pill(slide, "Diagnostic → RepairProposal → repair 子树 → 重试", 7.78, 6.25, 3.98, C.repairLight, C.repair, {
    h: 0.38,
    fontSize: 10,
    line: C.repair,
    bold: true,
  });
  pill(slide, "A* / IK / RRT 是能力，不是任务节点", 2.42, 6.25, 3.78, C.goldLight, C.gold, {
    h: 0.38,
    fontSize: 10,
    line: C.gold,
    bold: true,
  });
  finish(slide, 6, "总体架构", "这页是全 deck 的地图。强调三个箭头：主链、证据反馈、修复回路。");
}

// 6. Task-local vs shared state
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(slide, "06 / 状态边界", "最关键的工程取舍：树隔离，世界连续", "如果把多个任务的节点混在一棵树里，审计和恢复会变复杂；如果每次连物理世界一起重置，又失去连续操作能力。");

  rect(slide, 0.7, 1.55, 5.75, 4.88, C.goldLight, C.gold, true, { lineWidth: 1.4, shadow: true });
  addText(slide, "每个任务新建", 1.0, 1.85, 2.4, 0.3, { fontSize: 20, bold: true, color: C.gold });
  addText(slide, "任务局部控制平面", 1.0, 2.18, 2.4, 0.22, { fontSize: 11, color: C.muted });
  const localItems = [
    ["program/task-0001", "任务 1 根节点"],
    ["TaskTreeStore", "节点、边、栈、事件"],
    ["TaskTreeKernel", "生命周期与重试"],
    ["Inspector / Projection", "只读快照"],
  ];
  localItems.forEach((item, i) => {
    const y = 2.72 + i * 0.65;
    node(slide, item[0], item[1], 1.0, y, 3.65, 0.48, C.paper, C.gold, { titleSize: 12, subtitleSize: 9, subtitleY: 0.24, shadow: false });
    if (i < localItems.length - 1) line(slide, 2.82, y + 0.48, 2.82, y + 0.65, C.gold, 1.3);
  });
  pill(slide, "任务 2 再创建一套：program/task-0002", 1.0, 5.65, 4.85, C.paper, C.gold, {
    h: 0.4,
    fontSize: 11,
    line: C.gold,
    bold: true,
  });

  rect(slide, 6.85, 1.55, 5.75, 4.88, C.tealLight, C.teal, true, { lineWidth: 1.4, shadow: true });
  addText(slide, "跨任务复用", 7.15, 1.85, 2.4, 0.3, { fontSize: 20, bold: true, color: C.teal });
  addText(slide, "物理世界与运行基础设施", 7.15, 2.18, 2.8, 0.22, { fontSize: 11, color: C.muted });
  const sharedItems = [
    ["Scene / World Model", "实体、机器人、场景状态"],
    ["Perception / Predicates", "持续产生观测与证据"],
    ["HarnessRuntime", "工具、租约、事务"],
    ["MuJoCo / RobotBackend", "同一台机器人继续工作"],
  ];
  sharedItems.forEach((item, i) => {
    const y = 2.72 + i * 0.65;
    node(slide, item[0], item[1], 7.15, y, 3.65, 0.48, C.paper, C.teal, { titleSize: 12, subtitleSize: 9, subtitleY: 0.24, shadow: false });
    if (i < sharedItems.length - 1) line(slide, 8.97, y + 0.48, 8.97, y + 0.65, C.teal, 1.3);
  });
  pill(slide, "任务 1 改变的世界，成为任务 2 的起点", 7.15, 5.65, 4.85, C.paper, C.teal, {
    h: 0.4,
    fontSize: 11,
    line: C.teal,
    bold: true,
  });

  line(slide, 6.45, 3.95, 6.85, 3.95, C.ink, 2);
  addText(slide, "隔离树状态", 6.38, 3.47, 0.7, 0.25, { fontSize: 8.8, color: C.ink, align: "center", margin: 0 });
  addText(slide, "共享现实", 6.38, 4.18, 0.7, 0.25, { fontSize: 8.8, color: C.ink, align: "center", margin: 0 });
  callout(slide, "管理层语言：任务可以审计；现场不会被重置。", 2.05, 6.72, 9.2, 0.32, C.paper, C.ink, {
    fontSize: 12,
    bold: true,
    align: "center",
  });
  finish(slide, 7, "状态边界", "这是最值得向 CTO 强调的隔离模型，也是连续机器人操作区别于一次性脚本的关键。");
}

// 7. Kernel lifecycle
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(slide, "07 / 内核机制", "TaskTreeKernel 是整个系统的执行控制平面", "它不负责发明路径，也不负责控制电机；它负责决定哪个节点现在可以做、做完如何确认、失败如何收敛。");
  addText(slide, "一个节点的生命周期", 0.78, 1.45, 3.0, 0.28, { fontSize: 17, bold: true });
  const stages = [
    ["1", "进入", "建立运行态", C.gold],
    ["2", "目标检查", "已满足可跳过", C.green],
    ["3", "前置条件", "快照谓词", C.blue],
    ["4", "执行 / 展开", "系统操作或物理请求", C.teal],
    ["5", "子节点", "sequence / selector", C.gold],
    ["6", "后置验证", "观察证据", C.green],
    ["7", "修复 / 对账", "失败分流", C.repair],
    ["8", "完成", "成功 / 失败 / 阻塞", C.ink],
  ];
  stages.forEach((item, i) => {
    const y = 1.88 + i * 0.55;
    circle(slide, 1.03, y, 0.28, item[3], item[3]);
    addText(slide, item[0], 1.03, y + 0.055, 0.28, 0.16, { fontFace: "Aptos", fontSize: 9, bold: true, color: C.white, align: "center", margin: 0 });
    addText(slide, item[1], 1.52, y, 1.1, 0.25, { fontSize: 13, bold: true });
    addText(slide, item[2], 2.82, y, 2.45, 0.25, { fontSize: 11, color: C.muted });
    if (i < stages.length - 1) line(slide, 1.17, y + 0.28, 1.17, y + 0.55, C.line, 1.4, { arrow: false });
  });
  // Loop from repair back to goal check.
  line(slide, 4.9, 5.75, 4.9, 6.2, C.repair, 1.5, { dash: "dash" });
  line(slide, 4.9, 6.2, 0.78, 6.2, C.repair, 1.5, { dash: "dash" });
  line(slide, 0.78, 6.2, 0.78, 2.15, C.repair, 1.5, { dash: "dash" });
  addText(slide, "修复成功后回到失败节点的目标/前置检查", 1.15, 6.05, 3.7, 0.24, { fontSize: 10, color: C.repair, bold: true });

  rect(slide, 6.2, 1.52, 6.1, 4.95, C.paper, C.line, true, { lineWidth: 1.1, shadow: true });
  addText(slide, "Kernel 负责什么？", 6.55, 1.84, 2.8, 0.3, { fontSize: 18, bold: true, color: C.gold });
  bulletList(slide, [
    "维护执行栈、节点运行态和事件日志",
    "校验 GraphDelta：不越界、不成环、可达",
    "统一处理重试、暂停、取消、预算和对账",
    "决定何时调用分解器、系统操作、物理技能或修复器",
  ], 6.58, 2.32, 5.15, 0.56, { bulletColor: C.gold, fontSize: 12.2 });
  addText(slide, "Kernel 不负责什么？", 6.55, 4.42, 2.9, 0.3, { fontSize: 18, bold: true, color: C.muted });
  bulletList(slide, [
    "不把 A*、IK、RRT 变成任务节点",
    "不直接读取机器人后端内部状态",
    "不允许 LLM 或适配器直接修改 Store",
  ], 6.58, 4.88, 5.15, 0.36, { bulletColor: C.muted, fontSize: 12.2 });
  pill(slide, "唯一写入者 = Kernel ；唯一权威状态 = Store", 7.0, 6.05, 4.55, C.goldLight, C.gold, {
    h: 0.38,
    fontSize: 11,
    line: C.gold,
    bold: true,
  });
  finish(slide, 8, "内核生命周期", "用一句话解释 Kernel：它是任务树的操作系统内核，管理调度与状态，不取代规划器和控制器。");
}

// 8. Node taxonomy and contexts
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(slide, "08 / 职责分层", "三类节点，三种权限，避免“万能 Agent 模块”", "职责分离让扩展点清楚：新增任务不必改物理边界；更换机器人不必重写任务树。");
  const columns = [
    {
      x: 0.7,
      title: "分解器",
      sub: "Decomposer",
      fill: C.greenLight,
      stroke: C.green,
      input: "复合任务节点",
      output: "GraphDelta\n子节点 + child 边",
      can: "只能看节点定义",
      cannot: "不能访问 Runtime / Backend",
    },
    {
      x: 4.52,
      title: "系统操作",
      sub: "System Operation",
      fill: C.blueLight,
      stroke: C.blue,
      input: "系统节点 + WorldSnapshot",
      output: "NodeOutcome\n规划工件 / 诊断",
      can: "绑定、规划、只读工具、验证",
      cannot: "不能直接调用机器人",
    },
    {
      x: 8.34,
      title: "物理技能",
      sub: "Physical Skill",
      fill: C.tealLight,
      stroke: C.teal,
      input: "物理节点 + Artifact",
      output: "ActionRequest\n稳定 request_id",
      can: "把计划翻译成请求",
      cannot: "不能直接控制 Backend",
    },
  ];
  columns.forEach((col) => {
    rect(slide, col.x, 1.55, 3.48, 4.95, col.fill, col.stroke, true, { lineWidth: 1.25, shadow: true });
    addText(slide, col.title, col.x + 0.2, 1.83, 3.08, 0.3, { fontSize: 19, bold: true, color: col.stroke, align: "center" });
    addText(slide, col.sub, col.x + 0.2, 2.17, 3.08, 0.2, { fontFace: "Aptos", fontSize: 10, color: C.muted, align: "center" });
    addText(slide, "输入", col.x + 0.27, 2.72, 0.6, 0.2, { fontSize: 10, bold: true, color: col.stroke });
    callout(slide, col.input, col.x + 0.27, 2.98, 2.94, 0.58, C.paper, col.stroke, { fontSize: 11, align: "center" });
    line(slide, col.x + 1.74, 3.56, col.x + 1.74, 3.82, col.stroke, 1.5);
    addText(slide, "输出", col.x + 0.27, 3.91, 0.6, 0.2, { fontSize: 10, bold: true, color: col.stroke });
    callout(slide, col.output, col.x + 0.27, 4.17, 2.94, 0.7, C.paper, col.stroke, { fontSize: 11, align: "center" });
    addText(slide, "允许做什么", col.x + 0.27, 5.12, 1.1, 0.2, { fontSize: 10, bold: true, color: col.stroke });
    addText(slide, col.can, col.x + 0.27, 5.38, 2.94, 0.38, { fontSize: 10.2, color: C.ink, valign: "top" });
    addText(slide, "明确禁止", col.x + 0.27, 5.86, 1.1, 0.2, { fontSize: 10, bold: true, color: C.repair });
    addText(slide, col.cannot, col.x + 0.27, 6.1, 2.94, 0.3, { fontSize: 10.2, color: C.repair, valign: "top" });
  });
  callout(slide, "特别重要：A* / IK / RRT 属于 capabilities 的内部算法，不是任务树节点；算法迭代不会污染任务语义。", 1.25, 6.72, 10.9, 0.32, C.goldLight, C.gold, {
    fontSize: 11.5,
    color: C.gold,
    bold: true,
    align: "center",
  });
  finish(slide, 9, "职责与权限", "这页面向 CTO：让大家看到扩展点不是靠约定，而是靠上下文和协议强制隔离。");
}

// 9. Example 1 tree
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(slide, "09 / 案例一：正常任务", "例 1：把红色杯子放进投放区，任务树如何展开？", "编译器先只生成一个根节点；Kernel 运行到复合节点时，按需展开子树。");
  rect(slide, 0.7, 1.45, 3.1, 5.25, C.coralLight, C.coral, true, { lineWidth: 1.2, shadow: true });
  addText(slide, "用户指令", 1.0, 1.78, 2.45, 0.28, { fontSize: 17, bold: true, color: C.coral, align: "center" });
  addText(slide, "“把红色杯子放进投放区”", 1.0, 2.28, 2.45, 0.48, { fontSize: 18, bold: true, align: "center" });
  addText(slide, "系统先把自然语言变成两个确定的空间选择：", 1.0, 3.12, 2.45, 0.42, { fontSize: 11, color: C.muted, align: "center" });
  pill(slide, "object_selector → cup-red", 1.0, 3.72, 2.45, C.paper, C.coral, { h: 0.4, fontSize: 10.5, line: C.coral });
  pill(slide, "destination_selector → drop-zone", 1.0, 4.28, 2.45, C.paper, C.coral, { h: 0.4, fontSize: 10.5, line: C.coral });
  addText(slide, "根节点：Place\noperation_kind = decomposer\ncontrol_kind = sequence", 1.0, 5.12, 2.45, 0.95, {
    fontFace: MONO,
    fontSize: 10.5,
    color: C.ink,
    align: "center",
    valign: "top",
  });

  // Tree diagram.
  addText(slide, "运行时任务树（成功后）", 4.25, 1.48, 4.0, 0.28, { fontSize: 17, bold: true });
  node(slide, "Place", "根 / sequence", 7.45, 1.78, 1.4, 0.56, C.goldLight, C.gold, { titleSize: 13, subtitleSize: 8.5, subtitleY: 0.3, shadow: false });
  const roots = [
    ["Resolve", "system", 4.15, C.blueLight, C.blue],
    ["Plan Pick", "system", 5.35, C.blueLight, C.blue],
    ["Plan Transfer", "system", 6.55, C.blueLight, C.blue],
    ["Pick", "sequence", 7.75, C.goldLight, C.gold],
    ["Transfer", "sequence", 8.95, C.goldLight, C.gold],
    ["Release", "sequence", 10.15, C.goldLight, C.gold],
    ["Verify Goal", "system", 11.35, C.greenLight, C.green],
  ];
  roots.forEach((item, i) => {
    line(slide, 8.15, 2.34, item[2] + 0.5, 2.58, item[4], 1.3);
    drawTreeNode(slide, item[0], item[2], 2.58, 1.0, 0.6, item[3], item[4], {
      sub: item[1],
      subSize: 7.5,
      titleSize: item[0].length > 10 ? 8.8 : 10,
    });
  });
  // The sequence subtrees are shown as readable mini-pipelines; exact IDs are
  // listed below so the architecture remains precise without letter wrapping.
  const subtreeCards = [
    { x: 7.2, w: 1.65, parentX: 8.25, title: "Pick 子树", body: "Stance  →  Grasp  →  Held?", color: C.blue, fill: C.blueLight },
    { x: 8.98, w: 1.85, parentX: 9.45, title: "Transfer 子树", body: "Posture  →  Navigate  →  Ready?", color: C.blue, fill: C.blueLight },
    { x: 10.98, w: 1.35, parentX: 10.65, title: "Release 子树", body: "Release  →  Released?", color: C.blue, fill: C.blueLight },
  ];
  subtreeCards.forEach((card) => {
    line(slide, card.parentX, 3.18, card.x + card.w / 2, 3.48, card.color, 1.0);
    rect(slide, card.x, 3.48, card.w, 0.76, card.fill, card.color, true, { lineWidth: 1.0, shadow: false });
    addText(slide, card.title, card.x + 0.08, 3.58, card.w - 0.16, 0.2, {
      fontSize: 9.8,
      bold: true,
      color: card.color,
      align: "center",
      margin: 0,
    });
    addText(slide, card.body, card.x + 0.08, 3.88, card.w - 0.16, 0.22, {
      fontFace: MONO,
      fontSize: 7.5,
      color: C.ink,
      align: "center",
      margin: 0,
      fit: "shrink",
    });
  });
  addText(slide, "完整节点名：NavigateToPickStance · ExecuteGrasp · VerifyHeld", 7.15, 4.42, 5.15, 0.2, {
    fontFace: MONO,
    fontSize: 8.2,
    color: C.muted,
    align: "center",
    margin: 0,
  });
  addText(slide, "MoveToTransportPosture · NavigateHeld · VerifyPlacementReady", 7.15, 4.67, 5.15, 0.2, {
    fontFace: MONO,
    fontSize: 8.2,
    color: C.muted,
    align: "center",
    margin: 0,
  });
  addText(slide, "ExecuteRelease · VerifyReleased", 7.15, 4.92, 5.15, 0.2, {
    fontFace: MONO,
    fontSize: 8.2,
    color: C.muted,
    align: "center",
    margin: 0,
  });
  callout(slide, "图中读法：黄色是复合控制节点，蓝色是系统/物理叶子；算法名称没有出现在树里。", 4.22, 5.22, 8.03, 0.46, C.paper, C.line, {
    fontSize: 11,
    color: C.muted,
    align: "center",
  });
  metric(slide, "16", "最终节点数", 4.55, 5.88, 1.7, C.gold);
  metric(slide, "15", "child 边", 6.62, 5.88, 1.7, C.blue);
  metric(slide, "0", "repair 边", 8.7, 5.88, 1.7, C.repair);
  metric(slide, "成功", "根节点结果", 10.72, 5.88, 1.6, C.green);
  finish(slide, 10, "案例一：任务树", "这是最重要的树形示例。强调：根节点先存在，子节点在执行时由 GraphDelta 动态加入。");
}

// 10. Normal execution timeline
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(slide, "10 / 案例一：执行过程", "正常放置不是一次调用，而是 5 次物理事务 + 多次系统验证", "下图把系统动作和物理动作分开：系统节点负责决策与证据，物理节点负责请求与执行。");
  const steps = [
    ["01", "解析并绑定", "ResolveAndInspect", "cup-red + drop-zone", C.blue, C.blueLight],
    ["02", "规划抓取", "PlanPick", "导航计划 + PickPlan", C.blue, C.blueLight],
    ["03", "提前规划运输", "PlanTransfer", "持物路线 + TransferPlan", C.blue, C.blueLight],
    ["04", "抓取", "Pick", "靠近 → 夹取 → VerifyHeld", C.teal, C.tealLight],
    ["05", "持物运输", "TransferHeld", "运输姿态 → 移动 → Ready", C.teal, C.tealLight],
    ["06", "释放并确认", "Release + VerifyGoal", "释放 → 到达目标区域", C.green, C.greenLight],
  ];
  const startX = 0.78;
  const cellW = 1.9;
  steps.forEach((item, i) => {
    const x = startX + i * 2.02;
    rect(slide, x, 1.62, cellW, 2.05, item[5], item[4], true, { lineWidth: 1.15, shadow: true });
    circle(slide, x + 0.17, 1.83, 0.34, item[4], item[4]);
    addText(slide, item[0], x + 0.17, 1.91, 0.34, 0.15, { fontFace: "Aptos", fontSize: 9, bold: true, color: C.white, align: "center", margin: 0 });
    addText(slide, item[1], x + 0.58, 1.86, 1.16, 0.28, { fontSize: 13, bold: true });
    addText(slide, item[2], x + 0.16, 2.43, 1.58, 0.32, { fontFace: MONO, fontSize: 9.2, color: item[4], align: "center" });
    addText(slide, item[3], x + 0.16, 2.96, 1.58, 0.45, { fontSize: 10, color: C.muted, align: "center", valign: "top" });
    if (i < steps.length - 1) line(slide, x + cellW, 2.62, x + 2.02, 2.62, C.muted, 1.4);
  });
  addText(slide, "物理事务边界", 0.82, 4.15, 2.0, 0.25, { fontSize: 16, bold: true });
  const txLabels = [
    ["T1", "NavigateToPickStance", "移动到底盘抓取位"],
    ["T2", "ExecuteGrasp", "夹取杯子"],
    ["T3", "MoveToTransportPosture", "切换持物姿态"],
    ["T4", "NavigateHeld", "持物沿路线移动"],
    ["T5", "ExecuteRelease", "释放到目标"],
  ];
  txLabels.forEach((item, i) => {
    const x = 0.82 + i * 2.42;
    pill(slide, item[0], x, 4.65, 0.45, C.teal, C.white, { h: 0.34, fontSize: 10, line: C.teal, bold: true });
    addText(slide, item[1], x + 0.57, 4.65, 1.68, 0.22, { fontFace: MONO, fontSize: 9.3, color: C.ink });
    addText(slide, item[2], x + 0.57, 4.92, 1.68, 0.32, { fontSize: 9.5, color: C.muted, valign: "top" });
  });
  line(slide, 1.04, 5.57, 11.92, 5.57, C.teal, 1.8);
  addText(slide, "world:0", 0.74, 5.68, 0.9, 0.22, { fontFace: MONO, fontSize: 10, color: C.muted, margin: 0 });
  addText(slide, "空夹爪 / 杯子在起点", 1.55, 5.68, 2.15, 0.22, { fontSize: 10, color: C.muted, margin: 0 });
  addText(slide, "world:7", 11.72, 5.68, 0.9, 0.22, { fontFace: MONO, fontSize: 10, color: C.green, margin: 0, align: "right" });
  addText(slide, "杯子到位 / 夹爪已释放", 9.52, 5.68, 2.1, 0.22, { fontSize: 10, color: C.green, margin: 0, align: "right" });
  callout(slide, `真实运行结果：${n(evidence.normal.node_count, 16)} 个节点、${n(evidence.normal.event_count, 127)} 个事件、${evidence.normal.transactions.length || 5} 次物理事务，最终 ${evidence.normal.final_snapshot || "world:7"}。`, 2.15, 6.28, 9.1, 0.48, C.greenLight, C.green, {
    fontSize: 13,
    color: C.green,
    bold: true,
    align: "center",
  });
  finish(slide, 11, "案例一：执行过程", "强调物理事务数量和世界版本变化。系统不是只看工具返回成功，而是依赖动作后的观测与验证。");
}

// 11. Artifact dependency chain
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(slide, "11 / 案例一：规划工件", "规划结果不是散落在内存里的变量，而是可追溯的不可变工件", "工件连接了“谁生成、基于哪个世界、依赖什么、谁消费、何时失效”这几件事。");
  addText(slide, "工件依赖链", 0.78, 1.48, 2.4, 0.28, { fontSize: 17, bold: true });
  node(slide, "BindingArtifact", "ResolveAndInspect 生成", 0.8, 2.02, 2.15, 0.8, C.coralLight, C.coral, { titleSize: 14, subtitleSize: 10, subtitleY: 0.48 });
  node(slide, "PickPlan", "PlanPick 生成", 4.0, 2.02, 1.85, 0.8, C.blueLight, C.blue, { titleSize: 14, subtitleSize: 10, subtitleY: 0.48 });
  node(slide, "TransferPlan", "PlanTransfer 生成", 7.02, 2.02, 2.05, 0.8, C.purpleLight, C.purple, { titleSize: 14, subtitleSize: 10, subtitleY: 0.48 });
  node(slide, "PhysicalSkill", "消费请求所需工件", 10.15, 2.02, 2.05, 0.8, C.tealLight, C.teal, { titleSize: 14, subtitleSize: 10, subtitleY: 0.48 });
  line(slide, 2.95, 2.42, 4.0, 2.42, C.coral, 1.7);
  line(slide, 5.85, 2.42, 7.02, 2.42, C.blue, 1.7);
  line(slide, 9.07, 2.42, 10.15, 2.42, C.purple, 1.7);
  addText(slide, "对象/目标绑定", 3.07, 2.14, 0.82, 0.18, { fontSize: 8.5, color: C.coral, align: "center", margin: 0 });
  addText(slide, "依赖抓取结果", 6.02, 2.14, 0.88, 0.18, { fontSize: 8.5, color: C.blue, align: "center", margin: 0 });
  addText(slide, "动作请求消费", 9.2, 2.14, 0.82, 0.18, { fontSize: 8.5, color: C.purple, align: "center", margin: 0 });
  // Metadata panel.
  rect(slide, 0.78, 3.32, 5.9, 2.85, C.paper, C.line, true, { lineWidth: 1.1, shadow: true });
  addText(slide, "每个工件携带的证据", 1.08, 3.62, 2.9, 0.28, { fontSize: 17, bold: true, color: C.purple });
  bulletList(slide, [
    "source snapshot：例如 world:0",
    "依赖实体 / 地图 / 机器人状态版本",
    "frame graph、机器人模型、碰撞模型版本",
    "payload transform hash（持物计划）",
    "假设、随机种子和生产者 / 消费者契约",
  ], 1.08, 4.08, 5.1, 0.38, { bulletColor: C.purple, fontSize: 11.2 });
  rect(slide, 7.0, 3.32, 5.6, 2.85, C.purpleLight, C.purple, true, { lineWidth: 1.1, shadow: true });
  addText(slide, "为什么要不可变？", 7.3, 3.62, 2.8, 0.28, { fontSize: 17, bold: true, color: C.purple });
  addText(slide, "当世界改变时，不修改旧计划，而是：", 7.3, 4.08, 4.9, 0.25, { fontSize: 12, color: C.muted });
  const immutable = [
    ["1", "旧工件保留", "可审计：当时系统看到了什么"],
    ["2", "新工件发布", "可恢复：重规划有明确来源"],
    ["3", "别名指向新版本", "可执行：消费者拿到最新合法工件"],
  ];
  immutable.forEach((item, i) => {
    const y = 4.5 + i * 0.48;
    circle(slide, 7.35, y, 0.24, C.purple, C.purple);
    addText(slide, item[0], 7.35, y + 0.045, 0.24, 0.14, { fontFace: "Aptos", fontSize: 8.5, bold: true, color: C.white, align: "center", margin: 0 });
    addText(slide, item[1], 7.75, y, 1.35, 0.22, { fontSize: 11, bold: true });
    addText(slide, item[2], 9.13, y, 3.0, 0.22, { fontSize: 10.5, color: C.muted });
  });
  callout(slide, "计划失效是可计算的：比较声明的依赖版本与当前快照，而不是依赖一个模糊的 invalidated 标志。", 1.3, 6.48, 10.7, 0.42, C.goldLight, C.gold, {
    fontSize: 12,
    color: C.gold,
    bold: true,
    align: "center",
  });
  finish(slide, 12, "案例一：工件", "这是 CTO 关心的可追溯性：工件把规划变成有版本、有依赖、有消费契约的数据产品。");
}

// 13. Physical action loop
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(
    slide,
    "12 / 从工件到动作",
    "一个物理动作，必须穿过六个阶段才算完成",
    "以 NavigateHeld 为例：系统不是把计划直接交给机器人，而是先检查计划和现场，再执行、观测、提交或对账。",
  );

  const gates = [
    ["1", "读当前快照", "world:0\n读取实体、机器人和地图版本", C.blue, C.blueLight],
    ["2", "检查合法性", "freshness + guards\n计划仍适用于现场", C.purple, C.purpleLight],
    ["3", "锁定资源", "lease + checkpoint\n独占底盘 / 机械臂 / 夹爪", C.gold, C.goldLight],
    ["4", "派发请求", "ActionRequest\nPhysicalSkill 只构造请求", C.teal, C.tealLight],
    ["5", "执行并观测", "Backend → Observation\n每条物理命令后重新采样", C.green, C.greenLight],
    ["6", "提交或对账", "commit / rollback / reconciliation\n更新世界并决定后续节点", C.repair, C.repairLight],
  ];
  const startX = 0.65;
  const boxW = 1.88;
  const gap = 0.2;
  gates.forEach((item, index) => {
    const x = startX + index * (boxW + gap);
    rect(slide, x, 1.62, boxW, 1.56, item[4], item[3], true, {
      lineWidth: 1.15,
      shadow: true,
    });
    circle(slide, x + 0.16, 1.84, 0.34, item[3], item[3]);
    addText(slide, item[0], x + 0.16, 1.925, 0.34, 0.14, {
      fontFace: "Aptos",
      fontSize: 9,
      bold: true,
      color: C.white,
      align: "center",
      margin: 0,
    });
    addText(slide, item[1], x + 0.58, 1.87, 1.12, 0.25, {
      fontSize: 12.5,
      bold: true,
      color: item[3],
    });
    addText(slide, item[2], x + 0.16, 2.35, boxW - 0.32, 0.58, {
      fontSize: 9.4,
      color: C.muted,
      align: "center",
      valign: "top",
    });
    if (index < gates.length - 1) {
      line(slide, x + boxW, 2.4, x + boxW + gap, 2.4, C.muted, 1.4);
    }
  });

  addText(slide, "以 NavigateHeld 为例：计划如何变成事实", 0.78, 3.7, 5.4, 0.3, {
    fontSize: 16.5,
    bold: true,
  });
  rect(slide, 0.75, 4.12, 5.72, 2.05, C.paper, C.line, true, {
    lineWidth: 1.1,
    shadow: true,
  });
  addText(slide, "动作前", 1.05, 4.42, 1.0, 0.24, {
    fontSize: 14,
    bold: true,
    color: C.blue,
  });
  bulletList(slide, [
    "TransferPlan 指向一个明确的 source snapshot",
    "当前机器人、持物对象和路线依赖版本必须匹配",
    "前置谓词成立，资源可独占，才能生成请求",
  ], 1.05, 4.78, 4.95, 0.34, { bulletColor: C.blue, fontSize: 10.7 });
  addText(slide, "动作后", 1.05, 5.78, 1.0, 0.24, {
    fontSize: 14,
    bold: true,
    color: C.green,
  });
  addText(slide, "Observation 写回 WorldModel；Verifier 根据新快照确认“仍然持有、已经到位、路线没有失真”。", 2.0, 5.74, 4.08, 0.42, {
    fontSize: 10.1,
    color: C.muted,
    valign: "top",
  });

  rect(slide, 6.82, 4.12, 5.78, 2.05, C.tealLight, C.teal, true, {
    lineWidth: 1.1,
    shadow: true,
  });
  addText(slide, "三个必须坚持的判断", 7.12, 4.42, 2.9, 0.24, {
    fontSize: 14,
    bold: true,
    color: C.teal,
  });
  bulletList(slide, [
    "API 返回成功，不等于任务后置条件成立",
    "真实机器人不能假装回滚，只能观测后对账",
    "动作后的世界版本，是下一步规划的输入",
  ], 7.12, 4.82, 4.95, 0.32, { bulletColor: C.teal, fontSize: 10.7 });
  pill(slide, "ActionRequest → Observation → Verify → Commit / Repair", 7.18, 5.86, 4.95, C.paper, C.teal, {
    h: 0.28,
    fontSize: 9.2,
    line: C.teal,
    bold: true,
  });
  callout(slide, "核心结论：任务成功 = 动作完成 + 观测有证据 + 后置谓词成立，而不是“命令发出去了”。", 1.05, 6.48, 11.2, 0.4, C.goldLight, C.gold, {
    fontSize: 12.5,
    color: C.gold,
    bold: true,
    align: "center",
  });
  finish(slide, 13, "物理动作闭环", "用一个物理动作说明 Runtime 的作用：先检查，再锁资源和执行，动作后观测，最后提交或对账。");
}

// 14. Blocked route scene
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(slide, "13 / 案例二：故障入口", "例 2：路线被可移动箱子挡住，系统如何判断“该修什么”？", "系统不会让 A* 或控制器偷偷搬走障碍物；先证明阻挡者、可移动性和修复条件，再由任务树显式执行搬运。");
  rect(slide, 0.7, 1.5, 5.2, 5.15, C.paper, C.line, true, { lineWidth: 1.1, shadow: true });
  addText(slide, "参考场景（10 × 8 网格）", 1.0, 1.8, 3.2, 0.28, { fontSize: 17, bold: true });
  // Grid.
  const gx = 1.05;
  const gy = 2.28;
  const cell = 0.37;
  for (let ix = 0; ix < 10; ix += 1) {
    for (let iy = 0; iy < 8; iy += 1) {
      const isWall = ix === 4 && [0, 1, 2, 4, 5, 6, 7].includes(iy);
      rect(slide, gx + ix * cell, gy + (7 - iy) * cell, cell - 0.015, cell - 0.015, isWall ? "BFC8CC" : "F7F9F7", isWall ? "BFC8CC" : "DCE4E1", false, { lineWidth: 0.5 });
    }
  }
  circle(slide, gx + 2.5 * cell - 0.09, gy + (7 - 2.5) * cell + 0.04, 0.2, C.coral, C.coral);
  addText(slide, "杯", gx + 2.5 * cell - 0.1, gy + (7 - 2.5) * cell + 0.07, 0.22, 0.12, { fontSize: 7, bold: true, color: C.white, align: "center", margin: 0 });
  circle(slide, gx + 7.5 * cell - 0.1, gy + (7 - 5.5) * cell + 0.03, 0.22, C.green, C.green);
  addText(slide, "目", gx + 7.5 * cell - 0.1, gy + (7 - 5.5) * cell + 0.07, 0.22, 0.12, { fontSize: 7, bold: true, color: C.white, align: "center", margin: 0 });
  circle(slide, gx + 4.5 * cell - 0.13, gy + (7 - 3.5) * cell - 0.02, 0.28, C.repair, C.repair);
  addText(slide, "箱", gx + 4.5 * cell - 0.13, gy + (7 - 3.5) * cell + 0.065, 0.28, 0.12, { fontSize: 7, bold: true, color: C.white, align: "center", margin: 0 });
  line(slide, gx + 2.5 * cell, gy + (7 - 2.5) * cell + 0.12, gx + 7.5 * cell, gy + (7 - 5.5) * cell + 0.12, C.coral, 2, { dash: "dash" });
  addText(slide, "规划路线穿过箱子附近，连续碰撞检查拒绝该路线", 1.05, 5.45, 4.35, 0.3, { fontSize: 10.5, color: C.repair, bold: true, align: "center" });
  pill(slide, "阻挡物：movable-crate", 1.05, 5.95, 2.05, C.repairLight, C.repair, { h: 0.36, fontSize: 10, line: C.repair, bold: true });
  pill(slide, "修复区：parking-zone", 3.32, 5.95, 2.05, C.greenLight, C.green, { h: 0.36, fontSize: 10, line: C.green, bold: true });

  // Decision path.
  addText(slide, "修复判定链", 6.42, 1.8, 2.2, 0.28, { fontSize: 17, bold: true });
  const checks = [
    ["1", "PlanTransfer 失败", "Diagnostic = ROUTE_BLOCKED", C.repair, C.repairLight],
    ["2", "反事实 A*", "忽略该箱子后，路线恢复", C.blue, C.blueLight],
    ["3", "检查对象属性", "movable = true；是明确见证者", C.gold, C.goldLight],
    ["4", "检查停车区域", "parking-zone 存在且可到达", C.green, C.greenLight],
    ["5", "提出修复", "RepairProposal(GraphDelta)", C.repair, C.repairLight],
  ];
  checks.forEach((item, i) => {
    const y = 2.28 + i * 0.72;
    circle(slide, 6.48, y, 0.3, item[3], item[3]);
    addText(slide, item[0], 6.48, y + 0.06, 0.3, 0.15, { fontFace: "Aptos", fontSize: 9, bold: true, color: C.white, align: "center", margin: 0 });
    node(slide, item[1], item[2], 7.03, y - 0.02, 4.88, 0.56, item[4], item[3], {
      titleSize: 11,
      titleH: 0.22,
      subtitleSize: 8.8,
      subtitleY: 0.32,
      shadow: false,
    });
    if (i < checks.length - 1) line(slide, 6.63, y + 0.3, 6.63, y + 0.72, C.line, 1.2, { arrow: false });
  });
  callout(slide, "关键边界：修复动作本身也是任务，必须进入树、进入事件日志、进入物理事务。", 6.45, 6.05, 5.8, 0.58, C.repairLight, C.repair, {
    fontSize: 12.5,
    color: C.repair,
    bold: true,
    align: "center",
  });
  finish(slide, 14, "案例二：故障入口", "讲清楚“为什么不是任意重试”：修复必须有证据、有条件、有责任归属。");
}

// 15. Repair tree
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(slide, "14 / 案例二：递归修复树", "修复不是旁路脚本，而是挂在失败节点上的一棵真实任务树", "这让修复同样具备顺序、验证、物理事务、失败处理和递归深度限制。");
  addText(slide, "原任务树的关键位置", 0.78, 1.48, 3.1, 0.28, { fontSize: 17, bold: true });
  node(slide, "Place", "根任务", 1.12, 2.0, 1.25, 0.52, C.goldLight, C.gold, { titleSize: 12, subtitleSize: 8, subtitleY: 0.29, shadow: false });
  node(slide, "PlanTransfer", "原失败节点", 1.12, 3.2, 1.55, 0.62, C.repairLight, C.repair, { titleSize: 11, subtitleSize: 8, subtitleY: 0.35, shadow: false });
  line(slide, 1.74, 2.52, 1.74, 3.2, C.gold, 1.4);
  line(slide, 2.67, 3.5, 3.52, 3.5, C.repair, 2);
  pill(slide, "repair 边", 2.76, 3.16, 0.72, C.repairLight, C.repair, { h: 0.3, fontSize: 9, line: C.repair, bold: true });
  node(slide, "RouteBlockedRepair", "origin = repair", 3.42, 3.05, 2.18, 0.9, C.repairLight, C.repair, { titleSize: 10.8, subtitleSize: 9, subtitleY: 0.5, shadow: true });
  line(slide, 4.54, 3.95, 4.54, 4.35, C.repair, 1.5);
  addText(slide, "只允许一个修复入口，且修复子树必须从失败节点可达", 0.88, 4.55, 4.85, 0.55, {
    fontSize: 11.5,
    color: C.muted,
    align: "center",
  });
  callout(slide, "内核先校验 GraphDelta：repair 边、入口节点、作用域、无环、可达。通过后才挂载。", 0.95, 5.42, 4.7, 0.75, C.paper, C.repair, {
    fontSize: 11.5,
    color: C.repair,
    bold: true,
    align: "center",
  });

  // Detailed repair subtree.
  addText(slide, "修复子树展开", 6.2, 1.48, 3.1, 0.28, { fontSize: 17, bold: true });
  node(slide, "RouteBlockedRepair", "repair / sequence", 8.35, 1.9, 2.3, 0.58, C.repairLight, C.repair, { titleSize: 10.8, subtitleSize: 8.5, subtitleY: 0.32, shadow: false });
  node(slide, "Place", "递归搬走阻挡物", 6.35, 2.95, 1.95, 0.58, C.goldLight, C.gold, { titleSize: 13, subtitleSize: 8.5, subtitleY: 0.32, shadow: false });
  node(slide, "PlanPick", "刷新依赖计划", 10.55, 2.95, 1.95, 0.58, C.blueLight, C.blue, { titleSize: 13, subtitleSize: 8.5, subtitleY: 0.32, shadow: false });
  line(slide, 9.5, 2.48, 7.33, 2.95, C.repair, 1.4);
  line(slide, 9.5, 2.48, 11.53, 2.95, C.repair, 1.4);

  // The recursive Place subtree is a readable sequence rather than six
  // narrow boxes that would force long identifiers into vertical text.
  const subTree = [
    ["解析", 6.15],
    ["抓取规划", 7.16],
    ["运输规划", 8.17],
    ["抓取", 9.18],
    ["持物运输", 10.19],
    ["释放", 11.2],
  ];
  subTree.forEach((item, index) => {
    if (index === 0) {
      line(slide, 7.33, 3.53, item[1] + 0.4, 3.87, C.gold, 1.0);
    } else {
      line(slide, item[1] - 0.11, 4.15, item[1], 4.15, C.gold, 1.0);
    }
    rect(slide, item[1], 3.87, 0.8, 0.56, C.paper, C.gold, true, { lineWidth: 1.0, shadow: false });
    addText(slide, item[0], item[1] + 0.04, 4.04, 0.72, 0.18, {
      fontSize: item[0].length > 3 ? 8.3 : 9.5,
      bold: true,
      color: C.ink,
      align: "center",
      margin: 0,
      fit: "shrink",
    });
  });
  addText(slide, "完整节点名：ResolveAndInspect → PlanPick → PlanTransfer", 6.1, 4.62, 6.15, 0.2, {
    fontFace: MONO,
    fontSize: 8.5,
    color: C.muted,
    align: "center",
    margin: 0,
  });
  addText(slide, "Pick → TransferHeld → Release", 6.1, 4.88, 6.15, 0.2, {
    fontFace: MONO,
    fontSize: 8.5,
    color: C.muted,
    align: "center",
    margin: 0,
  });
  addText(slide, "Place(阻挡物, parking-zone) 自己也会展开为解析、规划、抓取、持物运输、释放和验证。", 6.3, 5.16, 6.0, 0.42, {
    fontSize: 11,
    color: C.muted,
    align: "center",
  });
  metric(slide, "34", "最终节点", 6.55, 5.78, 1.55, C.repair);
  metric(slide, "1", "repair 边", 8.52, 5.78, 1.55, C.repair);
  metric(slide, "10", "物理事务", 10.49, 5.78, 1.55, C.teal);
  metric(slide, "275", "事件", 12.0, 5.78, 0.75, C.blue);
  finish(slide, 15, "案例二：修复树", "这个案例要传达：修复与主任务使用同一套内核和节点契约，因此能够递归、审计和再次修复。");
}

// 16. Repair timeline
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(slide, "15 / 案例二：详细执行", "从路线失败到恢复成功，系统到底做了哪些事？", "把故障处理拆成可讲解的 8 个动作，听众就能看到“递归”不是一句口号。");
  const timeline = [
    ["1", "PlanTransfer", "world:0", "持物路线被箱子阻挡", C.repair],
    ["2", "Diagnostic", "ROUTE_BLOCKED", "记录失败原因与见证者", C.repair],
    ["3", "Mount", "repair 边", "内核挂载 RouteBlockedRepair", C.repair],
    ["4", "Recursive Place", "箱子 → 停放区", "修复树执行搬箱任务", C.gold],
    ["5", "Observe", "world:7", "观察到箱子已移走", C.green],
    ["6", "Replan", "新工件", "刷新 PickPlan / TransferPlan", C.blue],
    ["7", "Retry", "原节点", "回到原 PlanTransfer/主任务", C.teal],
    ["8", "Verify", "world:14", "杯子到位，任务成功", C.green],
  ];
  const y = 2.02;
  line(slide, 1.08, y + 0.31, 12.05, y + 0.31, C.line, 2, { arrow: false });
  timeline.forEach((item, i) => {
    const x = 0.72 + i * 1.55;
    circle(slide, x + 0.22, y, 0.44, item[4], item[4]);
    addText(slide, item[0], x + 0.22, y + 0.115, 0.44, 0.15, { fontFace: "Aptos", fontSize: 10, bold: true, color: C.white, align: "center", margin: 0 });
    addText(slide, item[1], x, y + 0.7, 0.88, 0.32, { fontFace: MONO, fontSize: 8.8, bold: true, color: item[4], align: "center" });
    addText(slide, item[2], x, y + 1.12, 0.88, 0.22, { fontSize: 9, color: C.ink, align: "center" });
    addText(slide, item[3], x - 0.18, y + 1.48, 1.25, 0.55, { fontSize: 8.8, color: C.muted, align: "center", valign: "top" });
  });
  rect(slide, 0.78, 4.35, 5.8, 1.78, C.repairLight, C.repair, true, { lineWidth: 1.2, shadow: true });
  addText(slide, "没有隐藏副作用", 1.08, 4.67, 2.4, 0.28, { fontSize: 17, bold: true, color: C.repair });
  bulletList(slide, [
    "A* 只证明路线，不负责搬障碍物",
    "搬箱子是 Place 任务，会进入物理事务",
    "失败和修复都在同一棵可审计树里",
  ], 1.08, 5.08, 4.95, 0.34, { bulletColor: C.repair, fontSize: 10.8 });
  rect(slide, 6.85, 4.35, 5.75, 1.78, C.tealLight, C.teal, true, { lineWidth: 1.2, shadow: true });
  addText(slide, "重试的前提", 7.15, 4.67, 2.4, 0.28, { fontSize: 17, bold: true, color: C.teal });
  bulletList(slide, [
    "新世界快照已被观测并进入 World Model",
    "旧路线 / 计划按依赖关系重新检查新鲜度",
    "只消费新的、契约匹配的工件",
  ], 7.15, 5.08, 4.95, 0.34, { bulletColor: C.teal, fontSize: 10.8 });
  callout(slide, "最终结果：PlanTransfer 从第一次失败变成第二次成功；根任务仍然是同一个目标。", 1.3, 6.48, 10.7, 0.42, C.goldLight, C.gold, {
    fontSize: 12,
    color: C.gold,
    bold: true,
    align: "center",
  });
  finish(slide, 16, "案例二：执行过程", "讲这页时按时间顺序走一遍，最后强调原任务目标没有被替换，只是修复了阻碍它的现实条件。");
}

// 17. Continuous multi-task example
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(slide, "16 / 案例三：连续会话", "连续操作：每条指令一棵新树，但现实世界不重置", "这是产品化体验的核心：用户可以连续下达任务，系统保持现场状态，同时保证每棵树独立可审计。");
  const firstWorld = n(continuous.first_state?.world?.revision, 7);
  const secondWorld = n(continuous.second_state?.world?.revision, 14);
  const firstTx = n(continuous.first_state?.last_result?.transactions, 5);
  const secondTx = n(continuous.second_state?.last_result?.transactions, 5);
  rect(slide, 0.75, 1.55, 12.0, 4.45, C.paper, C.line, true, { lineWidth: 1.1, shadow: true });
  // Shared world rail.
  addText(slide, "同一条世界时间线", 1.05, 5.25, 2.05, 0.24, { fontSize: 14, bold: true, color: C.teal });
  line(slide, 2.95, 5.41, 11.85, 5.41, C.teal, 2, { arrow: false });
  pill(slide, "world:0", 3.08, 5.2, 0.75, C.tealLight, C.teal, { h: 0.34, fontSize: 10, line: C.teal });
  pill(slide, `world:${firstWorld}`, 6.4, 5.2, 0.85, C.tealLight, C.teal, { h: 0.34, fontSize: 10, line: C.teal });
  pill(slide, `world:${secondWorld}`, 10.95, 5.2, 1.0, C.greenLight, C.green, { h: 0.34, fontSize: 10, line: C.green });
  addText(slide, "初始场景", 3.02, 5.72, 0.9, 0.2, { fontSize: 9.5, color: C.muted, align: "center", margin: 0 });
  addText(slide, "箱子进入停放区", 6.1, 5.72, 1.5, 0.2, { fontSize: 9.5, color: C.muted, align: "center", margin: 0 });
  addText(slide, "杯子进入投放区", 10.6, 5.72, 1.65, 0.2, { fontSize: 9.5, color: C.green, align: "center", margin: 0 });

  // Task cards above rail.
  node(slide, "Task 0001", "Place(movable-crate → parking-zone)", 1.05, 2.0, 4.15, 1.1, C.goldLight, C.gold, { titleSize: 15, subtitleSize: 10.2, subtitleY: 0.5, shadow: true });
  addText(slide, "root = program/task-0001", 1.35, 3.34, 3.55, 0.22, { fontFace: MONO, fontSize: 9.5, color: C.gold, align: "center" });
  metric(slide, "16", "节点", 1.25, 3.7, 0.92, C.gold);
  metric(slide, String(firstTx), "物理事务", 2.4, 3.7, 0.92, C.teal);
  metric(slide, `0→${firstWorld}`, "世界版本", 3.55, 3.7, 1.1, C.green);
  line(slide, 3.12, 4.83, 3.12, 5.2, C.gold, 1.5);

  node(slide, "Task 0002", "Place(cup-red → drop-zone)", 7.25, 2.0, 4.15, 1.1, C.blueLight, C.blue, { titleSize: 15, subtitleSize: 10.2, subtitleY: 0.5, shadow: true });
  addText(slide, "root = program/task-0002", 7.55, 3.34, 3.55, 0.22, { fontFace: MONO, fontSize: 9.5, color: C.blue, align: "center" });
  metric(slide, "16", "节点", 7.45, 3.7, 0.92, C.blue);
  metric(slide, String(secondTx), "物理事务", 8.6, 3.7, 0.92, C.teal);
  metric(slide, `${firstWorld}→${secondWorld}`, "世界版本", 9.75, 3.7, 1.25, C.green);
  line(slide, 9.32, 4.83, 9.32, 5.2, C.blue, 1.5);

  pill(slide, "新树：避免不同根目标的节点混合", 1.2, 6.25, 4.7, C.goldLight, C.gold, { h: 0.4, fontSize: 11, line: C.gold, bold: true });
  pill(slide, "共享世界：保留机器人和现场连续性", 7.35, 6.25, 4.7, C.tealLight, C.teal, { h: 0.4, fontSize: 11, line: C.teal, bold: true });
  finish(slide, 17, "案例三：连续会话", "这页适合向老板解释产品体验，向 CTO 解释状态隔离：两个目标不会混树，但第二个任务能看到第一个任务改变的现实。");
}

// 18. Physical safety gates
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(slide, "17 / 安全与治理", "每条物理动作都要穿过一组明确的闸门", "安全不依赖模型“自觉”，而是由工件契约、快照、资源、事务和观测共同约束。");
  const gates = [
    ["1", "工件新鲜度", "计划是否仍适用于当前世界？", C.purple],
    ["2", "快照谓词", "动作前置条件是否成立？", C.blue],
    ["3", "资源租约", "底盘、机械臂、夹爪是否可独占？", C.gold],
    ["4", "Checkpoint", "模拟器可回滚；真实后端准备对账", C.teal],
    ["5", "受限派发", "普通动作与 system_only 分流", C.coral],
    ["6", "动作后观测", "每条物理命令重新采样", C.green],
    ["7", "提交 / 对账", "成功提交；失败回滚或 reconciliation", C.repair],
  ];
  const gx = 0.75;
  gates.forEach((item, i) => {
    const x = gx + i * 1.77;
    circle(slide, x + 0.48, 1.85, 0.42, item[3], item[3]);
    addText(slide, item[0], x + 0.48, 1.96, 0.42, 0.15, { fontFace: "Aptos", fontSize: 10, bold: true, color: C.white, align: "center", margin: 0 });
    if (i < gates.length - 1) line(slide, x + 0.9, 2.06, x + 1.77, 2.06, C.line, 1.4);
    node(slide, item[1], item[2], x, 2.55, 1.42, 1.18, C.paper, item[3], { titleSize: 11, subtitleSize: 8.6, subtitleY: 0.45, shadow: true });
  });
  addText(slide, "物理边界的所有权", 0.78, 4.35, 2.6, 0.28, { fontSize: 17, bold: true });
  node(slide, "PhysicalSkill", "只构造 ActionRequest", 0.92, 4.9, 2.15, 0.8, C.blueLight, C.blue, { titleSize: 14, subtitleSize: 10, subtitleY: 0.48, shadow: false });
  node(slide, "Harness Gateway", "PhysicalGateway：只允许声明动作", 3.78, 4.9, 2.35, 0.8, C.tealLight, C.teal, { titleSize: 13, subtitleSize: 8.8, subtitleY: 0.48, shadow: false });
  node(slide, "HarnessRuntime", "唯一调用后端", 6.88, 4.9, 2.0, 0.8, C.goldLight, C.gold, { titleSize: 14, subtitleSize: 10, subtitleY: 0.48, shadow: false });
  node(slide, "RobotBackend", "执行 + 观测", 9.63, 4.9, 1.85, 0.8, C.greenLight, C.green, { titleSize: 14, subtitleSize: 10, subtitleY: 0.48, shadow: false });
  node(slide, "World Model", "证据回流", 12.0, 4.9, 0.85, 0.8, C.greenLight, C.green, { titleSize: 10, subtitleSize: 9, subtitleY: 0.48, shadow: false });
  line(slide, 3.07, 5.3, 3.78, 5.3, C.muted, 1.5);
  line(slide, 6.13, 5.3, 6.88, 5.3, C.muted, 1.5);
  line(slide, 8.88, 5.3, 9.63, 5.3, C.muted, 1.5);
  line(slide, 11.48, 5.3, 12.0, 5.3, C.muted, 1.5);
  callout(slide, "路线安全还要求连续验证：A* 结果、实际起点/终点、结构化障碍物、机器人 footprint 和执行时重验证使用同一套策略。", 1.05, 6.25, 11.2, 0.47, C.paper, C.teal, {
    fontSize: 11.5,
    color: C.teal,
    bold: true,
    align: "center",
  });
  finish(slide, 18, "安全与治理", "这页要让听众放心：安全是架构边界，不是提示词。可把 7 道闸门理解为物理动作的审批链。");
}

// 19. Advantages
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(slide, "18 / 价值与优势", "这套架构带来的不是“多几个模块”，而是四种可量化能力", "用管理层语言讲价值，用 CTO 语言讲机制；下面每项都能在源码和案例中找到对应证据。");
  const advantages = [
    ["可靠性", "失败可收敛", "路线阻塞会进入 repair 子树，修复后重试原目标，而不是靠模型随机重试。", C.repair, C.repairLight, "案例二：34 节点 / 1 repair 边 / 成功"],
    ["解释性", "每一步有证据", "树、事件、工件、快照和事务记录让动作前因后果可复盘。", C.blue, C.blueLight, "案例一：127 个事件可追踪"],
    ["扩展性", "替换局部实现", "新增任务加 decomposer；新增机器人实现 Backend；A* / IK / RRT 可独立演进。", C.teal, C.tealLight, "任务语义与算法解耦"],
    ["安全性", "物理动作有边界", "只有 Runtime 能调用后端，工件、guards、租约和对账共同限制副作用。", C.gold, C.goldLight, "动作后必观测"],
    ["连续性", "现场不重置", "每个任务新树，World/Runtime/Backend 跨任务复用，适合多步协作。", C.green, C.greenLight, "task-0001 → task-0002"],
    ["工程效率", "问题定位更快", "故障落在具体节点、具体工件和具体世界版本上，减少“整条链路猜测”。", C.coral, C.coralLight, "局部重规划而非全量重做"],
  ];
  advantages.forEach((item, index) => {
    const col = index % 3;
    const row = Math.floor(index / 3);
    const x = 0.7 + col * 4.1;
    const y = 1.58 + row * 2.18;
    rect(slide, x, y, 3.65, 1.78, item[4], item[3], true, { lineWidth: 1.15, shadow: true });
    addText(slide, item[0], x + 0.2, y + 0.2, 1.05, 0.23, { fontSize: 12, bold: true, color: item[3] });
    addText(slide, item[1], x + 0.2, y + 0.54, 3.15, 0.3, { fontSize: 17, bold: true });
    addText(slide, item[2], x + 0.2, y + 0.96, 3.15, 0.45, { fontSize: 10.5, color: C.muted, valign: "top" });
    pill(slide, item[5], x + 0.2, y + 1.47, 3.15, C.paper, item[3], { h: 0.28, fontSize: 8.8, line: item[3], bold: true });
  });
  callout(slide, "管理层可见结果：风险可控、过程可解释、故障可恢复；技术团队可见结果：边界清晰、替换点明确、测试可落地。", 0.95, 6.32, 11.5, 0.48, C.ink, C.ink, {
    fontSize: 13,
    color: C.white,
    bold: true,
    align: "center",
  });
  finish(slide, 19, "价值与优势", "不要只说架构优雅，要把价值和案例数字绑定：可靠性、解释性、扩展性、安全性、连续性。");
}

// 20. Productionization
{
  const slide = pptx.addSlide("TRT_MASTER");
  sectionHeader(slide, "19 / 生产化边界", "参考实现已经把替换点留出来，下一步是把边界变成生产能力", "当前仓库是 reference modular monolith；它验证的是架构契约，不等于所有生产基础设施已经完成。");
  rect(slide, 0.75, 1.55, 5.65, 4.95, C.paper, C.line, true, { lineWidth: 1.1, shadow: true });
  addText(slide, "当前参考实现", 1.05, 1.88, 2.6, 0.3, { fontSize: 19, bold: true, color: C.blue });
  bulletList(slide, [
    "TaskTreeStore：内存权威状态，JSON 用于检查",
    "确定性平面双连杆模拟器 + 保守碰撞模型",
    "本地单操作员 Web 控制台，默认 loopback",
    "完整测试覆盖内核、工件、物理边界和修复",
    "GeminiER2 适配层复用 Harness 的世界与工具",
  ], 1.05, 2.42, 4.9, 0.58, { bulletColor: C.blue, fontSize: 11.5 });
  pill(slide, "架构验证重点：契约、所有权、恢复", 1.15, 5.85, 4.85, C.blueLight, C.blue, { h: 0.4, fontSize: 11, line: C.blue, bold: true });

  rect(slide, 6.92, 1.55, 5.65, 4.95, C.tealLight, C.teal, true, { lineWidth: 1.1, shadow: true });
  addText(slide, "生产化优先级", 7.22, 1.88, 2.6, 0.3, { fontSize: 19, bold: true, color: C.teal });
  const roadmap = [
    ["P0", "持久化 Store", "SQLite/PostgreSQL/event log；树、栈、事件、工件同一事务边界"],
    ["P0", "真实机器人适配", "保留 ActionRequest、Observation、guard 和 reconciliation 契约"],
    ["P1", "安全与运维", "认证、多操作员、指标、分布式追踪、告警和回放"],
    ["P1", "评测与策略", "固定条件评测、故障注入、路线策略版本治理"],
    ["P2", "规模化编排", "任务模板、能力注册、资源池和多机器人协调"],
  ];
  roadmap.forEach((item, i) => {
    const y = 2.32 + i * 0.68;
    pill(slide, item[0], 7.25, y, 0.48, item[0] === "P0" ? C.repair : C.gold, C.white, { h: 0.3, fontSize: 9, line: item[0] === "P0" ? C.repair : C.gold, bold: true });
    addText(slide, item[1], 7.95, y, 1.45, 0.24, { fontSize: 11.5, bold: true });
    addText(slide, item[2], 9.45, y, 2.72, 0.4, { fontSize: 9.5, color: C.muted, valign: "top" });
  });
  callout(slide, "决策点：先选一个可观测、可回放、可注入故障的机器人任务作为生产试点。", 1.1, 6.62, 11.1, 0.38, C.goldLight, C.gold, {
    fontSize: 12,
    color: C.gold,
    bold: true,
    align: "center",
  });
  finish(slide, 20, "生产化边界", "主动讲局限会增加可信度：当前实现验证架构，生产化主要补持久化、真实后端、运维治理和评测体系。");
}

// 21. Closing
{
  const slide = pptx.addSlide("TRT_MASTER");
  slide.background = { color: C.ink };
  rect(slide, 0, 0, SW, SH, C.ink, C.ink, false);
  rect(slide, 0, 0, 0.18, SH, C.teal, C.teal, false);
  addText(slide, "核心判断", 0.78, 0.78, 2.4, 0.3, {
    fontFace: "Aptos",
    fontSize: 12,
    bold: true,
    color: "8AD2C8",
    charSpacing: 1.1,
  });
  addText(slide, "让 Agent 进入真实世界，关键不是让模型更“会说”，而是让执行过程更“可控”。", 0.78, 1.35, 8.8, 0.85, {
    fontSize: 27,
    bold: true,
    color: C.white,
  });
  const closeItems = [
    ["TaskTreeKernel", "执行控制平面", "统一解释任务树、状态和恢复"],
    ["Harness", "物理世界适配层", "承载感知、工具、事务和机器人"],
    ["Repair", "一等公民", "把失败变成可递归的工作"],
  ];
  closeItems.forEach((item, i) => {
    const x = 0.85 + i * 3.95;
    rect(slide, x, 3.05, 3.45, 1.45, i === 0 ? "3D4A36" : i === 1 ? "274C4A" : "4E3240", i === 0 ? C.gold : i === 1 ? C.teal : C.repair, true, { lineWidth: 1.2 });
    addText(slide, item[0], x + 0.2, 3.32, 3.05, 0.3, { fontFace: MONO, fontSize: 14, bold: true, color: C.white, align: "center" });
    addText(slide, item[1], x + 0.2, 3.75, 3.05, 0.25, { fontSize: 15, bold: true, color: C.white, align: "center" });
    addText(slide, item[2], x + 0.2, 4.13, 3.05, 0.25, { fontSize: 10.5, color: "C7D4D5", align: "center" });
  });
  line(slide, 1.0, 5.35, 12.1, 5.35, "567177", 1.2, { arrow: false });
  addText(slide, "下一步建议", 0.85, 5.65, 1.5, 0.25, { fontSize: 14, bold: true, color: "8AD2C8" });
  addText(slide, "确定试点任务  →  接入持久化 Store  →  接入真实机器人  →  建立故障注入与回放评测", 2.35, 5.65, 9.7, 0.25, {
    fontSize: 14,
    color: C.white,
  });
  addText(slide, "Task Recursive Tree Agent  ·  2026-08-30", 0.85, 6.72, 5.8, 0.22, {
    fontFace: "Aptos",
    fontSize: 9,
    color: "8C9AA0",
  });
  addText(slide, "谢谢", 11.6, 6.52, 0.9, 0.4, { fontSize: 17, color: "8AD2C8", align: "right" });
  const closingNumber = slides.length + 1;
  const closingNotes = formatSpeakerNotes(
    closingNumber,
    "结论",
    "最后只留下三句话：Kernel 管控制平面，Harness 承载物理世界，Repair 把失败变成工作。然后进入试点决策。",
  );
  footer(slide, closingNumber);
  slides.push({
    number: closingNumber,
    title: "结论",
    notes: closingNotes,
  });
  slide.addNotes(closingNotes);
}

function writeNotes() {
  const lines = [
    "# Task Recursive Tree Agent 架构讲解讲稿",
    "",
    "适用听众：老板、CTO、架构评审会。",
    "",
    "## 建议讲法",
    "",
    "建议节奏：前 3 页建立共同语言；第 4 至 9 页解释边界和控制平面；第 10 至 17 页用正常任务、故障修复和连续会话证明架构在运行；第 18 至 20 页回答价值、生产化和决策。",
    "",
  ];
  for (const item of slides) {
    lines.push(`## ${String(item.number).padStart(2, "0")}. ${item.title}`);
    lines.push("");
    lines.push(item.notes || "本页按图讲解。");
    lines.push("");
  }
  lines.push("## 附录 A：术语速译");
  lines.push("");
  lines.push("| 技术术语 | 通俗解释 |");
  lines.push("| --- | --- |");
  lines.push("| TaskProgram | 用户目标的结构化任务意图，模型可以提交，但不能直接执行物理动作 |");
  lines.push("| TaskTree | 一棵记录步骤、顺序、状态、验证和修复的任务树 |");
  lines.push("| TaskTreeKernel | 任务树的执行控制平面，决定现在做什么以及失败后怎么走 |");
  lines.push("| TaskTreeStore | 运行时任务树的唯一权威状态和事件记录 |");
  lines.push("| WorldModel | 对机器人、物体、障碍物和版本的现场事实账本 |");
  lines.push("| Artifact | 基于某个世界快照生成的不可变规划工件，类似带版本的施工图 |");
  lines.push("| GraphDelta | 对任务树的受约束增量修改，例如按需增加子节点 |");
  lines.push("| Diagnostic | 结构化失败原因，例如 ROUTE_BLOCKED 或 HOLD_LOST |");
  lines.push("| RepairProposal | 修复器提出的修复子树方案，最终仍由 Kernel 校验和挂载 |");
  lines.push("| ActionRequest | 物理技能生成的受限动作请求，不等于直接调用机器人 |");
  lines.push("| Observation | 机器人动作后的观测证据，是更新世界模型和验证结果的输入 |");
  lines.push("| reconciliation | 真实设备无法回滚时，根据观测重新对账并恢复事实 |");
  lines.push("");
  lines.push("## 附录 B：管理层价值与 CTO 机制对照");
  lines.push("");
  lines.push("| 管理层关心 | 架构机制 | 可观察证据 |");
  lines.push("| --- | --- | --- |");
  lines.push("| 风险可控 | 角色权限、Runtime 唯一物理入口、七道安全闸门 | 物理请求、事务、观测和对账记录 |");
  lines.push("| 故障可恢复 | Diagnostic、repair 边、递归修复、依赖重规划 | 34 节点、1 条 repair 边、最终成功 |");
  lines.push("| 过程可解释 | 任务树、执行栈、事件日志、世界快照 | 正常案例 127 个事件可回放 |");
  lines.push("| 能持续使用 | 任务树按任务隔离，World/Backend 跨任务复用 | world:0 → world:7 → world:14 |");
  lines.push("| 能持续演进 | 任务、算法、机器人通过协议解耦 | decomposer / capability / backend 可替换 |");
  lines.push("");
  lines.push("## 附录 C：建议现场确认的决策问题");
  lines.push("");
  lines.push("1. 先选哪个真实任务作为试点，成功条件和不可接受副作用是什么？");
  lines.push("2. 试点是否具备观测、回放和故障注入条件？");
  lines.push("3. 第一阶段需要接入哪一种真实机器人后端，谁负责设备安全验收？");
  lines.push("4. P0 是否先完成持久化 Store 和事务边界，再扩大任务类型？");
  lines.push("5. 用哪些指标判断试点值得继续：成功率、恢复收敛率、人工介入率、过期计划拦截率还是回放完整率？");
  lines.push("");
  fs.writeFileSync(OUT_NOTES, lines.join("\n"), "utf8");
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  await pptx.writeFile({ fileName: OUT_FILE });
  writeNotes();
  fs.writeFileSync(
    OUT_MANIFEST,
    JSON.stringify(
      {
        title: pptx.title,
        slide_count: slides.length,
        source: "D:\\Task Recursive Tree",
        evidence_files: [
          ".artifacts/ppt-examples/execution-evidence.json",
          ".artifacts/ppt-examples/continuous-two-tasks.json",
        ],
        slides,
        output: OUT_FILE,
      },
      null,
      2,
    ),
    "utf8",
  );
  console.log(`Wrote ${OUT_FILE}`);
  console.log(`Wrote ${OUT_NOTES}`);
  console.log(`Wrote ${OUT_MANIFEST}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
