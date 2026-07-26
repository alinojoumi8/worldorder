export type ViewId =
  | "map"
  | "charts"
  | "agents"
  | "inspector"
  | "causal"
  | "search"
  | "compare"
  | "arena";

export type Agent = {
  id: string;
  label: string;
  age: number;
  district: string;
  occupation: string;
  employer: string;
  mode: "deliberate" | "reflex" | "reflect";
  income: number;
  wealth: number;
  wellbeing: number;
  status: "working" | "seeking" | "studying";
};

export const agents: Agent[] = [
  {
    id: "ag_0421",
    label: "Agent 0421",
    age: 34,
    district: "Southside",
    occupation: "Operations coordinator",
    employer: "TechWorks Solutions",
    mode: "deliberate",
    income: 52400,
    wealth: 18720,
    wellbeing: 56,
    status: "working"
  },
  {
    id: "ag_0088",
    label: "Agent 0088",
    age: 26,
    district: "Northgate",
    occupation: "Teacher",
    employer: "Northgate School",
    mode: "reflect",
    income: 48600,
    wealth: 9400,
    wellbeing: 71,
    status: "working"
  },
  {
    id: "ag_0176",
    label: "Agent 0176",
    age: 43,
    district: "Riverside",
    occupation: "Credit analyst",
    employer: "Civic Bank",
    mode: "reflex",
    income: 70200,
    wealth: 82900,
    wellbeing: 63,
    status: "working"
  },
  {
    id: "ag_0239",
    label: "Agent 0239",
    age: 21,
    district: "Oldtown",
    occupation: "Apprentice",
    employer: "Market Row Cooperative",
    mode: "deliberate",
    income: 28100,
    wealth: 2400,
    wellbeing: 68,
    status: "studying"
  },
  {
    id: "ag_0314",
    label: "Agent 0314",
    age: 39,
    district: "Market Row",
    occupation: "Shop owner",
    employer: "Self-employed",
    mode: "reflex",
    income: 61200,
    wealth: 116300,
    wellbeing: 74,
    status: "working"
  },
  {
    id: "ag_0507",
    label: "Agent 0507",
    age: 29,
    district: "Tech Park",
    occupation: "Research engineer",
    employer: "Civic Systems Lab",
    mode: "deliberate",
    income: 88400,
    wealth: 42800,
    wellbeing: 65,
    status: "working"
  },
  {
    id: "ag_0612",
    label: "Agent 0612",
    age: 51,
    district: "Harborview",
    occupation: "Logistics planner",
    employer: "Harbor Freight Union",
    mode: "reflect",
    income: 65800,
    wealth: 143900,
    wellbeing: 59,
    status: "working"
  },
  {
    id: "ag_0744",
    label: "Agent 0744",
    age: 31,
    district: "Southside",
    occupation: "Unemployed",
    employer: "—",
    mode: "deliberate",
    income: 0,
    wealth: 6200,
    wellbeing: 42,
    status: "seeking"
  },
  {
    id: "ag_0911",
    label: "Agent 0911",
    age: 17,
    district: "Riverside",
    occupation: "Student",
    employer: "Riverside College",
    mode: "reflex",
    income: 0,
    wealth: 600,
    wellbeing: 79,
    status: "studying"
  }
];

export const evidenceSteps = [
  {
    key: "macro",
    title: "City-level anomaly",
    tick: 4200,
    summary: "Unemployment moved from 10.2% to 11.8% against the 7-day baseline.",
    tone: "danger"
  },
  {
    key: "firm",
    title: "Firm event",
    tick: 4172,
    summary: "TechWorks Solutions announced a hiring freeze and reduced contract roles.",
    tone: "cobalt"
  },
  {
    key: "exposure",
    title: "Agent exposure",
    tick: 4173,
    summary: "Agent 0421’s role entered the exposed worker set.",
    tone: "cobalt"
  },
  {
    key: "perception",
    title: "Perception",
    tick: 4174,
    summary: "Observed: “I’m worried about losing my job.”",
    tone: "violet"
  },
  {
    key: "memory",
    title: "Memory retrieval",
    tick: 4174,
    summary: "Retrieved 4 related memories, including a peer’s prior layoff.",
    tone: "violet"
  },
  {
    key: "decision",
    title: "Decision",
    tick: 4175,
    summary: "Accepted a temporary role and reduced discretionary spending.",
    tone: "amber"
  },
  {
    key: "outcome",
    title: "Outcome",
    tick: 4201,
    summary: "Income −14%, stress −5%, security +6%.",
    tone: "green"
  }
] as const;

