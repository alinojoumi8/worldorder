/**
 * THESIS: POLIS is a living city that can explain itself; the city remains visible while evidence moves from macro signal to individual decision.
 * OWN-WORLD: Cool drafting-film surfaces, graphite geometry, cobalt evidence routes, compact civic typography, and indexed analytical rails.
 * STORY: A researcher notices a city-level change, follows its causes, inspects one agent, and verifies the outcome without encountering a mutation control.
 * FIRST VIEWPORT: Atlas index left, abstract city map center, run-state strip above, city metrics below, and the selected agent’s evidence chain at right.
 * FORM: Approved Atlas Cockpit composition inside the Living Systems Atlas direction; map-first staging with a persistent evidence rail.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowRight,
  Banknote,
  Bot,
  BriefcaseBusiness,
  Building2,
  ChartNoAxesColumnIncreasing,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Clock3,
  Database,
  FileSearch,
  GitCompareArrows,
  GraduationCap,
  Home,
  Landmark,
  Layers3,
  Map as MapIcon,
  Network,
  Search,
  ShieldCheck,
  Store,
  Trophy,
  UsersRound,
  Wifi,
  X
} from "lucide-react";
import {
  agents,
  comparisonDiffs,
  evidenceSteps,
  events,
  metrics,
  scoreDimensions,
  scorecards,
  type Agent,
  type MetricId,
  type ViewId
} from "./mockData";

const navItems: Array<{
  id: ViewId;
  label: string;
  icon: typeof MapIcon;
}> = [
  { id: "map", label: "Map", icon: MapIcon },
  { id: "charts", label: "Charts", icon: ChartNoAxesColumnIncreasing },
  { id: "agents", label: "Agents", icon: UsersRound },
  { id: "inspector", label: "Inspector", icon: FileSearch },
  { id: "causal", label: "Causal", icon: Network },
  { id: "search", label: "Search", icon: Search },
  { id: "compare", label: "Compare", icon: GitCompareArrows },
  { id: "arena", label: "Arena", icon: Trophy }
];

const districtPolygons = [
  { id: "oldtown", label: "Oldtown", points: "32,46 302,30 354,200 272,330 18,286", fill: "#e8eef8" },
  { id: "northgate", label: "Northgate", points: "304,30 640,26 674,238 356,200", fill: "#e7f2ed" },
  { id: "riverside", label: "Riverside", points: "642,26 960,62 932,300 676,238", fill: "#e9eef9" },
  { id: "market", label: "Market Row", points: "272,332 356,202 674,240 628,446 330,472", fill: "#f4eddf" },
  { id: "tech", label: "Tech Park", points: "676,240 932,302 962,526 628,446", fill: "#ece9f7" },
  { id: "southside", label: "Southside", points: "18,288 272,332 330,474 306,642 30,614", fill: "#e8f2ed" },
  { id: "harbor", label: "Harborview", points: "330,474 628,448 962,528 926,642 306,642", fill: "#e4eff3" }
];

const buildingSeeds = [
  { x: 72, y: 88, district: 0 },
  { x: 158, y: 118, district: 0 },
  { x: 240, y: 74, district: 0 },
  { x: 374, y: 84, district: 1 },
  { x: 462, y: 72, district: 1 },
  { x: 554, y: 112, district: 1 },
  { x: 726, y: 94, district: 2 },
  { x: 826, y: 128, district: 2 },
  { x: 334, y: 282, district: 3 },
  { x: 430, y: 314, district: 3 },
  { x: 526, y: 286, district: 3 },
  { x: 712, y: 320, district: 4 },
  { x: 820, y: 366, district: 4 },
  { x: 88, y: 430, district: 5 },
  { x: 192, y: 488, district: 5 },
  { x: 372, y: 532, district: 6 },
  { x: 508, y: 520, district: 6 },
  { x: 704, y: 536, district: 6 },
  { x: 828, y: 562, district: 6 }
];

const layerOptions = [
  { id: "districts", label: "Districts", active: true },
  { id: "buildings", label: "Buildings", active: true },
  { id: "flows", label: "Transit flows", active: true },
  { id: "agents", label: "Agent sample", active: true },
  { id: "work", label: "Workplaces", active: true },
  { id: "schools", label: "Schools", active: false },
  { id: "markets", label: "Markets", active: false }
];

const choroplethPalettes: Record<string, string[]> = {
  Unemployment: ["#e8eef8", "#e7f2ed", "#e9eef9", "#f4eddf", "#ece9f7", "#e8f2ed", "#e4eff3"],
  "Land value": ["#eef1e8", "#e4efe6", "#dcebe4", "#f1e5d3", "#e5e7dd", "#edf0e8", "#dce9e6"],
  "Rent index": ["#e8edf5", "#e3eaf2", "#e6e8f1", "#eee4dc", "#e7e2ef", "#ebedf3", "#dfe8ee"],
  "School quality": ["#e8f0ec", "#dceee7", "#e7f1ec", "#edf0e5", "#e3ede8", "#eaf1ed", "#dcebe5"],
  "Crime rate": ["#eee9e7", "#e9eeeb", "#efe7e4", "#f2e6df", "#ebe5e8", "#efeae7", "#e6ecea"]
};

const choroplethRanges: Record<string, [string, string]> = {
  Unemployment: ["0%", "16%"],
  "Land value": ["$82k", "$1.2m"],
  "Rent index": ["74", "168"],
  "School quality": ["41", "94"],
  "Crime rate": ["0.8", "7.4"]
};

const viewIds = new Set<ViewId>(navItems.map((item) => item.id));

function routeFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const requestedView = params.get("view") as ViewId | null;
  const requestedAgent = params.get("agent");
  return {
    view: requestedView && viewIds.has(requestedView) ? requestedView : "map",
    agent: agents.find((candidate) => candidate.id === requestedAgent) ?? agents[0]
  };
}

function useUrlParam(key: string, fallback: string) {
  const read = () => new URLSearchParams(window.location.search).get(key) ?? fallback;
  const [value, setValue] = useState(read);
  useEffect(() => {
    const sync = () => setValue(read());
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, [key, fallback]);
  const update = (next: string) => {
    setValue(next);
    const url = new URL(window.location.href);
    if (next === fallback || next === "") url.searchParams.delete(key);
    else url.searchParams.set(key, next);
    window.history.replaceState({}, "", url);
  };
  return [value, update] as const;
}

function evidenceForAgent(agent: Agent) {
  if (agent.id === "ag_0421") return evidenceSteps;
  const employer = agent.employer === "—" ? "the local labour market" : agent.employer;
  const decision =
    agent.status === "seeking"
      ? "Expanded the job search and reduced discretionary spending."
      : agent.status === "studying"
        ? "Kept the current study plan and deferred a large purchase."
        : "Kept the current role while increasing the household cash buffer.";
  const outcome =
    agent.status === "seeking"
      ? "Search intensity +18%, cash runway −3%, stress +4%."
      : agent.status === "studying"
        ? "Skill progress +2%, spending −6%, security unchanged."
        : "Savings +5%, stress −2%, role stability unchanged.";
  return evidenceSteps.map((step) => {
    if (step.key === "firm") return { ...step, summary: `${employer} entered a lower-hiring regime.` };
    if (step.key === "exposure") return { ...step, summary: `${agent.label}'s ${agent.occupation.toLowerCase()} state entered the affected set.` };
    if (step.key === "perception") return { ...step, summary: `Observed changing conditions in ${agent.district} and at ${employer}.` };
    if (step.key === "memory") return { ...step, summary: `Retrieved four ${agent.id}-specific memories relevant to work and household security.` };
    if (step.key === "decision") return { ...step, summary: decision };
    if (step.key === "outcome") return { ...step, summary: outcome };
    return step;
  });
}

const districtAgentPositions: Record<string, { x: number; y: number }> = {
  Oldtown: { x: 180, y: 205 },
  Northgate: { x: 480, y: 142 },
  Riverside: { x: 816, y: 190 },
  "Market Row": { x: 430, y: 356 },
  "Tech Park": { x: 760, y: 320 },
  Southside: { x: 236, y: 500 },
  Harborview: { x: 620, y: 545 }
};

function formatMoney(value: number) {
  return new Intl.NumberFormat("en-CA", {
    style: "currency",
    currency: "CAD",
    maximumFractionDigits: 0
  }).format(value);
}

function pathFor(points: readonly number[], width = 320, height = 96, pad = 8) {
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  return points
    .map((point, index) => {
      const x = pad + (index / (points.length - 1)) * (width - pad * 2);
      const y = height - pad - ((point - min) / range) * (height - pad * 2);
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

function TopBar() {
  return (
    <header className="topbar">
      <div className="brand-lockup">
        <span className="brand-word">POLIS</span>
        <span className="brand-subtitle">Living Systems Atlas</span>
      </div>
      <div className="run-strip" aria-label="Current synthetic run status">
        <div className="run-strip-item run-address">
          <Clock3 size={14} />
          <span>Tick</span>
          <strong>4,201</strong>
        </div>
        <div className="run-strip-item run-time">
          <span>Sim time</span>
          <strong>Year 05 · Day 176 · 14:00</strong>
        </div>
        <div className="run-strip-item">
          <span className="status-dot status-good" />
          <span>Run health</span>
          <strong>Nominal</strong>
        </div>
        <div className="run-strip-item">
          <Wifi size={14} />
          <span>Freshness</span>
          <strong>2.7s</strong>
        </div>
        <div className="run-strip-item">
          <span>Lag</span>
          <strong>0.4 ticks</strong>
        </div>
      </div>
      <div className="demo-stamp">Synthetic demo</div>
    </header>
  );
}

function AtlasNav({
  activeView,
  onChange
}: {
  activeView: ViewId;
  onChange: (view: ViewId) => void;
}) {
  return (
    <nav className="atlas-nav" aria-label="Observatory views">
      <div className="atlas-mark" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <div className="nav-items">
        {navItems.map((item) => {
          const Icon = item.icon;
          return (
            <button
              className="nav-item"
              data-active={activeView === item.id}
              key={item.id}
              onClick={() => onChange(item.id)}
              aria-current={activeView === item.id ? "page" : undefined}
              type="button"
            >
              <Icon size={19} strokeWidth={1.7} />
              <span>{item.label}</span>
            </button>
          );
        })}
      </div>
      <div className="nav-readonly">
        <ShieldCheck size={16} />
        <span>Read-only</span>
      </div>
    </nav>
  );
}

function ViewHeader({
  eyebrow,
  title,
  description,
  trailing
}: {
  eyebrow: string;
  title: string;
  description: string;
  trailing?: React.ReactNode;
}) {
  return (
    <div className="view-header">
      <div>
        <div className="view-address">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {trailing ? <div className="view-header-trailing">{trailing}</div> : null}
    </div>
  );
}

function MetricLine({
  metric,
  height = 96,
  width = 320,
  showArea = false
}: {
  metric: MetricId;
  height?: number;
  width?: number;
  showArea?: boolean;
}) {
  const item = metrics[metric];
  const path = pathFor(item.points, width, height);
  const area = `${path} L${width - 8},${height - 8} L8,${height - 8} Z`;
  return (
    <svg
      className="metric-line"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`${item.label} synthetic series`}
      preserveAspectRatio="none"
    >
      <title>{item.label} synthetic metric series</title>
      {[0.25, 0.5, 0.75].map((fraction) => (
        <line
          key={fraction}
          x1="8"
          y1={height * fraction}
          x2={width - 8}
          y2={height * fraction}
          className="chart-gridline"
        />
      ))}
      {showArea ? <path d={area} fill={item.color} opacity="0.08" /> : null}
      <path d={path} fill="none" stroke={item.color} strokeWidth="2.3" vectorEffect="non-scaling-stroke" />
      <line
        x1={width * 0.74}
        y1="6"
        x2={width * 0.74}
        y2={height - 8}
        stroke="var(--cobalt)"
        strokeWidth="1"
        strokeDasharray="3 3"
      />
      <circle
        cx={width - 8}
        cy={Number(path.split("L").at(-1)?.split(",")[1]) || height / 2}
        r="3.4"
        fill={item.color}
      />
    </svg>
  );
}

function CityMap({
  onInspect,
  selectedAgent,
  activeLayers,
  choropleth
}: {
  onInspect: () => void;
  selectedAgent: Agent;
  activeLayers: Set<string>;
  choropleth: string;
}) {
  const agentDots = useMemo(
    () =>
      Array.from({ length: 92 }, (_, index) => ({
        x: 48 + ((index * 83) % 862),
        y: 64 + ((index * 137) % 520),
        mode: index % 13 === 0 ? "deliberate" : index % 29 === 0 ? "reflect" : "reflex"
      })),
    []
  );
  const selectedPosition =
    districtAgentPositions[selectedAgent.district] ?? districtAgentPositions.Southside;
  const selectedAnchorStyle = {
    top: `${(selectedPosition.y / 660) * 100}%`,
    right: `${((980 - selectedPosition.x) / 980) * 100}%`
  };
  const districtPalette =
    choroplethPalettes[choropleth] ?? choroplethPalettes.Unemployment;

  return (
    <div className="city-map-shell">
      <svg
        className="city-map"
        viewBox="0 0 980 660"
        role="img"
        aria-labelledby="city-map-title city-map-description"
      >
        <title id="city-map-title">POLIS synthetic city map at tick 4,201</title>
        <desc id="city-map-description">
          Seven districts with places, transit flows, workplaces, sampled agent positions, and a highlighted route to Agent 0421.
        </desc>
        <defs>
          <filter id="building-shadow" x="-20%" y="-20%" width="140%" height="160%">
            <feDropShadow dx="1.5" dy="3" stdDeviation="2.5" floodColor="#52657a" floodOpacity="0.18" />
          </filter>
          <pattern id="map-grid" width="24" height="24" patternUnits="userSpaceOnUse">
            <path d="M24 0H0V24" fill="none" stroke="#aebdcc" strokeWidth="0.5" opacity="0.38" />
          </pattern>
        </defs>
        <rect width="980" height="660" fill="#f3f7fa" />
        <rect width="980" height="660" fill="url(#map-grid)" />
        {activeLayers.has("districts")
          ? districtPolygons.map((district, index) => (
              <g key={district.id}>
                <polygon
                  points={district.points}
                  fill={districtPalette[index] ?? district.fill}
                  stroke="#a9b8c8"
                  strokeWidth="1.25"
                />
                <text
                  className="district-label"
                  x={district.points.split(" ")[0].split(",")[0]}
                  y={Number(district.points.split(" ")[0].split(",")[1]) + 22}
                >
                  {district.label}
                </text>
              </g>
            ))
          : null}
        <path className="river" d="M928 -20 C850 100 860 205 908 282 C950 350 966 468 882 690" />
        <g className="roads" aria-hidden="true">
          <path d="M38 208 C180 180 292 230 432 206 S740 174 948 224" />
          <path d="M56 382 C214 354 322 406 496 376 S780 340 930 398" />
          <path d="M188 22 C196 130 248 230 234 348 S214 526 252 644" />
          <path d="M520 20 C518 166 558 250 544 382 S512 538 528 644" />
          <path d="M766 34 C738 150 748 256 760 362 S790 538 756 636" />
        </g>
        {activeLayers.has("flows") ? (
          <g className="transit-flow" aria-hidden="true">
            <path d="M62 404 C226 338 362 416 514 352 S772 292 912 220" />
            <path d="M104 148 C278 218 416 122 592 176 S780 306 884 430" />
          </g>
        ) : null}
        {activeLayers.has("buildings") ? (
          <g filter="url(#building-shadow)">
            {buildingSeeds.flatMap((seed, seedIndex) =>
              Array.from({ length: 4 }, (_, buildingIndex) => {
                const width = 22 + ((seedIndex + buildingIndex * 7) % 22);
                const height = 18 + ((seedIndex * 11 + buildingIndex * 9) % 44);
                const x = seed.x + (buildingIndex % 2) * 42;
                const y = seed.y + Math.floor(buildingIndex / 2) * 48;
                const fills = ["#ffffff", "#e4edf5", "#d9e5ee", "#eef2f6"];
                return (
                  <g key={`${seedIndex}-${buildingIndex}`}>
                    <rect
                      x={x}
                      y={y}
                      width={width}
                      height={height}
                      rx="1.5"
                      fill={fills[(seed.district + buildingIndex) % fills.length]}
                      stroke="#8294a8"
                      strokeWidth="0.9"
                    />
                    <path
                      d={`M${x + width},${y} l7,-5 v${height} l-7,5 z`}
                      fill="#c8d6e2"
                      stroke="#8294a8"
                      strokeWidth="0.65"
                    />
                  </g>
                );
              })
            )}
          </g>
        ) : null}
        <g className="place-symbols">
          <g transform="translate(452 236)">
            <circle r="18" />
            <Landmark size={20} x={-10} y={-10} />
            <text x="25" y="5">Civic Hall</text>
          </g>
          {activeLayers.has("work") ? (
            <g transform="translate(680 164)">
              <circle r="18" />
              <BriefcaseBusiness size={20} x={-10} y={-10} />
              <text x="25" y="5">TechWorks</text>
            </g>
          ) : null}
          <g transform="translate(164 428)">
            <circle r="18" />
            <Home size={20} x={-10} y={-10} />
            <text x="25" y="5">Housing</text>
          </g>
          {activeLayers.has("markets") ? (
            <g transform="translate(366 354)">
              <circle r="18" />
              <Store size={20} x={-10} y={-10} />
              <text x="25" y="5">Market Hall</text>
            </g>
          ) : null}
          {activeLayers.has("schools") ? (
            <g transform="translate(824 322)">
              <circle r="18" />
              <GraduationCap size={20} x={-10} y={-10} />
              <text x="25" y="5">Riverside College</text>
            </g>
          ) : null}
        </g>
        {activeLayers.has("agents") ? (
          <g className="agent-sample" aria-label="Sampled agent positions">
            {agentDots.map((agent, index) => (
              <circle
                key={index}
                cx={agent.x}
                cy={agent.y}
                r={agent.mode === "deliberate" ? 3.4 : 2.2}
                data-mode={agent.mode}
              />
            ))}
          </g>
        ) : null}
        <g className="evidence-route" aria-hidden="true">
          <path
            d={`M300 544 C380 516 394 430 480 408 S610 300 ${selectedPosition.x},${selectedPosition.y}`}
          />
          <circle cx="300" cy="544" r="6" />
          <circle cx="480" cy="408" r="6" />
          <circle cx={selectedPosition.x} cy={selectedPosition.y} r="6" />
        </g>
      </svg>
      <button
        className="selected-agent-anchor"
        style={selectedAnchorStyle}
        onClick={onInspect}
        type="button"
      >
        <span className="agent-pulse" aria-hidden="true" />
        <span>
          <strong>{selectedAgent.id}</strong>
          <small>Open inspector</small>
        </span>
        <ArrowRight size={15} />
      </button>
      <div className="map-compass" aria-hidden="true">
        <span>N</span>
        <i />
      </div>
      <div className="map-scale" aria-hidden="true">
        <span>0</span>
        <i />
        <span>500 m</span>
      </div>
    </div>
  );
}

function LayerPanel({
  layers,
  choropleth,
  onToggleLayer,
  onChangeChoropleth
}: {
  layers: typeof layerOptions;
  choropleth: string;
  onToggleLayer: (id: string) => void;
  onChangeChoropleth: (value: string) => void;
}) {
  const range = choroplethRanges[choropleth] ?? choroplethRanges.Unemployment;
  return (
    <aside className="map-layer-panel" aria-label="Map layers">
      <div className="panel-heading">
        <Layers3 size={16} />
        <span>Map layers</span>
      </div>
      <div className="layer-list">
        {layers.map((layer) => (
          <label key={layer.id} className="layer-option">
            <input
              type="checkbox"
              checked={layer.active}
              onChange={() => onToggleLayer(layer.id)}
            />
            <span className="custom-checkbox">
              <Check size={11} />
            </span>
            <span>{layer.label}</span>
          </label>
        ))}
      </div>
      <div className="layer-divider" />
      <label className="field-label" htmlFor="choropleth">
        District field
      </label>
      <div className="select-wrap">
        <select
          id="choropleth"
          value={choropleth}
          onChange={(event) => onChangeChoropleth(event.target.value)}
        >
          <option>Unemployment</option>
          <option>Land value</option>
          <option>Rent index</option>
          <option>School quality</option>
          <option>Crime rate</option>
        </select>
        <ChevronDown size={14} aria-hidden="true" />
      </div>
      <div className="legend-ramp">
        <span>{range[0]}</span>
        <i />
        <span>{range[1]}</span>
      </div>
      <div className="sample-note">
        <CircleAlert size={14} />
        <span>Agent dots are a sampled live layer.</span>
      </div>
    </aside>
  );
}

function CityIndicators({ onOpenCharts }: { onOpenCharts: () => void }) {
  return (
    <section className="city-indicators" aria-label="City indicators">
      {(Object.keys(metrics) as MetricId[]).slice(0, 3).map((metricId) => {
        const metric = metrics[metricId];
        return (
          <div className="indicator" key={metricId}>
            <div>
              <span>{metric.label}</span>
              <strong>{metric.value}</strong>
            </div>
            <div className="indicator-chart">
              <MetricLine metric={metricId} width={160} height={42} />
            </div>
          </div>
        );
      })}
      <button className="text-action" onClick={onOpenCharts} type="button">
        Open charts <ArrowRight size={14} />
      </button>
    </section>
  );
}

function EvidenceRail({
  agent,
  onInspect,
  onClose
}: {
  agent: Agent;
  onInspect: () => void;
  onClose?: () => void;
}) {
  const agentEvidence = evidenceForAgent(agent);
  return (
    <aside className="evidence-rail" aria-label={`Evidence chain for ${agent.label}`}>
      <div className="agent-rail-header">
        <div className="agent-symbol">
          <Bot size={20} />
        </div>
        <div>
          <div className="rail-address">{agent.id}</div>
          <strong>{agent.label}</strong>
          <span>
            {agent.occupation} · {agent.district}
          </span>
        </div>
        {onClose ? (
          <button className="icon-button" aria-label="Close evidence rail" onClick={onClose} type="button">
            <X size={16} />
          </button>
        ) : null}
      </div>
      <div className="evidence-question">
        <span>Why did unemployment move?</span>
        <small>Evidence chain from city signal to agent outcome</small>
      </div>
      <ol className="evidence-steps">
        {agentEvidence.map((step, index) => (
          <li key={step.key} data-tone={step.tone}>
            <div className="step-index">{index + 1}</div>
            <div className="step-content">
              <div className="step-meta">
                <span>{step.title}</span>
                <code>Tick {step.tick.toLocaleString()}</code>
              </div>
              <p>{step.summary}</p>
            </div>
          </li>
        ))}
      </ol>
      <div className="chain-integrity">
        <CheckCircle2 size={16} />
        <span>
          Chain integrity verified
          <code>ec_9f3a7b2c4d1e</code>
        </span>
      </div>
      <button className="primary-action" onClick={onInspect} type="button">
        Open full inspector
        <ArrowRight size={15} />
      </button>
    </aside>
  );
}

function MapView({
  selectedAgent,
  onInspect,
  onOpenCharts
}: {
  selectedAgent: Agent;
  onInspect: () => void;
  onOpenCharts: () => void;
}) {
  const defaultLayerIds = layerOptions
    .filter((layer) => layer.active)
    .map((layer) => layer.id)
    .join(",");
  const [layerParam, setLayerParam] = useUrlParam("layers", defaultLayerIds);
  const [choropleth, setChoropleth] = useUrlParam("choropleth", "Unemployment");
  const activeLayers = new Set(layerParam.split(",").filter(Boolean));
  const layers = layerOptions.map((layer) => ({
    ...layer,
    active: activeLayers.has(layer.id)
  }));
  const toggleLayer = (id: string) => {
    const next = new Set(activeLayers);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setLayerParam(layerOptions.filter((layer) => next.has(layer.id)).map((layer) => layer.id).join(","));
  };
  return (
    <div className="map-view">
      <div className="map-stage">
        <LayerPanel
          layers={layers}
          choropleth={choropleth}
          onToggleLayer={toggleLayer}
          onChangeChoropleth={setChoropleth}
        />
        <CityMap
          selectedAgent={selectedAgent}
          onInspect={onInspect}
          activeLayers={activeLayers}
          choropleth={choropleth}
        />
        <CityIndicators onOpenCharts={onOpenCharts} />
      </div>
      <EvidenceRail agent={selectedAgent} onInspect={onInspect} />
    </div>
  );
}

function ChartsView({ onInspect }: { onInspect: () => void }) {
  const [metricParam, setMetricParam] = useUrlParam("metric", "unemployment");
  const activeMetric: MetricId =
    metricParam in metrics ? (metricParam as MetricId) : "unemployment";
  const metric = metrics[activeMetric];
  return (
    <div className="view-canvas">
      <ViewHeader
        eyebrow="Run run_05_20_a · metric catalogue"
        title="City signals"
        description="Registered measures only. Cadence gaps stay null; shock, warning, and intervention markers remain attached."
        trailing={
          <div className="freshness-chip">
            <Wifi size={14} /> As of tick 4,201 · seq 8,812,443
          </div>
        }
      />
      <div className="charts-layout">
        <aside className="metric-catalogue" aria-label="Metric catalogue">
          <div className="section-label">Catalogue</div>
          {(Object.keys(metrics) as MetricId[]).map((metricId) => {
            const item = metrics[metricId];
            return (
              <button
                key={metricId}
                className="metric-catalogue-item"
                data-active={activeMetric === metricId}
                onClick={() => setMetricParam(metricId)}
                type="button"
              >
                <span className="metric-swatch" style={{ background: item.color }} />
                <span>
                  <strong>{item.label}</strong>
                  <small>{item.unit} · sim_day</small>
                </span>
                <code>{item.value}</code>
              </button>
            );
          })}
          <div className="catalogue-definition">
            <span>Formal definition</span>
            <p>
              Share of labour-force agents without active employment at the registered cadence.
            </p>
            <code>definition_hash mx_47d1</code>
          </div>
        </aside>
        <section className="primary-chart-panel">
          <div className="chart-title-row">
            <div>
              <span>{metric.label}</span>
              <strong>{metric.value}</strong>
              <em data-negative={metric.delta.startsWith("+") && activeMetric === "unemployment"}>
                {metric.delta}
              </em>
            </div>
            <div className="chart-range" aria-label="Selected chart range">
              <button type="button">1 month</button>
              <button type="button" data-active>
                1 year
              </button>
              <button type="button">All</button>
            </div>
          </div>
          <div className="large-chart">
            <MetricLine metric={activeMetric} width={920} height={340} showArea />
            <div className="shock-marker">
              <span>Shock marker</span>
              <strong>Hiring freeze</strong>
              <code>tick 4,172</code>
            </div>
            <button className="chart-anomaly" onClick={onInspect} type="button">
              <span>+1.6pp anomaly</span>
              <strong>Trace to Agent 0421</strong>
              <ArrowRight size={14} />
            </button>
            <div className="chart-axis-labels">
              <span>Tick 3,840</span>
              <span>Tick 4,020</span>
              <span>Tick 4,201</span>
            </div>
          </div>
          <div className="chart-disclosure">
            <CircleAlert size={16} />
            <p>
              Synthetic demonstration series. Downsampled from 43,200 source points using LTTB;
              extrema and endpoints preserved.
            </p>
          </div>
        </section>
        <aside className="chart-context">
          <div className="section-label">At selected tick</div>
          <dl className="context-ledger">
            <div>
              <dt>Active agents</dt>
              <dd>1,000</dd>
            </div>
            <div>
              <dt>Events</dt>
              <dd>15,221</dd>
            </div>
            <div>
              <dt>LLM calls</dt>
              <dd>88</dd>
            </div>
            <div>
              <dt>Reflex / deliberate</dt>
              <dd>92.1 / 7.1%</dd>
            </div>
            <div>
              <dt>Invariant warnings</dt>
              <dd className="warning-text">1</dd>
            </div>
          </dl>
          <div className="linked-events">
            <span>Events that moved this metric</span>
            {events.slice(4, 7).map((event) => (
              <button type="button" key={event.seq}>
                <code>{event.kind}</code>
                <small>tick {event.tick}</small>
              </button>
            ))}
          </div>
        </aside>
      </div>
    </div>
  );
}

function AgentsView({
  onSelect
}: {
  onSelect: (agent: Agent) => void;
}) {
  const [query, setQuery] = useUrlParam("agentQuery", "");
  const [status, setStatus] = useUrlParam("agentStatus", "all");
  const filtered = agents.filter(
    (agent) =>
      (status === "all" || agent.status === status) &&
      `${agent.id} ${agent.occupation} ${agent.district}`.toLowerCase().includes(query.toLowerCase())
  );
  return (
    <div className="view-canvas">
      <ViewHeader
        eyebrow="Run run_05_20_a · 1,000 projected agents"
        title="Agent registry"
        description="Browse current projected state, then jump directly to a recorded deliberate tick."
        trailing={<div className="freshness-chip">As of tick 4,201</div>}
      />
      <div className="agent-toolbar">
        <label className="search-field">
          <Search size={16} />
          <span className="sr-only">Search agents</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search id, role, or district"
          />
          {query ? (
            <button type="button" onClick={() => setQuery("")} aria-label="Clear agent search">
              <X size={14} />
            </button>
          ) : null}
        </label>
        <div className="segmented-control" aria-label="Filter agent status">
          {[
            ["all", "All"],
            ["working", "Working"],
            ["seeking", "Seeking"],
            ["studying", "Studying"]
          ].map(([value, label]) => (
            <button
              key={value}
              type="button"
              data-active={status === value}
              onClick={() => setStatus(value)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="table-result-count">{filtered.length} shown · synthetic fixture</div>
      </div>
      <div className="agent-table-wrap">
        <table className="data-table agent-table">
          <thead>
            <tr>
              <th>Agent</th>
              <th>Status</th>
              <th>Occupation</th>
              <th>District</th>
              <th>Income</th>
              <th>Wealth</th>
              <th>Cognition</th>
              <th>Wellbeing</th>
              <th aria-label="Open" />
            </tr>
          </thead>
          <tbody>
            {filtered.map((agent) => (
              <tr key={agent.id} onClick={() => onSelect(agent)}>
                <td>
                  <div className="agent-cell">
                    <span className="agent-mini-symbol">
                      <Bot size={15} />
                    </span>
                    <span>
                      <strong>{agent.label}</strong>
                      <code>{agent.id}</code>
                    </span>
                  </div>
                </td>
                <td>
                  <span className="status-label" data-status={agent.status}>
                    {agent.status}
                  </span>
                </td>
                <td>{agent.occupation}</td>
                <td>{agent.district}</td>
                <td>{formatMoney(agent.income)}</td>
                <td>{formatMoney(agent.wealth)}</td>
                <td>
                  <span className="mode-label" data-mode={agent.mode}>
                    {agent.mode}
                  </span>
                </td>
                <td>
                  <div className="wellbeing-cell">
                    <span>{agent.wellbeing}</span>
                    <i style={{ "--value": `${agent.wellbeing}%` } as React.CSSProperties} />
                  </div>
                </td>
                <td>
                  <button
                    className="row-action"
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onSelect(agent);
                    }}
                    aria-label={`Inspect ${agent.label}`}
                  >
                    <ArrowRight size={15} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length === 0 ? (
          <div className="empty-state">
            <Search size={24} />
            <strong>No matching agents</strong>
            <p>Try a broader id, role, or district search.</p>
            <button type="button" onClick={() => { setQuery(""); setStatus("all"); }}>
              Clear filters
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function DecisionPath({ agent }: { agent: Agent }) {
  const agentEvidence = evidenceForAgent(agent);
  const actionType =
    agent.status === "seeking"
      ? "APPLY_FOR_JOB"
      : agent.status === "studying"
        ? "CONTINUE_STUDY"
        : "ADJUST_BUDGET";
  const blocks = [
    {
      key: "perception",
      label: "Perception",
      status: "recorded",
      content: agentEvidence[3].summary,
      detail: `digest_hash ${agent.id.slice(-4)}…a912`
    },
    {
      key: "salience",
      label: "Salience",
      status: "recorded",
      content: "0.71 score · 0.63 cutoff · rank 41 · deliberate route.",
      detail: "stakes 0.90 · surprise 0.40"
    },
    {
      key: "retrieval",
      label: "Memory retrieval",
      status: "recorded",
      content: agentEvidence[4].summary,
      detail: "top score 2.19 · exact tick values"
    },
    {
      key: "prompt",
      label: "Prompt",
      status: "warning",
      content: "Reconstructed from deliberate.j2; source text was not stored.",
      detail: "hash_matches true"
    },
    {
      key: "response",
      label: "Response",
      status: "recorded",
      content: `Parsed on first attempt. ${agentEvidence[5].summary}`,
      detail: "cache hit · cost $0.00"
    },
    {
      key: "action",
      label: "Action",
      status: "recorded",
      content: `${actionType} · all five validation gates passed.`,
      detail: "legality clean · origin deliberate"
    },
    {
      key: "outcome",
      label: "Outcome",
      status: "verified",
      content: agentEvidence[6].summary,
      detail: "3 downstream events · ledger closes"
    }
  ];
  return (
    <div className="decision-path">
      {blocks.map((block, index) => (
        <article key={block.key} className="inspection-block" data-status={block.status}>
          <div className="inspection-number">{index + 1}</div>
          <div className="inspection-content">
            <div className="inspection-label-row">
              <h3>{block.label}</h3>
              <span>{block.status}</span>
            </div>
            <p>{block.content}</p>
            <code>{block.detail}</code>
          </div>
          {index < blocks.length - 1 ? <ArrowRight className="inspection-arrow" size={18} /> : null}
        </article>
      ))}
      <div className="agent-outcome-summary">
        <div>
          <span>Selected agent</span>
          <strong>{agent.id}</strong>
        </div>
        <div>
          <span>Evidence coverage</span>
          <strong>100%</strong>
        </div>
        <div>
          <span>Sampled</span>
          <strong>Yes</strong>
        </div>
        <div>
          <span>As of sequence</span>
          <strong>8,812,443</strong>
        </div>
      </div>
    </div>
  );
}

function TimelinePanel() {
  return (
    <div className="timeline-panel">
      {events.slice(0, 7).map((event, index) => (
        <div className="timeline-row" key={event.seq}>
          <div className="timeline-time">
            <code>Tick {event.tick}</code>
            <span>seq {event.seq}</span>
          </div>
          <div className="timeline-node" data-warning={event.integrity === "warning"} />
          <div>
            <strong>{event.kind}</strong>
            <p>{event.summary}</p>
          </div>
          <button type="button">Open event</button>
          {index === 4 ? (
            <div className="sampling-gap">
              <CircleAlert size={14} />
              Reflex interval: cognition not recorded for 7 ticks
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function MemoryPanel() {
  const memories = [
    { type: "reflection", tick: 4118, importance: 0.86, text: "I should keep a larger cash buffer when my employer slows hiring.", parents: 4 },
    { type: "observation", tick: 4072, importance: 0.74, text: "A teammate lost a contract role with little notice.", parents: 0 },
    { type: "observation", tick: 3981, importance: 0.68, text: "The temporary operations role offers stability but less income.", parents: 0 },
    { type: "reflection", tick: 3760, importance: 0.61, text: "Security matters more when household obligations are near.", parents: 3 }
  ];
  return (
    <div className="memory-panel">
      {memories.map((memory, index) => (
        <article key={index}>
          <div className="memory-meta">
            <span data-type={memory.type}>{memory.type}</span>
            <code>tick {memory.tick}</code>
            <em>importance {memory.importance}</em>
          </div>
          <p>{memory.text}</p>
          {memory.parents ? <button type="button">Show {memory.parents} parent memories</button> : null}
        </article>
      ))}
    </div>
  );
}

function BeliefPanel() {
  const beliefs = [
    { label: "Job security", value: 31, previous: 54 },
    { label: "Employer trust", value: 43, previous: 67 },
    { label: "Civic safety net", value: 69, previous: 62 },
    { label: "Economic optimism", value: 38, previous: 58 },
    { label: "Social belonging", value: 74, previous: 71 }
  ];
  return (
    <div className="belief-panel">
      {beliefs.map((belief) => (
        <div key={belief.label}>
          <div>
            <strong>{belief.label}</strong>
            <span>
              {belief.previous} → {belief.value}
            </span>
          </div>
          <i>
            <span style={{ "--value": `${belief.previous}%` } as React.CSSProperties} />
            <em style={{ "--value": `${belief.value}%` } as React.CSSProperties} />
          </i>
        </div>
      ))}
      <p>
        Synthetic belief vector. Values are simulation-state dimensions, not human psychological measurements.
      </p>
    </div>
  );
}

function InspectorView({ agent }: { agent: Agent }) {
  const [tab, setTab] = useState<"decision" | "timeline" | "memory" | "beliefs">("decision");
  return (
    <div className="view-canvas">
      <ViewHeader
        eyebrow={`${agent.id} · deliberate tick 4,201`}
        title="Agent inspector"
        description="Perception → memory → prompt → response → action → outcome, with every gap and reconstruction state visible."
        trailing={
          <div className="inspector-freshness">
            <span><CheckCircle2 size={14} /> sampled</span>
            <code>as_of_seq 8,812,443</code>
          </div>
        }
      />
      <div className="agent-summary-band">
        <div className="agent-large-symbol"><Bot size={25} /></div>
        <div>
          <strong>{agent.label}</strong>
          <code>{agent.id}</code>
        </div>
        <dl>
          <div><dt>Age</dt><dd>{agent.age}</dd></div>
          <div><dt>District</dt><dd>{agent.district}</dd></div>
          <div><dt>Occupation</dt><dd>{agent.occupation}</dd></div>
          <div><dt>Employer</dt><dd>{agent.employer}</dd></div>
          <div><dt>Income</dt><dd>{formatMoney(agent.income)}</dd></div>
          <div><dt>Wealth</dt><dd>{formatMoney(agent.wealth)}</dd></div>
        </dl>
        <div className="inspection-run-note">
          <CircleAlert size={15} />
          <span>Inspection-quality run: prompts retained and cognition sampling raised.</span>
        </div>
      </div>
      <div className="view-tabs" role="tablist" aria-label="Agent inspection sections">
        {[
          ["decision", "Decision path"],
          ["timeline", "Lifetime timeline"],
          ["memory", "Memory provenance"],
          ["beliefs", "Belief trajectories"]
        ].map(([value, label]) => (
          <button
            key={value}
            role="tab"
            aria-selected={tab === value}
            data-active={tab === value}
            onClick={() => setTab(value as typeof tab)}
            type="button"
          >
            {label}
          </button>
        ))}
      </div>
      <div className="tab-surface">
        {tab === "decision" ? <DecisionPath agent={agent} /> : null}
        {tab === "timeline" ? <TimelinePanel /> : null}
        {tab === "memory" ? <MemoryPanel /> : null}
        {tab === "beliefs" ? <BeliefPanel /> : null}
      </div>
    </div>
  );
}

function CausalView({
  onSelectAgent,
  onOpenSearch
}: {
  onSelectAgent: (agent: Agent) => void;
  onOpenSearch: () => void;
}) {
  const [directionParam, setDirectionParam] = useUrlParam("causalDirection", "causes");
  const direction: "causes" | "effects" =
    directionParam === "effects" ? "effects" : "causes";
  const [rootParam, setRootParam] = useUrlParam("causalRoot", "freeze");
  const rootOptions = [
    { id: "freeze", label: "Hiring freezes", share: "31%", count: "112 movers", tick: 4194, actor: "fm_031", kind: 5018 },
    { id: "closure", label: "Firm closures", share: "22%", count: "79 movers", tick: 4187, actor: "fm_014", kind: 5034 },
    { id: "expiry", label: "Contract expiry", share: "18%", count: "65 movers", tick: 4190, actor: "fm_022", kind: 5042 },
    { id: "exit", label: "Voluntary exit", share: "13%", count: "47 movers", tick: 4196, actor: "ag_0661", kind: 5012 }
  ];
  const selectedRoot =
    rootOptions.find((candidate) => candidate.id === rootParam) ?? rootOptions[0];
  const graphNodes: Array<
    [number, number, string, string, "root" | "event" | "agent" | "metric"]
  > =
    direction === "causes"
      ? [
          [110, 260, selectedRoot.label, `ev_${selectedRoot.tick}`, "root"],
          [308, 132, "Role exposed", "ev_77912", "event"],
          [308, 260, "Hours cut", "ev_78004", "event"],
          [308, 390, "Search begins", "ev_78441", "event"],
          [592, 210, "Agent 0421", "ag_0421", "agent"],
          [592, 260, "Agent 0744", "ag_0744", "agent"],
          [592, 310, "Agent 0239", "ag_0239", "agent"],
          [830, 260, "Unemployment", "+1.6pp", "metric"]
        ]
      : [
          [110, 260, "Agent decision", "ag_0421", "agent"],
          [308, 132, "Role accepted", "ev_81243", "event"],
          [308, 260, "Budget changed", "ev_81247", "event"],
          [308, 390, "Search closes", "ev_81254", "event"],
          [592, 210, "Household cash", "−6%", "metric"],
          [592, 260, "Firm staffing", "+1", "metric"],
          [592, 310, "Stress state", "−5%", "metric"],
          [830, 260, "Recorded effects", "3 events", "root"]
        ];
  return (
    <div className="view-canvas">
      <ViewHeader
        eyebrow="Metric unemployment_rate · tick 4,201 · window 240"
        title="Why did this move?"
        description="Ranked roots account for 84% of recorded mover events. Uncovered share remains visible."
        trailing={
          <div className="segmented-control">
            <button type="button" data-active={direction === "causes"} onClick={() => setDirectionParam("causes")}>Causes</button>
            <button type="button" data-active={direction === "effects"} onClick={() => setDirectionParam("effects")}>Effects</button>
          </div>
        }
      />
      <div className="causal-layout">
        <aside className="root-ranking">
          <div className="section-label">Ranked roots</div>
          {rootOptions.map((root, index) => (
            <button
              key={root.id}
              type="button"
              data-active={selectedRoot.id === root.id}
              onClick={() => setRootParam(root.id)}
            >
              <span>{index + 1}</span>
              <strong>{root.label}</strong>
              <em>{root.share}</em>
              <small>{root.count}</small>
            </button>
          ))}
          <div className="coverage-block">
            <span>Covered share</span>
            <strong>84%</strong>
            <i><span style={{ width: "84%" }} /></i>
            <p>16% has no recorded cause chain. POLIS does not invent one.</p>
          </div>
        </aside>
        <section className="causal-graph-panel">
          <svg viewBox="0 0 920 540" role="img" aria-label="Synthetic causal graph for unemployment movement">
            <title>Synthetic causal graph</title>
            <g className="causal-edges">
              <path d="M110 260 C200 260 202 132 308 132" />
              <path d="M110 260 C210 260 204 260 308 260" />
              <path d="M110 260 C200 260 202 390 308 390" />
              <path d="M412 132 C500 132 504 210 592 210" />
              <path d="M412 260 C500 260 504 260 592 260" />
              <path d="M412 390 C500 390 504 310 592 310" />
              <path d="M698 210 C760 210 760 260 830 260" />
              <path d="M698 260 C760 260 760 260 830 260" />
              <path d="M698 310 C760 310 760 260 830 260" />
            </g>
            {graphNodes.map(([x, y, label, address, kind]) => (
              <g
                className="causal-node"
                data-kind={kind}
                key={String(address)}
                transform={`translate(${x} ${y})`}
                role={kind === "agent" ? "button" : undefined}
                tabIndex={kind === "agent" ? 0 : undefined}
                onClick={
                  kind === "agent"
                    ? () => {
                        const agent = agents.find((candidate) => candidate.id === address);
                        if (agent) onSelectAgent(agent);
                      }
                    : undefined
                }
                onKeyDown={
                  kind === "agent"
                    ? (event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          const agent = agents.find((candidate) => candidate.id === address);
                          if (agent) onSelectAgent(agent);
                        }
                      }
                    : undefined
                }
              >
                <rect x="-54" y="-33" width="108" height="66" rx="3" />
                <text className="node-label" textAnchor="middle" y="-4">{label}</text>
                <text className="node-address" textAnchor="middle" y="16">{address}</text>
              </g>
            ))}
          </svg>
          <div className="graph-legend">
            <span><i data-kind="root" /> root event</span>
            <span><i data-kind="event" /> downstream event</span>
            <span><i data-kind="agent" /> agent</span>
            <span><i data-kind="metric" /> metric mover</span>
          </div>
        </section>
        <aside className="causal-detail">
          <div className="section-label">Selected root</div>
          <code>seq 8,811,{selectedRoot.tick} · kind {selectedRoot.kind}</code>
          <h2>{selectedRoot.label}</h2>
          <p>
            Selected root at tick {selectedRoot.tick.toLocaleString()} links recorded labour
            events to the mover set without filling uncovered causal gaps.
          </p>
          <dl>
            <div><dt>Tick</dt><dd>{selectedRoot.tick.toLocaleString()}</dd></div>
            <div><dt>Actor</dt><dd>{selectedRoot.actor}</dd></div>
            <div><dt>Downstream movers</dt><dd>{selectedRoot.count.split(" ")[0]}</dd></div>
            <div><dt>Subtree share</dt><dd>{selectedRoot.share}</dd></div>
          </dl>
          <button className="secondary-action" type="button" onClick={onOpenSearch}>
            Open in event search <ArrowRight size={14} />
          </button>
          <div className="truncation-note"><CheckCircle2 size={15} /> Not truncated · depth 7 / 12</div>
        </aside>
      </div>
    </div>
  );
}

function SearchView({ onInspect }: { onInspect: (agent: Agent) => void }) {
  const [query, setQuery] = useUrlParam("eventQuery", "");
  const [kind, setKind] = useUrlParam("eventKind", "all");
  const [jsonPath, setJsonPath] = useUrlParam("jsonPath", "");
  const [showJsonPath, setShowJsonPath] = useState(Boolean(jsonPath));
  const filtered = events.filter(
    (event) =>
      (kind === "all" || event.kind === kind) &&
      `${event.kind} ${event.actor} ${event.summary}`.toLowerCase().includes(query.toLowerCase())
  );
  return (
    <div className="view-canvas">
      <ViewHeader
        eyebrow="Run run_05_20_a · bounded event query"
        title="Event log search"
        description="Every search is scoped to a run and either a kind or tick range. Unsafe full-partition scans are refused."
      />
      <div className="search-query-bar">
        <label className="search-field">
          <Search size={16} />
          <span className="sr-only">Search event text</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search event text, actor, or subject" />
        </label>
        <label>
          <span>Kind</span>
          <select value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="all">All registered kinds</option>
            {Array.from(new Set(events.map((event) => event.kind))).map((eventKind) => (
              <option key={eventKind}>{eventKind}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Tick range</span>
          <input value="4,160 — 4,201" readOnly />
        </label>
        <button
          className="secondary-action"
          type="button"
          aria-expanded={showJsonPath}
          onClick={() => {
            if (showJsonPath) setJsonPath("");
            setShowJsonPath((current) => !current);
          }}
        >
          {showJsonPath ? "Remove JSON path" : "Add JSON path"}
        </button>
        {showJsonPath ? (
          <label className="json-path-field">
            <span>JSON path</span>
            <input
              value={jsonPath}
              onChange={(event) => setJsonPath(event.target.value)}
              placeholder="payload.subject_id"
            />
          </label>
        ) : null}
      </div>
      <div className="query-safety">
        <ShieldCheck size={16} />
        <span>Index-safe query</span>
        <code>
          run_id + tick range · ev_tick
          {jsonPath ? ` · ${jsonPath}` : ""}
        </code>
      </div>
      <div className="event-results">
        <div className="event-results-header">
          <span>{filtered.length} matching events</span>
          <code>as_of_seq 8,812,443</code>
        </div>
        {filtered.map((event) => {
          const eventAgent = agents.find((candidate) => candidate.id === event.actor);
          return (
            <article className="event-row" key={event.seq}>
              <div className="event-address">
                <code>seq {event.seq}</code>
                <span>tick {event.tick}</span>
              </div>
              <div className="event-kind-symbol" data-warning={event.integrity === "warning"}>
                {event.integrity === "warning" ? <CircleAlert size={17} /> : <Activity size={17} />}
              </div>
              <div className="event-summary">
                <strong>{event.kind}</strong>
                <p>{event.summary}</p>
                <span>actor {event.actor}</span>
              </div>
              <div className="integrity-label" data-warning={event.integrity === "warning"}>
                {event.integrity}
              </div>
              <button
                className="row-action"
                onClick={eventAgent ? () => onInspect(eventAgent) : undefined}
                disabled={!eventAgent}
                type="button"
                aria-label={
                  eventAgent ? `Inspect ${eventAgent.label}` : "No agent inspector for this event"
                }
              >
                <ArrowRight size={15} />
              </button>
            </article>
          );
        })}
        {filtered.length === 0 ? (
          <div className="empty-state">
            <Search size={24} />
            <strong>No events in this bounded query</strong>
            <p>Adjust the text or kind filter while keeping a safe tick range.</p>
            <button type="button" onClick={() => { setQuery(""); setKind("all"); }}>Reset query</button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function CompareView() {
  const [driftParam, setDriftParam] = useUrlParam("allowMetricDrift", "0");
  const allowDrift = driftParam === "1";
  return (
    <div className="view-canvas">
      <ViewHeader
        eyebrow="Comparison · 2 selected runs"
        title="Run comparison"
        description="Reproducibility differences are rendered before any series, exactly where they cannot be overlooked."
        trailing={<div className="run-pair"><code>run_baseline_12</code><span>vs</span><code>run_treatment_12</code></div>}
      />
      <section className="tuple-diff-section">
        <div className="section-heading-row">
          <div>
            <span className="section-label">1 · Reproducibility tuple diff</span>
            <h2>One undeclared difference blocks the metric overlay</h2>
          </div>
          <div className="blocking-count"><CircleAlert size={16} /> 1 blocking</div>
        </div>
        <div className="mobile-scroll-hint">Swipe horizontally to inspect every tuple field</div>
        <div className="table-scroll">
          <table className="data-table diff-table">
            <thead><tr><th>Field</th><th>Baseline</th><th>Treatment</th><th>Declared axis</th><th>Verdict</th></tr></thead>
            <tbody>
              {comparisonDiffs.map((diff) => (
                <tr key={diff.field} data-blocking={diff.blocking}>
                  <td><code>{diff.field}</code></td>
                  <td>{diff.base}</td>
                  <td>{diff.compare}</td>
                  <td>{diff.declared ? "yes" : "no"}</td>
                  <td>{diff.blocking ? <span className="diff-blocking">Blocking drift</span> : <span className="diff-clear">Aligned</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="drift-section">
        <div className="metric-drift-banner" data-override={allowDrift}>
          <CircleAlert size={20} />
          <div>
            <strong>{allowDrift ? "Metric drift override active" : "Metric overlay refused"}</strong>
            <p>
              unemployment_rate has a different definition_hash across these runs. The chart remains
              unavailable unless the explicit override is carried onto every rendered output.
            </p>
          </div>
          <label className="override-toggle">
            <input
              type="checkbox"
              checked={allowDrift}
              onChange={(event) => setDriftParam(event.target.checked ? "1" : "0")}
            />
            <span />
            Allow drift
          </label>
        </div>
        <div className="comparison-chart" data-disabled={!allowDrift}>
          {allowDrift ? (
            <>
              <div className="drift-stamp">Metric drift · overridden</div>
              <div className="comparison-lines">
                <svg viewBox="0 0 900 260" preserveAspectRatio="none" role="img" aria-label="Synthetic drift-overridden comparison chart">
                  <path d={pathFor(metrics.unemployment.points, 900, 260)} stroke="var(--cobalt)" fill="none" strokeWidth="2.4" />
                  <path d={pathFor(metrics.unemployment.points.map((p, i) => p + Math.sin(i) * 0.8 - 0.6), 900, 260)} stroke="var(--vermilion)" fill="none" strokeWidth="2.4" />
                </svg>
              </div>
              <div className="chart-series-legend"><span><i data-series="base" /> baseline</span><span><i data-series="treatment" /> treatment</span></div>
            </>
          ) : (
            <div className="refused-chart">
              <GitCompareArrows size={32} />
              <strong>Series withheld</strong>
              <p>Resolve the metric manifest difference or deliberately allow drift.</p>
            </div>
          )}
        </div>
      </section>
      <section className="gate-matrix-section">
        <div className="section-label">3 · Gate matrix</div>
        <div className="mobile-scroll-hint">Swipe horizontally to inspect all seven gates</div>
        <div className="table-scroll">
          <table className="gate-matrix">
            <thead><tr><th>Run</th>{["V1","V2","V3","V4","V5","V6","V7"].map((gate) => <th key={gate}>{gate}</th>)}</tr></thead>
            <tbody>
              <tr><td>Baseline</td>{["pass","pass","pass","pass","pass","pass","n/a"].map((result, i) => <td key={i} data-result={result}>{result}</td>)}</tr>
              <tr><td>Treatment</td>{["pass","pass","pass","warn","pass","n/a","n/a"].map((result, i) => <td key={i} data-result={result}>{result}</td>)}</tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function ArenaView() {
  return (
    <div className="view-canvas">
      <ViewHeader
        eyebrow="External agent arena · completed runs only"
        title="Capability vectors"
        description="Nine disclosed dimensions per model-and-scaffold cell. No composite score and no overall ranking."
        trailing={<div className="freshness-chip"><Database size={14} /> Snapshot at tick 4,201</div>}
      />
      <div className="arena-disclosure">
        <ShieldCheck size={18} />
        <div>
          <strong>Comparison contract</strong>
          <p>Action budget and tick deadline are held equal. Model tier, scaffold, custody, and eligibility remain attached to every row.</p>
        </div>
      </div>
      <div className="mobile-scroll-hint arena-scroll-hint">
        Swipe horizontally; implementation labels remain pinned
      </div>
      <div className="scorecard-wrap">
        <table className="scorecard-table">
          <thead>
            <tr>
              <th>Agent implementation</th>
              {scoreDimensions.map((dimension) => <th key={dimension}><span>{dimension}</span></th>)}
              <th>Eligibility</th>
            </tr>
          </thead>
          <tbody>
            {scorecards.map((row) => (
              <tr key={row.id} data-ineligible={!row.eligible}>
                <td>
                  <strong>{row.model}</strong>
                  <span>{row.scaffold}</span>
                  <code>{row.id}</code>
                </td>
                {row.dimensions.map((value, index) => (
                  <td key={index}>
                    <div className="dimension-cell" title={`${scoreDimensions[index]}: ${value}`}>
                      <span style={{ "--score": `${value}%` } as React.CSSProperties} />
                      <strong>{value}</strong>
                    </div>
                  </td>
                ))}
                <td>
                  {row.eligible ? (
                    <span className="eligibility eligible"><CheckCircle2 size={14} /> Eligible</span>
                  ) : (
                    <span className="eligibility ineligible"><CircleAlert size={14} /> Ineligible<small>{row.reason}</small></span>
                  )}
                </td>
              </tr>
            ))}
            <tr className="native-reference">
              <td><strong>Native reference</strong><span>Distribution band</span><code>1,000 native agents</code></td>
              {scoreDimensions.map((_, index) => (
                <td key={index}><div className="reference-band" style={{ "--start": `${44 + (index % 3) * 3}%`, "--end": `${72 + (index % 4) * 4}%` } as React.CSSProperties} /></td>
              ))}
              <td><span className="eligibility eligible">Reference only</span></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div className="scorecard-footnotes">
        <span>No composite score</span>
        <span>No default overall sort</span>
        <span>Synthetic demonstration data</span>
        <span>Live scorecard disabled</span>
      </div>
    </div>
  );
}

function MobileStatusStrip() {
  return (
    <div className="mobile-status-strip" aria-label="Run status">
      <span><ShieldCheck size={13} /> Read-only</span>
      <span><i className="status-dot status-good" /> Nominal</span>
      <span><Wifi size={13} /> Fresh 2.7s</span>
      <span><Activity size={13} /> Lag 0.4</span>
    </div>
  );
}

function TraceBreadcrumb({
  agent,
  onNavigate
}: {
  agent: Agent;
  onNavigate: (view: ViewId, agent?: Agent) => void;
}) {
  const outcome = evidenceForAgent(agent).at(-1)?.summary ?? "Recorded outcome";
  return (
    <aside className="trace-breadcrumb" aria-label="Persistent evidence trace">
      <span className="trace-label">Evidence trace</span>
      <button type="button" onClick={() => onNavigate("map", agent)}>
        <Activity size={13} />
        Macro signal
      </button>
      <ArrowRight size={13} aria-hidden="true" />
      <button type="button" onClick={() => onNavigate("inspector", agent)}>
        <Bot size={13} />
        {agent.label}
      </button>
      <ArrowRight size={13} aria-hidden="true" />
      <span className="trace-outcome" title={outcome}>
        <CheckCircle2 size={13} />
        {outcome}
      </span>
    </aside>
  );
}

export default function App() {
  const initialRoute = routeFromUrl();
  const [activeView, setActiveView] = useState<ViewId>(initialRoute.view);
  const [selectedAgent, setSelectedAgent] = useState<Agent>(initialRoute.agent);
  const [announcement, setAnnouncement] = useState(
    `${navItems.find((item) => item.id === initialRoute.view)?.label ?? "Map"} view`
  );

  useEffect(() => {
    const syncRoute = () => {
      const route = routeFromUrl();
      setActiveView(route.view);
      setSelectedAgent(route.agent);
      setAnnouncement(
        `${navItems.find((item) => item.id === route.view)?.label ?? "Map"} view`
      );
      window.requestAnimationFrame(() => {
        document.getElementById("main-content")?.focus({ preventScroll: true });
      });
    };
    window.addEventListener("popstate", syncRoute);
    return () => window.removeEventListener("popstate", syncRoute);
  }, []);

  const navigate = (view: ViewId, agent = selectedAgent) => {
    const url = new URL(window.location.href);
    url.searchParams.set("view", view);
    url.searchParams.set("agent", agent.id);
    window.history.pushState({}, "", url);
    setActiveView(view);
    setSelectedAgent(agent);
    setAnnouncement(`${navItems.find((item) => item.id === view)?.label ?? view} view`);
    window.requestAnimationFrame(() => {
      document.getElementById("main-content")?.focus({ preventScroll: true });
    });
  };

  const inspectAgent = (agent = selectedAgent) => {
    navigate("inspector", agent);
  };

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <TopBar />
      <AtlasNav activeView={activeView} onChange={(view) => navigate(view)} />
      <main id="main-content" className="app-main" tabIndex={-1}>
        <div className="sr-only" aria-live="polite" aria-atomic="true">
          {announcement}
        </div>
        <MobileStatusStrip />
        {activeView !== "map" ? (
          <TraceBreadcrumb agent={selectedAgent} onNavigate={navigate} />
        ) : null}
        {activeView === "map" ? (
          <MapView
            selectedAgent={selectedAgent}
            onInspect={() => inspectAgent()}
            onOpenCharts={() => navigate("charts")}
          />
        ) : null}
        {activeView === "charts" ? <ChartsView onInspect={() => inspectAgent()} /> : null}
        {activeView === "agents" ? <AgentsView onSelect={inspectAgent} /> : null}
        {activeView === "inspector" ? <InspectorView agent={selectedAgent} /> : null}
        {activeView === "causal" ? (
          <CausalView
            onSelectAgent={inspectAgent}
            onOpenSearch={() => navigate("search")}
          />
        ) : null}
        {activeView === "search" ? <SearchView onInspect={inspectAgent} /> : null}
        {activeView === "compare" ? <CompareView /> : null}
        {activeView === "arena" ? <ArenaView /> : null}
      </main>
    </div>
  );
}
