const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const DOCS = path.join(ROOT, "docs");
const OUT_HTML = path.join(
  DOCS,
  "Task-Recursive-Tree-Agent-架构讲解-个人学习详解版.html",
);

const COLORS = {
  ink: "#20313a",
  muted: "#66767d",
  light: "#f4f7f5",
  line: "#d9e1df",
  coral: "#d96c4c",
  coralLight: "#fff0ea",
  teal: "#187f75",
  tealLight: "#e8f5f1",
  gold: "#c48b24",
  goldLight: "#fff5d8",
  blue: "#4b78a8",
  blueLight: "#eaf1fa",
  purple: "#74559a",
  purpleLight: "#f1ecf8",
  green: "#5a8c55",
  greenLight: "#edf6e9",
  repair: "#b34a70",
  repairLight: "#fbeaf0",
  dark: "#202a30",
  white: "#ffffff",
  red: "#b84040",
};

function esc(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function pageNumber(n) {
  return String(n).padStart(2, "0");
}

function pill(text, tone = "teal") {
  return `<span class="pill pill-${tone}">${esc(text)}</span>`;
}

function callout(title, text, tone = "teal") {
  return `
    <div class="callout callout-${tone}">
      <div class="callout-title">${esc(title)}</div>
      <div class="callout-text">${text}</div>
    </div>
  `;
}

function bullets(items, className = "") {
  return `<ul class="bullets ${className}">${items
    .map((item) => `<li>${item}</li>`)
    .join("")}</ul>`;
}

function codeBlock(text, className = "") {
  return `<pre class="code-block ${className}">${esc(text)}</pre>`;
}

function stat(label, value, tone = "teal", note = "") {
  return `
    <div class="stat stat-${tone}">
      <div class="stat-value">${esc(value)}</div>
      <div class="stat-label">${esc(label)}</div>
      ${note ? `<div class="stat-note">${esc(note)}</div>` : ""}
    </div>
  `;
}

function nodeCard(name, kind, detail, tone = "teal", extra = "") {
  return `
    <div class="node-card node-${tone}">
      <div class="node-top">
        <strong>${esc(name)}</strong>
        ${pill(kind, tone)}
      </div>
      <div class="node-detail">${detail}</div>
      ${extra ? `<div class="node-extra">${extra}</div>` : ""}
    </div>
  `;
}

function arrowLabel(label, tone = "muted") {
  return `<div class="arrow-label arrow-${tone}"><span>${esc(label)}</span><i>→</i></div>`;
}

function architectureSvg() {
  return `
  <svg class="diagram-svg architecture-svg" viewBox="0 0 1000 590" role="img" aria-label="四层架构图">
    <defs>
      <marker id="arr-arch" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
        <path d="M0,0 L8,4 L0,8 Z" fill="${COLORS.ink}"></path>
      </marker>
      <marker id="arr-repair" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
        <path d="M0,0 L8,4 L0,8 Z" fill="${COLORS.repair}"></path>
      </marker>
    </defs>
    <rect x="20" y="18" width="960" height="112" rx="12" fill="${COLORS.coralLight}" stroke="${COLORS.coral}" stroke-width="2"/>
    <text x="42" y="47" class="svg-section">1 入口与意图</text>
    <rect x="45" y="63" width="160" height="45" rx="8" fill="#fff" stroke="${COLORS.coral}"/>
    <text x="125" y="90" class="svg-label" text-anchor="middle">用户：把红杯放好</text>
    <line x1="208" y1="86" x2="252" y2="86" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-arch)"/>
    <rect x="258" y="63" width="190" height="45" rx="8" fill="#fff" stroke="${COLORS.coral}"/>
    <text x="353" y="82" class="svg-label" text-anchor="middle">LLM / Agent</text>
    <text x="353" y="99" class="svg-small" text-anchor="middle">理解、澄清、绑定对象</text>
    <line x1="451" y1="86" x2="495" y2="86" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-arch)"/>
    <rect x="501" y="63" width="205" height="45" rx="8" fill="#fff" stroke="${COLORS.coral}"/>
    <text x="603" y="82" class="svg-label" text-anchor="middle">TaskProgram</text>
    <text x="603" y="99" class="svg-small" text-anchor="middle">受约束的任务意图</text>
    <line x1="709" y1="86" x2="753" y2="86" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-arch)"/>
    <rect x="759" y="63" width="190" height="45" rx="8" fill="#fff" stroke="${COLORS.coral}"/>
    <text x="854" y="82" class="svg-label" text-anchor="middle">编译边界</text>
    <text x="854" y="99" class="svg-small" text-anchor="middle">不直接写运行时树</text>

    <rect x="20" y="145" width="960" height="125" rx="12" fill="${COLORS.blueLight}" stroke="${COLORS.blue}" stroke-width="2"/>
    <text x="42" y="174" class="svg-section">2 任务控制面</text>
    <rect x="55" y="193" width="205" height="52" rx="8" fill="#fff" stroke="${COLORS.blue}"/>
    <text x="157" y="215" class="svg-label" text-anchor="middle">TaskTreeKernel</text>
    <text x="157" y="233" class="svg-small" text-anchor="middle">解释生命周期、决定下一步</text>
    <rect x="293" y="193" width="205" height="52" rx="8" fill="#fff" stroke="${COLORS.purple}"/>
    <text x="395" y="215" class="svg-label" text-anchor="middle">TaskTreeStore</text>
    <text x="395" y="233" class="svg-small" text-anchor="middle">唯一权威树状态</text>
    <rect x="531" y="193" width="180" height="52" rx="8" fill="#fff" stroke="${COLORS.green}"/>
    <text x="621" y="215" class="svg-label" text-anchor="middle">Decomposer</text>
    <text x="621" y="233" class="svg-small" text-anchor="middle">只返回 GraphDelta</text>
    <rect x="744" y="193" width="190" height="52" rx="8" fill="#fff" stroke="${COLORS.teal}"/>
    <text x="839" y="215" class="svg-label" text-anchor="middle">System Operations</text>
    <text x="839" y="233" class="svg-small" text-anchor="middle">感知、规划、发布工件</text>
    <line x1="157" y1="130" x2="157" y2="190" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-arch)"/>
    <line x1="260" y1="219" x2="287" y2="219" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-arch)"/>
    <line x1="498" y1="219" x2="525" y2="219" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-arch)"/>
    <line x1="711" y1="219" x2="738" y2="219" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-arch)"/>

    <rect x="20" y="285" width="960" height="126" rx="12" fill="${COLORS.goldLight}" stroke="${COLORS.gold}" stroke-width="2"/>
    <text x="42" y="314" class="svg-section">3 能力与物理边界</text>
    <rect x="55" y="334" width="190" height="52" rx="8" fill="#fff" stroke="${COLORS.gold}"/>
    <text x="150" y="356" class="svg-label" text-anchor="middle">A* / IK / RRT</text>
    <text x="150" y="374" class="svg-small" text-anchor="middle">能力内部算法，不是节点</text>
    <rect x="280" y="334" width="190" height="52" rx="8" fill="#fff" stroke="${COLORS.blue}"/>
    <text x="375" y="356" class="svg-label" text-anchor="middle">Plan Artifacts</text>
    <text x="375" y="374" class="svg-small" text-anchor="middle">带快照与依赖版本</text>
    <rect x="505" y="334" width="190" height="52" rx="8" fill="#fff" stroke="${COLORS.teal}"/>
    <text x="600" y="356" class="svg-label" text-anchor="middle">Physical Skill</text>
    <text x="600" y="374" class="svg-small" text-anchor="middle">工件 → ActionRequest</text>
    <rect x="730" y="334" width="205" height="52" rx="8" fill="#fff" stroke="${COLORS.red}"/>
    <text x="832" y="356" class="svg-label" text-anchor="middle">HarnessRuntime</text>
    <text x="832" y="374" class="svg-small" text-anchor="middle">唯一物理执行入口</text>
    <line x1="375" y1="270" x2="375" y2="330" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-arch)"/>
    <line x1="470" y1="360" x2="498" y2="360" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-arch)"/>
    <line x1="695" y1="360" x2="723" y2="360" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-arch)"/>

    <rect x="20" y="426" width="960" height="145" rx="12" fill="${COLORS.greenLight}" stroke="${COLORS.green}" stroke-width="2"/>
    <text x="42" y="455" class="svg-section">4 世界、观察与修复闭环</text>
    <rect x="55" y="476" width="180" height="55" rx="8" fill="#fff" stroke="${COLORS.green}"/>
    <text x="145" y="498" class="svg-label" text-anchor="middle">RobotBackend</text>
    <text x="145" y="516" class="svg-small" text-anchor="middle">模拟器 / 真实机器人</text>
    <rect x="280" y="476" width="180" height="55" rx="8" fill="#fff" stroke="${COLORS.green}"/>
    <text x="370" y="498" class="svg-label" text-anchor="middle">Observation</text>
    <text x="370" y="516" class="svg-small" text-anchor="middle">每条命令后的事实</text>
    <rect x="505" y="476" width="180" height="55" rx="8" fill="#fff" stroke="${COLORS.green}"/>
    <text x="595" y="498" class="svg-label" text-anchor="middle">WorldModel</text>
    <text x="595" y="516" class="svg-small" text-anchor="middle">world:0 → world:7</text>
    <rect x="730" y="476" width="205" height="55" rx="8" fill="#fff" stroke="${COLORS.repair}"/>
    <text x="832" y="498" class="svg-label" text-anchor="middle">Verifier / Repair</text>
    <text x="832" y="516" class="svg-small" text-anchor="middle">证据 → Diagnostic → 修复</text>
    <line x1="832" y1="386" x2="832" y2="472" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-arch)"/>
    <line x1="235" y1="503" x2="273" y2="503" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-arch)"/>
    <line x1="460" y1="503" x2="498" y2="503" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-arch)"/>
    <line x1="685" y1="503" x2="723" y2="503" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-arch)"/>
    <path d="M730 548 C520 575 250 575 150 535" fill="none" stroke="${COLORS.repair}" stroke-width="3" stroke-dasharray="8 6" marker-end="url(#arr-repair)"/>
    <text x="450" y="565" class="svg-small repair-text" text-anchor="middle">失败时挂载 repair 子树，修好后回到原节点继续</text>
  </svg>`;
}

function analogySvg() {
  return `
  <svg class="diagram-svg" viewBox="0 0 980 390" role="img" aria-label="工厂类比图">
    <defs>
      <marker id="arr-analogy" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
        <path d="M0,0 L8,4 L0,8 Z" fill="${COLORS.ink}"></path>
      </marker>
    </defs>
    <rect x="30" y="70" width="170" height="160" rx="12" fill="${COLORS.coralLight}" stroke="${COLORS.coral}" stroke-width="2"/>
    <text x="115" y="103" class="svg-section" text-anchor="middle">客户</text>
    <text x="115" y="140" class="svg-label" text-anchor="middle">“把红杯放到</text>
    <text x="115" y="164" class="svg-label" text-anchor="middle">右边的区域”</text>
    <text x="115" y="204" class="svg-small" text-anchor="middle">只说结果</text>
    <line x1="205" y1="150" x2="264" y2="150" stroke="${COLORS.ink}" stroke-width="3" marker-end="url(#arr-analogy)"/>
    <rect x="270" y="42" width="185" height="216" rx="12" fill="${COLORS.blueLight}" stroke="${COLORS.blue}" stroke-width="2"/>
    <text x="362" y="78" class="svg-section" text-anchor="middle">项目经理</text>
    <text x="362" y="116" class="svg-label" text-anchor="middle">拆步骤</text>
    <text x="362" y="145" class="svg-label" text-anchor="middle">排顺序</text>
    <text x="362" y="174" class="svg-label" text-anchor="middle">检查结果</text>
    <text x="362" y="203" class="svg-label" text-anchor="middle">出问题就开修复单</text>
    <line x1="460" y1="150" x2="519" y2="150" stroke="${COLORS.ink}" stroke-width="3" marker-end="url(#arr-analogy)"/>
    <rect x="525" y="70" width="170" height="160" rx="12" fill="${COLORS.goldLight}" stroke="${COLORS.gold}" stroke-width="2"/>
    <text x="610" y="103" class="svg-section" text-anchor="middle">工艺工程师</text>
    <text x="610" y="140" class="svg-label" text-anchor="middle">算路线</text>
    <text x="610" y="169" class="svg-label" text-anchor="middle">算姿态</text>
    <text x="610" y="198" class="svg-label" text-anchor="middle">产出施工图</text>
    <line x1="700" y1="150" x2="759" y2="150" stroke="${COLORS.ink}" stroke-width="3" marker-end="url(#arr-analogy)"/>
    <rect x="765" y="42" width="185" height="216" rx="12" fill="${COLORS.greenLight}" stroke="${COLORS.green}" stroke-width="2"/>
    <text x="857" y="78" class="svg-section" text-anchor="middle">施工现场</text>
    <text x="857" y="116" class="svg-label" text-anchor="middle">锁门</text>
    <text x="857" y="145" class="svg-label" text-anchor="middle">施工</text>
    <text x="857" y="174" class="svg-label" text-anchor="middle">拍照取证</text>
    <text x="857" y="203" class="svg-label" text-anchor="middle">发现现场变了</text>
    <path d="M855 270 C700 345 300 345 360 260" fill="none" stroke="${COLORS.repair}" stroke-width="3" stroke-dasharray="8 6" marker-end="url(#arr-analogy)"/>
    <text x="605" y="327" class="svg-small repair-text" text-anchor="middle">发现问题：不是偷偷重来，而是记录原因并开修复任务</text>
  </svg>`;
}

function objectTriangleSvg() {
  return `
  <svg class="diagram-svg" viewBox="0 0 980 400" role="img" aria-label="三个核心对象关系图">
    <defs>
      <marker id="arr-tri" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
        <path d="M0,0 L8,4 L0,8 Z" fill="${COLORS.ink}"></path>
      </marker>
    </defs>
    <path d="M490 90 L180 305 L800 305 Z" fill="none" stroke="${COLORS.line}" stroke-width="3"/>
    <rect x="360" y="22" width="260" height="100" rx="12" fill="${COLORS.blueLight}" stroke="${COLORS.blue}" stroke-width="2"/>
    <text x="490" y="57" class="svg-section" text-anchor="middle">任务树 TaskTree</text>
    <text x="490" y="83" class="svg-label" text-anchor="middle">决定“下一步做什么”</text>
    <text x="490" y="104" class="svg-small" text-anchor="middle">顺序、状态、失败、修复</text>
    <rect x="45" y="270" width="270" height="100" rx="12" fill="${COLORS.greenLight}" stroke="${COLORS.green}" stroke-width="2"/>
    <text x="180" y="305" class="svg-section" text-anchor="middle">世界模型 WorldModel</text>
    <text x="180" y="331" class="svg-label" text-anchor="middle">回答“现场现在是什么样”</text>
    <text x="180" y="352" class="svg-small" text-anchor="middle">实体、障碍物、机器人状态、版本</text>
    <rect x="665" y="270" width="270" height="100" rx="12" fill="${COLORS.goldLight}" stroke="${COLORS.gold}" stroke-width="2"/>
    <text x="800" y="305" class="svg-section" text-anchor="middle">规划工件 Artifact</text>
    <text x="800" y="331" class="svg-label" text-anchor="middle">回答“按哪个版本怎么做”</text>
    <text x="800" y="352" class="svg-small" text-anchor="middle">来源快照、依赖、路线、姿态</text>
    <line x1="390" y1="118" x2="270" y2="262" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-tri)"/>
    <line x1="590" y1="118" x2="710" y2="262" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-tri)"/>
    <line x1="320" y1="317" x2="655" y2="317" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-tri)"/>
    <text x="326" y="301" class="svg-small" text-anchor="middle">读取事实</text>
    <text x="654" y="301" class="svg-small" text-anchor="middle">产生于某个快照</text>
    <text x="490" y="387" class="svg-small" text-anchor="middle">世界变了 → 旧工件可能过期 → 发布新工件，不偷偷改旧工件</text>
  </svg>`;
}

function lifecycleSvg() {
  return `
  <svg class="diagram-svg" viewBox="0 0 980 390" role="img" aria-label="节点生命周期图">
    <defs>
      <marker id="arr-life" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
        <path d="M0,0 L8,4 L0,8 Z" fill="${COLORS.ink}"></path>
      </marker>
    </defs>
    <rect x="35" y="142" width="120" height="68" rx="34" fill="${COLORS.light}" stroke="${COLORS.blue}" stroke-width="2"/>
    <text x="95" y="171" class="svg-label" text-anchor="middle">pending</text>
    <text x="95" y="190" class="svg-small" text-anchor="middle">尚未开始</text>
    <line x1="158" y1="176" x2="220" y2="176" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-life)"/>
    <rect x="225" y="130" width="140" height="92" rx="12" fill="${COLORS.blueLight}" stroke="${COLORS.blue}" stroke-width="2"/>
    <text x="295" y="165" class="svg-label" text-anchor="middle">enter</text>
    <text x="295" y="188" class="svg-small" text-anchor="middle">目标 / 前置条件</text>
    <text x="295" y="205" class="svg-small" text-anchor="middle">新鲜度检查</text>
    <line x1="368" y1="176" x2="430" y2="176" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-life)"/>
    <rect x="435" y="130" width="140" height="92" rx="12" fill="${COLORS.goldLight}" stroke="${COLORS.gold}" stroke-width="2"/>
    <text x="505" y="165" class="svg-label" text-anchor="middle">run / expand</text>
    <text x="505" y="188" class="svg-small" text-anchor="middle">执行叶子</text>
    <text x="505" y="205" class="svg-small" text-anchor="middle">或挂 child</text>
    <line x1="578" y1="176" x2="640" y2="176" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-life)"/>
    <rect x="645" y="130" width="140" height="92" rx="12" fill="${COLORS.greenLight}" stroke="${COLORS.green}" stroke-width="2"/>
    <text x="715" y="165" class="svg-label" text-anchor="middle">verify</text>
    <text x="715" y="188" class="svg-small" text-anchor="middle">读取快照证据</text>
    <text x="715" y="205" class="svg-small" text-anchor="middle">确认真实结果</text>
    <line x1="788" y1="176" x2="850" y2="176" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-life)"/>
    <rect x="855" y="142" width="95" height="68" rx="34" fill="${COLORS.greenLight}" stroke="${COLORS.green}" stroke-width="2"/>
    <text x="902" y="171" class="svg-label" text-anchor="middle">succeeded</text>
    <text x="902" y="190" class="svg-small" text-anchor="middle">完成</text>
    <path d="M715 236 C715 315 505 340 505 230" fill="none" stroke="${COLORS.repair}" stroke-width="3" stroke-dasharray="8 6" marker-end="url(#arr-life)"/>
    <text x="620" y="319" class="svg-small repair-text" text-anchor="middle">失败 → Diagnostic → repair 子树 → 重试或 blocked</text>
    <path d="M295 115 C295 50 715 50 715 115" fill="none" stroke="${COLORS.blue}" stroke-width="2" stroke-dasharray="5 5"/>
    <text x="505" y="44" class="svg-small" text-anchor="middle">每个节点都按同一套生命周期解释</text>
  </svg>`;
}

function sixGateSvg() {
  const gates = [
    ["1", "读快照", "计划来自哪个世界版本？", COLORS.blue],
    ["2", "查前置", "现在是否仍满足条件？", COLORS.teal],
    ["3", "锁资源", "机器人、夹爪是否被占用？", COLORS.gold],
    ["4", "做动作", "只通过 Runtime 进入后端", COLORS.coral],
    ["5", "收观察", "动作后真实状态是什么？", COLORS.green],
    ["6", "对账提交", "结果能否被证据确认？", COLORS.repair],
  ];
  return `
  <svg class="diagram-svg" viewBox="0 0 980 480" role="img" aria-label="物理动作六道检查">
    <defs>
      <marker id="arr-gate" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
        <path d="M0,0 L8,4 L0,8 Z" fill="${COLORS.ink}"></path>
      </marker>
    </defs>
    ${gates
      .map((g, i) => {
        const x = 45 + (i % 3) * 305;
        const y = 35 + Math.floor(i / 3) * 205;
        const cx = x + 38;
        const cy = y + 40;
        return `
          <rect x="${x}" y="${y}" width="270" height="130" rx="12" fill="#fff" stroke="${g[3]}" stroke-width="2"/>
          <circle cx="${cx}" cy="${cy}" r="27" fill="${g[3]}"/>
          <text x="${cx}" y="${cy + 7}" class="gate-number" text-anchor="middle">${g[0]}</text>
          <text x="${x + 80}" y="${y + 42}" class="svg-section">${g[1]}</text>
          <text x="${x + 80}" y="${y + 72}" class="svg-small">${g[2]}</text>
          <text x="${x + 80}" y="${y + 95}" class="svg-small">${i === 3 ? "失败会产生 Diagnostic" : i === 5 ? "成功才允许节点完成" : "不通过就停止推进"}</text>
        `;
      })
      .join("")}
    <line x1="315" y1="100" x2="345" y2="100" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-gate)"/>
    <line x1="620" y1="100" x2="650" y2="100" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-gate)"/>
    <path d="M802 165 L802 204 L192 204 L192 239" fill="none" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-gate)"/>
    <line x1="315" y1="305" x2="345" y2="305" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-gate)"/>
    <line x1="620" y1="305" x2="650" y2="305" stroke="${COLORS.ink}" stroke-width="2" marker-end="url(#arr-gate)"/>
  </svg>`;
}

function sceneSvg(blocked = false, after = false) {
  const crateX = blocked ? 555 : 555;
  const crateY = blocked ? 160 : 345;
  const route = blocked
    ? "M180 330 L340 330 L470 220 L620 220"
    : after
      ? "M180 330 L320 330 L460 410 L620 410 L780 290"
      : "M180 330 L320 330 L460 250 L620 250 L780 290";
  return `
  <svg class="diagram-svg scene-svg" viewBox="0 0 980 520" role="img" aria-label="${blocked ? "路线被箱子挡住的场景" : "正常放置场景"}">
    <defs>
      <marker id="arr-scene" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
        <path d="M0,0 L8,4 L0,8 Z" fill="${blocked ? COLORS.repair : COLORS.teal}"></path>
      </marker>
      <pattern id="grid-scene" width="42" height="42" patternUnits="userSpaceOnUse">
        <path d="M42 0 L0 0 0 42" fill="none" stroke="#e5ebe8" stroke-width="1"/>
      </pattern>
    </defs>
    <rect x="20" y="20" width="940" height="450" rx="14" fill="#fbfcfb" stroke="${COLORS.line}" stroke-width="2"/>
    <rect x="35" y="35" width="910" height="420" rx="8" fill="url(#grid-scene)"/>
    <rect x="425" y="35" width="42" height="126" fill="#5b666b"/>
    <rect x="425" y="245" width="42" height="210" fill="#5b666b"/>
    <text x="446" y="190" class="svg-small" text-anchor="middle">墙</text>
    <circle cx="180" cy="330" r="25" fill="${COLORS.blue}"/>
    <text x="180" y="337" class="gate-number" text-anchor="middle">R</text>
    <text x="180" y="380" class="svg-label" text-anchor="middle">机器人</text>
    <circle cx="315" cy="330" r="18" fill="${COLORS.coral}"/>
    <text x="315" y="336" class="gate-number" text-anchor="middle">杯</text>
    <text x="315" y="370" class="svg-small" text-anchor="middle">cup-red</text>
    <circle cx="780" cy="290" r="36" fill="${COLORS.greenLight}" stroke="${COLORS.green}" stroke-width="3"/>
    <text x="780" y="297" class="svg-label" text-anchor="middle">目标</text>
    <text x="780" y="347" class="svg-small" text-anchor="middle">drop-zone</text>
    ${
      blocked
        ? `<rect x="${crateX - 40}" y="${crateY - 32}" width="80" height="64" rx="7" fill="${COLORS.repairLight}" stroke="${COLORS.repair}" stroke-width="3"/>
           <text x="${crateX}" y="${crateY + 6}" class="svg-label" text-anchor="middle">箱子</text>
           <text x="${crateX}" y="${crateY + 78}" class="svg-small repair-text" text-anchor="middle">movable-crate</text>`
        : `<rect x="${crateX - 40}" y="${crateY - 32}" width="80" height="64" rx="7" fill="#eef1f0" stroke="#7b878b" stroke-width="2"/>
           <text x="${crateX}" y="${crateY + 6}" class="svg-label" text-anchor="middle">箱子</text>
           <text x="${crateX}" y="${crateY + 78}" class="svg-small" text-anchor="middle">其他物体</text>`
    }
    <path d="${route}" fill="none" stroke="${blocked ? COLORS.repair : COLORS.teal}" stroke-width="6" stroke-linecap="round" stroke-dasharray="12 8" marker-end="url(#arr-scene)"/>
    ${
      blocked
        ? `<path d="M505 220 L555 160" stroke="${COLORS.repair}" stroke-width="3" marker-end="url(#arr-scene)"/>
           <text x="610" y="92" class="svg-section repair-text" text-anchor="middle">A* 发现：这条路被具体箱子挡住</text>
           <text x="610" y="116" class="svg-small repair-text" text-anchor="middle">不是“规划器心情不好”，而是有可定位的证据</text>`
        : `<text x="610" y="92" class="svg-section" text-anchor="middle">路线可行</text>
           <text x="610" y="116" class="svg-small" text-anchor="middle">先规划，再拿起物体</text>`
    }
    ${
      after
        ? `<path d="M555 160 L555 400" stroke="${COLORS.repair}" stroke-width="4" stroke-dasharray="8 6" marker-end="url(#arr-scene)"/>
           <text x="650" y="430" class="svg-section repair-text" text-anchor="middle">箱子先被放入 parking-zone</text>`
        : ""
    }
  </svg>`;
}

function taskTreeHtml(blocked = false, compact = false) {
  const repairBranch = blocked
    ? `
      <div class="tree-node repair-indent">
        <span class="tree-branch repair">repair</span>
        <span class="tree-name repair-text">RouteBlockedRepair</span>
        <span class="tree-state state-success">succeeded</span>
      </div>
      <div class="tree-node repair-indent-2">
        <span class="tree-branch repair">└─</span>
        <span class="tree-name">Place(movable-crate → parking-zone)</span>
      </div>
      <div class="tree-node repair-indent-2">
        <span class="tree-branch repair">└─</span>
        <span class="tree-name">重新生成 PickPlan / TransferPlan</span>
      </div>
    `
    : "";
  const lines = [
    { label: "Place(cup-red → drop-zone)", type: "root", depth: 0, branch: "" },
    { label: "ResolveAndInspect", type: "system", depth: 0, branch: "├─" },
    { label: "PlanPick", type: "system", depth: 0, branch: "├─" },
    { label: "PlanTransfer", type: "system", depth: 0, branch: "├─" },
    { label: "Pick", type: "group", depth: 0, branch: "├─" },
    { label: "NavigateToPickStance", type: "physical", depth: 1, branch: "├─" },
    { label: "ExecuteGrasp", type: "physical", depth: 1, branch: "├─" },
    { label: "VerifyHeld", type: "verify", depth: 1, branch: "└─" },
    { label: "TransferHeld", type: "group", depth: 0, branch: "├─" },
    { label: "MoveToTransportPosture", type: "physical", depth: 1, branch: "├─" },
    { label: "NavigateHeld", type: "physical", depth: 1, branch: "├─" },
    { label: "VerifyPlacementReady", type: "verify", depth: 1, branch: "└─" },
    { label: "Release", type: "group", depth: 0, branch: "├─" },
    { label: "ExecuteRelease", type: "physical", depth: 1, branch: "├─" },
    { label: "VerifyReleased", type: "verify", depth: 1, branch: "└─" },
    { label: "VerifyPlaceGoal", type: "verify", depth: 0, branch: "└─" },
  ];
  return `
    <div class="task-tree ${compact ? "task-tree-compact" : ""}">
      ${lines
        .map(
          ({ label, type, depth, branch }, index) => `
          <div class="tree-line tree-${type}" data-depth="${depth}">
            <span class="tree-branch">${esc(branch)}</span>
            <span class="tree-text">${esc(label)}</span>
            <span class="tree-state state-success">${index === 0 || blocked ? (blocked && index === 2 ? "repaired" : "succeeded") : "succeeded"}</span>
          </div>
        `,
        )
        .join("")}
      ${repairBranch}
    </div>
  `;
}

function timelineHtml(items, tone = "teal") {
  return `
  <div class="timeline timeline-${tone}">
    ${items
      .map(
        (item, i) => `
        <div class="timeline-item">
          <div class="timeline-dot">${i + 1}</div>
          <div class="timeline-body">
            <div class="timeline-title">${item.title}</div>
            <div class="timeline-text">${item.text}</div>
            ${item.meta ? `<div class="timeline-meta">${item.meta}</div>` : ""}
          </div>
        </div>
      `,
      )
      .join("")}
  </div>`;
}

function page(title, subtitle, body, tone = "teal", kicker = "") {
  const n = pages.length + 1;
  pages.push(`
    <section class="page page-${tone}">
      <div class="page-header">
      <div class="page-kicker">${esc(kicker || `个人学习 · 第 ${pageNumber(n)} 页`)}</div>
        <div class="page-rule"></div>
      </div>
      <h1>${title}</h1>
      ${subtitle ? `<p class="page-subtitle">${subtitle}</p>` : ""}
      <div class="page-body">${body}</div>
      <div class="page-footer">
        <span>Task Recursive Tree Agent · 个人学习详解版</span>
        <span>${pageNumber(n)}</span>
      </div>
    </section>
  `);
}

const pages = [];

// 1. Cover
pages.push(`
  <section class="page page-cover">
    <div class="cover-top">
      <span class="cover-tag">Task Recursive Tree Agent</span>
      <span class="cover-date">参考实现讲解 · 2026-08-30</span>
    </div>
    <div class="cover-content">
      <div class="cover-copy">
        <div class="cover-eyebrow">给完全不了解 Agent 架构的人</div>
        <h1>把 Agent 框架<br><em>讲到人人都能看懂</em></h1>
        <p class="cover-lead">从一句“把红杯放好”，一路讲到任务树、规划工件、机器人动作、观察证据，以及失败后的递归修复。</p>
        <div class="cover-chips">
          ${pill("白话", "coral")}
          ${pill("结构图", "teal")}
          ${pill("真实案例", "gold")}
          ${pill("故障修复", "repair")}
        </div>
      </div>
      <div class="cover-visual">
        ${architectureSvg()}
      </div>
    </div>
    <div class="cover-bottom">
      <strong>先记住一句话：</strong> LLM 负责理解目标，Kernel 负责控制过程，Harness 负责承载真实世界，Repair 负责把失败重新变成工作。
    </div>
    <div class="page-footer">
      <span>给你自己学习和反复查阅</span>
      <span>01</span>
    </div>
  </section>
`);

// 2. Final answer first
page(
  "先建立一张心智地图：这套系统到底在做什么？",
  "先把下面这条主线记住，后面每个技术名词都会回到它。",
  `
  ${callout("一句人话", "它不是“让大模型直接控制机器人”，而是给 Agent 加了一层可检查、可追踪、可修复的执行控制面。", "coral")}
  <div class="four-steps">
    <div class="big-step"><b>1</b><strong>理解</strong><span>把人话变成明确目标</span></div>
    <div class="big-step"><b>2</b><strong>拆解</strong><span>把目标变成任务树</span></div>
    <div class="big-step"><b>3</b><strong>执行</strong><span>把计划变成受控动作</span></div>
    <div class="big-step"><b>4</b><strong>恢复</strong><span>发现失败就挂修复树</span></div>
  </div>
  <div class="split-box">
    <div>
      <h3>你先要建立的直觉</h3>
      ${bullets([
        "出了问题能说清楚：哪一步、为什么、影响什么。",
        "不是每次失败都人工从头开始，能局部修复。",
        "换算法、换机器人时，业务任务不用全部重写。",
        "连续任务能保持真实现场，不会每次都把世界重置。",
      ])}
    </div>
    <div>
      <h3>你随后要看懂的机制</h3>
      ${bullets([
        "<code>TaskTreeStore</code> 是唯一权威运行时树状态。",
        "<code>TaskTreeKernel</code> 是唯一节点生命周期解释器和 Store 写入者。",
        "LLM 只能提交规范化 <code>TaskProgram</code>，不能写树或调机器人。",
        "<code>HarnessRuntime</code> 是唯一物理执行入口，每条命令后都有 <code>Observation</code>。",
      ])}
    </div>
  </div>
  ${callout("读图方法", "后面每一页都回答一个具体问题：谁负责？看什么证据？失败后怎么办？不要把“模型会说话”误认为“系统已经可靠执行”。", "gold")}
  <div class="reading-route">
    <div><b>第一段</b><span>第 3–7 页：先用杯子任务建立直觉</span></div>
    <div><b>第二段</b><span>第 8–20 页：看清对象、分层、权限和生命周期</span></div>
    <div><b>第三段</b><span>第 21–26 页：完整走一遍正常任务</span></div>
    <div><b>第四段</b><span>第 27–32 页：完整走一遍故障与递归修复</span></div>
    <div><b>第五段</b><span>第 33–41 页：连续会话、安全、边界和总结；第 42 页以后继续看对象编码与运行时细节</span></div>
  </div>
  `,
  "coral",
  "先看结论",
);

// 3. What a task is
page(
  "先别怕术语：我们真正要解决的是什么？",
  "先用一个生活里最简单的动作，把所有复杂词汇压缩成一句话。",
  `
  <div class="example-hero">
    <div class="example-quote">“请把红色杯子放到右边的投放区。”</div>
    <div class="example-translation">这句话听起来只有一个动作，但机器人要面对一串现实问题。</div>
  </div>
  <div class="question-grid">
    ${nodeCard("对象是谁？", "识别", "红色杯子是哪个实体？现场可能有多个杯子，不能只凭名字猜。", "coral")}
    ${nodeCard("目标在哪里？", "绑定", "“右边”是哪个区域？需要一个可验证的目标位置和坐标。", "blue")}
    ${nodeCard("路线能走吗？", "规划", "中间有没有墙、箱子或其他障碍？拿起杯子后路线是否仍然成立？", "gold")}
    ${nodeCard("动作真的成功吗？", "验证", "命令发出不等于杯子被拿起，更不等于杯子最终已经放好。", "green")}
  </div>
  ${callout("核心转折", "人只说“结果”，系统必须把结果拆成一连串可检查的前置条件、动作和后置证据。", "teal")}
  `,
  "teal",
  "从一个杯子开始",
);

// 4. Direct black-box problem
page(
  "为什么不能让 LLM 直接调用机器人？",
  "直接调用很快能跑通 Demo，但在真实世界里，最危险的恰恰是“看起来跑通了”。",
  `
  <div class="compare">
    <div class="compare-col compare-bad">
      <div class="compare-title">黑盒方式</div>
      <div class="compare-flow">LLM → robot.move() → 返回 success</div>
      ${bullets([
        "模型既决定做什么，又决定怎么做，还可能直接触发副作用。",
        "失败只剩一句“没成功”，很难定位到底是路线、抓取还是验证出了问题。",
        "重试可能重复抓取、重复释放，造成二次副作用。",
        "过程没有统一的任务状态、快照、事务和审计边界。",
      ])}
    </div>
    <div class="compare-col compare-good">
      <div class="compare-title">控制面方式</div>
      <div class="compare-flow">意图 → 任务树 → 工件 → ActionRequest → Runtime → Observation</div>
      ${bullets([
        "每一步都有明确责任人和权限。",
        "每次动作都能关联到任务节点、计划版本和世界快照。",
        "失败先变成结构化 Diagnostic，再决定是否修复。",
        "模型不能越过 Runtime 直接碰后端。",
      ])}
    </div>
  </div>
  <div class="warning-line"><strong>关键区别：</strong> 黑盒关注“有没有返回值”；控制面关注“现场是否真的变了，而且变成了预期的样子”。</div>
  `,
  "coral",
  "为什么要有控制面",
);

// 5. Factory analogy
page(
  "用一座工厂来理解整套架构",
  "这是最容易记住的类比：用户是客户，LLM 是接单员，Kernel 是项目经理，规划器是工艺工程师，Runtime 是施工总闸门。",
  `
  ${analogySvg()}
  <div class="analogy-table">
    <div><b>客户</b><span>只说想要的结果：“杯子放好”。</span></div>
    <div><b>接单员 / LLM</b><span>理解人话，确认对象，填写标准订单。</span></div>
    <div><b>项目经理 / Kernel</b><span>安排顺序，记录状态，决定失败后下一步。</span></div>
    <div><b>工艺工程师 / A*、IK、RRT</b><span>计算路线、姿态和运动方案，但不偷偷搬东西。</span></div>
    <div><b>施工总闸门 / HarnessRuntime</b><span>检查现场、锁资源、执行动作、收集证据。</span></div>
  </div>
  ${callout("为什么这个类比重要？", "因为它把“会思考”和“有权执行”分开了。项目经理可以决定下一步，但不应该绕过安全闸门直接开机器。", "blue")}
  `,
  "blue",
  "先建立直觉",
);

// 6. From sentence to formal request
page(
  "一句人话，如何变成系统能执行的任务？",
  "系统不会把整句自然语言原样塞给机器人，而是先转换成一个受约束的任务意图。",
  `
  <div class="conversion-row">
    <div class="conversion-input"><small>人话</small><strong>把红杯放到投放区</strong><span>含义模糊、没有步骤</span></div>
    ${arrowLabel("理解", "coral")}
    <div class="conversion-card"><small>TaskProgram</small><strong>Place</strong><span>object = cup-red<br>destination = drop-zone</span></div>
    ${arrowLabel("编译", "blue")}
    <div class="conversion-card"><small>TaskTreeDefinition</small><strong>根节点 Place</strong><span>类型、顺序、约束、策略</span></div>
    ${arrowLabel("执行", "teal")}
    <div class="conversion-card"><small>Runtime</small><strong>受控动作</strong><span>ActionRequest + Observation</span></div>
  </div>
  <div class="mini-table">
    <div class="mini-row mini-head"><span>人说的</span><span>系统必须明确的</span><span>为什么</span></div>
    <div class="mini-row"><span>红杯</span><span>实体 ID：<code>cup-red</code></span><span>防止抓错对象</span></div>
    <div class="mini-row"><span>投放区</span><span>区域 ID：<code>drop-zone</code></span><span>防止目标漂移</span></div>
    <div class="mini-row"><span>放好</span><span>位置误差、释放状态、最终验证</span><span>防止“命令成功但结果不对”</span></div>
  </div>
  ${callout("不要把它理解成形式主义", "这些字段的作用是把模糊的人话变成可以检查、可以追责、可以重试的对象。", "gold")}
  `,
  "blue",
  "从人话到任务",
);

// 7. Place task
page(
  "最小案例：Place 到底包含多少事情？",
  "用户只说一个“放置”，系统内部至少要回答五类问题。",
  `
  <div class="place-lanes">
    <div class="lane lane-coral"><b>目标</b><span>把 cup-red 放到 drop-zone</span></div>
    <div class="lane lane-blue"><b>准备</b><span>找到对象、确认目标、生成抓取计划</span></div>
    <div class="lane lane-gold"><b>移动</b><span>先到抓取位置，再拿起，再带着物体走</span></div>
    <div class="lane lane-green"><b>完成</b><span>释放、确认已松开、确认最终位置正确</span></div>
    <div class="lane lane-repair"><b>异常</b><span>路线被挡时，先修复阻碍，再回到原任务</span></div>
  </div>
  <div class="formula">
    <span>Place</span><b>=</b><span>理解目标</span><b>+</b><span>准备计划</span><b>+</b><span>拿起</span><b>+</b><span>携带</span><b>+</b><span>释放</span><b>+</b><span>验证</span>
  </div>
  <p class="plain-paragraph">注意：这里的“+”不是数学加法，而是说明一个大目标由多个有先后关系的小工作组成。每个小工作都能独立记录成功或失败。</p>
  ${callout("提前规划的原因", "系统会在还没有拿起杯子时，先检查“带着杯子能不能走到目标”。如果这时就发现路不通，修复会更安全，因为夹爪还是空的。", "teal")}
  `,
  "gold",
  "最简单的任务",
);

// 8. Three objects overview
page(
  "整套架构只有三个核心对象要先记住",
  "把这三样东西分开，后面的所有设计就不容易混乱。",
  `
  ${objectTriangleSvg()}
  <div class="object-cards">
    ${nodeCard("任务树", "决定下一步", "记录步骤、顺序、状态、失败原因和修复分支。它不等于现场，也不等于路线本身。", "blue")}
    ${nodeCard("世界模型", "记录现在", "记录机器人、物体、障碍物、区域和版本。它只接受观察和世界更新。", "green")}
    ${nodeCard("规划工件", "记录怎么做", "基于某个世界快照生成的路线、抓取姿态或转移方案。工件不可变，过期就发布新版本。", "gold")}
  </div>
  ${callout("最简单的记忆口诀", "树管顺序，世界管事实，工件管方案。", "coral")}
  `,
  "teal",
  "三件最重要的东西",
);

// 9. Task tree
page(
  "什么是任务树？把它当成“会执行的待办清单”",
  "普通待办清单只写要做什么；任务树还记录顺序、状态、证据和失败后的分支。",
  `
  <div class="tree-analogy">
    <div class="todo-list">
      <div class="todo-title">普通待办清单</div>
      <div>□ 找杯子</div><div>□ 走过去</div><div>□ 拿起来</div><div>□ 放下</div>
    </div>
    <div class="tree-arrow">升级为 →</div>
    <div class="tree-list">
      <div class="todo-title">任务树</div>
      <div>✓ 找杯子 <small>证据：cup-red</small></div>
      <div>✓ 规划路线 <small>工件：nav-v1</small></div>
      <div>… 拿起来 <small>正在执行</small></div>
      <div>↳ 失败时挂 repair 子树</div>
    </div>
  </div>
  <div class="definition-grid">
    <div><b>节点</b><span>一个可以被解释、执行或展开的工作。</span></div>
    <div><b>child 边</b><span>正常的子任务关系，表示“这个步骤属于上一级”。</span></div>
    <div><b>repair 边</b><span>失败后挂载的修复关系，表示“先处理这个障碍”。</span></div>
    <div><b>状态</b><span>pending、running、succeeded、failed、blocked 等运行状态。</span></div>
  </div>
  `,
  "blue",
  "任务树",
);

// 10. Tree vs log
page(
  "任务树不是日志，也不是一大坨 JSON",
  "这三个东西各有职责，混在一起以后，谁拥有状态、哪个计划过期、失败在哪里都会变得不清楚。",
  `
  <div class="three-column">
    <div class="concept-box concept-blue"><h3>任务树</h3><strong>结构</strong><p>“应该按什么顺序做？现在做到哪一步？”</p><small>由 Kernel 解释，由 Store 保存。</small></div>
    <div class="concept-box concept-green"><h3>世界快照</h3><strong>事实</strong><p>“机器人和物体此刻在哪里？现场版本是多少？”</p><small>由 Observation 推进。</small></div>
    <div class="concept-box concept-gold"><h3>规划工件</h3><strong>方案</strong><p>“在 world:0 的条件下，路线和姿态怎么走？”</p><small>不可变，按依赖判断新鲜度。</small></div>
  </div>
  <div class="wrong-model">
    <div class="wrong-label">错误想象</div>
    <div class="wrong-json">{ intent, current_state, plan, result, error, retry }</div>
    <p>所有东西挤在一个对象里，看似省事，实际上无法准确判断“计划为什么失效”。</p>
  </div>
  ${callout("正确的分工", "任务树可以继续存在，世界快照可以继续推进，旧工件可以保留作审计；三者不需要互相覆盖。", "teal")}
  `,
  "purple",
  "避免概念混淆",
);

// 11. World model
page(
  "什么是 WorldModel？它就是系统的“现场账本”",
  "机器人不是生活在模型的想象里，而是生活在一个会变化的物理世界里。",
  `
  <div class="world-board">
    <div class="world-title">world:7 · 当前世界快照</div>
    <div class="world-items">
      <div><b>机器人</b><span>base=(6.5, 4.5)，夹爪已打开，未持物</span></div>
      <div><b>cup-red</b><span>位于 drop-zone 中，实体版本 4</span></div>
      <div><b>drop-zone</b><span>目标区域，允许误差 0.3</span></div>
      <div><b>map</b><span>障碍占用版本已记录</span></div>
    </div>
  </div>
  <div class="world-version">
    <div class="version-node"><strong>world:0</strong><span>杯子在起点<br>机器人未持物</span></div>
    <div class="version-line"><i>每次观察推动版本</i>→</div>
    <div class="version-node version-current"><strong>world:7</strong><span>杯子已到目标区<br>机器人已释放</span></div>
  </div>
  <p class="plain-paragraph">“世界版本”不是为了让数字好看，而是为了回答：这份计划是根据哪一刻的现场算出来的？在它之后现场有没有发生过会影响计划的变化？</p>
  ${callout("最重要的边界", "规划器和 Verifier 读取 WorldSnapshot；它们不能直接读取机器人后端的私有状态，更不能绕过观察把“猜测”写成事实。", "green")}
  `,
  "green",
  "世界模型",
);

// 12. Artifact
page(
  "什么是 Artifact？把它当成“带版本的施工图”",
  "规划算法算出来的不是一句“我觉得能走”，而是一份带来源、依赖和使用条件的不可变工件。",
  `
  <div class="artifact-paper">
    <div class="artifact-head"><span>TransferPlan</span>${pill("不可变", "gold")}</div>
    <div class="artifact-row"><b>来源快照</b><code>world:0</code></div>
    <div class="artifact-row"><b>依赖版本</b><span>drop-zone=0 · map=0 · robot=0</span></div>
    <div class="artifact-row"><b>路线内容</b><span>起点姿态 → 中间路径点 → 目标姿态</span></div>
    <div class="artifact-row"><b>安全信息</b><span>占用指纹、机器人模型、载荷约束、校验策略</span></div>
    <div class="artifact-row"><b>消费者</b><span>TransferHeld physical skill</span></div>
  </div>
  <div class="artifact-analogy">
    <div class="stamp">v1</div><div><strong>旧施工图不能被偷偷涂改</strong><p>如果现场变了，就保留 v1，重新生成 v2，并记录为什么换图。</p></div>
  </div>
  <div class="artifact-rules">
    <div><b>保留旧工件</b><span>便于审计、回放和解释。</span></div>
    <div><b>发布新工件</b><span>让后续动作使用与当前现场匹配的方案。</span></div>
    <div><b>按依赖判断新鲜度</b><span>不是随意设置一个“过期”布尔值。</span></div>
  </div>
  `,
  "gold",
  "规划工件",
);

// 13. Three connect
page(
  "三个核心对象如何连起来？",
  "系统不是先凭空写一棵大树，再把它强行执行；而是让树、世界和工件互相校验。",
  `
  <div class="connection-flow">
    <div class="connection-block connection-blue"><b>任务树</b><span>我要做 Place</span></div>
    <div class="connection-arrow">读取 →</div>
    <div class="connection-block connection-green"><b>世界模型</b><span>杯子在这里，路况是这样</span></div>
    <div class="connection-arrow">规划 →</div>
    <div class="connection-block connection-gold"><b>工件</b><span>在这个快照下按这条路线走</span></div>
    <div class="connection-arrow">执行 →</div>
    <div class="connection-block connection-coral"><b>Observation</b><span>动作后现场变成这样</span></div>
  </div>
  <div class="loop-card">
    <div class="loop-title">闭环不是“一次规划到底”</div>
    <div class="loop-step">树提出下一步</div><span>→</span><div class="loop-step">世界提供事实</div><span>→</span><div class="loop-step">工件提供方案</div><span>→</span><div class="loop-step">观察更新世界</div><span>→</span><div class="loop-step">树决定继续 / 修复</div>
  </div>
  ${callout("用一句话说", "树决定“做什么”，工件说明“怎么做”，观察证明“做完后真的发生了什么”。", "coral")}
  `,
  "teal",
  "三者协同",
);

// 14. Full architecture
page(
  "全局架构图：从输入一直走到物理世界",
  "下面这张图是整套系统的“地图”。先看四层，再看箭头，不要一上来背模块名。",
  `
  ${architectureSvg()}
  <div class="legend-row">
    <span><i class="legend-dot dot-coral"></i>入口与意图</span>
    <span><i class="legend-dot dot-blue"></i>任务控制面</span>
    <span><i class="legend-dot dot-gold"></i>规划与动作边界</span>
    <span><i class="legend-dot dot-green"></i>世界、观察与修复</span>
  </div>
  ${callout("看箭头的顺序", "实线是正常推进：输入 → 编译 → Kernel → 工件 → Runtime → 机器人；虚线是反馈：Observation → WorldModel → 验证 / 修复 → 回到 Kernel。", "blue")}
  `,
  "blue",
  "总架构地图",
);

// 15. Input and session
page(
  "入口层负责“收任务”，不负责“偷偷做动作”",
  "浏览器、语音、HTTP 和连续会话都只是入口适配器，它们不拥有运行时树的写权限。",
  `
  <div class="input-pipeline">
    <div class="input-box"><b>用户</b><span>语音 / 文本 / 暂停 / 继续</span></div>
    <div class="pipeline-arrow">→</div>
    <div class="input-box"><b>Browser Console</b><span>提交任务、查看状态</span></div>
    <div class="pipeline-arrow">→</div>
    <div class="input-box"><b>ContinuousTaskSession</b><span>串行接收，一次一个任务</span></div>
    <div class="pipeline-arrow">→</div>
    <div class="input-box"><b>TaskProgram</b><span>规范化意图</span></div>
  </div>
  <div class="boundary-box">
    <div class="boundary-yes"><h3>入口可以做</h3>${bullets(["提交对象和目标标识。", "读取世界、树、事件和历史。", "请求完整场景重置。", "显示任务进度和最终结果。"])}</div>
    <div class="boundary-no"><h3>入口不能做</h3>${bullets(["直接修改运行时节点。", "直接挂载 repair 子树。", "直接调用规划器或机器人后端。", "把多个根目标硬塞进同一棵树。"])}</div>
  </div>
  ${callout("当前实现的真实边界", "连续会话目前是串行执行：每个任务有自己的 Store / Kernel / 树，但共享同一个 WorldModel、Runtime、事务账本和机器人后端。", "gold")}
  `,
  "coral",
  "入口与会话",
);

// 16. LLM role
page(
  "LLM 能做什么，不能做什么？",
  "这不是削弱模型，而是把模型放在它最擅长、也最容易被约束的位置。",
  `
  <div class="llm-split">
    <div class="llm-can"><div class="llm-heading">LLM 可以做</div>${bullets(["理解自然语言目标。", "向用户追问缺失信息。", "把对象和目标绑定成明确标识。", "提交规范化的 <code>TaskProgram</code>。", "提出高层任务意图。"])}</div>
    <div class="llm-cannot"><div class="llm-heading">LLM 不可以做</div>${bullets(["直接修改 <code>TaskTreeStore</code>。", "直接调用 <code>RobotBackend</code>。", "伪造 <code>Observation</code> 或验证结果。", "跳过 Runtime 的安全、租约和事务。", "把内部算法迭代伪装成业务节点。"])}</div>
  </div>
  <div class="permission-analogy">
    <span class="lock-icon">权限</span>
    <strong>模型提交“申请单”，系统决定“能不能执行”。</strong>
  </div>
  <p class="plain-paragraph">如果模型给出了一个不合法任务，系统应该拒绝或要求澄清，而不是因为“模型很聪明”就放宽边界。可靠性来自约束，不来自祈祷。</p>
  `,
  "purple",
  "模型边界",
);

// 17. Compiler and GraphDelta
page(
  "编译器和 GraphDelta：为什么不允许随便改树？",
  "分解器不是拿到一把“改数据库”的钥匙，而是提交一份受约束的结构增量。",
  `
  <div class="delta-flow">
    <div class="delta-box"><b>Decomposer</b><span>发现 Place 需要三个子任务</span></div>
    <div class="delta-arrow">提交</div>
    <div class="delta-box delta-code-box"><b>GraphDelta</b><span>新增哪些节点<br>每条边是什么类型<br>挂在哪个父节点下面</span></div>
    <div class="delta-arrow">校验</div>
    <div class="delta-box"><b>Kernel</b><span>检查范围、边类型、可达性、无环性</span></div>
    <div class="delta-arrow">原子写入</div>
    <div class="delta-box"><b>Store</b><span>真正更新运行时树</span></div>
  </div>
  <div class="rules-grid">
    <div>只能把 child 挂在当前展开节点下面。</div>
    <div>repair 必须恰好从失败节点挂一条 repair 边。</div>
    <div>新节点不能跑出声明的子树范围。</div>
    <div>完整图必须无环、可达、可解释。</div>
  </div>
  ${callout("为什么叫 Delta？", "因为它只描述“增加什么结构”，而不是让外部组件直接改掉整棵树。这样 Kernel 仍然是唯一的解释者和写入者。", "teal")}
  `,
  "blue",
  "编译与增量",
);

// 18. Kernel and Store
page(
  "Kernel 与 Store：一个负责解释，一个负责保存",
  "这是整个架构最重要的所有权边界。谁能写状态，决定了系统能不能被控制。",
  `
  <div class="ownership-visual">
    <div class="owner-box owner-kernel"><div class="owner-badge">唯一写入者</div><h3>TaskTreeKernel</h3><p>解释节点生命周期<br>决定展开 / 执行 / 验证 / 修复</p></div>
    <div class="ownership-line"><span>不可伪造 writer capability</span>→</div>
    <div class="owner-box owner-store"><div class="owner-badge">唯一权威状态</div><h3>TaskTreeStore</h3><p>保存节点规格、运行态、边、执行栈、事件日志</p></div>
  </div>
  <div class="reader-row">
    <span>Inspector</span><span>Web Console</span><span>Verifier</span><span>审计 / 导出</span>
    <small>都可以读，但都不能绕过 Kernel 写树</small>
  </div>
  <div class="anti-pattern">
    <b>如果每个模块都能写树，会发生什么？</b>
    <span>一个模块标记 succeeded，另一个模块又改成 failed；修复分支没有父节点；执行记录和实际状态互相打架。</span>
  </div>
  `,
  "blue",
  "所有权边界",
);

// 19. Node taxonomy
page(
  "三类节点：分解、系统、物理，各自只能拿到一部分权限",
  "节点类型不是名字游戏，而是“它能看到什么、能改变什么”的运行时契约。",
  `
  <div class="role-cards">
    <div class="role-card role-decomposer"><div class="role-icon">D</div><h3>Decomposer</h3><p>把一个复合任务拆成 child 节点。</p><strong>能做：</strong><span>返回 GraphDelta</span><strong>不能做：</strong><span>调机器人、直接写 Store</span></div>
    <div class="role-card role-system"><div class="role-icon">S</div><h3>System Operation</h3><p>读世界、做选择、做规划、发布工件、做验证。</p><strong>能做：</strong><span>读 WorldSnapshot、调用能力</span><strong>不能做：</strong><span>直接触碰 Backend</span></div>
    <div class="role-card role-physical"><div class="role-icon">P</div><h3>Physical Skill</h3><p>把已经批准的工件翻译成动作请求。</p><strong>能做：</strong><span>读声明的 Artifact</span><strong>不能做：</strong><span>绕过 Runtime 执行</span></div>
  </div>
  <div class="algorithm-note"><b>A*、IK、RRT 放在哪里？</b><span>它们是能力内部的算法。算法迭代次数不应该变成任务树节点，否则树会记录大量没有业务意义的内部细节。</span></div>
  `,
  "gold",
  "角色与权限",
);

// 20. Lifecycle
page(
  "一个节点从 pending 到 succeeded，究竟经历了什么？",
  "所有节点都经过同一个 Kernel 生命周期。复合节点可以展开，叶子节点可以执行，但最后都要验证。",
  `
  ${lifecycleSvg()}
  <div class="lifecycle-list">
    <div><b>进入</b><span>读取目标和当前状态，检查是否已经满足。</span></div>
    <div><b>前置</b><span>确认工件新鲜、条件成立、资源可用。</span></div>
    <div><b>执行 / 展开</b><span>叶子做工作，复合节点返回 GraphDelta。</span></div>
    <div><b>后置</b><span>根据不可变快照和证据判断结果。</span></div>
    <div><b>失败</b><span>生成 Diagnostic，可能挂 repair，也可能进入 blocked。</span></div>
  </div>
  ${callout("一个容易误解的细节", "某个目标已经满足时，叶子节点可以跳过动作；但结构节点不能因为目标满足就跳过必要的展开，否则树形结构和后置验证会失去意义。", "repair")}
  `,
  "teal",
  "节点生命周期",
);

// 21. Normal tree
page(
  "案例一：正常放置任务的完整任务递归树",
  "这是“没有故障”的参考执行。树里记录的是业务步骤，不是 A* 或控制器的每一轮计算。",
  `
  <div class="tree-metrics">
    ${stat("最终节点数", "16", "blue", "1 个根 + 15 条 child 边对应的节点关系")}
    ${stat("child 边", "15", "teal", "正常分解关系")}
    ${stat("repair 边", "0", "green", "本次没有故障修复")}
    ${stat("物理事务", "5", "gold", "5 个物理节点")}
    ${stat("事件数", "127", "purple", "可回放的执行证据")}
  </div>
  ${taskTreeHtml(false)}
  ${callout("读这棵树", "先看根节点 Place，再按从上到下的顺序读。缩进表示子任务；绿色 succeeded 表示后置验证已经通过，而不只是“函数返回了”。", "blue")}
  `,
  "blue",
  "案例一 · 树形结构",
);

// 22. Normal steps
page(
  "案例一：正常任务按步骤走一遍",
  "现在不要看代码，只把它当成一个现场工作人员的操作流程。",
  `
  ${timelineHtml([
    { title: "ResolveAndInspect", text: "确认 cup-red 和 drop-zone 确实存在，建立对象绑定。", meta: "产出 BindingArtifact" },
    { title: "PlanPick", text: "规划机器人如何到杯子旁边，以及如何抓取。", meta: "产出 NavigationPlan + PickPlan" },
    { title: "PlanTransfer", text: "在还没拿杯子时，先规划带着杯子去目标区的路线。", meta: "产出 TransferPlan" },
    { title: "Pick", text: "到抓取位置 → 执行抓取 → 观察确认杯子确实被夹住。", meta: "3 个子节点" },
    { title: "TransferHeld", text: "切换携物姿态 → 带着杯子走 → 确认可以释放。", meta: "3 个子节点" },
    { title: "Release", text: "执行释放 → 观察确认夹爪松开且杯子不再被持有。", meta: "2 个子节点" },
    { title: "VerifyPlaceGoal", text: "检查杯子最终位置落在 drop-zone 接受范围内。", meta: "最终结论由证据得出" },
  ])}
  `,
  "teal",
  "案例一 · 执行顺序",
);

// 23. Normal timeline metrics
page(
  "案例一：为什么一次成功不是“一次 API 调用成功”？",
  "参考数据里，一次正常 Place 任务有 5 次物理事务、127 个事件，并把世界从 world:0 推进到 world:7。",
  `
  <div class="world-strip">
    <div class="world-chip"><b>world:0</b><span>起始现场</span></div>
    <div class="world-events">
      <div class="event-block"><b>T1</b><span>NavigateToPickStance</span><small>观察后 world:1</small></div>
      <div class="event-block"><b>T2</b><span>ExecuteGrasp</span><small>观察后 world:2</small></div>
      <div class="event-block"><b>T3</b><span>MoveToTransportPosture</span><small>观察后 world:3</small></div>
      <div class="event-block"><b>T4</b><span>NavigateHeld</span><small>观察后 world:4</small></div>
      <div class="event-block"><b>T5</b><span>ExecuteRelease</span><small>观察后 world:7</small></div>
    </div>
    <div class="world-chip world-chip-success"><b>world:7</b><span>目标已满足</span></div>
  </div>
  <div class="metric-explain">
    ${stat("127", "事件", "purple", "包括进栈、状态变化、GraphDelta、出栈等")}
    ${stat("5", "物理事务", "gold", "每个事务都有请求 ID、检查、执行、观察")}
    ${stat("16", "节点", "blue", "业务可读、可定位、可回放")}
  </div>
  ${callout("为什么事件比物理事务多？", "因为一个物理动作前后还要记录节点进入、状态变化、执行栈和观察处理。多出的记录不是噪音，而是为了知道“系统是怎样走到这个结果的”。", "purple")}
  `,
  "purple",
  "案例一 · 真实执行数据",
);

// 24. Six gates
page(
  "一个物理动作，要过六道门才算完成",
  "这六道门把“计划”变成“受控副作用”。任何一门不通过，都不能假装动作已经发生。",
  `
  ${sixGateSvg()}
  <div class="gate-bottom">物理技能只负责构造 <code>ActionRequest</code>；真正的检查、租约、事务、执行、观察和对账由 <code>HarnessRuntime</code> 统一完成。</div>
  `,
  "gold",
  "物理边界",
);

// 25. Observation and verifier
page(
  "为什么每条命令后都必须有 Observation？",
  "因为命令是“请求”，观察才是“事实”。系统不能把两者混为一谈。",
  `
  <div class="request-observation">
    <div class="request-card"><div class="ro-label">ActionRequest</div><strong>请执行抓取</strong><span>它只说明：系统请求后端做什么。</span><b>不代表：</b><small>杯子一定被夹住。</small></div>
    <div class="ro-arrow">执行 →</div>
    <div class="observation-card"><div class="ro-label">Observation</div><strong>夹爪闭合，cup-red 仍在目标相对位置</strong><span>它说明：动作后观察到了什么。</span><b>用于：</b><small>更新 WorldModel、运行 Verifier。</small></div>
  </div>
  <div class="verification-chain">
    <span>Observation</span><b>→</b><span>WorldModel.ingest</span><b>→</b><span>不可变 WorldSnapshot</span><b>→</b><span>Verifier.evaluate</span><b>→</b><span>成功 / Diagnostic</span>
  </div>
  <div class="diagnostic-grid">
    <div><b>HOLD_LOST</b><span>观察到物体不再被持有。</span></div>
    <div><b>OUTCOME_UNKNOWN</b><span>没有足够证据确认后置条件。</span></div>
    <div><b>GUARD_FAILED</b><span>动作前置条件已经不成立。</span></div>
  </div>
  `,
  "green",
  "证据与验证",
);

// 26. Freshness
page(
  "为什么计划会过期？因为世界会变",
  "同一条路线在 world:0 可行，不代表到了 world:7 还应该照着执行。",
  `
  <div class="freshness-flow">
    <div class="freshness-state"><b>world:0</b><span>生成 nav-v1<br>map=0, robot=0</span><i>新鲜</i></div>
    <div class="freshness-arrow">现场变化 →</div>
    <div class="freshness-state freshness-stale"><b>world:1</b><span>箱子移动 / 机器人姿态变了<br>依赖版本不一致</span><i>过期</i></div>
    <div class="freshness-arrow">重新规划 →</div>
    <div class="freshness-state freshness-new"><b>world:1</b><span>生成 nav-v2<br>使用新依赖</span><i>可消费</i></div>
  </div>
  <div class="dependency-table">
    <div class="dep-row dep-head"><span>工件记录</span><span>当前世界比较什么</span><span>不一致意味着</span></div>
    <div class="dep-row"><span>source snapshot</span><span>快照引用</span><span>不能把旧现场当新现场</span></div>
    <div class="dep-row"><span>entity / map version</span><span>实体和地图版本</span><span>障碍或目标可能变化</span></div>
    <div class="dep-row"><span>robot state epoch</span><span>机器人状态纪元</span><span>轨迹起点可能不再连续</span></div>
    <div class="dep-row"><span>model / policy fingerprint</span><span>模型与安全策略</span><span>原计划不满足当前规则</span></div>
  </div>
  ${callout("不可变的意义", "旧工件仍然可以被审计；新工件负责继续执行。系统不是把历史擦掉，而是把“为什么换计划”留下来。", "gold")}
  `,
  "gold",
  "工件新鲜度",
);

// 27. Blocked scene
page(
  "案例二：路线被箱子挡住了",
  "故障不是“机器人突然不聪明”，而是规划器发现了一个可以描述、可以验证的现实条件。",
  `
  ${sceneSvg(true)}
  <div class="blocked-facts">
    <div><b>故障类型</b><span><code>ROUTE_BLOCKED</code></span></div>
    <div><b>阻碍物</b><span><code>movable-crate</code>，可移动，且被 A* 识别为具体 witness</span></div>
    <div><b>修复前提</b><span>移走这个箱子后，反事实 A* 能证明路线恢复；并且存在可达 parking-zone。</span></div>
    <div><b>当前状态</b><span>还没有拿起杯子，夹爪是空的，因此可以安全先处理箱子。</span></div>
  </div>
  `,
  "repair",
  "案例二 · 故障现场",
);

// 28. Why not retry
page(
  "为什么不能简单地“再试一次”？",
  "如果现场条件没有改变，重试只会重复同一个失败；如果现场已经部分改变，盲目重试还可能造成二次副作用。",
  `
  <div class="retry-matrix">
    <div class="retry-row retry-head"><span>做法</span><span>表面上发生什么</span><span>实际风险</span></div>
    <div class="retry-row"><span>原计划再跑一次</span><span>继续撞同一条被挡的路线</span><span>没有改变失败原因</span></div>
    <div class="retry-row"><span>让 A* 偷偷搬箱子</span><span>路线算法内部产生物理副作用</span><span>任务树看不到搬箱子，无法审计和验证</span></div>
    <div class="retry-row"><span>让模型自由发挥</span><span>可能临时调用未授权动作</span><span>越过安全边界，难以回放</span></div>
    <div class="retry-row retry-good"><span>结构化修复</span><span>先挂 repair 子树搬箱子，再刷新依赖工件</span><span>原因、动作、结果都可追踪</span></div>
  </div>
  <div class="principle-banner"><strong>规则：</strong>算法可以证明“移动某障碍后会有路”，但不能在算法内部偷偷移动障碍。物理副作用必须成为任务树里显式的工作。</div>
  ${callout("更直白地说", "如果系统搬了东西，却没有在任务树里留下“谁搬的、为什么搬、搬到哪里、是否验证成功”，那它就无法可靠地解释和恢复。", "repair")}
  `,
  "repair",
  "案例二 · 为什么不盲目重试",
);

// 29. Diagnostic and repair proposal
page(
  "故障如何从一句报错，变成一份可执行的修复方案？",
  "Repair 不是一个隐藏的 catch 分支，而是一个结构化闭环。",
  `
  <div class="repair-chain">
    <div class="repair-step"><b>1</b><strong>Diagnostic</strong><span>ROUTE_BLOCKED<br>包含失败节点、快照、原因证据</span></div>
    <div class="repair-connector">→</div>
    <div class="repair-step"><b>2</b><strong>RepairResolver</strong><span>检查阻碍物、停车区、递归深度和资源条件</span></div>
    <div class="repair-connector">→</div>
    <div class="repair-step"><b>3</b><strong>RepairProposal</strong><span>返回 GraphDelta、入口节点、理由、需要失效的工件</span></div>
    <div class="repair-connector">→</div>
    <div class="repair-step"><b>4</b><strong>Kernel</strong><span>校验并原子挂载 repair 边，然后执行</span></div>
  </div>
  <div class="repair-contract">
    <div><b>Resolver 能做</b>${bullets(["读世界和工件。", "分析故障原因。", "提出修复子树。", "说明理由和影响范围。"])}</div>
    <div><b>Resolver 不能做</b>${bullets(["直接改 Store。", "直接执行机器人动作。", "自己宣布修复成功。", "绕过 Kernel 挂边。"])}</div>
  </div>
  ${callout("成功由谁判断？", "不是 RepairResolver 说“我修好了”，而是 repair 子树执行后，由新的 Observation 和 Verifier 判断修复是否真的成功。", "green")}
  `,
  "repair",
  "案例二 · 故障结构化",
);

// 30. Repair tree
page(
  "案例二：递归修复树到底长什么样？",
  "修复任务不是漂浮在主流程外面的脚本，而是从失败节点下面显式挂出的一棵子树。",
  `
  <div class="repair-tree-visual">
    <div class="repair-root">PlanTransfer <span>失败：ROUTE_BLOCKED</span></div>
    <div class="repair-line"></div>
    <div class="repair-branch-box">
      <div class="repair-entry">repair 边 → RouteBlockedRepair</div>
      <div class="repair-child-grid">
        <div><b>Place(movable-crate → parking-zone)</b><span>这本身又是一个完整 Place 任务</span></div>
        <div><b>replan-pick</b><span>箱子位置改变后，重新生成抓取相关工件</span></div>
        <div><b>replan-transfer</b><span>按新的世界和依赖生成携物路线</span></div>
        <div><b>回到原节点</b><span>修复成功后重试原来的 PlanTransfer</span></div>
      </div>
    </div>
  </div>
  <div class="tree-metrics">
    ${stat("最终节点数", "34", "repair", "主任务 + 修复子树")}
    ${stat("repair 边", "1", "repair", "从失败的 PlanTransfer 挂载")}
    ${stat("事件数", "275", "purple", "包括修复与重试的完整轨迹")}
    ${stat("物理事务", "10", "gold", "原任务 5 + 修复任务 5")}
    ${stat("世界快照", "0 → 14", "green", "修复带来真实世界变化")}
  </div>
  ${callout("为什么叫递归？", "因为修复动作本身也遵守同一套任务树、生命周期、工件、观察和验证规则；它不是一条特殊的旁路。", "repair")}
  `,
  "repair",
  "案例二 · 修复树形状",
);

// 31. Repair timeline
page(
  "案例二：一次路线故障的详细执行过程",
  "按时间顺序看，系统不是放弃原目标，而是先改变阻碍目标的现场条件。",
  `
  ${timelineHtml([
    { title: "PlanTransfer", text: "在 world:0 规划携物路线，发现路线被 movable-crate 挡住。", meta: "产生 ROUTE_BLOCKED" },
    { title: "反事实 A*", text: "假设只移走这个箱子，重新计算后路线恢复；证明它是可修复 witness。", meta: "不是随便挑一个障碍物" },
    { title: "挂载 repair", text: "Kernel 校验 RepairProposal，并从失败的 PlanTransfer 挂一条 repair 边。", meta: "记录 rationale 与影响范围" },
    { title: "递归 Place 箱子", text: "对 movable-crate 执行完整的 Place：规划、抓取、携带、释放、验证。", meta: "世界推进到 world:7" },
    { title: "刷新工件", text: "箱子位置已经变化，原依赖可能过期；重新生成 PickPlan / TransferPlan。", meta: "新工件基于新快照" },
    { title: "重试原节点", text: "回到原来的 PlanTransfer，在新现场条件下重新执行。", meta: "不是换了用户目标" },
    { title: "最终 Verify", text: "杯子到达 drop-zone，原 Place 根节点 succeeded。", meta: "世界最终到 world:14" },
  ], "repair")}
  `,
  "repair",
  "案例二 · 时间线",
);

// 32. Recursion safety
page(
  "递归修复会不会无限套娃？不会，系统有明确的收敛条件",
  "“能修复”不等于“永远修复”。真正的工程系统必须知道什么时候停止。",
  `
  <div class="limit-grid">
    <div class="limit-card"><b>递归深度上限</b><span>修复任务带有 repair_depth，超过上限就不能继续挂新的修复。</span></div>
    <div class="limit-card"><b>证据门槛</b><span>没有具体 movable witness、可行停车区和反事实路径证明，就不能提出搬障碍修复。</span></div>
    <div class="limit-card"><b>资源占用限制</b><span>如果机器人已经拿着杯子，不能再去抓第二个障碍物；应改为重新规划携物路线。</span></div>
    <div class="limit-card"><b>失败收敛</b><span>重复失败、无安全绕行、无可达停车区时，进入 blocked，交给人工或上层策略。</span></div>
  </div>
  <div class="convergence-flow">
    <span>失败</span><b>→</b><span>有证据？</span><b>→</b><span>有安全修复？</span><b>→</b><span>深度和资源允许？</span><b>→</b><span class="green-text">挂 repair</span>
    <br><small>任一答案为否：不继续猜，明确报告 blocked。</small>
  </div>
  ${callout("工程上的诚实", "一个明确的 blocked，比系统不断尝试未知动作更有价值。它让人知道需要补充能力、改变现场，还是由人工接管。", "red")}
  `,
  "repair",
  "案例二 · 收敛与停止",
);

// 33. Continuous tasks
page(
  "连续多任务：树隔离，物理世界连续",
  "这是整套架构很容易被误解的一点：每条指令有自己的树，但下一条指令看到的是上一条执行后的真实现场。",
  `
  <div class="session-visual">
    <div class="session-task">
      <div class="session-head">Task 0001</div>
      <strong>Place(movable-crate → parking-zone)</strong>
      <span>root = program/task-0001</span>
      <small>16 节点 · 5 次物理事务</small>
    </div>
    <div class="session-world"><b>共享物理世界</b><span>world:0 → world:7 → world:14</span><i>同一台机器人、同一现场</i></div>
    <div class="session-task">
      <div class="session-head session-head-blue">Task 0002</div>
      <strong>Place(cup-red → drop-zone)</strong>
      <span>root = program/task-0002</span>
      <small>16 节点 · 5 次物理事务</small>
    </div>
  </div>
  <div class="isolation-table">
    <div class="isolation-row isolation-head"><span>每个任务独立</span><span>跨任务共享</span></div>
    <div class="isolation-row"><span>TaskTreeStore / Kernel / Inspector</span><span>WorldModel / HarnessRuntime / Backend</span></div>
    <div class="isolation-row"><span>根节点、执行栈、事件和任务目标</span><span>机器人真实状态、实体位置、事务账本</span></div>
    <div class="isolation-row"><span>防止两条根目标混在一棵树里</span><span>保证第二条指令看到第一条的结果</span></div>
  </div>
  ${callout("当前实现的准确描述", "这是串行连续会话，不是已经完成的并行调度系统。若未来需要并行，必须另加资源调度、冲突仲裁和多任务一致性设计。", "blue")}
  `,
  "teal",
  "连续会话",
);

// 34. Safety governance
page(
  "安全与治理：不是一句提示词，而是一串硬边界",
  "物理动作的安全性来自多个层次共同作用，而不是寄希望于模型每次都理解对。",
  `
  <div class="governance-stack">
    <div class="gov-row gov-intent"><b>意图边界</b><span>LLM 只能提交规范化 TaskProgram</span></div>
    <div class="gov-row gov-tree"><b>树边界</b><span>Kernel 唯一解释生命周期，Store 唯一权威状态</span></div>
    <div class="gov-row gov-artifact"><b>工件边界</b><span>计划带快照、依赖版本和安全策略指纹</span></div>
    <div class="gov-row gov-runtime"><b>执行边界</b><span>Runtime 统一做 freshness、guards、leases、checkpoint、事务</span></div>
    <div class="gov-row gov-evidence"><b>证据边界</b><span>每条命令后收 Observation，Verifier 只读快照</span></div>
    <div class="gov-row gov-repair"><b>修复边界</b><span>失败变 Diagnostic，修复以 repair 子树显式出现</span></div>
  </div>
  <div class="safety-note">
    <b>真实机器人和模拟器的差异</b>
    <p>模拟器可以回滚内存；真实后端通常不能物理回滚。因此生产适配器应报告 <code>supports_rollback = False</code>，依靠 Observation reconciliation 对账，而不是假装动作被撤销。</p>
  </div>
  `,
  "red",
  "安全与治理",
);

// 35. Advantages
page(
  "高层设计原则：为什么这套架构值得做？",
  "优势不在于模块数量更多，而在于把隐含行为变成可检查的契约。",
  `
  <div class="principle-grid">
    <div><b>单一所有权</b><span>一个 Store、一个 Kernel 写入者，减少状态冲突。</span></div>
    <div><b>职责隔离</b><span>意图、规划、物理动作、验证分别由不同角色承担。</span></div>
    <div><b>证据驱动</b><span>成功来自 Observation 和快照证据，不来自函数返回值。</span></div>
    <div><b>工件不可变</b><span>历史不被偷偷修改，新现场发布新计划。</span></div>
    <div><b>失败显式化</b><span>Diagnostic 和 repair 边让恢复进入正常工作流。</span></div>
    <div><b>任务 / 世界分层</b><span>每条任务树可隔离，真实现场可连续复用。</span></div>
  </div>
  <div class="value-map">
    <div class="value-head"><span>管理层看到的价值</span><span>技术机制</span><span>可以观察的证据</span></div>
    <div><span>风险可控</span><span>角色权限 + Runtime 唯一物理入口</span><span>ActionRequest、事务、Observation</span></div>
    <div><span>故障可恢复</span><span>Diagnostic + repair subtree + 依赖重规划</span><span>repair 边、重试次数、最终状态</span></div>
    <div><span>过程可解释</span><span>任务树、执行栈、事件日志、世界快照</span><span>127 / 275 个事件可回放</span></div>
    <div><span>能力可替换</span><span>任务与算法、机器人通过协议隔离</span><span>替换 decomposer / backend 的契约测试</span></div>
  </div>
  `,
  "teal",
  "高层原则与优势",
);

// 36. Business value
page(
  "这套设计为什么值得这样拆？",
  "这一页把前面看过的机制，翻译成你能用来判断系统好坏的理由。",
  `
  <div class="business-cards">
    <div class="business-card"><b>少一点“玄学失败”</b><p>失败会落到具体节点、具体诊断和具体证据，减少“模型刚才不知道怎么了”。</p></div>
    <div class="business-card"><b>少一点整条流程重做</b><p>障碍物只影响局部计划时，可以局部修复和局部重规划。</p></div>
    <div class="business-card"><b>更容易换能力</b><p>A*、IK、RRT、机器人后端可以替换，任务语义和控制面保持稳定。</p></div>
    <div class="business-card"><b>更容易做试点验收</b><p>可以定义成功率、恢复收敛率、人工介入率、过期计划拦截率和回放完整率。</p></div>
  </div>
  <div class="kpi-strip">
    <div><strong>任务成功率</strong><span>最终 Verify 是否通过</span></div>
    <div><strong>恢复收敛率</strong><span>发生故障后最终成功的比例</span></div>
    <div><strong>人工介入率</strong><span>进入 blocked 后需要人处理的比例</span></div>
    <div><strong>回放完整率</strong><span>任务、事件、工件、快照能否对齐</span></div>
  </div>
  ${callout("决策提醒", "参考案例里的 16 / 34 节点、127 / 275 事件，是架构证据，不是对未来生产吞吐量或成功率的承诺。生产验收要用真实设备和真实任务重新测量。", "gold")}
  `,
  "gold",
  "业务价值",
);

// 37. Production gap
page(
  "当前参考实现，离生产系统还差什么？",
  "架构契约已经明确，但参考实现仍然有清楚的边界。把边界讲出来，反而更专业。",
  `
  <div class="gap-table">
    <div class="gap-row gap-head"><span>当前参考实现</span><span>生产化需要补齐</span><span>优先级</span></div>
    <div class="gap-row"><span>Store 主要是内存实现</span><span>树、执行栈、事件、别名、工件需要同一事务持久化</span><b>P0</b></div>
    <div class="gap-row"><span>JSON 用于检查和导出</span><span>需要完整崩溃恢复协议和幂等重放</span><b>P0</b></div>
    <div class="gap-row"><span>确定性的平面双连杆模拟器</span><span>真实机器人适配、传感器噪声、控制周期和安全验收</span><b>P0</b></div>
    <div class="gap-row"><span>本地单操作员控制台</span><span>认证、权限、多操作员审计和部署隔离</span><b>P1</b></div>
    <div class="gap-row"><span>串行连续会话</span><span>如需并行，增加调度、资源仲裁和冲突解决</span><b>P1</b></div>
    <div class="gap-row"><span>保守网格 / 圆形碰撞模型</span><span>接入生产级碰撞库、附着载荷模型和在线监控</span><b>P1</b></div>
  </div>
  <div class="maturity-roadmap"><span>P0 持久化与真实后端</span><b>→</b><span>P1 运维治理与评估</span><b>→</b><span>P2 并行规模化</span></div>
  `,
  "red",
  "生产化边界",
);

// 38. Extension
page(
  "未来增加新任务、新机器人、新算法，应该怎么接？",
  "扩展点应该落在协议上，而不是把所有逻辑重新塞进 Kernel。",
  `
  <div class="extension-columns">
    <div><h3>新增高层任务</h3>${bullets(["增加已校验的 TaskProgram action。", "只编译根节点。", "注册 decomposer，返回 GraphDelta。", "复用已有 system operation 和 physical skill。", "补端到端测试和树形状断言。"])}</div>
    <div><h3>新增机器人</h3>${bullets(["实现 RobotBackend protocol。", "把厂商 SDK 类型封装在 adapter 内。", "把 telemetry 转成 Observation。", "不把厂商私有状态暴露给 planner 或 verifier。", "保留 ActionRequest / reconciliation 契约。"])}</div>
    <div><h3>新增 MPC</h3>${bullets(["实现 JointTrajectoryController。", "注入 backend / controller adapter。", "保持 ActionRequest 和 Observation 不变。", "不要把 MPC 迭代变成任务树节点。", "用安全边界和回放测试验收。"])}</div>
  </div>
  <div class="extension-rule"><strong>判断标准：</strong>新增能力应该改变“局部实现”，而不是破坏“任务树、工件、Runtime、Observation”的公共契约。</div>
  `,
  "blue",
  "扩展方式",
);

// 39. Glossary
page(
  "术语表：把技术词翻译成人话",
  "看到这些词时，先用右边的人话理解，再回头看左边的英文。",
  `
  <div class="glossary">
    <div><b>TaskProgram</b><span>模型提交的结构化任务意图，像标准订单。</span></div>
    <div><b>TaskTree</b><span>会执行、会记录状态、会挂修复的任务树。</span></div>
    <div><b>TaskTreeKernel</b><span>任务树的执行控制内核，决定下一步怎么推进。</span></div>
    <div><b>TaskTreeStore</b><span>运行时树状态的唯一权威账本。</span></div>
    <div><b>WorldModel</b><span>机器人和现场事实的版本化账本。</span></div>
    <div><b>WorldSnapshot</b><span>某一时刻的不可变现场照片。</span></div>
    <div><b>Artifact</b><span>基于某个快照生成的不可变施工图。</span></div>
    <div><b>GraphDelta</b><span>受约束的树结构增量，不是随便改数据库。</span></div>
    <div><b>Diagnostic</b><span>结构化失败原因，例如 ROUTE_BLOCKED。</span></div>
    <div><b>RepairProposal</b><span>修复器提出的修复子树方案。</span></div>
    <div><b>ActionRequest</b><span>物理技能生成的受限动作请求。</span></div>
    <div><b>Observation</b><span>动作后观察到的真实证据。</span></div>
    <div><b>reconciliation</b><span>无法物理回滚时，根据观察重新对账事实。</span></div>
    <div><b>freshness</b><span>工件依赖是否仍与当前世界一致。</span></div>
  </div>
  ${callout("一行速记", "TaskProgram 是订单，TaskTree 是施工计划，Artifact 是施工图，ActionRequest 是派工单，Observation 是现场照片。", "coral")}
  `,
  "purple",
  "附录 · 术语",
);

// 40. Source map
page(
  "如果你想继续往代码里看：模块分别在哪里？",
  "这一页是查代码的地图，不要求一次记住；遇到名词时回来对照即可。",
  `
  <div class="source-map">
    <div><b>服务入口</b><code>src/task_recursive_tree/integrations/gemini_er2/server.py</code><span>浏览器 / HTTP / 连续会话入口</span></div>
    <div><b>编译桥</b><code>src/task_recursive_tree/integrations/gemini_er2/compiler.py</code><span>Harness DTO → Kernel 定义</span></div>
    <div><b>执行桥</b><code>src/task_recursive_tree/integrations/gemini_er2/executor.py</code><span>装配 Store、Kernel、适配器</span></div>
    <div><b>任务内核</b><code>src/task_recursive_tree/task/kernel.py</code><span>唯一生命周期解释器</span></div>
    <div><b>权威 Store</b><code>src/task_recursive_tree/task/store.py</code><span>节点、运行态、边、栈、事件</span></div>
    <div><b>节点模型</b><code>src/task_recursive_tree/task/model.py</code><span>Node、Edge、GraphDelta</span></div>
    <div><b>系统操作</b><code>src/task_recursive_tree/integrations/gemini_er2/operations.py</code><span>感知、选择、规划、验证</span></div>
    <div><b>物理技能</b><code>src/task_recursive_tree/integrations/gemini_er2/skill.py</code><span>Artifact → ActionRequest</span></div>
    <div><b>物理运行时</b><code>src/task_recursive_tree/integrations/gemini_er2/physical_runtime.py</code><span>唯一后端执行边界</span></div>
    <div><b>语义与修复</b><code>src/task_recursive_tree/integrations/gemini_er2/semantics.py / repair.py</code><span>验证、Diagnostic、RepairProposal</span></div>
  </div>
  <div class="source-note">本 PDF 依据项目根目录的 <code>ARCHITECTURE.md</code>、<code>README.md</code>、中文架构说明和 <code>.artifacts/ppt-examples</code> 中的执行证据编写。参考数据用于解释架构，不代表生产 SLA。</div>
  `,
  "blue",
  "附录 · 代码索引",
);

// 41. Final memory map
page(
  "最后只记住这张图：四句话讲完整套架构",
  "如果你要向别人复述，按照下面四句讲，基本不会跑偏。",
  `
  <div class="final-map">
    <div class="final-row final-coral"><b>第一句：模型</b><span>LLM 负责理解人话、澄清目标、提交受约束的 TaskProgram。</span></div>
    <div class="final-row final-blue"><b>第二句：控制</b><span>Kernel 负责解释任务树，Store 保存唯一权威状态，决定下一步怎么推进。</span></div>
    <div class="final-row final-gold"><b>第三句：执行</b><span>系统基于世界快照生成不可变工件，物理技能把工件变成 ActionRequest，Runtime 才能碰机器人。</span></div>
    <div class="final-row final-repair"><b>第四句：恢复</b><span>每条动作后收 Observation；失败变 Diagnostic，修复以 repair 子树进入同一套执行流程。</span></div>
  </div>
  <div class="memory-formula"><span>意图</span><b>→</b><span>任务树</span><b>→</b><span>工件</span><b>→</b><span>受控动作</span><b>→</b><span>观察证据</span><b>→</b><span>修复 / 完成</span></div>
  ${callout("最终判断标准", "不要问“模型看起来聪不聪明”，要问：任务是否可解释、动作是否受控、结果是否有证据、失败是否能收敛。", "teal")}
  <div class="thank-line">Task Recursive Tree Agent · 个人学习详解版</div>
  `,
  "teal",
  "最终记忆图",
);

function tutorialTable(headers, rows, className = "") {
  return `
    <div class="tutorial-table ${className}">
      <div class="tutorial-row tutorial-head">
        ${headers.map((header) => `<span>${header}</span>`).join("")}
      </div>
      ${rows
        .map(
          (row) => `
            <div class="tutorial-row">
              ${row.map((cell) => `<span>${cell}</span>`).join("")}
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function lessonFlow(steps, className = "") {
  return `
    <div class="lesson-flow ${className}">
      ${steps
        .map(
          (step, index) => `
            <div class="lesson-step">
              <b>${index + 1}</b>
              <strong>${step.title}</strong>
              <span>${step.text}</span>
            </div>
            ${index < steps.length - 1 ? `<div class="lesson-arrow">→</div>` : ""}
          `,
        )
        .join("")}
    </div>
  `;
}

function worldLayersSvg() {
  return `
  <svg class="diagram-svg" viewBox="0 0 1000 430" role="img" aria-label="WorldSnapshot 分层图">
    <rect x="25" y="22" width="950" height="378" rx="14" fill="${COLORS.greenLight}" stroke="${COLORS.green}" stroke-width="3"/>
    <text x="55" y="58" class="svg-section">WorldSnapshot：某一个版本的现场事实</text>
    <text x="55" y="82" class="svg-small">它像一张“带编号的照片”，不是模型随口猜出来的记忆</text>
    <rect x="55" y="108" width="890" height="48" rx="8" fill="#fff" stroke="${COLORS.blue}" stroke-width="2"/>
    <text x="75" y="137" class="svg-label">版本外壳</text>
    <text x="235" y="137" class="svg-small">revision = 7   ·   snapshot_ref = world:7   ·   frame_graph_revision = 0</text>
    <rect x="55" y="174" width="270" height="180" rx="10" fill="#fff" stroke="${COLORS.coral}" stroke-width="2"/>
    <text x="190" y="207" class="svg-section" text-anchor="middle">RobotState</text>
    <text x="190" y="238" class="svg-label" text-anchor="middle">base_pose</text>
    <text x="190" y="264" class="svg-label" text-anchor="middle">joints</text>
    <text x="190" y="290" class="svg-label" text-anchor="middle">gripper_open</text>
    <text x="190" y="316" class="svg-label" text-anchor="middle">held_object_id</text>
    <text x="190" y="340" class="svg-small" text-anchor="middle">机器人当前状态</text>
    <rect x="365" y="174" width="270" height="180" rx="10" fill="#fff" stroke="${COLORS.teal}" stroke-width="2"/>
    <text x="500" y="207" class="svg-section" text-anchor="middle">entities</text>
    <text x="500" y="238" class="svg-label" text-anchor="middle">物体：cup-red</text>
    <text x="500" y="264" class="svg-label" text-anchor="middle">区域：drop-zone</text>
    <text x="500" y="290" class="svg-label" text-anchor="middle">障碍：movable-crate</text>
    <text x="500" y="316" class="svg-small" text-anchor="middle">每个实体有自己的 version</text>
    <rect x="675" y="174" width="270" height="180" rx="10" fill="#fff" stroke="${COLORS.gold}" stroke-width="2"/>
    <text x="810" y="207" class="svg-section" text-anchor="middle">GridMap</text>
    <text x="810" y="238" class="svg-label" text-anchor="middle">width / height</text>
    <text x="810" y="264" class="svg-label" text-anchor="middle">resolution / origin</text>
    <text x="810" y="290" class="svg-label" text-anchor="middle">static_occupied</text>
    <text x="810" y="316" class="svg-small" text-anchor="middle">路线规划使用的地图事实</text>
  </svg>`;
}

function specRuntimeSvg() {
  return `
  <svg class="diagram-svg" viewBox="0 0 1000 330" role="img" aria-label="任务定义和运行态分离图">
    <defs>
      <marker id="arr-spec-runtime" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
        <path d="M0,0 L8,4 L0,8 Z" fill="${COLORS.ink}"></path>
      </marker>
    </defs>
    <rect x="30" y="58" width="270" height="205" rx="12" fill="${COLORS.blueLight}" stroke="${COLORS.blue}" stroke-width="2"/>
    <text x="165" y="95" class="svg-section" text-anchor="middle">TaskNodeSpec</text>
    <text x="165" y="124" class="svg-label" text-anchor="middle">“要做什么”</text>
    <text x="165" y="160" class="svg-small" text-anchor="middle">task_type · parameters</text>
    <text x="165" y="183" class="svg-small" text-anchor="middle">preconditions · postconditions</text>
    <text x="165" y="206" class="svg-small" text-anchor="middle">max_attempts · max_repairs</text>
    <text x="165" y="236" class="svg-small" text-anchor="middle">冻结的任务定义</text>
    <line x1="312" y1="160" x2="420" y2="160" stroke="${COLORS.ink}" stroke-width="3" marker-end="url(#arr-spec-runtime)"/>
    <text x="366" y="142" class="svg-small" text-anchor="middle">Kernel 解释</text>
    <rect x="430" y="58" width="270" height="205" rx="12" fill="${COLORS.tealLight}" stroke="${COLORS.teal}" stroke-width="2"/>
    <text x="565" y="95" class="svg-section" text-anchor="middle">TaskNodeRuntime</text>
    <text x="565" y="124" class="svg-label" text-anchor="middle">“现在做到哪了”</text>
    <text x="565" y="160" class="svg-small" text-anchor="middle">status · phase · attempts</text>
    <text x="565" y="183" class="svg-small" text-anchor="middle">expanded · artifacts · diagnostic</text>
    <text x="565" y="206" class="svg-small" text-anchor="middle">started_at · finished_at</text>
    <text x="565" y="236" class="svg-small" text-anchor="middle">随执行变化的记录</text>
    <line x1="712" y1="160" x2="820" y2="160" stroke="${COLORS.ink}" stroke-width="3" marker-end="url(#arr-spec-runtime)"/>
    <text x="766" y="142" class="svg-small" text-anchor="middle">写入 Store</text>
    <rect x="830" y="58" width="140" height="205" rx="12" fill="${COLORS.purpleLight}" stroke="${COLORS.purple}" stroke-width="2"/>
    <text x="900" y="101" class="svg-section" text-anchor="middle">Store</text>
    <text x="900" y="138" class="svg-small" text-anchor="middle">唯一权威</text>
    <text x="900" y="166" class="svg-small" text-anchor="middle">定义 + 运行态</text>
    <text x="900" y="194" class="svg-small" text-anchor="middle">边 + 栈</text>
    <text x="900" y="222" class="svg-small" text-anchor="middle">事件</text>
  </svg>`;
}

function stackSvg() {
  return `
  <svg class="diagram-svg" viewBox="0 0 1000 390" role="img" aria-label="执行栈和事件日志示意图">
    <rect x="55" y="35" width="350" height="315" rx="12" fill="${COLORS.blueLight}" stroke="${COLORS.blue}" stroke-width="2"/>
    <text x="230" y="70" class="svg-section" text-anchor="middle">执行栈：下一步的书签</text>
    <rect x="95" y="102" width="270" height="55" rx="8" fill="#fff" stroke="${COLORS.blue}"/>
    <text x="230" y="126" class="svg-label" text-anchor="middle">Place</text>
    <text x="230" y="145" class="svg-small" text-anchor="middle">phase = children</text>
    <rect x="95" y="175" width="270" height="55" rx="8" fill="#fff" stroke="${COLORS.teal}"/>
    <text x="230" y="199" class="svg-label" text-anchor="middle">PlanTransfer</text>
    <text x="230" y="218" class="svg-small" text-anchor="middle">phase = repair</text>
    <rect x="95" y="248" width="270" height="55" rx="8" fill="${COLORS.repairLight}" stroke="${COLORS.repair}"/>
    <text x="230" y="272" class="svg-label" text-anchor="middle">RouteBlockedRepair</text>
    <text x="230" y="291" class="svg-small" text-anchor="middle">top = 当前正在执行</text>
    <text x="230" y="330" class="svg-small" text-anchor="middle">栈顶完成后弹出，回到父节点继续</text>
    <rect x="520" y="35" width="425" height="315" rx="12" fill="${COLORS.goldLight}" stroke="${COLORS.gold}" stroke-width="2"/>
    <text x="732" y="70" class="svg-section" text-anchor="middle">事件日志：发生过什么</text>
    <text x="555" y="111" class="svg-label">021  stack_push</text>
    <text x="555" y="133" class="svg-small">node = plan-transfer, phase = enter</text>
    <text x="555" y="175" class="svg-label">022  runtime</text>
    <text x="555" y="197" class="svg-small">pending → running</text>
    <text x="555" y="239" class="svg-label">027  repair_mounted</text>
    <text x="555" y="261" class="svg-small">kind = repair, entry = route-blocked</text>
    <text x="555" y="303" class="svg-small">日志只追加，不把历史覆盖掉</text>
  </svg>`;
}

function traceSvg() {
  return `
  <svg class="diagram-svg" viewBox="0 0 1000 255" role="img" aria-label="从 world:0 到 world:14 的完整追踪图">
    <defs>
      <marker id="arr-trace" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
        <path d="M0,0 L8,4 L0,8 Z" fill="${COLORS.ink}"></path>
      </marker>
    </defs>
    <line x1="100" y1="125" x2="900" y2="125" stroke="${COLORS.line}" stroke-width="6"/>
    <circle cx="100" cy="125" r="28" fill="${COLORS.blue}" />
    <text x="100" y="131" class="svg-label" fill="#fff" text-anchor="middle">0</text>
    <text x="100" y="76" class="svg-section" text-anchor="middle">world:0</text>
    <text x="100" y="178" class="svg-small" text-anchor="middle">杯子、箱子都在原位</text>
    <line x1="130" y1="125" x2="340" y2="125" stroke="${COLORS.repair}" stroke-width="4" marker-end="url(#arr-trace)"/>
    <text x="235" y="103" class="svg-small" text-anchor="middle">PlanTransfer 发现阻塞</text>
    <circle cx="380" cy="125" r="28" fill="${COLORS.repair}" />
    <text x="380" y="131" class="svg-label" fill="#fff" text-anchor="middle">7</text>
    <text x="380" y="76" class="svg-section" text-anchor="middle">world:7</text>
    <text x="380" y="178" class="svg-small" text-anchor="middle">箱子已停放，机器人回到空手状态</text>
    <line x1="410" y1="125" x2="700" y2="125" stroke="${COLORS.teal}" stroke-width="4" marker-end="url(#arr-trace)"/>
    <text x="555" y="103" class="svg-small" text-anchor="middle">刷新工件 → 抓杯 → 持物移动 → 释放</text>
    <circle cx="740" cy="125" r="28" fill="${COLORS.green}" />
    <text x="740" y="131" class="svg-label" fill="#fff" text-anchor="middle">14</text>
    <text x="740" y="76" class="svg-section" text-anchor="middle">world:14</text>
    <text x="740" y="178" class="svg-small" text-anchor="middle">杯子在 drop-zone，目标验证通过</text>
    <text x="900" y="132" class="svg-small" text-anchor="middle">任务完成</text>
  </svg>`;
}

// 42. Personal learning route
page(
  "从这里开始：把它当成一门小课来学",
  "这一组页面不是给别人汇报的讲稿，而是帮助你建立自己的架构直觉。",
  `
  ${callout("学习目标", "读完后，你应该能回答六个问题：系统保存了什么？任务如何拆开？谁能写树？计划为什么会过期？动作后的事实从哪里来？失败如何变成修复任务？", "coral")}
  ${lessonFlow([
    { title: "先看事实", text: "世界里有哪些对象，机器人现在在哪里？" },
    { title: "再看意图", text: "人想要什么，TaskProgram 怎样表达？" },
    { title: "再看任务", text: "Kernel 怎样把意图变成可执行的树？" },
    { title: "再看动作", text: "工件怎样变成 ActionRequest 并进入后端？" },
    { title: "最后看修复", text: "观察到失败后，为什么要挂 repair 子树？" },
  ])}
  <div class="lesson-rule">
    <b>读图口诀：</b>
    <span>事实 → 决定 → 计划 → 请求 → 观察 → 更新 → 再决定</span>
  </div>
  <div class="study-tips">
    <div><b>不要先背英文</b><span>先把每个名字翻译成一句人话，再记英文名。</span></div>
    <div><b>不要把计划当事实</b><span>计划只是“打算怎么做”，Observation 才是“做完后看见什么”。</span></div>
    <div><b>不要把算法当任务</b><span>A*、IK、RRT 是工具内部的计算，不是用户要理解的业务步骤。</span></div>
  </div>
  `,
  "coral",
  "学习路线",
);

// 43. Minimal mental model
page(
  "先只认识四种东西：目标、任务树、世界、工件",
  "如果这四种东西没有分开，后面所有术语都会混在一起。",
  `
  <div class="four-concepts">
    <div class="study-concept concept-intent"><b>1. 目标 / Intent</b><strong>“把红杯放到右边”</strong><span>人真正想要的结果。它还没有说明每一步怎么做。</span></div>
    <div class="study-concept concept-tree"><b>2. 任务树 / TaskTree</b><strong>先规划、再抓取、再移动、再释放</strong><span>把目标变成有顺序、有状态、有验证的工作清单。</span></div>
    <div class="study-concept concept-world"><b>3. 世界 / World</b><strong>杯子在哪、箱子在哪、机器人是否拿着东西</strong><span>系统对当前现场事实的版本化记录。</span></div>
    <div class="study-concept concept-artifact"><b>4. 工件 / Artifact</b><strong>基于 world:0 生成的路线和姿态</strong><span>把“怎么做”具体化，并记住它依赖哪个世界版本。</span></div>
  </div>
  <div class="mental-model-equation">
    <span>目标</span><b>→</b><span>任务树</span><b>→</b><span>工件</span><b>→</b><span>动作请求</span><b>→</b><span>观察</span><b>→</b><span>新世界</span>
  </div>
  ${callout("最容易犯的混淆", "“我已经算出了路线”不等于“机器人已经走完了”；“机器人收到了命令”也不等于“目标已经满足”。架构把这些状态分开，正是为了不自欺。", "repair")}
  ${tutorialTable(
    ["你看到的东西", "它回答的问题", "它不回答的问题"],
    [
      ["TaskProgram", "用户想要什么？", "具体哪条轨迹最安全？"],
      ["WorldSnapshot", "现场现在是什么样？", "下一步一定会成功吗？"],
      ["Artifact", "按照哪个版本、怎样做？", "动作后实际发生了什么？"],
      ["Observation", "动作后观察到了什么？", "是否应该直接修改任务树？"],
    ],
    "three-col-table",
  )}
  `,
  "blue",
  "第一课 · 心智模型",
);

// 44. Component motivations
page(
  "组件为什么存在：从“会出错的地方”倒推设计",
  "不要把组件看成一堆类名；每个组件都是为了堵住一种具体的错误。",
  `
  ${tutorialTable(
    ["如果没有它，容易发生什么", "对应组件", "它具体堵住的错误"],
    [
      ["模型直接发出任意机器人命令", "<code>TaskProgram</code> + Compiler", "把自由文本限制成可校验的任务意图。"],
      ["多个模块同时修改任务树", "<code>TaskTreeStore</code>", "只允许一个 Kernel 持有写入能力。"],
      ["任务做到一半不知道下一步", "<code>TaskTreeKernel</code>", "统一解释生命周期、子节点、验证和修复。"],
      ["分解器偷偷把节点塞到别处", "<code>GraphDelta</code> 校验", "限制挂载范围、父子关系、无环和可达性。"],
      ["路线还是旧现场的路线", "<code>ArtifactFreshness</code>", "比较快照、地图、实体和机器人版本。"],
      ["系统操作绕过安全层直接碰机器人", "<code>HarnessRuntime</code>", "成为唯一物理执行入口。"],
      ["命令成功但物体其实没抓住", "<code>Observation</code> + Verifier", "用动作后的事实判断，不信请求本身。"],
      ["失败后模型临时发挥，无法回放", "Diagnostic + Repair", "把失败原因和修复工作显式放进树里。"],
    ],
    "motivation-table",
  )}
  <div class="motivation-summary">
    <b>把这页压缩成一句话：</b>
    <span>每个边界都在回答“谁有权做这件事、依据什么事实、做完如何证明”。</span>
  </div>
  ${callout("学习方法", "以后看到一个新组件，先问它要防哪一种错误。如果答不出来，说明你还没有理解它的设计动机，先不要急着记 API。", "gold")}
  `,
  "teal",
  "第二课 · 设计动机",
);

// 45. Design principles
page(
  "每个组件的高层原则：能做什么，也要知道不能做什么",
  "架构的质量不只靠“它能完成任务”，还靠“它被禁止做什么”。",
  `
  <div class="principle-cards">
    <div class="principle-card pc-coral"><b>LLM / Agent</b><strong>只提交意图，不提交副作用</strong><span>可以理解、澄清、选择对象；不能写运行态树，不能直接调机器人。</span></div>
    <div class="principle-card pc-blue"><b>Compiler</b><strong>先校验，再进入执行世界</strong><span>把 TaskProgram 编成根节点；不把任意外部 DTO 直接当权威运行态。</span></div>
    <div class="principle-card pc-purple"><b>Store</b><strong>单一事实来源</strong><span>节点规格、运行态、边、执行栈和事件都由同一个 Store 保存。</span></div>
    <div class="principle-card pc-teal"><b>Kernel</b><strong>唯一生命周期解释器</strong><span>只有它能推进状态、挂载 GraphDelta、处理失败和重试。</span></div>
    <div class="principle-card pc-green"><b>WorldModel</b><strong>只记录可观察事实</strong><span>世界版本由 Observation 推进；规划器不能把自己的猜测写成事实。</span></div>
    <div class="principle-card pc-gold"><b>Artifact</b><strong>不可变，依赖可检查</strong><span>旧工件保留用于审计；世界改变时发布新工件，而不是偷偷改旧工件。</span></div>
    <div class="principle-card pc-red"><b>HarnessRuntime</b><strong>副作用集中管理</strong><span>做新鲜度检查、前置守卫、资源租约、事务和观察对账。</span></div>
    <div class="principle-card pc-repair"><b>Verifier / Repair</b><strong>结果先证据，修复再入树</strong><span>Verifier 不调用规划器；RepairResolver 只能提出方案，不能自己改树。</span></div>
  </div>
  <div class="principle-contrast">
    <div><b>允许的方向</b><span>任务树读世界 → 系统操作生成工件 → 技能构造请求 → Runtime 执行 → Observation 更新世界</span></div>
    <div><b>禁止的捷径</b><span>模型直接调机器人、A* 偷偷搬障碍、技能直接改状态、验证器自己重规划。</span></div>
  </div>
  `,
  "purple",
  "第二课 · 设计原则",
);

// 46. World snapshot overview
page(
  "世界状态先讲清：系统到底在记录什么？",
  "后面的规划、守卫、验证和修复，都只能基于某个版本的世界状态。",
  `
  ${worldLayersSvg()}
  <div class="world-teaching-grid">
    <div><b>世界模型 WorldModel</b><span>像一个持续更新的“现场账本”。它接收 Observation，并在内存中维护当前状态。</span></div>
    <div><b>世界快照 WorldSnapshot</b><span>像从账本上取出的只读照片。规划器和 Verifier 读取它，但不能直接改它。</span></div>
    <div><b>版本 revision</b><span>每次 ingest 一次观察就推进一个世界版本；例如 world:0、world:1、world:7。</span></div>
    <div><b>事实与计划</b><span>“箱子在 (4.5, 3.5)”是事实；“从这里走到那里”是计划，两者不能混写。</span></div>
  </div>
  ${callout("参考实现的边界", "当前参考实现的 WorldModel 和 TaskTreeStore 都主要在内存里；JSON 导出方便检查和回放，不等于已经具备完整的崩溃恢复协议。", "gold")}
  `,
  "green",
  "第三课 · 世界状态",
);

// 47. World snapshot JSON
page(
  "WorldSnapshot 的具体编码：看一张完整的“现场照片”",
  "下面是为了阅读整理的 JSON 形态；真实 Python dataclass 导出会把元组、枚举和集合转换成 JSON 数组或字符串。",
  `
  ${codeBlock(String.raw`{
  "snapshot_ref": "world:0",
  "revision": 0,
  "frame_graph_revision": 0,
  "robot": {
    "base_pose": {"x": 1.5, "y": 1.5, "z": 0.0, "yaw": 0.0, "frame": "world"},
    "joints": [0.0, 0.0],
    "gripper_open": true,
    "held_object_id": null,
    "state_epoch": 0
  },
  "entities": {
    "cup-red": {
      "kind": "object", "pose": {"x": 2.5, "y": 2.5, "z": 0.0, "yaw": 0.0, "frame": "world"},
      "radius": 0.16, "tags": ["cup", "red"],
      "properties": {"movable": true}, "version": 0
    },
    "drop-zone": {
      "kind": "region", "pose": {"x": 7.5, "y": 5.5, "z": 0.0, "yaw": 0.0, "frame": "world"},
      "radius": 0.45, "tags": ["destination"],
      "properties": {"acceptance_radius": 0.3, "placement_offsets": [[0.0, 0.0]]},
      "version": 0
    }
  },
  "grid": {
    "width": 10, "height": 8, "resolution": 1.0,
    "origin_x": 0.0, "origin_y": 0.0,
    "static_occupied": [[4, 0], [4, 1], [4, 2], [4, 4]]
  }
}`)}
  ${tutorialTable(
    ["字段", "你可以把它理解成", "它为什么重要"],
    [
      ["snapshot_ref", "这张照片的编号", "工件和证据都能说清自己基于哪一版现场。"],
      ["revision", "世界版本计数器", "判断“现场是否更新过”。"],
      ["robot", "机器人自己的状态", "抓取、移动、释放的前提都要依赖它。"],
      ["entities", "物体、区域、障碍物", "选择对象、碰撞判断和目标验证都从这里读。"],
      ["grid", "路线规划使用的地图", "A* 需要知道哪些格子可走。"],
    ],
    "world-json-table",
  )}
  `,
  "green",
  "第三课 · 世界状态编码",
);

// 48. EntityState field by field
page(
  "EntityState：一个物体的状态到底包括哪些属性？",
  "先把一个杯子拆开看；理解了一个实体，理解区域和障碍就容易了。",
  `
  ${codeBlock(String.raw`{
  "entity_id": "cup-red",
  "kind": "object",
  "pose": {
    "x": 2.5,
    "y": 2.5,
    "z": 0.0,
    "yaw": 0.0,
    "frame": "world"
  },
  "radius": 0.16,
  "tags": ["cup", "red"],
  "properties": {
    "movable": true
  },
  "version": 0
}`)}
  ${tutorialTable(
    ["字段", "白话解释", "在杯子任务中怎么用"],
    [
      ["entity_id", "稳定身份证，不是显示名称", "任务引用 cup-red；即使展示文案改变，引用仍然稳定。"],
      ["kind", "实体大类", "object 表示物体；region 表示目标或停放区域。"],
      ["pose", "位置、方向、坐标系", "规划器知道杯子在哪里；frame 防止把不同坐标系混在一起。"],
      ["radius", "用于碰撞和空间近似的尺寸", "杯子半径影响可达性、占用格子和载荷包络。"],
      ["tags", "语义标签集合", "可用 cup、red 等标签做选择，不必只靠名字猜。"],
      ["properties", "领域属性字典", "movable 表示可移动；nav_obstacle 表示会挡导航。"],
      ["version", "这个实体自己的变化次数", "杯子被放下后版本增加，旧计划就可能不再新鲜。"],
    ],
    "entity-field-table",
  )}
  ${callout("为什么不把所有东西塞进一个 description 字符串？", "因为规划和验证需要可计算的字段。字符串适合给人看，结构化属性才适合做前置条件、碰撞判断和版本比较。", "teal")}
  `,
  "teal",
  "第三课 · 实体状态",
);

// 49. Entity kinds
page(
  "物体、区域、障碍物：看起来都叫实体，实际用途不同",
  "它们共享 EntityState 的外壳，但通过 kind、tags 和 properties 表达不同语义。",
  `
  <div class="entity-kind-grid">
    <div class="entity-kind kind-object">
      <h3>object · 物体</h3>
      ${codeBlock(String.raw`{
  "entity_id": "movable-crate",
  "kind": "object",
  "radius": 0.35,
  "tags": ["crate"],
  "properties": {
    "movable": true,
    "nav_obstacle": true
  }
}`)}
      <p>可以被抓取和搬运；同时会占用导航空间。</p>
    </div>
    <div class="entity-kind kind-region">
      <h3>region · 区域</h3>
      ${codeBlock(String.raw`{
  "entity_id": "drop-zone",
  "kind": "region",
  "radius": 0.45,
  "tags": ["destination"],
  "properties": {
    "acceptance_radius": 0.3,
    "placement_offsets": [[0.0, 0.0]]
  }
}`)}
      <p>它不是要被搬走的东西，而是判断“放得够不够近”的参考区域。</p>
    </div>
    <div class="entity-kind kind-robot">
      <h3>robot · 为什么单独编码</h3>
      ${codeBlock(String.raw`{
  "base_pose": {"x": 1.5, "y": 1.5, "yaw": 0.0},
  "joints": [0.0, 0.0],
  "gripper_open": true,
  "held_object_id": null,
  "state_epoch": 0
}`)}
      <p>机器人有可驱动关节、夹爪和资源占用，不适合伪装成普通实体。</p>
    </div>
  </div>
  ${tutorialTable(
    ["问题", "读哪个字段", "例子"],
    [
      ["“它是不是我想找的杯子？”", "kind + tags + selector", "kind=object，tags 包含 cup、red。"],
      ["“它会不会挡路？”", "properties.nav_obstacle", "movable-crate=true。"],
      ["“它能不能被搬走？”", "properties.movable", "箱子可移动，静态墙通常不可移动。"],
      ["“放到哪里算成功？”", "region.properties", "acceptance_radius=0.3。"],
    ],
    "entity-question-table",
  )}
  `,
  "gold",
  "第三课 · 实体分类",
);

// 50. Robot state and versions
page(
  "机器人状态与版本：为什么 state_epoch 不能省？",
  "世界版本告诉你“现场更新过几次”，机器人状态纪元告诉你“机器人自身是否变过”。",
  `
  <div class="robot-state-compare">
    <div class="robot-state-card state-before">
      <b>world:0 · 空手</b>
      ${codeBlock(String.raw`{
  "base_pose": {"x": 1.5, "y": 1.5, "yaw": 0.0},
  "joints": [0.0, 0.0],
  "gripper_open": true,
  "held_object_id": null,
  "state_epoch": 0
}`)}
    </div>
    <div class="state-transition">执行抓取并收到观察 →</div>
    <div class="robot-state-card state-after">
      <b>world:2 · 已持有杯子</b>
      ${codeBlock(String.raw`{
  "base_pose": {"x": 2.5, "y": 2.5, "yaw": 0.0},
  "joints": [0.48, -0.97],
  "gripper_open": false,
  "held_object_id": "cup-red",
  "state_epoch": 2
}`)}
    </div>
  </div>
  ${tutorialTable(
    ["属性", "记录什么", "如果忽略会怎样"],
    [
      ["base_pose", "移动底盘的位置和朝向", "导航计划的起点可能已经不对。"],
      ["joints", "机械臂当前关节角", "旧的抓取轨迹可能无法连续接上。"],
      ["gripper_open", "夹爪是否张开", "无法判断能否抓取或释放。"],
      ["held_object_id", "当前是否持有某物", "系统可能错误地尝试抓第二个物体。"],
      ["state_epoch", "机器人状态变化的纪元", "工件无法检测“轨迹起点已变”。"],
    ],
    "robot-field-table",
  )}
  ${callout("参考模型边界", "当前项目里的机器人是确定性的平面双连杆参考模拟器。这里的字段契约可保留到真实机器人，但真实控制器、噪声和在线安全监控仍需另行接入。", "gold")}
  `,
  "coral",
  "第三课 · 机器人状态",
);

// 51. Observation ingestion
page(
  "Observation 如何推进世界：动作之后才有新事实",
  "把“请求”和“事实”分开，是这套架构最重要的学习点之一。",
  `
  ${lessonFlow([
    { title: "ActionRequest", text: "技能提出：请执行抓取。" },
    { title: "HarnessRuntime", text: "检查工件、守卫、资源和事务。" },
    { title: "RobotBackend", text: "模拟器或真实机器人执行命令。" },
    { title: "Observation", text: "读取夹爪、关节和物体的实际状态。" },
    { title: "WorldModel.ingest", text: "生成下一个不可变快照。" },
  ], "observation-flow")}
  <div class="observation-example">
    <div>
      <h3>动作前：world:1</h3>
      ${codeBlock(String.raw`robot.gripper_open = true
robot.held_object_id = null
cup-red.version = 0`)}
    </div>
    <div class="observation-arrow">收到 Observation →</div>
    <div>
      <h3>动作后：world:2</h3>
      ${codeBlock(String.raw`robot.gripper_open = false
robot.held_object_id = "cup-red"
robot.state_epoch = 2
cup-red.version = 0`)}
    </div>
  </div>
  ${tutorialTable(
    ["阶段", "它是什么", "谁可以改变它"],
    [
      ["请求", "ActionRequest：想让后端做什么", "PhysicalSkill 构造，Runtime 接收。"],
      ["执行", "Backend：实际尝试做什么", "HarnessRuntime 唯一调用入口。"],
      ["观察", "Observation：动作后看见什么", "Backend 提供，WorldModel 接收。"],
      ["结论", "Verifier：后置条件是否满足", "只读快照，返回结果和证据。"],
    ],
    "observation-table",
  )}
  ${callout("关键句", "命令返回成功，只说明命令被执行流程接受或完成；只有 Observation 加上 Verifier，才能说明杯子真的被夹住、真的到达目标。", "green")}
  `,
  "green",
  "第四课 · 观察闭环",
);

// 52. TaskProgram
page(
  "任务不是一句话：TaskProgram 怎样表达“我要做什么”",
  "LLM 的输出要先变成一个受约束的中间表示，才允许进入任务树；选择器的完整字段放在下一页。",
  `
  <div class="program-conversion">
    <div class="program-human"><b>人话</b><strong>“把红色杯子放到右边的目标区”</strong><span>含糊：红色杯子是哪一个？右边目标区是哪一个？</span></div>
    <div class="program-arrow">规范化 →</div>
    <div class="program-structured"><b>TaskProgram</b><span>action = place</span><span>object_selector = exact cup-red</span><span>destination_selector = exact drop-zone</span></div>
  </div>
  ${codeBlock(String.raw`{
  "program_id": "demo-place",
  "action": "place",
  "arguments": {
    "object_selector": {
      "entity_kind": "object",
      "relation": "exact",
      "entity_id": "cup-red",
      "required_tags": ["cup", "red"]
    },
    "destination_selector": {
      "entity_kind": "region",
      "relation": "exact",
      "entity_id": "drop-zone",
      "required_tags": ["destination"]
    }
  }
}`)}
  ${tutorialTable(
    ["字段", "白话意义", "设计动机"],
    [
      ["program_id", "这份任务意图的身份证", "让根节点、日志和会话历史能对上。"],
      ["action", "高层动作类型", "当前参考实现支持 place，其他动作先拒绝。"],
      ["object_selector", "怎样找被操作的物体", "用结构化选择代替模型猜名字。"],
      ["destination_selector", "怎样找目标区域", "目标也要明确绑定，不能只说“右边”。"],
      ["cardinality / tie_policy", "必须选几个、同分时如何决胜", "防止一次选择多个或结果不稳定。"],
    ],
    "program-field-table",
  )}
  `,
  "coral",
  "第五课 · 任务意图",
);

// 53. SpatialSelector and BindingArtifact
page(
  "TaskProgram 里的选择器：系统怎样确认“到底是哪一个对象”？",
  "任务里的对象不能只靠一句描述；选择器把语义、空间和决胜规则写成可检查的结构。",
  `
  ${codeBlock(String.raw`{
  "entity_kind": "object",
  "relation": "exact",
  "entity_id": "cup-red",
  "required_tags": ["cup", "red"],
  "region_id": null,
  "reference_entity_id": null,
  "reference": "robot",
  "frame": "world",
  "metric": "euclidean",
  "cardinality": "one",
  "tie_policy": "stable_id"
}`)}
  ${tutorialTable(
    ["选择器字段", "它在说什么", "为什么不能省"],
    [
      ["entity_kind", "我要找 object 还是 region", "避免把目标区域当成要搬的物体。"],
      ["relation", "exact、nearest、leftmost 等选择关系", "把“怎么选”从模型猜测变成规则。"],
      ["entity_id / required_tags", "精确 ID 或语义标签", "既能稳定引用，也能表达类别。"],
      ["reference / frame", "相对于谁、在哪个坐标系", "防止“右边”“附近”失去参照。"],
      ["metric / cardinality", "如何计算距离、选几个", "保证选择结果可重复、不会偷偷选多个。"],
      ["tie_policy", "同分时谁优先", "让同一现场多次运行得到稳定结果。"],
    ],
    "selector-field-table",
  )}
  <div class="selector-binding-flow">
    <div><b>SpatialSelector</b><span>描述选择规则</span></div>
    <div>→</div>
    <div><b>SelectorEngine</b><span>读取 WorldSnapshot 并筛选</span></div>
    <div>→</div>
    <div><b>BindingArtifact</b><span>记录最终绑定和证据</span></div>
  </div>
  ${codeBlock(String.raw`{
  "artifact_id": "binding-f74f98cb32e14750bae35db65b7744d0",
  "object_id": "cup-red",
  "destination_id": "drop-zone",
  "object_candidates": ["cup-red"],
  "destination_candidates": ["drop-zone"],
  "snapshot_ref": "world:0",
  "grounding_evidence": ["exact id and required tags matched"]
}`)}
  `,
  "blue",
  "第五课 · 对象选择",
);

// 54. TaskNodeSpec
page(
  "TaskNodeSpec：任务树节点的“身份证和工作说明书”",
  "Spec 描述节点本来是什么；它不记录这个节点已经跑了几次。",
  `
  ${codeBlock(String.raw`{
  "node_id": "program/demo-place",
  "task_type": "Place",
  "operation_kind": "decomposer",
  "control_kind": "sequence",
  "origin": "program",
  "parameters": {
    "scope": "demo-place",
    "object_selector": "exact cup-red",
    "destination_selector": "exact drop-zone",
    "repair_depth": 0
  },
  "preconditions": [],
  "postconditions": [],
  "max_attempts": 1,
  "max_repairs": 0,
  "execution_policy": "skip_if_goal_satisfied"
}`)}
  ${tutorialTable(
    ["字段", "它回答的问题", "Place 示例"],
    [
      ["node_id", "这个节点在树中的稳定位置？", "program/demo-place/plan-pick"],
      ["task_type", "业务上要做哪件事？", "Place、PlanPick、ExecuteGrasp。"],
      ["operation_kind", "它属于哪种权限角色？", "decomposer、system 或 physical。"],
      ["control_kind", "它怎样控制子节点？", "leaf、sequence、selector。"],
      ["origin", "它从哪里产生？", "program、compiler、decomposer 或 repair。"],
      ["parameters", "它执行时需要哪些参数？", "对象、目标、scope、repair_depth。"],
      ["pre/postconditions", "开始前和结束后要满足什么？", "object_held、placed_in_region 等。"],
      ["limits / policy", "最多重试几次、目标已满足时怎样处理？", "防止无限循环和重复副作用。"],
    ],
    "spec-field-table",
  )}
  <div class="spec-reading-tip"><b>读一个节点的方法：</b><span>先看它是什么角色，再看参数；然后看它前置、后置和上限。不要只看 task_type，因为同名任务在不同角色下可能拥有不同权限。</span></div>
  `,
  "blue",
  "第五课 · 节点定义",
);

// 54. TaskNodeRuntime
page(
  "TaskNodeRuntime：同一个节点现在跑到哪一步了？",
  "运行态会变，任务定义尽量不变；这就是为什么两者要分开。",
  `
  ${codeBlock(String.raw`{
  "status": "running",
  "phase": "preconditions",
  "attempts": 1,
  "repairs": 0,
  "expanded": true,
  "output_artifacts": [
    "nav-dfef826f0b0c4b1faa566d1447acadd5",
    "pick-efbdc179e47a459d94fe036166172764"
  ],
  "last_diagnostic": null,
  "started_at": 1725000000.0,
  "finished_at": null,
  "adapter_state": {
    "active_request_id": "tree:demo-place:...:attempt:1"
  }
}`)}
  <div class="runtime-state-flow">
    <div><b>pending</b><span>还没有进入</span></div><div>→</div>
    <div><b>running</b><span>当前正在解释</span></div><div>→</div>
    <div><b>succeeded</b><span>后置条件通过</span></div>
    <div class="runtime-failure"><b>failed / blocked</b><span>有诊断但没有完成</span></div>
  </div>
  ${tutorialTable(
    ["字段", "它记录什么", "学习时怎么理解"],
    [
      ["status", "pending / running / succeeded / failed / blocked / cancelled", "节点的最终或当前结果。"],
      ["phase", "enter、preconditions、execute、verify、repair 等", "这个节点内部生命周期走到哪一站。"],
      ["attempts", "已经尝试执行几次", "区分首次失败和重试失败。"],
      ["repairs", "已经挂过几次修复", "防止修复无限递归。"],
      ["expanded", "是否已经展开过子节点", "复合节点不能每次 tick 都重复加孩子。"],
      ["output_artifacts", "这个节点发布了哪些工件", "后面的物理节点通过 scope 找到计划。"],
      ["last_diagnostic", "最近一次结构化失败", "让失败不只是一个字符串。"],
    ],
    "runtime-field-table",
  )}
  `,
  "teal",
  "第五课 · 节点运行态",
);

// 55. Spec/runtime separation
page(
  "为什么一定要把 Spec 和 Runtime 分离？",
  "把它想成“施工图”和“施工记录”：图纸不能因为今天下雨就被改写。",
  `
  ${specRuntimeSvg()}
  ${tutorialTable(
    ["时刻", "Spec 保持什么", "Runtime 发生什么"],
    [
      ["刚编译", "Place 是 sequence，参数是 cup-red → drop-zone", "status=pending，attempts=0。"],
      ["开始执行", "任务定义不变", "status=running，phase=enter。"],
      ["发布计划", "PlanPick 仍然是 PlanPick", "output_artifacts 写入 pick / nav 引用。"],
      ["路线失败", "原节点仍然代表 PlanTransfer", "last_diagnostic=ROUTE_BLOCKED，repairs=1。"],
      ["修复成功", "原任务仍可被重新解释", "repair 子树 succeeded，原节点再次尝试。"],
    ],
    "spec-runtime-table",
  )}
  ${callout("如果混在一起会怎样？", "为了重试而直接改 task_type、参数和历史状态，最后你无法判断：原来要做什么？后来为什么变了？哪一次动作对应哪份计划？分离让定义和执行记录各自清楚。", "repair")}
  `,
  "blue",
  "第五课 · 定义与记录",
);

// 56. Edges and GraphDelta
page(
  "TaskEdge 和 GraphDelta：树不是靠“改一大坨 JSON”长出来的",
  "分解器每次只提交一个受约束的结构增量，Kernel 验证后才交给 Store。",
  `
  <div class="edge-diagram">
    <div class="edge-node edge-parent">Place<br><small>parent_id</small></div>
    <div class="edge-vertical"></div>
    <div class="edge-row">
      <div class="edge-node edge-child">PlanPick<br><small>kind=child</small></div>
      <div class="edge-node edge-child">Pick<br><small>kind=child</small></div>
      <div class="edge-node edge-repair">Repair<br><small>kind=repair</small></div>
    </div>
  </div>
  ${codeBlock(String.raw`// 普通 child 边
{
  "parent_id": "program/demo-place",
  "child_id": "program/demo-place/pick",
  "kind": "child",
  "order": 3
}

// 失败节点挂出的 repair 边
{
  "parent_id": "program/demo-place/plan-transfer",
  "child_id": "program/demo-place/plan-transfer/repair-0/route-blocked",
  "kind": "repair",
  "order": 0
}`)}
  <div class="delta-contract">
    <div><b>GraphDelta 带来什么</b><span>只描述新增节点和新增边；外部组件没有机会直接重写整棵运行时树。</span></div>
    <div><b>Kernel 检查什么</b><span>不能自环、不能重复父节点、不能成环、不能脱离根节点、repair 必须从失败节点挂出。</span></div>
    <div><b>Store 做什么</b><span>通过唯一 writer capability 原子应用增量，并追加 graph_delta 事件。</span></div>
  </div>
  ${callout("为什么叫“增量”", "因为系统需要知道树是怎样一步步长出来的。每个 GraphDelta 都能对应到某个分解、修复或编译动作，审计时可以回放。", "blue")}
  `,
  "blue",
  "第六课 · 树结构",
);

// 57. Stack and event log
page(
  "执行栈和事件日志：一个管“下一步”，一个管“已经发生过什么”",
  "这两个对象常被混为一谈；把它们分清，你就能读懂 Kernel 的运行过程。",
  `
  ${stackSvg()}
  ${tutorialTable(
    ["对象", "像什么", "包含什么", "用途"],
    [
      ["ExecutionFrame", "书签 / 调用栈帧", "node_id、phase、next_child_index、active_child_id、诊断", "暂停后知道从哪里继续。"],
      ["Execution stack", "正在展开的路径", "从根到当前节点的一串 frame", "表达当前控制位置。"],
      ["TaskEvent", "黑匣子流水账", "sequence、event_type、node_id、data", "解释过去发生过什么。"],
      ["事件日志", "只追加的历史", "graph_delta、runtime、stack_push/pop、repair_mounted", "回放、调试和审计。"],
    ],
    "stack-event-table",
  )}
  ${callout("一个简单比喻", "执行栈像你手里夹着的书签：告诉你现在读到哪；事件日志像读书笔记：记录你之前翻过哪些页、在哪里停过、为什么回头。", "gold")}
  `,
  "gold",
  "第六课 · 执行记录",
);

// 58. Artifacts and dependencies
page(
  "Artifact：它不是一条孤零零的路线，而是一份带版本的施工图",
  "如果只保存 path，不保存它基于什么现场算出来，系统就无法判断它还能不能用。",
  `
  ${codeBlock(String.raw`{
  "artifact_id": "transfer-58e75ad6f16f4a4bbb247fec6d4f7fdb",
  "kind": "transfer_plan",
  "metadata": {
    "snapshot_ref": "world:0",
    "dependency_versions": [
      ["entity:drop-zone", 0],
      ["frame_graph", 0],
      ["map", 0],
      ["nav_occupancy:cup-red", 10334876803476250410]
    ],
    "frame_graph_revision": 0,
    "robot_state_epoch": 0,
    "robot_model_version": "planar-2link-v1",
    "collision_model_version": "grid-circle-v1",
    "payload_transform_hash": "cup-red:0.160000",
    "assumptions": ["gripper is empty at planning time"],
    "random_seed": 0
  },
  "object_id": "cup-red",
  "destination_id": "drop-zone",
  "navigation_plan_ref": "nav-9cc2bc1deeef4bd59f89a58886e5499d",
  "placement_pose": {"x": 7.5, "y": 5.5, "z": 0.0, "yaw": 0.0, "frame": "world"}
}`)}
  ${tutorialTable(
    ["元数据", "它记住什么", "用来拦截什么问题"],
    [
      ["source snapshot", "从 world:0 哪张照片算出来", "不把旧现场计划当新现场。"],
      ["dependency_versions", "地图、实体、机器人、占用指纹的版本", "障碍物或机器人姿态变化。"],
      ["model versions", "机器人和碰撞模型版本", "算法/模型升级后继续误用旧计划。"],
      ["payload_transform_hash", "载荷几何身份", "拿了不同尺寸的物体仍沿用旧轨迹。"],
      ["assumptions / seed", "计算时的前提和随机种子", "复现、审计和解释规划结果。"],
    ],
    "artifact-field-table",
  )}
  <div class="artifact-freshness-rule"><b>新鲜度判断：</b><span>当前快照的依赖版本全部匹配 → 工件可消费；任意关键依赖不匹配 → 工件过期，发布新版本。</span></div>
  `,
  "gold",
  "第七课 · 规划工件",
);

// 59. ActionRequest and Observation encoding
page(
  "ActionRequest 和 Observation：请求不是结果",
  "下面把“执行抓取”从规划工件一路翻译到机器人接口。",
  `
  <div class="request-observation-large">
    <div class="large-request">
      <h3>PhysicalSkill 构造请求</h3>
      ${codeBlock(String.raw`{
  "request_id": "tree:demo-place:program/demo-place/pick/grasp:attempt:1",
  "action_name": "ExecuteGrasp",
  "commands": [
    {"type": "ArmPathCommand", "path": "pick.approach_path"},
    {"type": "GripperCommand", "close": true, "object_id": "cup-red"}
  ],
  "resources": ["arm", "gripper"],
  "artifact_refs": ["pick-efbdc179e47a459d94fe036166172764"],
  "preconditions": [
    {"name": "base_near", "tolerance": 0.2},
    {"name": "joints_near", "tolerance": 0.00001}
  ]
}`)}
    </div>
    <div class="large-observation">
      <h3>Backend 执行后返回观察</h3>
      ${codeBlock(String.raw`{
  "robot": {
    "base_pose": {"x": 2.5, "y": 2.5, "yaw": 0.0},
    "joints": [0.486695, -0.973390],
    "gripper_open": false,
    "held_object_id": "cup-red",
    "state_epoch": 2
  },
  "entities": ["cup-red at (2.5, 2.5)"],
  "frame_graph_revision": 0
}`)}
    </div>
  </div>
  ${tutorialTable(
    ["ActionRequest 字段", "作用"],
    [
      ["request_id", "把一次动作和节点、attempt、事务记录关联起来。"],
      ["commands", "真正要交给 backend 的类型化命令序列。"],
      ["resources", "申请 arm、base、gripper 等资源租约。"],
      ["artifact_refs", "声明动作依赖哪些不可变工件。"],
      ["preconditions", "Runtime 在当前快照上先验证的守卫。"],
    ],
    "request-field-table",
  )}
  ${callout("你要记住的顺序", "Skill 只能构造请求；HarnessRuntime 才检查新鲜度、守卫、资源和事务；Backend 执行后必须观察；WorldModel 再生成新版本。", "green")}
  `,
  "teal",
  "第八课 · 物理边界",
);

// 60. Diagnostic and RepairProposal
page(
  "Diagnostic 和 RepairProposal：失败如何变成结构化工作",
  "一个错误字符串只能告诉你“坏了”；结构化诊断要告诉系统“哪里坏、依据什么、能不能修”。",
  `
  <div class="diagnostic-proposal">
    <div class="diagnostic-paper">
      <h3>Diagnostic</h3>
      ${codeBlock(String.raw`{
  "code": "ROUTE_BLOCKED",
  "message": "candidate obstacle blocks route",
  "details": {
    "failed_node_id": "program/demo-place/plan-transfer",
    "snapshot_ref": "world:0",
    "witness": "movable-crate",
    "counterfactual_route": true
  },
  "retryable": true
}`)}
      <p>它描述已经发生的失败，并附上证据和是否值得尝试恢复。</p>
    </div>
    <div class="proposal-paper">
      <h3>RepairProposal</h3>
      ${codeBlock(String.raw`{
  "entry_node_id": ".../repair-0/route-blocked",
  "rationale": "relocate movable-crate to parking-zone, then refresh PickPlan",
  "kind": "repair",
  "invalidates_artifacts": [],
  "delta": {
    "new_nodes": ["RouteBlockedRepair", "Place(crate, parking-zone)", "PlanPick"],
    "new_edges": ["failed-node --repair--> entry"]
  }
}`)}
      <p>它只是“建议挂什么修复子树”；真正挂载仍由 Kernel 完成。</p>
    </div>
  </div>
  ${tutorialTable(
    ["失败类型", "说明", "下一步通常是什么"],
    [
      ["ROUTE_BLOCKED", "路线被具体障碍物挡住", "验证可移动 witness，执行递归搬移修复。"],
      ["STALE_PLAN", "工件依赖版本不匹配", "重新规划，不重复执行旧工件。"],
      ["HOLD_LOST", "观察到物体不再被持有", "根据现场事实重新对账或进入人工恢复。"],
      ["OUTCOME_UNKNOWN", "证据不足，无法确认结果", "先观察/人工确认，不能直接宣称成功。"],
      ["GUARD_FAILED", "动作前置条件不成立", "不执行副作用，重新定位或修复前置条件。"],
    ],
    "diagnostic-table",
  )}
  ${callout("两条边界", "Verifier 负责判断和给证据，不负责规划；RepairResolver 负责提出 GraphDelta，不负责直接写 Store。这样修复本身也必须被 Kernel 管住。", "repair")}
  `,
  "repair",
  "第九课 · 失败与修复",
);

// 61. Full normal trace
page(
  "完整例子一：正常 Place 从意图走到完成",
  "把前面所有对象串在一起，先看没有故障时的最短路径。",
  `
  <div class="full-trace-head">
    <div><b>输入</b><span>Place(cup-red → drop-zone)</span></div>
    <div><b>结果</b><span>succeeded</span></div>
    <div><b>范围</b><span>world:0 → world:7</span></div>
  </div>
  ${tutorialTable(
    ["序号", "树节点 / 组件", "产生或读取的对象", "发生了什么"],
    [
      ["1", "LLM / Planner Adapter", "TaskProgram", "把人话规范化为 place + 两个精确选择器。"],
      ["2", "Compiler", "TaskNodeSpec(root)", "只编译出 program/demo-place 根节点。"],
      ["3", "Kernel + Decomposer", "GraphDelta", "展开 Place，挂 7 个直接 child。"],
      ["4", "ResolveAndInspect", "BindingArtifact", "确认 cup-red 和 drop-zone 存在且绑定稳定。"],
      ["5", "PlanPick", "NavigationPlan + PickPlan", "基于 world:0 计算抓取位置和机械臂路径。"],
      ["6", "PlanTransfer", "NavigationPlan + TransferPlan", "提前检查持物运输路线。"],
      ["7", "Pick", "5 个物理事务中的前 2 个", "到抓取位、闭合夹爪，Observation 确认持有。"],
      ["8", "TransferHeld", "第 3、4 个物理事务", "切换运输姿态并带杯移动到目标附近。"],
      ["9", "Release + Verify", "第 5 个物理事务 + 新快照", "释放杯子，验证它落在接受半径内。"],
    ],
    "full-trace-table",
  )}
  <div class="normal-result-strip">
    ${stat("16", "最终节点", "blue", "业务步骤和验证节点")}
    ${stat("15", "child 边", "teal", "树结构关系")}
    ${stat("0", "repair 边", "green", "没有发生故障")}
    ${stat("127", "事件", "purple", "可回放执行过程")}
    ${stat("5", "物理事务", "gold", "每个都有观察")}
  </div>
  ${callout("读法", "先看树的业务顺序，再看每个物理节点的请求和观察；不要把 127 个事件误解成 127 次机器人动作。", "blue")}
  `,
  "blue",
  "第十课 · 完整追踪",
);

// 62. Full blocked trace
page(
  "完整例子二：路线阻塞时，任务树怎样递归长出来？",
  "这是最能体现架构价值的例子：系统没有偷偷绕过失败，而是把修复本身变成一棵可执行子树。",
  `
  ${traceSvg()}
  <div class="blocked-trace-steps">
    <div><b>1. 先发现</b><span>PlanTransfer 在 world:0 发现 movable-crate 挡路，并用反事实 A* 证明“移走这个具体箱子后会恢复路线”。</span></div>
    <div><b>2. 再提案</b><span>Diagnostic=ROUTE_BLOCKED；RepairResolver 提出 RouteBlockedRepair，不直接改树。</span></div>
    <div><b>3. 挂修复</b><span>Kernel 从失败节点挂一条 repair 边，入口是 repair-0/route-blocked。</span></div>
    <div><b>4. 递归执行</b><span>修复子树内部再次执行 Place(movable-crate → parking-zone)，所以它也有解析、规划、抓取、运输、释放、验证。</span></div>
    <div><b>5. 刷新再试</b><span>箱子到 parking-zone 后得到 world:7，重新发布依赖新世界的 PickPlan / TransferPlan，重试原任务。</span></div>
  </div>
  ${codeBlock(String.raw`Place(cup-red -> drop-zone)
├─ ResolveAndInspect
├─ PlanPick
├─ PlanTransfer
│  └─ RouteBlockedRepair [repair]
│     ├─ Place(movable-crate -> parking-zone)
│     │  ├─ ResolveAndInspect
│     │  ├─ PlanPick
│     │  ├─ PlanTransfer
│     │  ├─ Pick
│     │  ├─ TransferHeld
│     │  ├─ Release
│     │  └─ VerifyPlaceGoal
│     └─ PlanPick
├─ Pick
├─ TransferHeld
├─ Release
└─ VerifyPlaceGoal`)}
  `,
  "repair",
  "第十课 · 递归修复",
);

// 63. Full blocked trace metrics and continuous session
page(
  "把故障案例的数字和连续任务放在一起看",
  "树可以隔离，物理世界不能凭空重置；这两个事实需要同时记住。",
  `
  <div class="blocked-metrics">
    ${stat("34", "最终节点", "blue", "原任务 + 修复子树")}
    ${stat("33", "总边数", "teal", "其中 1 条 repair 边")}
    ${stat("1", "repair 边", "repair", "从 PlanTransfer 挂出")}
    ${stat("275", "事件", "purple", "包含挂修复和两段执行")}
    ${stat("10", "物理事务", "gold", "先搬箱子 5 次，再搬杯子 5 次")}
  </div>
  <div class="continuous-two-tasks">
    <div class="session-column">
      <b>Task 0001</b>
      <strong>Place(movable-crate → parking-zone)</strong>
      <span>root = program/task-0001</span>
      <span>5 次物理事务：world:0 → world:7</span>
      <small>任务树执行完后，箱子真的留在 parking-zone。</small>
    </div>
    <div class="session-world-link"><span>同一个物理世界继续</span><b>world:7 →</b></div>
    <div class="session-column session-column-blue">
      <b>Task 0002</b>
      <strong>Place(cup-red → drop-zone)</strong>
      <span>root = program/task-0002</span>
      <span>5 次物理事务：world:7 → world:14</span>
      <small>新任务树干净，但读取的是上一个任务留下的现场。</small>
    </div>
  </div>
  ${tutorialTable(
    ["每个任务新建", "跨任务复用", "为什么这样做"],
    [
      ["TaskTreeStore / Kernel / Inspector", "WorldModel / HarnessRuntime", "树之间不混节点，现场却保持连续。"],
      ["执行栈 / 节点运行态 / 事件", "RobotBackend / 事务账本", "任务历史隔离，真实资源和动作历史连续。"],
      ["root = program/task-0001 或 0002", "同一场景状态", "可以单独查看每棵树，也能解释世界如何演进。"],
    ],
    "session-table",
  )}
  ${callout("当前实现的边界", "连续会话目前是串行提交和串行执行，不要把它描述成已经完成了并行调度。若未来并行，需要新增资源仲裁、冲突解决和调度层。", "gold")}
  `,
  "teal",
  "第十一课 · 连续会话",
);

// 64. Final learning checklist
page(
  "学完后的自测：你是否真的看懂了？",
  "不用背所有类名；能用下面的问题解释系统，就说明主线已经掌握。",
  `
  <div class="self-check-list">
    <div><b>01</b><strong>为什么 LLM 不能直接调机器人？</strong><span>因为模型输出要先变成受约束的 TaskProgram，副作用必须经过 Runtime。</span></div>
    <div><b>02</b><strong>谁是运行时任务树的唯一权威？</strong><span>TaskTreeStore 保存状态，TaskTreeKernel 是唯一生命周期解释器和唯一写入者。</span></div>
    <div><b>03</b><strong>世界状态怎样推进？</strong><span>物理命令后产生 Observation，WorldModel.ingest 生成新的 WorldSnapshot 版本。</span></div>
    <div><b>04</b><strong>为什么旧计划不能直接继续用？</strong><span>Artifact 记录快照和依赖版本；当前世界不匹配时，计划过期。</span></div>
    <div><b>05</b><strong>为什么失败要挂 repair 边？</strong><span>让修复动作成为可见、可验证、可回放的任务，而不是隐藏副作用。</span></div>
    <div><b>06</b><strong>为什么 A*、IK、RRT 不是任务节点？</strong><span>它们是能力内部算法；任务树只记录对业务有意义的操作和验证。</span></div>
  </div>
  ${callout("自测标准", "如果你能用自己的话解释上面六个问题，说明你已经抓住主线；如果某一题说不清，就回到对应的课程页查对象和例子。", "teal")}
  <div class="thank-line">Task Recursive Tree Agent · 个人学习详解版</div>
  `,
  "teal",
  "学习总结",
);

// 65. Final learning map
page(
  "一张图复习：从人话到可验证的物理结果",
  "把整套架构重新压缩成六个问题，你以后读代码就按这个顺序定位。",
  `
  <div class="final-learning-map">
    <div class="final-learning-row row-intent"><b>人话</b><span>我想把哪个对象放到哪里？这会变成什么样的 TaskProgram？</span></div>
    <div class="final-learning-row row-world"><b>事实</b><span>当前 world:N 里对象、机器人、地图和版本号是什么状态？</span></div>
    <div class="final-learning-row row-tree"><b>控制</b><span>Kernel 按什么树、什么 phase、什么边继续推进？谁有权写入 Store？</span></div>
    <div class="final-learning-row row-artifact"><b>计划</b><span>这个 Artifact 基于哪个快照、哪些依赖和哪些模型版本？</span></div>
    <div class="final-learning-row row-action"><b>动作</b><span>请求经过哪些前置守卫、资源租约、事务和唯一后端入口？</span></div>
    <div class="final-learning-row row-repair"><b>证据</b><span>Observation 证明了什么？失败后修复是否仍然以 repair 子树回到同一套执行器？</span></div>
  </div>
  <div class="mental-model-equation"><span>意图</span><b>→</b><span>任务树</span><b>→</b><span>版本化工件</span><b>→</b><span>ActionRequest</span><b>→</b><span>Observation</span><b>→</b><span>完成或修复</span></div>
  ${callout("最后一句话", "这套架构的核心不是让模型“更会想”，而是让每一次决定都有来源、每一次动作都有边界、每一个结果都有证据、每一次失败都有可见的下一步。", "teal")}
  <div class="thank-line">Task Recursive Tree Agent · 个人学习详解版</div>
  `,
  "teal",
  "学习总结 · 总复习",
);

const css = `
@page { size: A4; margin: 0; }
:root {
  color-scheme: light;
  --ink: ${COLORS.ink};
  --muted: ${COLORS.muted};
  --line: ${COLORS.line};
  --paper: #ffffff;
  --page: ${COLORS.light};
  --coral: ${COLORS.coral};
  --teal: ${COLORS.teal};
  --gold: ${COLORS.gold};
  --blue: ${COLORS.blue};
  --purple: ${COLORS.purple};
  --green: ${COLORS.green};
  --repair: ${COLORS.repair};
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--page); color: var(--ink); }
body {
  font-family: "Microsoft YaHei", "Noto Sans SC", "Noto Sans CJK SC", "DengXian", Arial, sans-serif;
  font-size: 10.5pt;
  line-height: 1.52;
  letter-spacing: 0;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}
code, pre, .source-map code, .code-block {
  font-family: Consolas, "Cascadia Mono", "DengXian", monospace;
}
.page {
  position: relative;
  width: 210mm;
  min-height: 297mm;
  padding: 14mm 15mm 14mm;
  background: var(--paper);
  overflow: hidden;
  page-break-after: always;
  break-after: page;
}
.page:last-child { page-break-after: auto; break-after: auto; }
.page::before {
  content: "";
  position: absolute;
  left: 0; top: 0; bottom: 0; width: 7px;
  background: var(--teal);
}
.page-coral::before { background: var(--coral); }
.page-blue::before { background: var(--blue); }
.page-gold::before { background: var(--gold); }
.page-purple::before { background: var(--purple); }
.page-green::before { background: var(--green); }
.page-repair::before { background: var(--repair); }
.page-red::before { background: var(--red); }
.page-header { display: flex; align-items: center; gap: 10px; color: var(--muted); font-size: 8.5pt; }
.page-kicker { white-space: nowrap; }
.page-rule { height: 1px; background: var(--line); flex: 1; }
h1, h2, h3, p { margin-top: 0; }
h1 { margin: 9mm 0 2mm; font-size: 23pt; line-height: 1.18; letter-spacing: 0; }
.page-subtitle { color: var(--muted); font-size: 12pt; margin-bottom: 6mm; }
h3 { font-size: 13pt; margin-bottom: 2mm; }
.page-body { position: relative; }
.page-footer {
  position: absolute; left: 15mm; right: 15mm; bottom: 7mm;
  display: flex; justify-content: space-between;
  border-top: 1px solid var(--line); padding-top: 2mm;
  color: #8a989e; font-size: 8pt;
}
.callout { margin: 5mm 0; padding: 3.6mm 4.2mm; border-left: 4px solid var(--teal); background: ${COLORS.tealLight}; border-radius: 0 6px 6px 0; }
.callout-coral { border-color: var(--coral); background: ${COLORS.coralLight}; }
.callout-blue { border-color: var(--blue); background: ${COLORS.blueLight}; }
.callout-gold { border-color: var(--gold); background: ${COLORS.goldLight}; }
.callout-purple { border-color: var(--purple); background: ${COLORS.purpleLight}; }
.callout-green { border-color: var(--green); background: ${COLORS.greenLight}; }
.callout-repair, .callout-red { border-color: var(--repair); background: ${COLORS.repairLight}; }
.callout-title { font-weight: 700; margin-bottom: 1mm; }
.callout-text { color: var(--ink); }
.pill { display: inline-block; border-radius: 999px; padding: 1px 7px; font-size: 8pt; line-height: 1.5; white-space: nowrap; }
.pill-teal { background: ${COLORS.tealLight}; color: ${COLORS.teal}; }
.pill-coral { background: ${COLORS.coralLight}; color: ${COLORS.coral}; }
.pill-gold { background: ${COLORS.goldLight}; color: #8b6419; }
.pill-blue { background: ${COLORS.blueLight}; color: ${COLORS.blue}; }
.pill-purple { background: ${COLORS.purpleLight}; color: ${COLORS.purple}; }
.pill-green { background: ${COLORS.greenLight}; color: ${COLORS.green}; }
.pill-repair { background: ${COLORS.repairLight}; color: ${COLORS.repair}; }
.bullets { margin: 2mm 0 0; padding-left: 5mm; }
.bullets li { margin: 1.2mm 0; }
.bullets li::marker { color: var(--teal); }
.diagram-svg { width: 100%; height: auto; display: block; }
.svg-section { font: 700 16px "Microsoft YaHei", "Noto Sans SC", sans-serif; fill: ${COLORS.ink}; }
.svg-label { font: 600 14px "Microsoft YaHei", "Noto Sans SC", sans-serif; fill: ${COLORS.ink}; }
.svg-small { font: 12px "Microsoft YaHei", "Noto Sans SC", sans-serif; fill: ${COLORS.muted}; }
.repair-text { fill: ${COLORS.repair}; }
.gate-number { font: 700 17px Arial, sans-serif; fill: #fff; }
.four-steps { display: grid; grid-template-columns: repeat(4, 1fr); gap: 3mm; margin: 8mm 0; }
.big-step { min-height: 29mm; padding: 4mm; background: ${COLORS.light}; border-top: 4px solid var(--teal); }
.big-step:nth-child(1) { border-color: var(--coral); }
.big-step:nth-child(2) { border-color: var(--blue); }
.big-step:nth-child(3) { border-color: var(--gold); }
.big-step:nth-child(4) { border-color: var(--repair); }
.big-step b { display: block; font-size: 21pt; line-height: 1; margin-bottom: 2mm; }
.big-step strong { display: block; font-size: 13pt; }
.big-step span { display: block; color: var(--muted); font-size: 9pt; margin-top: 1mm; }
.split-box { display: grid; grid-template-columns: 1fr 1fr; gap: 8mm; border-top: 1px solid var(--line); padding-top: 5mm; }
.reading-route { display: grid; grid-template-columns: repeat(5, 1fr); gap: 2mm; margin-top: 6mm; }
.reading-route div { padding: 2.5mm; border-top: 3px solid var(--teal); background: ${COLORS.light}; }
.reading-route div:nth-child(1) { border-color: var(--coral); }
.reading-route div:nth-child(2) { border-color: var(--blue); }
.reading-route div:nth-child(3) { border-color: var(--gold); }
.reading-route div:nth-child(4) { border-color: var(--repair); }
.reading-route div:nth-child(5) { border-color: var(--green); }
.reading-route b, .reading-route span { display: block; }
.reading-route span { color: var(--muted); font-size: 7.8pt; margin-top: 1mm; line-height: 1.35; }
.example-hero { padding: 8mm; background: ${COLORS.coralLight}; border-left: 5px solid var(--coral); margin-bottom: 6mm; }
.example-quote { font-size: 20pt; font-weight: 700; line-height: 1.35; }
.example-translation { color: var(--muted); margin-top: 3mm; }
.question-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; }
.node-card { border: 1px solid var(--line); border-left: 4px solid var(--teal); padding: 4mm; background: #fff; break-inside: avoid; }
.node-coral { border-left-color: var(--coral); }
.node-blue { border-left-color: var(--blue); }
.node-gold { border-left-color: var(--gold); }
.node-green { border-left-color: var(--green); }
.node-repair { border-left-color: var(--repair); }
.node-top { display: flex; align-items: center; justify-content: space-between; gap: 3mm; }
.node-detail { color: var(--muted); margin-top: 2mm; }
.node-extra { margin-top: 2mm; font-size: 9pt; color: var(--muted); }
.compare { display: grid; grid-template-columns: 1fr 1fr; gap: 7mm; }
.compare-col { padding: 5mm; border: 1px solid var(--line); min-height: 84mm; }
.compare-bad { background: #fff7f4; border-top: 5px solid var(--coral); }
.compare-good { background: #f4fbf8; border-top: 5px solid var(--teal); }
.compare-title { font-size: 14pt; font-weight: 700; margin-bottom: 4mm; }
.compare-flow { padding: 3mm; background: #fff; border: 1px dashed var(--line); font-family: Consolas, monospace; font-size: 9pt; margin-bottom: 3mm; }
.warning-line, .principle-banner, .extension-rule { margin-top: 6mm; padding: 4mm; background: ${COLORS.goldLight}; border: 1px solid #ead49a; }
.analogy-table { display: grid; grid-template-columns: 1fr 2.8fr; border: 1px solid var(--line); margin-top: 3mm; }
.analogy-table div { display: contents; }
.analogy-table b, .analogy-table span { padding: 2.5mm 3mm; border-bottom: 1px solid var(--line); }
.analogy-table b { background: ${COLORS.light}; }
.conversion-row { display: flex; align-items: center; gap: 2mm; margin: 8mm 0; }
.conversion-input, .conversion-card { flex: 1; min-height: 32mm; padding: 3mm; border: 1px solid var(--line); background: #fff; }
.conversion-input { background: ${COLORS.coralLight}; border-color: var(--coral); }
.conversion-card:nth-of-type(3) { background: ${COLORS.blueLight}; }
.conversion-card small, .conversion-input small { display: block; color: var(--muted); font-size: 8pt; }
.conversion-card strong, .conversion-input strong { display: block; margin: 2mm 0; font-size: 12pt; }
.conversion-card span, .conversion-input span { color: var(--muted); font-size: 8.5pt; }
.arrow-label { display: flex; flex-direction: column; align-items: center; gap: 1mm; color: var(--muted); font-size: 8pt; }
.arrow-label i { font-style: normal; font-size: 19pt; line-height: 1; color: var(--teal); }
.arrow-coral i { color: var(--coral); }
.arrow-blue i { color: var(--blue); }
.mini-table, .dependency-table, .gap-table, .isolation-table { border: 1px solid var(--line); }
.mini-row, .dep-row, .gap-row, .isolation-row { display: grid; grid-template-columns: 1fr 1.3fr 1.4fr; }
.mini-row > *, .dep-row > *, .gap-row > *, .isolation-row > * { padding: 2.5mm 3mm; border-bottom: 1px solid var(--line); }
.mini-row:last-child > *, .dep-row:last-child > *, .gap-row:last-child > *, .isolation-row:last-child > * { border-bottom: 0; }
.mini-head, .dep-head, .gap-head, .isolation-head { background: ${COLORS.light}; font-weight: 700; }
.place-lanes { display: grid; gap: 3mm; }
.lane { display: grid; grid-template-columns: 24mm 1fr; padding: 3.2mm 4mm; border-left: 5px solid var(--teal); background: ${COLORS.tealLight}; }
.lane b { font-size: 12pt; }
.lane-coral { border-color: var(--coral); background: ${COLORS.coralLight}; }
.lane-blue { border-color: var(--blue); background: ${COLORS.blueLight}; }
.lane-gold { border-color: var(--gold); background: ${COLORS.goldLight}; }
.lane-green { border-color: var(--green); background: ${COLORS.greenLight}; }
.lane-repair { border-color: var(--repair); background: ${COLORS.repairLight}; }
.formula { display: flex; justify-content: center; gap: 3mm; align-items: center; margin: 8mm 0 4mm; font-size: 13pt; flex-wrap: wrap; }
.formula span { padding: 2mm 3mm; background: ${COLORS.light}; border: 1px solid var(--line); }
.formula span:first-child { background: ${COLORS.coralLight}; border-color: var(--coral); font-weight: 700; }
.formula b { color: var(--muted); }
.plain-paragraph { color: var(--muted); margin: 4mm 0; }
.object-cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4mm; }
.tree-analogy { display: grid; grid-template-columns: 1fr 34mm 1fr; align-items: center; gap: 4mm; }
.todo-list, .tree-list { padding: 5mm; min-height: 65mm; border: 1px solid var(--line); background: ${COLORS.light}; line-height: 2; }
.tree-list { background: ${COLORS.blueLight}; border-color: var(--blue); }
.todo-title { font-weight: 700; border-bottom: 1px solid var(--line); margin-bottom: 2mm; }
.tree-list small { display: block; color: var(--muted); font-size: 8pt; padding-left: 5mm; line-height: 1.4; }
.tree-arrow { text-align: center; color: var(--blue); font-weight: 700; }
.definition-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 3mm; margin-top: 6mm; }
.definition-grid div { display: grid; grid-template-columns: 28mm 1fr; padding: 3mm; border: 1px solid var(--line); }
.definition-grid span { color: var(--muted); }
.three-column { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4mm; }
.concept-box { min-height: 60mm; padding: 4mm; border-top: 5px solid var(--blue); background: ${COLORS.blueLight}; }
.concept-green { border-color: var(--green); background: ${COLORS.greenLight}; }
.concept-gold { border-color: var(--gold); background: ${COLORS.goldLight}; }
.concept-box strong { display: block; margin-bottom: 2mm; }
.concept-box p { color: var(--muted); }
.concept-box small { color: var(--muted); }
.wrong-model { margin-top: 7mm; padding: 4mm; background: #fff7f4; border: 1px solid #f0c8ba; }
.wrong-label { color: var(--coral); font-weight: 700; }
.wrong-json { margin: 2mm 0; padding: 3mm; background: ${COLORS.dark}; color: #f5f7f7; font-family: Consolas, monospace; font-size: 9pt; }
.world-board { border: 1px solid var(--green); background: ${COLORS.greenLight}; padding: 5mm; }
.world-title { font-weight: 700; font-size: 14pt; margin-bottom: 3mm; }
.world-items { display: grid; grid-template-columns: 1fr 1fr; gap: 2mm 6mm; }
.world-items div { display: grid; grid-template-columns: 30mm 1fr; padding: 2mm; background: #fff; }
.world-items span { color: var(--muted); }
.world-version { display: flex; align-items: center; justify-content: center; gap: 7mm; margin: 8mm 0; }
.version-node { padding: 4mm 8mm; border: 1px solid var(--line); background: ${COLORS.light}; text-align: center; }
.version-node strong { display: block; font-size: 15pt; }
.version-node span { color: var(--muted); font-size: 9pt; }
.version-current { border-color: var(--green); background: ${COLORS.greenLight}; }
.version-line { color: var(--muted); text-align: center; }
.artifact-paper { max-width: 140mm; margin: 5mm auto; padding: 5mm; border: 1px solid #d9c58d; background: #fffdf5; box-shadow: 2px 3px 0 #eadfb9; }
.artifact-head { display: flex; justify-content: space-between; align-items: center; font-weight: 700; font-size: 14pt; padding-bottom: 3mm; border-bottom: 1px solid #eadfb9; }
.artifact-row { display: grid; grid-template-columns: 35mm 1fr; padding: 2.5mm 0; border-bottom: 1px dashed #eadfb9; }
.artifact-row:last-child { border-bottom: 0; }
.artifact-row > *:last-child { color: var(--muted); }
.artifact-analogy { display: flex; gap: 5mm; align-items: center; margin: 7mm auto; max-width: 140mm; padding: 4mm; background: ${COLORS.goldLight}; }
.stamp { width: 18mm; height: 18mm; border: 3px solid var(--gold); color: var(--gold); display: grid; place-items: center; font-weight: 700; transform: rotate(-8deg); }
.artifact-analogy p { margin: 1mm 0 0; color: var(--muted); }
.artifact-rules { display: grid; grid-template-columns: repeat(3, 1fr); gap: 3mm; }
.artifact-rules div { padding: 3mm; border: 1px solid var(--line); }
.artifact-rules span { display: block; color: var(--muted); margin-top: 1mm; }
.connection-flow { display: flex; align-items: center; gap: 2mm; margin: 9mm 0; }
.connection-block { flex: 1; min-height: 30mm; padding: 4mm; text-align: center; border: 1px solid var(--line); }
.connection-block b, .connection-block span { display: block; }
.connection-block span { color: var(--muted); margin-top: 2mm; font-size: 9pt; }
.connection-blue { background: ${COLORS.blueLight}; border-color: var(--blue); }
.connection-green { background: ${COLORS.greenLight}; border-color: var(--green); }
.connection-gold { background: ${COLORS.goldLight}; border-color: var(--gold); }
.connection-coral { background: ${COLORS.coralLight}; border-color: var(--coral); }
.connection-arrow { color: var(--muted); font-size: 9pt; }
.loop-card { display: flex; align-items: center; justify-content: center; gap: 2mm; flex-wrap: wrap; border: 1px dashed var(--line); padding: 4mm; }
.loop-title { width: 100%; text-align: center; font-weight: 700; margin-bottom: 1mm; }
.loop-step { padding: 2mm 3mm; background: ${COLORS.light}; }
.legend-row { display: flex; justify-content: center; gap: 7mm; margin-top: 2mm; font-size: 8.5pt; color: var(--muted); }
.legend-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 1.5mm; }
.dot-coral { background: var(--coral); }.dot-blue { background: var(--blue); }.dot-gold { background: var(--gold); }.dot-green { background: var(--green); }
.input-pipeline { display: flex; align-items: center; gap: 2mm; margin: 8mm 0; }
.input-box { flex: 1; padding: 4mm; min-height: 26mm; border: 1px solid var(--line); background: ${COLORS.coralLight}; }
.input-box b, .input-box span { display: block; }.input-box span { color: var(--muted); font-size: 9pt; margin-top: 1mm; }
.pipeline-arrow { font-size: 18pt; color: var(--teal); }
.boundary-box { display: grid; grid-template-columns: 1fr 1fr; gap: 6mm; }
.boundary-yes, .boundary-no { padding: 4mm; border: 1px solid var(--line); }
.boundary-yes { background: ${COLORS.greenLight}; border-top: 5px solid var(--green); }.boundary-no { background: ${COLORS.repairLight}; border-top: 5px solid var(--repair); }
.llm-split { display: grid; grid-template-columns: 1fr 1fr; gap: 6mm; }
.llm-can, .llm-cannot { padding: 5mm; min-height: 86mm; border: 1px solid var(--line); }
.llm-can { background: ${COLORS.greenLight}; border-top: 5px solid var(--green); }.llm-cannot { background: ${COLORS.repairLight}; border-top: 5px solid var(--repair); }
.llm-heading { font-size: 14pt; font-weight: 700; margin-bottom: 2mm; }
.permission-analogy { display: flex; align-items: center; gap: 4mm; justify-content: center; padding: 5mm; margin-top: 7mm; background: ${COLORS.blueLight}; }
.lock-icon { padding: 2mm 3mm; background: var(--blue); color: white; font-weight: 700; }
.delta-flow { display: flex; align-items: center; gap: 2mm; margin: 9mm 0; }
.delta-box { flex: 1; min-height: 34mm; padding: 4mm; border: 1px solid var(--line); background: ${COLORS.blueLight}; text-align: center; }
.delta-box b, .delta-box span { display: block; }.delta-box span { color: var(--muted); font-size: 8.5pt; margin-top: 2mm; }
.delta-code-box { background: ${COLORS.goldLight}; border-color: var(--gold); }
.delta-arrow { color: var(--muted); font-size: 8.5pt; white-space: nowrap; }
.rules-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 3mm; }.rules-grid div { padding: 3mm; background: ${COLORS.light}; border-left: 4px solid var(--blue); }
.ownership-visual { display: flex; align-items: center; gap: 4mm; justify-content: center; margin: 8mm 0; }
.owner-box { width: 58mm; min-height: 48mm; padding: 4mm; border: 2px solid var(--blue); background: ${COLORS.blueLight}; }
.owner-store { border-color: var(--purple); background: ${COLORS.purpleLight}; }.owner-badge { font-size: 8pt; color: var(--muted); }.owner-box h3 { margin: 2mm 0; }.owner-box p { color: var(--muted); font-size: 9pt; }
.ownership-line { text-align: center; color: var(--muted); font-size: 8.5pt; }.ownership-line::first-line { color: var(--blue); }
.reader-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 2mm; text-align: center; padding: 4mm; background: ${COLORS.light}; border: 1px dashed var(--line); }
.reader-row span { padding: 2mm; background: #fff; }.reader-row small { grid-column: 1 / -1; color: var(--muted); margin-top: 1mm; }
.anti-pattern { margin-top: 7mm; padding: 4mm; background: #fff7f4; border-left: 5px solid var(--coral); }.anti-pattern span { display: block; color: var(--muted); margin-top: 1mm; }
.role-cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4mm; }.role-card { min-height: 85mm; padding: 4mm; border: 1px solid var(--line); }.role-decomposer { background: ${COLORS.blueLight}; border-top: 5px solid var(--blue); }.role-system { background: ${COLORS.tealLight}; border-top: 5px solid var(--teal); }.role-physical { background: ${COLORS.goldLight}; border-top: 5px solid var(--gold); }
.role-icon { width: 12mm; height: 12mm; display: grid; place-items: center; border-radius: 50%; background: var(--ink); color: #fff; font-weight: 700; }.role-card h3 { margin: 3mm 0 2mm; }.role-card p { color: var(--muted); min-height: 19mm; }.role-card strong, .role-card span { display: block; }.role-card span { color: var(--muted); margin-bottom: 2mm; }
.algorithm-note { display: grid; grid-template-columns: 45mm 1fr; gap: 3mm; padding: 4mm; margin-top: 7mm; background: ${COLORS.light}; border: 1px solid var(--line); }.algorithm-note span { color: var(--muted); }
.lifecycle-list { display: grid; grid-template-columns: repeat(5, 1fr); gap: 2mm; margin-top: 3mm; }.lifecycle-list div { padding: 2.5mm; background: ${COLORS.light}; border-top: 3px solid var(--teal); }.lifecycle-list span { display: block; color: var(--muted); font-size: 8.5pt; margin-top: 1mm; }
.tree-metrics { display: grid; grid-template-columns: repeat(5, 1fr); gap: 2.5mm; margin: 5mm 0; }.stat { padding: 3mm; background: ${COLORS.light}; border-top: 4px solid var(--teal); min-height: 26mm; }.stat-blue { border-color: var(--blue); background: ${COLORS.blueLight}; }.stat-teal { border-color: var(--teal); background: ${COLORS.tealLight}; }.stat-green { border-color: var(--green); background: ${COLORS.greenLight}; }.stat-gold { border-color: var(--gold); background: ${COLORS.goldLight}; }.stat-purple { border-color: var(--purple); background: ${COLORS.purpleLight}; }.stat-repair { border-color: var(--repair); background: ${COLORS.repairLight}; }.stat-value { font-size: 18pt; font-weight: 700; line-height: 1; }.stat-label { font-weight: 700; margin-top: 1.5mm; }.stat-note { color: var(--muted); font-size: 7.7pt; margin-top: 1mm; line-height: 1.3; }
.task-tree { margin: 3mm auto; max-width: 175mm; padding: 4mm 5mm; background: ${COLORS.dark}; color: #eef5f3; border-radius: 5px; font: 9.2pt/1.48 Consolas, "Microsoft YaHei", monospace; }
.tree-line { display: grid; grid-template-columns: 18mm 1fr 22mm; align-items: center; min-height: 6.3mm; }.tree-line[data-depth="1"] { padding-left: 8mm; }.tree-branch { color: #93aaa9; white-space: pre; }.tree-text { white-space: nowrap; }.tree-state { justify-self: end; font-size: 7.5pt; padding: .5mm 1.5mm; border-radius: 10px; }.state-success { background: #335d4f; color: #b9e7d2; }.tree-root .tree-text { color: #ffd68b; font-weight: 700; }.tree-system .tree-text { color: #b8d4f5; }.tree-physical .tree-text { color: #f7c7a8; }.tree-verify .tree-text { color: #bfe1b7; }.tree-group .tree-text { color: #d9c4ee; }.repair-indent { margin-top: 3mm; padding-top: 2mm; border-top: 1px dashed #7b4c62; }.repair-indent-2 { padding-left: 10mm; }.repair-indent .tree-branch { color: #eaa3bd; }.repair-text { color: var(--repair); }
.timeline { margin-top: 3mm; }.timeline-item { position: relative; display: grid; grid-template-columns: 12mm 1fr; gap: 4mm; min-height: 24mm; }.timeline-item:not(:last-child)::before { content: ""; position: absolute; left: 5.2mm; top: 11mm; bottom: -2mm; width: 1px; background: var(--line); }.timeline-dot { z-index: 1; width: 10.5mm; height: 10.5mm; display: grid; place-items: center; border-radius: 50%; background: var(--teal); color: #fff; font-weight: 700; }.timeline-repair .timeline-dot { background: var(--repair); }.timeline-body { padding: 1mm 0 4mm; border-bottom: 1px solid var(--line); }.timeline-title { font-weight: 700; font-size: 12pt; }.timeline-text { margin-top: 1mm; }.timeline-meta { color: var(--muted); font-size: 8.5pt; margin-top: 1mm; }
.world-strip { display: flex; align-items: stretch; gap: 2mm; padding: 5mm 0; }.world-chip { width: 27mm; display: flex; flex-direction: column; justify-content: center; text-align: center; padding: 3mm; background: ${COLORS.blueLight}; border: 1px solid var(--blue); }.world-chip b { font-size: 12pt; }.world-chip span { color: var(--muted); font-size: 8pt; }.world-chip-success { background: ${COLORS.greenLight}; border-color: var(--green); }.world-events { flex: 1; display: grid; grid-template-columns: repeat(5, 1fr); gap: 1.5mm; }.event-block { padding: 2.5mm; background: ${COLORS.goldLight}; border-top: 3px solid var(--gold); }.event-block b, .event-block span, .event-block small { display: block; }.event-block span { font-size: 8.2pt; margin-top: 1mm; }.event-block small { color: var(--muted); font-size: 7.2pt; margin-top: 2mm; }.metric-explain { display: grid; grid-template-columns: repeat(3, 1fr); gap: 3mm; margin: 5mm 0; }.metric-explain .stat { min-height: 27mm; }
.gate-bottom { margin-top: 2mm; padding: 3mm; text-align: center; background: ${COLORS.light}; color: var(--muted); }
.request-observation { display: flex; align-items: center; gap: 5mm; justify-content: center; margin: 8mm 0; }.request-card, .observation-card { width: 70mm; min-height: 55mm; padding: 4mm; border: 1px solid var(--line); }.request-card { background: ${COLORS.blueLight}; border-color: var(--blue); }.observation-card { background: ${COLORS.greenLight}; border-color: var(--green); }.ro-label { color: var(--muted); font-size: 8.5pt; }.request-card strong, .observation-card strong { display: block; margin: 2mm 0; font-size: 12pt; }.request-card span, .observation-card span, .request-card small, .observation-card small { display: block; color: var(--muted); }.request-card b, .observation-card b { display: block; margin-top: 2mm; font-size: 8.5pt; }.ro-arrow { color: var(--teal); font-size: 18pt; }.verification-chain { display: flex; justify-content: center; align-items: center; gap: 2mm; flex-wrap: wrap; padding: 4mm; background: ${COLORS.light}; border: 1px dashed var(--line); }.verification-chain span { padding: 1.5mm 2.5mm; background: #fff; border: 1px solid var(--line); font-size: 8.5pt; }.verification-chain b { color: var(--teal); }.diagnostic-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 3mm; margin-top: 5mm; }.diagnostic-grid div { padding: 3mm; background: ${COLORS.repairLight}; border-left: 4px solid var(--repair); }.diagnostic-grid span { display: block; color: var(--muted); margin-top: 1mm; }
.freshness-flow { display: flex; align-items: center; justify-content: center; gap: 3mm; margin: 9mm 0; }.freshness-state { width: 46mm; min-height: 43mm; padding: 4mm; text-align: center; border: 1px solid var(--green); background: ${COLORS.greenLight}; }.freshness-state b, .freshness-state span, .freshness-state i { display: block; }.freshness-state b { font-size: 14pt; }.freshness-state span { color: var(--muted); margin: 2mm 0; font-size: 9pt; }.freshness-state i { font-style: normal; color: var(--green); font-weight: 700; }.freshness-stale { background: ${COLORS.repairLight}; border-color: var(--repair); }.freshness-stale i { color: var(--repair); }.freshness-new { background: ${COLORS.blueLight}; border-color: var(--blue); }.freshness-new i { color: var(--blue); }.freshness-arrow { color: var(--muted); font-size: 8.5pt; text-align: center; }.dependency-table { margin-top: 5mm; }.dep-row { grid-template-columns: 1.1fr 1.4fr 1.5fr; }.dep-row > *:last-child { color: var(--muted); }
.scene-svg { max-height: 108mm; }.blocked-facts { display: grid; grid-template-columns: 1fr 1fr; gap: 3mm; margin-top: 4mm; }.blocked-facts div { display: grid; grid-template-columns: 28mm 1fr; padding: 3mm; background: ${COLORS.repairLight}; border-left: 4px solid var(--repair); }.blocked-facts span { color: var(--muted); }
.retry-matrix { border: 1px solid var(--line); }.retry-row { display: grid; grid-template-columns: 1.1fr 1.5fr 1.7fr; }.retry-row > * { padding: 3.2mm; border-bottom: 1px solid var(--line); }.retry-row:last-child > * { border-bottom: 0; }.retry-head { background: ${COLORS.light}; font-weight: 700; }.retry-row:not(.retry-head):not(.retry-good) span:first-child { color: var(--repair); }.retry-good { background: ${COLORS.greenLight}; }.retry-good span:first-child { color: var(--green); font-weight: 700; }
.repair-chain { display: flex; align-items: center; gap: 2mm; margin: 8mm 0; }.repair-step { flex: 1; min-height: 52mm; padding: 3mm; border: 1px solid var(--repair); background: ${COLORS.repairLight}; }.repair-step b { display: inline-grid; place-items: center; width: 8mm; height: 8mm; border-radius: 50%; color: #fff; background: var(--repair); }.repair-step strong { display: block; margin: 2mm 0; }.repair-step span { color: var(--muted); font-size: 8.5pt; }.repair-connector { color: var(--repair); font-size: 18pt; }.repair-contract { display: grid; grid-template-columns: 1fr 1fr; gap: 5mm; }.repair-contract > div { padding: 4mm; border: 1px solid var(--line); }.repair-contract > div:first-child { background: ${COLORS.greenLight}; border-top: 5px solid var(--green); }.repair-contract > div:last-child { background: ${COLORS.repairLight}; border-top: 5px solid var(--repair); }
.repair-tree-visual { margin: 6mm 0; }.repair-root { width: 85mm; margin: 0 auto; padding: 4mm; text-align: center; background: ${COLORS.blueLight}; border: 2px solid var(--blue); font-weight: 700; }.repair-root span { display: block; color: var(--repair); font-size: 8.5pt; margin-top: 1mm; }.repair-line { height: 12mm; width: 1px; background: var(--repair); margin: 0 auto; }.repair-branch-box { margin: 0 10mm; padding: 4mm; border: 2px dashed var(--repair); background: ${COLORS.repairLight}; }.repair-entry { font-weight: 700; color: var(--repair); margin-bottom: 4mm; }.repair-child-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 3mm; }.repair-child-grid div { padding: 3mm; background: #fff; border: 1px solid #e9c1ce; }.repair-child-grid span { display: block; color: var(--muted); font-size: 8.5pt; margin-top: 1mm; }
.limit-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; }.limit-card { min-height: 41mm; padding: 4mm; border: 1px solid var(--line); border-top: 5px solid var(--repair); background: ${COLORS.repairLight}; }.limit-card span { display: block; color: var(--muted); margin-top: 2mm; }.convergence-flow { margin-top: 8mm; padding: 5mm; text-align: center; background: ${COLORS.light}; font-size: 12pt; }.convergence-flow span { display: inline-block; padding: 2mm 3mm; background: #fff; border: 1px solid var(--line); }.convergence-flow b { color: var(--repair); margin: 0 2mm; }.convergence-flow small { display: block; color: var(--muted); margin-top: 3mm; font-size: 9pt; }.green-text { color: var(--green); font-weight: 700; }
.session-visual { display: grid; grid-template-columns: 1fr 1.1fr 1fr; align-items: center; gap: 4mm; margin: 9mm 0; }.session-task { min-height: 50mm; padding: 4mm; border: 1px solid var(--teal); background: ${COLORS.tealLight}; }.session-task strong, .session-task span, .session-task small { display: block; }.session-task strong { margin: 2mm 0; }.session-task span, .session-task small { color: var(--muted); font-size: 8.5pt; }.session-head { color: var(--teal); font-weight: 700; }.session-head-blue { color: var(--blue); }.session-world { min-height: 36mm; padding: 4mm; text-align: center; background: ${COLORS.goldLight}; border: 2px solid var(--gold); }.session-world b, .session-world span, .session-world i { display: block; }.session-world span { font-size: 13pt; font-weight: 700; margin: 2mm 0; }.session-world i { color: var(--muted); font-style: normal; font-size: 8.5pt; }.isolation-row { grid-template-columns: 1fr 1fr; }.isolation-row > *:last-child { color: var(--muted); }
.governance-stack { display: grid; gap: 2mm; margin: 7mm 0; }.gov-row { display: grid; grid-template-columns: 32mm 1fr; padding: 3.3mm 4mm; border-left: 5px solid var(--teal); background: ${COLORS.tealLight}; }.gov-row:nth-child(1) { border-color: var(--coral); background: ${COLORS.coralLight}; }.gov-row:nth-child(2) { border-color: var(--blue); background: ${COLORS.blueLight}; }.gov-row:nth-child(3) { border-color: var(--gold); background: ${COLORS.goldLight}; }.gov-row:nth-child(4) { border-color: var(--teal); background: ${COLORS.tealLight}; }.gov-row:nth-child(5) { border-color: var(--green); background: ${COLORS.greenLight}; }.gov-row:nth-child(6) { border-color: var(--repair); background: ${COLORS.repairLight}; }.safety-note { margin-top: 7mm; padding: 4mm; border: 1px solid var(--line); background: ${COLORS.light}; }.safety-note p { color: var(--muted); margin: 2mm 0 0; }
.principle-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 3mm; }.principle-grid div { display: grid; grid-template-columns: 30mm 1fr; gap: 2mm; padding: 3mm; border: 1px solid var(--line); background: ${COLORS.light}; }.principle-grid span { color: var(--muted); }.value-map { margin-top: 6mm; border: 1px solid var(--line); }.value-head, .value-map > div:not(.value-head) { display: grid; grid-template-columns: 1fr 1.5fr 1.3fr; }.value-map > div > * { padding: 2.7mm 3mm; border-bottom: 1px solid var(--line); }.value-map > div:last-child > * { border-bottom: 0; }.value-head { font-weight: 700; background: ${COLORS.light}; }
.business-cards { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; }.business-card { min-height: 42mm; padding: 4mm; border-top: 5px solid var(--teal); background: ${COLORS.tealLight}; }.business-card:nth-child(2) { border-color: var(--repair); background: ${COLORS.repairLight}; }.business-card:nth-child(3) { border-color: var(--blue); background: ${COLORS.blueLight}; }.business-card:nth-child(4) { border-color: var(--gold); background: ${COLORS.goldLight}; }.business-card p { color: var(--muted); margin: 2mm 0 0; }.kpi-strip { display: grid; grid-template-columns: repeat(4, 1fr); gap: 2mm; margin-top: 7mm; }.kpi-strip div { padding: 3mm; background: ${COLORS.light}; border-bottom: 3px solid var(--teal); }.kpi-strip strong, .kpi-strip span { display: block; }.kpi-strip span { color: var(--muted); font-size: 8.5pt; margin-top: 1mm; }
.gap-row { grid-template-columns: 1.25fr 1.9fr .35fr; }.gap-row > *:last-child { color: var(--muted); }.gap-row b { text-align: center; color: var(--repair); }.maturity-roadmap { display: flex; justify-content: center; align-items: center; gap: 4mm; margin-top: 8mm; padding: 4mm; background: ${COLORS.light}; }.maturity-roadmap span { padding: 2mm 3mm; background: #fff; border: 1px solid var(--line); }.maturity-roadmap b { color: var(--teal); }
.extension-columns { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4mm; }.extension-columns > div { padding: 4mm; min-height: 90mm; border: 1px solid var(--line); background: ${COLORS.blueLight}; }.extension-columns > div:nth-child(2) { background: ${COLORS.greenLight}; border-top: 5px solid var(--green); }.extension-columns > div:nth-child(3) { background: ${COLORS.goldLight}; border-top: 5px solid var(--gold); }.extension-columns > div:first-child { border-top: 5px solid var(--blue); }.extension-columns h3 { margin-bottom: 2mm; }
.glossary { display: grid; grid-template-columns: 1fr 1.8fr; border: 1px solid var(--line); }.glossary div { display: contents; }.glossary b, .glossary span { padding: 2.1mm 3mm; border-bottom: 1px solid var(--line); }.glossary b { background: ${COLORS.light}; font-family: Consolas, monospace; font-size: 9pt; }.glossary span { color: var(--muted); }.glossary div:last-child b, .glossary div:last-child span { border-bottom: 0; }
.source-map { border: 1px solid var(--line); }.source-map div { display: grid; grid-template-columns: 28mm 1fr; gap: 2mm; padding: 2.4mm 3mm; border-bottom: 1px solid var(--line); }.source-map div:last-child { border-bottom: 0; }.source-map code { color: var(--blue); font-size: 8.3pt; }.source-map span { grid-column: 2; color: var(--muted); font-size: 8.5pt; }.source-note { margin-top: 5mm; padding: 4mm; background: ${COLORS.light}; color: var(--muted); }
.final-map { display: grid; gap: 3mm; margin: 8mm 0; }.final-row { display: grid; grid-template-columns: 30mm 1fr; padding: 5mm; border-left: 6px solid var(--teal); background: ${COLORS.tealLight}; }.final-row b { font-size: 12pt; }.final-row span { padding-left: 3mm; }.final-coral { border-color: var(--coral); background: ${COLORS.coralLight}; }.final-blue { border-color: var(--blue); background: ${COLORS.blueLight}; }.final-gold { border-color: var(--gold); background: ${COLORS.goldLight}; }.final-repair { border-color: var(--repair); background: ${COLORS.repairLight}; }.memory-formula { display: flex; align-items: center; justify-content: center; gap: 2mm; flex-wrap: wrap; margin: 9mm 0; font-size: 13pt; }.memory-formula span { padding: 2.5mm 3.5mm; background: ${COLORS.light}; border: 1px solid var(--line); }.memory-formula b { color: var(--teal); }.thank-line { margin-top: 13mm; text-align: center; color: var(--muted); font-size: 12pt; }
.lesson-flow { display: flex; align-items: stretch; gap: 2mm; margin: 7mm 0; }.lesson-step { flex: 1; min-height: 38mm; padding: 3.5mm; background: ${COLORS.light}; border-top: 4px solid var(--teal); }.lesson-step:nth-child(1) { border-color: var(--coral); }.lesson-step:nth-child(3) { border-color: var(--blue); }.lesson-step:nth-child(5) { border-color: var(--gold); }.lesson-step:nth-child(7) { border-color: var(--repair); }.lesson-step:nth-child(9) { border-color: var(--green); }.lesson-step b { display: block; font-size: 17pt; line-height: 1; }.lesson-step strong { display: block; margin-top: 2mm; }.lesson-step span { display: block; color: var(--muted); font-size: 8.4pt; margin-top: 1.5mm; line-height: 1.35; }.lesson-arrow { align-self: center; color: var(--muted); font-size: 15pt; }.lesson-rule { display: flex; gap: 3mm; align-items: center; justify-content: center; margin: 6mm 0; padding: 4mm; background: ${COLORS.blueLight}; border: 1px solid #bfd3e8; }.lesson-rule span { font-size: 13pt; font-weight: 700; color: var(--blue); }.study-tips { display: grid; grid-template-columns: repeat(3, 1fr); gap: 3mm; }.study-tips div { padding: 3.5mm; border-left: 4px solid var(--teal); background: ${COLORS.tealLight}; }.study-tips div:nth-child(2) { border-color: var(--gold); background: ${COLORS.goldLight}; }.study-tips div:nth-child(3) { border-color: var(--repair); background: ${COLORS.repairLight}; }.study-tips b, .study-tips span { display: block; }.study-tips span { color: var(--muted); font-size: 8.7pt; margin-top: 1.5mm; }
.four-concepts { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; margin: 5mm 0; }.study-concept { min-height: 48mm; padding: 4mm; border-top: 5px solid var(--blue); background: ${COLORS.blueLight}; }.study-concept:nth-child(2) { border-color: var(--teal); background: ${COLORS.tealLight}; }.study-concept:nth-child(3) { border-color: var(--green); background: ${COLORS.greenLight}; }.study-concept:nth-child(4) { border-color: var(--gold); background: ${COLORS.goldLight}; }.study-concept b, .study-concept strong, .study-concept span { display: block; }.study-concept strong { margin: 2mm 0; font-size: 12pt; }.study-concept span { color: var(--muted); font-size: 9pt; }.mental-model-equation { display: flex; align-items: center; justify-content: center; gap: 2mm; flex-wrap: wrap; margin: 7mm 0; font-size: 12pt; }.mental-model-equation span { padding: 2.5mm 3mm; background: #fff; border: 1px solid var(--line); }.mental-model-equation b { color: var(--teal); }.three-col-table .tutorial-row { grid-template-columns: 1fr 1.25fr 1.25fr; }
.tutorial-table { border: 1px solid var(--line); margin-top: 4mm; font-size: 8.8pt; }.tutorial-row { display: grid; grid-template-columns: 1fr 1.4fr 1.7fr; }.tutorial-row > * { padding: 2.2mm 2.8mm; border-bottom: 1px solid var(--line); overflow-wrap: anywhere; }.tutorial-row:last-child > * { border-bottom: 0; }.tutorial-head { background: ${COLORS.light}; font-weight: 700; }.tutorial-head > * { color: var(--ink); }.tutorial-row > *:not(:first-child) { color: var(--muted); }.motivation-table .tutorial-row { grid-template-columns: 1.15fr .8fr 1.5fr; }.motivation-table .tutorial-row > * { font-size: 8.4pt; }.motivation-summary { margin-top: 6mm; padding: 4mm; background: ${COLORS.tealLight}; border-left: 5px solid var(--teal); }.motivation-summary span { margin-left: 2mm; color: var(--muted); }.principle-cards { display: grid; grid-template-columns: 1fr 1fr; gap: 3mm; }.principle-card { min-height: 34mm; padding: 3.2mm 4mm; border-left: 5px solid var(--teal); background: ${COLORS.tealLight}; }.principle-card:nth-child(even) { background: ${COLORS.light}; }.principle-card b, .principle-card strong, .principle-card span { display: block; }.principle-card b { color: var(--muted); font-size: 8.5pt; }.principle-card strong { margin: 1mm 0; }.principle-card span { color: var(--muted); font-size: 8.5pt; line-height: 1.35; }.pc-coral { border-color: var(--coral); background: ${COLORS.coralLight} !important; }.pc-blue { border-color: var(--blue); }.pc-purple { border-color: var(--purple); background: ${COLORS.purpleLight} !important; }.pc-teal { border-color: var(--teal); }.pc-green { border-color: var(--green); background: ${COLORS.greenLight} !important; }.pc-gold { border-color: var(--gold); background: ${COLORS.goldLight} !important; }.pc-red { border-color: var(--red); background: #fff5f1 !important; }.pc-repair { border-color: var(--repair); background: ${COLORS.repairLight} !important; }.principle-contrast { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; margin-top: 5mm; }.principle-contrast div { padding: 3.5mm; border-top: 4px solid var(--green); background: ${COLORS.greenLight}; }.principle-contrast div:last-child { border-color: var(--repair); background: ${COLORS.repairLight}; }.principle-contrast b, .principle-contrast span { display: block; }.principle-contrast span { color: var(--muted); font-size: 8.7pt; margin-top: 1.5mm; }
.world-teaching-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 3mm; margin-top: 5mm; }.world-teaching-grid div { padding: 3mm; background: #fff; border-left: 4px solid var(--green); }.world-teaching-grid div:nth-child(2) { border-color: var(--blue); }.world-teaching-grid div:nth-child(3) { border-color: var(--gold); }.world-teaching-grid div:nth-child(4) { border-color: var(--coral); }.world-teaching-grid b, .world-teaching-grid span { display: block; }.world-teaching-grid span { color: var(--muted); font-size: 8.8pt; margin-top: 1mm; }.world-json-table .tutorial-row { grid-template-columns: .8fr 1.2fr 1.5fr; }.entity-field-table .tutorial-row { grid-template-columns: .85fr 1.35fr 1.6fr; }.entity-question-table .tutorial-row { grid-template-columns: 1.3fr 1fr 1.4fr; }.robot-field-table .tutorial-row { grid-template-columns: 1fr 1.35fr 1.55fr; }.observation-table .tutorial-row { grid-template-columns: .9fr 1.45fr 1.5fr; }.program-field-table .tutorial-row { grid-template-columns: 1fr 1.3fr 1.4fr; }.spec-field-table .tutorial-row { grid-template-columns: 1fr 1.15fr 1.5fr; }.runtime-field-table .tutorial-row { grid-template-columns: 1fr 1.3fr 1.5fr; }.spec-runtime-table .tutorial-row { grid-template-columns: .8fr 1.25fr 1.5fr; }.stack-event-table .tutorial-row { grid-template-columns: .9fr 1.2fr 1.35fr 1.35fr; }.artifact-field-table .tutorial-row { grid-template-columns: 1.15fr 1.35fr 1.4fr; }.request-field-table .tutorial-row { grid-template-columns: 1.1fr 2.3fr; }.diagnostic-table .tutorial-row { grid-template-columns: 1fr 1.4fr 1.5fr; }.full-trace-table .tutorial-row { grid-template-columns: .35fr 1.25fr 1.5fr 2fr; }.session-table .tutorial-row { grid-template-columns: 1.15fr 1.3fr 1.4fr; }
.entity-kind-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 3mm; }.entity-kind { min-height: 96mm; padding: 3mm; border: 1px solid var(--line); border-top: 5px solid var(--teal); }.entity-kind h3 { font-size: 12pt; margin-bottom: 2mm; }.entity-kind .code-block { font-size: 7.3pt; line-height: 1.35; min-height: 49mm; }.entity-kind p { color: var(--muted); font-size: 8.7pt; }.kind-object { border-color: var(--coral); background: ${COLORS.coralLight}; }.kind-region { border-color: var(--green); background: ${COLORS.greenLight}; }.kind-robot { border-color: var(--blue); background: ${COLORS.blueLight}; }
.robot-state-compare { display: grid; grid-template-columns: 1fr 28mm 1fr; align-items: center; gap: 3mm; }.robot-state-card { padding: 3.5mm; border: 1px solid var(--line); }.robot-state-card > b { display: block; margin-bottom: 1mm; }.state-before { background: ${COLORS.blueLight}; border-color: var(--blue); }.state-after { background: ${COLORS.greenLight}; border-color: var(--green); }.robot-state-card .code-block { font-size: 7.7pt; line-height: 1.35; }.state-transition { color: var(--muted); text-align: center; font-size: 8.5pt; }.program-conversion { display: grid; grid-template-columns: 1.2fr 28mm 1.2fr; align-items: center; gap: 3mm; margin: 4mm 0; }.program-human, .program-structured { min-height: 34mm; padding: 4mm; border: 1px solid var(--coral); background: ${COLORS.coralLight}; }.program-structured { border-color: var(--blue); background: ${COLORS.blueLight}; }.program-human b, .program-human strong, .program-human span, .program-structured b, .program-structured span { display: block; }.program-human strong { margin: 2mm 0; font-size: 12pt; }.program-human span, .program-structured span { color: var(--muted); font-size: 8.6pt; }.program-arrow { text-align: center; color: var(--teal); font-size: 9pt; }.spec-reading-tip { margin-top: 5mm; padding: 4mm; background: ${COLORS.light}; border-left: 5px solid var(--blue); }.spec-reading-tip span { color: var(--muted); margin-left: 2mm; }.runtime-state-flow { display: flex; justify-content: center; align-items: center; gap: 2mm; margin: 5mm 0; }.runtime-state-flow > div { min-width: 32mm; padding: 3mm; text-align: center; background: ${COLORS.light}; border-top: 4px solid var(--blue); }.runtime-state-flow > div:nth-child(3) { border-color: var(--teal); background: ${COLORS.tealLight}; }.runtime-state-flow > div:nth-child(5) { border-color: var(--green); background: ${COLORS.greenLight}; }.runtime-state-flow > div:nth-child(even) { min-width: auto; padding: 0; border: 0; background: transparent; color: var(--muted); }.runtime-state-flow .runtime-failure { margin-left: 4mm; border-color: var(--repair); background: ${COLORS.repairLight}; }.runtime-state-flow b, .runtime-state-flow span { display: block; }.runtime-state-flow span { color: var(--muted); font-size: 8.3pt; margin-top: 1mm; }
.edge-diagram { margin: 4mm auto 5mm; max-width: 155mm; }.edge-parent { width: 48mm; margin: auto; }.edge-node { padding: 3mm; text-align: center; border: 2px solid var(--blue); background: ${COLORS.blueLight}; font-weight: 700; }.edge-node small { display: block; color: var(--muted); font-weight: 400; font-size: 8pt; margin-top: 1mm; }.edge-vertical { width: 1px; height: 10mm; margin: auto; background: var(--line); }.edge-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4mm; position: relative; }.edge-row::before { content: ""; position: absolute; left: 16%; right: 16%; top: -5mm; height: 1px; background: var(--line); }.edge-child { border-color: var(--teal); background: ${COLORS.tealLight}; }.edge-repair { border-color: var(--repair); background: ${COLORS.repairLight}; }.delta-contract { display: grid; grid-template-columns: repeat(3, 1fr); gap: 3mm; margin-top: 5mm; }.delta-contract div { padding: 3mm; border-top: 4px solid var(--blue); background: ${COLORS.blueLight}; }.delta-contract div:nth-child(2) { border-color: var(--repair); background: ${COLORS.repairLight}; }.delta-contract div:nth-child(3) { border-color: var(--purple); background: ${COLORS.purpleLight}; }.delta-contract b, .delta-contract span { display: block; }.delta-contract span { color: var(--muted); font-size: 8.6pt; margin-top: 1.5mm; }.artifact-freshness-rule { margin-top: 5mm; padding: 4mm; background: ${COLORS.greenLight}; border-left: 5px solid var(--green); }.artifact-freshness-rule span { color: var(--muted); margin-left: 2mm; }
.selector-binding-flow { display: flex; align-items: center; justify-content: center; gap: 2mm; margin: 5mm 0; }.selector-binding-flow > div { padding: 3mm 4mm; text-align: center; border: 1px solid var(--line); background: ${COLORS.light}; }.selector-binding-flow > div:nth-child(even) { padding: 0 1mm; border: 0; background: transparent; color: var(--teal); font-size: 15pt; }.selector-binding-flow b, .selector-binding-flow span { display: block; }.selector-binding-flow span { color: var(--muted); font-size: 8.2pt; margin-top: 1mm; }
.request-observation-large { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; }.large-request, .large-observation { padding: 3mm; border: 1px solid var(--line); }.large-request { background: ${COLORS.blueLight}; border-color: var(--blue); }.large-observation { background: ${COLORS.greenLight}; border-color: var(--green); }.large-request h3, .large-observation h3 { font-size: 11.5pt; }.large-request .code-block, .large-observation .code-block { font-size: 7.4pt; line-height: 1.35; min-height: 72mm; }.diagnostic-proposal { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; }.diagnostic-paper, .proposal-paper { padding: 3.5mm; border: 1px solid var(--repair); background: ${COLORS.repairLight}; }.proposal-paper { border-color: var(--blue); background: ${COLORS.blueLight}; }.diagnostic-paper h3, .proposal-paper h3 { font-size: 12pt; }.diagnostic-paper .code-block, .proposal-paper .code-block { font-size: 7.5pt; line-height: 1.35; min-height: 62mm; }.diagnostic-paper p, .proposal-paper p { color: var(--muted); font-size: 8.6pt; }
.full-trace-head { display: grid; grid-template-columns: 1.4fr .7fr 1fr; gap: 3mm; margin-bottom: 4mm; }.full-trace-head div { padding: 3mm; background: ${COLORS.light}; border-top: 4px solid var(--blue); }.full-trace-head div:nth-child(2) { border-color: var(--green); background: ${COLORS.greenLight}; }.full-trace-head div:nth-child(3) { border-color: var(--gold); background: ${COLORS.goldLight}; }.full-trace-head b, .full-trace-head span { display: block; }.full-trace-head span { margin-top: 1mm; font-size: 9pt; }.normal-result-strip, .blocked-metrics { display: grid; grid-template-columns: repeat(5, 1fr); gap: 2mm; margin-top: 5mm; }.normal-result-strip .stat, .blocked-metrics .stat { min-height: 25mm; }.blocked-trace-steps { display: grid; grid-template-columns: 1fr 1fr; gap: 2.5mm 4mm; margin: 4mm 0; }.blocked-trace-steps div { padding: 2.8mm 3mm; background: ${COLORS.repairLight}; border-left: 4px solid var(--repair); }.blocked-trace-steps b, .blocked-trace-steps span { display: block; }.blocked-trace-steps span { color: var(--muted); font-size: 8.6pt; margin-top: 1mm; }.continuous-two-tasks { display: grid; grid-template-columns: 1fr 34mm 1fr; align-items: center; gap: 3mm; margin: 6mm 0; }.session-column { min-height: 45mm; padding: 4mm; border: 1px solid var(--teal); border-top: 5px solid var(--teal); background: ${COLORS.tealLight}; }.session-column-blue { border-color: var(--blue); background: ${COLORS.blueLight}; }.session-column b, .session-column strong, .session-column span, .session-column small { display: block; }.session-column strong { margin: 2mm 0; }.session-column span, .session-column small { color: var(--muted); font-size: 8.6pt; }.session-world-link { text-align: center; color: var(--gold); }.session-world-link span, .session-world-link b { display: block; }.session-world-link span { color: var(--muted); font-size: 8pt; margin-bottom: 2mm; }.self-check-list { display: grid; grid-template-columns: 1fr 1fr; gap: 3mm; }.self-check-list div { display: grid; grid-template-columns: 11mm 1fr; gap: 2mm; padding: 3mm; border-left: 4px solid var(--teal); background: ${COLORS.tealLight}; }.self-check-list div:nth-child(odd) { border-color: var(--blue); background: ${COLORS.blueLight}; }.self-check-list div:nth-child(3n) { border-color: var(--gold); background: ${COLORS.goldLight}; }.self-check-list b { font-size: 14pt; color: var(--teal); }.self-check-list strong, .self-check-list span { display: block; }.self-check-list span { color: var(--muted); font-size: 8.6pt; margin-top: 1mm; }.final-learning-map { display: grid; gap: 2mm; margin: 6mm 0; }.final-learning-row { display: grid; grid-template-columns: 21mm 1fr; padding: 3.2mm 4mm; border-left: 5px solid var(--teal); background: ${COLORS.tealLight}; }.final-learning-row span { color: var(--muted); }.row-intent { border-color: var(--coral); background: ${COLORS.coralLight}; }.row-world { border-color: var(--green); background: ${COLORS.greenLight}; }.row-tree { border-color: var(--blue); background: ${COLORS.blueLight}; }.row-artifact { border-color: var(--gold); background: ${COLORS.goldLight}; }.row-action { border-color: var(--teal); background: ${COLORS.tealLight}; }.row-repair { border-color: var(--repair); background: ${COLORS.repairLight}; }
.self-check-list b { grid-column: 1; grid-row: 1 / span 2; }
.self-check-list strong { grid-column: 2; grid-row: 1; }
.self-check-list span { grid-column: 2; grid-row: 2; }
.code-block { white-space: pre-wrap; margin: 3mm 0; padding: 4mm; color: #f5f7f7; background: ${COLORS.dark}; border-radius: 5px; font-size: 8.5pt; line-height: 1.45; }
@media print {
  body { background: #fff; }
  .page { box-shadow: none; }
  .page, .node-card, .callout, .compare-col, .role-card, .timeline-item, .artifact-paper, .source-map, .glossary { break-inside: avoid; }
}
`;

const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Task Recursive Tree Agent 架构讲解 · 个人学习详解版</title>
  <style>${css}</style>
</head>
<body>
${pages.join("\n")}
</body>
</html>
`;

fs.writeFileSync(OUT_HTML, html, "utf8");
console.log(`Wrote ${OUT_HTML}`);
console.log(`Pages: ${pages.length}`);