export const metrics = {
  unemployment: {
    label: "Unemployment rate",
    value: "11.8%",
    delta: "+1.6pp",
    unit: "%",
    color: "var(--vermilion)",
    points: [7.8, 7.9, 8.2, 8.1, 8.5, 8.9, 8.6, 9.2, 9.1, 9.8, 10.2, 10.6, 10.4, 11.1, 10.9, 11.8]
  },
  cpi: {
    label: "CPI",
    value: "101.84",
    delta: "+0.3%",
    unit: "index",
    color: "var(--amber)",
    points: [99.8, 99.9, 100, 100.1, 100.1, 100.3, 100.4, 100.5, 100.7, 100.8, 100.9, 101.1, 101.2, 101.4, 101.6, 101.84]
  },
  trust: {
    label: "Civic trust",
    value: "54",
    delta: "−2",
    unit: "index",
    color: "var(--teal)",
    points: [61, 60, 61, 59, 60, 58, 58, 57, 58, 56, 57, 55, 56, 54, 55, 54]
  },
  deliberate: {
    label: "Deliberate routing",
    value: "7.1%",
    delta: "+0.4pp",
    unit: "%",
    color: "var(--violet)",
    points: [6.5, 6.8, 6.4, 6.7, 6.9, 6.7, 7.1, 6.8, 7.2, 6.9, 7.1, 7.3, 7.0, 7.2, 7.4, 7.1]
  }
};

export type MetricId = keyof typeof metrics;

export const events = [
  { seq: 8812443, tick: 4201, kind: "JOB_ACCEPTED", actor: "ag_0421", summary: "Temporary operations contract accepted", integrity: "verified" },
  { seq: 8812438, tick: 4201, kind: "ACTION_VALIDATED", actor: "ag_0421", summary: "Five validation gates passed", integrity: "verified" },
  { seq: 8812390, tick: 4200, kind: "COGNITION_ROUTED", actor: "ag_0421", summary: "Agent routed to deliberate cognition", integrity: "verified" },
  { seq: 8812387, tick: 4200, kind: "MEMORY_RETRIEVED", actor: "ag_0421", summary: "Four memories selected for prompt context", integrity: "verified" },
  { seq: 8812218, tick: 4198, kind: "ROLE_AT_RISK", actor: "fm_031", summary: "Sixteen contract roles marked exposed", integrity: "verified" },
  { seq: 8811994, tick: 4194, kind: "HIRING_FREEZE", actor: "fm_031", summary: "Hiring freeze announced by TechWorks Solutions", integrity: "verified" },
  { seq: 8811882, tick: 4191, kind: "METRIC_WARN", actor: "sys", summary: "Unemployment crossed registered warning threshold", integrity: "warning" },
  { seq: 8811611, tick: 4188, kind: "NEWS_PUBLISHED", actor: "nw_004", summary: "Regional labour outlook revised downward", integrity: "verified" }
] as const;

export const comparisonDiffs = [
  { field: "config_hash", base: "b71f…0c22", compare: "b71f…0c22", declared: true, blocking: false },
  { field: "prompt_manifest", base: "pm_83bc", compare: "pm_83bc", declared: true, blocking: false },
  { field: "model_manifest", base: "mm_19d4", compare: "mm_19d4", declared: true, blocking: false },
  { field: "code_git_sha", base: "9c28de1", compare: "9c28de1", declared: true, blocking: false },
  { field: "mechanism_manifest", base: "feed: chronological", compare: "feed: engagement", declared: true, blocking: false },
  { field: "metric_manifest", base: "mx_47d1", compare: "mx_9a22", declared: false, blocking: true }
] as const;

export const scorecards = [
  {
    id: "ext_hermes_01",
    model: "Model family A",
    scaffold: "Hermes-compatible",
    eligible: true,
    dimensions: [72, 61, 84, 57, 76, 68, 91, 63, 79]
  },
  {
    id: "ext_mcp_03",
    model: "Model family B",
    scaffold: "Direct MCP",
    eligible: true,
    dimensions: [65, 74, 70, 82, 60, 77, 85, 71, 69]
  },
  {
    id: "ext_custom_07",
    model: "Model family C",
    scaffold: "Custom loop",
    eligible: false,
    reason: "Paused for external operator",
    dimensions: [88, 46, 62, 54, 83, 51, 40, 92, 58]
  }
] as const;

export const scoreDimensions = [
  "Economic survival",
  "Goal progress",
  "Social reach",
  "Institutional access",
  "Information quality",
  "Action validity",
  "Deadline compliance",
  "Resource efficiency",
  "Recovery"
];
