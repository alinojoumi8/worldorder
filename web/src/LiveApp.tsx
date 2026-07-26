import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  Bot,
  ChartNoAxesColumnIncreasing,
  CircleAlert,
  FileSearch,
  Map as MapIcon,
  Network,
  Search,
  Trophy,
  UsersRound,
  Wifi
} from "lucide-react";
import {
  liveSocketUrl,
  polisApi,
  type AgentRecord,
  type DistrictRecord,
  type Freshness,
  type InspectorTrace,
  type MapAgent,
  type MetricSeries,
  type PlaceRecord,
  type RunRecord
} from "./liveApi";

type LiveView = "map" | "charts" | "agents" | "inspector";

const liveViews: Array<{ id: LiveView; label: string; icon: typeof MapIcon }> = [
  { id: "map", label: "Map", icon: MapIcon },
  { id: "charts", label: "Charts", icon: ChartNoAxesColumnIncreasing },
  { id: "agents", label: "Agents", icon: UsersRound },
  { id: "inspector", label: "Inspector", icon: FileSearch }
];

const futureViews = [
  { label: "Causal", icon: Network },
  { label: "Search", icon: Search },
  { label: "Compare", icon: Activity },
  { label: "Arena", icon: Trophy }
];

type LoadedData = {
  run: RunRecord;
  freshness: Freshness;
  districts: DistrictRecord[];
  places: PlaceRecord[];
  mapAgents: MapAgent[];
  agents: AgentRecord[];
  metrics: MetricSeries[];
  unavailable: string[];
};

function formatValue(metric: MetricSeries, value: number): string {
  if (metric.unit === "bp") return `${(value / 100).toFixed(1)}%`;
  if (metric.unit === "index_0_100") return value.toFixed(1);
  return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(3);
}

function Sparkline({ series }: { series: MetricSeries }) {
  const points = series.points;
  if (!points.length) return <div className="live-empty">No samples recorded.</div>;
  const values = points.map((point) => Number(point.value));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const spread = max - min || 1;
  const path = points
    .map((point, index) => {
      const x = points.length === 1 ? 0 : (index / (points.length - 1)) * 100;
      const y = 92 - ((Number(point.value) - min) / spread) * 82;
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
  const latest = values.at(-1) ?? 0;
  return (
    <article className="live-chart-card">
      <div>
        <span className="live-kicker">{series.unit}</span>
        <h3>{series.metric}</h3>
        <strong>{formatValue(series, latest)}</strong>
      </div>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-label={series.definition}>
        <path d={path} vectorEffect="non-scaling-stroke" />
      </svg>
      <p>{series.definition}</p>
      <small>
        Ticks {points[0].tick.toLocaleString()}–{points.at(-1)?.tick.toLocaleString()}
      </small>
    </article>
  );
}

function CityMap({
  districts,
  places,
  agents,
  selected,
  onSelect
}: {
  districts: DistrictRecord[];
  places: PlaceRecord[];
  agents: MapAgent[];
  selected: string | null;
  onSelect: (agentId: string) => void;
}) {
  const bounds = useMemo(() => {
    const boxes = districts.map((district) => district.polygon.bbox);
    return {
      width: Math.max(1, ...boxes.map((box) => box[2])),
      height: Math.max(1, ...boxes.map((box) => box[3]))
    };
  }, [districts]);
  const sx = (x: number) => (x / bounds.width) * 1000;
  const sy = (y: number) => (y / bounds.height) * 650;
  return (
    <svg className="live-map" viewBox="0 0 1000 650" role="img" aria-label="Current city map">
      {districts.map((district, index) => {
        const [x1, y1, x2, y2] = district.polygon.bbox;
        return (
          <g key={district.district_id}>
            <rect
              x={sx(x1)}
              y={sy(y1)}
              width={sx(x2 - x1)}
              height={sy(y2 - y1)}
              className={`live-district district-${index % 6}`}
            />
            <text x={sx(x1) + 12} y={sy(y1) + 24}>
              {district.name}
            </text>
          </g>
        );
      })}
      {places.map((place) => (
        <rect
          key={place.place_id}
          x={sx(place.x) - 3}
          y={sy(place.y) - 3}
          width="6"
          height="6"
          className="live-place"
        >
          <title>{`${place.name} · ${place.type}`}</title>
        </rect>
      ))}
      {agents.map((agent) => (
        <circle
          key={agent.agent_id}
          cx={sx(agent.x)}
          cy={sy(agent.y)}
          r={selected === agent.agent_id ? 7 : 3.5}
          className={`live-agent mode-${agent.mode}${selected === agent.agent_id ? " selected" : ""}`}
          onClick={() => onSelect(agent.agent_id)}
          tabIndex={0}
        >
          <title>{`${agent.agent_id} · ${agent.mode}`}</title>
        </circle>
      ))}
    </svg>
  );
}

function TraceValue({ value }: { value: unknown }) {
  if (value === "not recorded") return <span className="live-not-recorded">not recorded</span>;
  if (typeof value === "string") return <p>{value}</p>;
  return <pre>{JSON.stringify(value, null, 2)}</pre>;
}

export default function LiveApp() {
  const [view, setView] = useState<LiveView>("map");
  const [data, setData] = useState<LoadedData | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [trace, setTrace] = useState<InspectorTrace | null>(null);
  const [status, setStatus] = useState("Connecting to the Observatory API…");
  const [socketState, setSocketState] = useState<"live" | "stored" | "lagged">("stored");

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const runs = await polisApi.runs();
        const run = runs.items[0];
        if (!run) throw new Error("No simulation runs are stored yet.");
        const [mapStatic, map, agents, catalogue, wellbeing, deliberate, diversity] =
          await Promise.all([
            polisApi.mapStatic(run.run_id),
            polisApi.map(run.run_id),
            polisApi.agents(run.run_id),
            polisApi.metricCatalogue(run.run_id),
            polisApi.metric(run.run_id, "city.wellbeing_mean"),
            polisApi.metric(run.run_id, "sys.cognition.deliberate_share"),
            polisApi.metric(run.run_id, "sys.actions.unique")
          ]);
        if (cancelled) return;
        setData({
          run,
          freshness: {
            as_of_tick: map.as_of_tick,
            as_of_seq: map.as_of_seq,
            engine: map.engine
          },
          districts: mapStatic.districts,
          places: mapStatic.places,
          mapAgents: map.agents,
          agents: agents.items,
          metrics: [wellbeing, deliberate, diversity],
          unavailable: catalogue.unavailable_in_m1
        });
        setSelected(agents.items[0]?.agent_id ?? null);
        setStatus(`${run.name} loaded from the read-only projection`);
      } catch (error) {
        if (!cancelled) setStatus(error instanceof Error ? error.message : String(error));
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!data) return;
    const socket = new WebSocket(liveSocketUrl(data.run.run_id));
    socket.onopen = () => setSocketState("live");
    socket.onclose = () => setSocketState("stored");
    socket.onerror = () => setSocketState("stored");
    socket.onmessage = (message) => {
      const frame = JSON.parse(String(message.data)) as {
        op: string;
        kind?: number;
        tick?: number;
        as_of_seq?: number;
        dropped?: number;
        payload?: { agents?: MapAgent[]; metrics?: Record<string, number> };
      };
      if (frame.op === "lag") {
        setSocketState("lagged");
        return;
      }
      if (frame.op === "hello") return;
      if (frame.kind === 90051 && frame.payload?.agents) {
        setData((current) => {
          if (!current) return current;
          const positions = new Map(
            frame.payload?.agents?.map((agent) => [agent.agent_id, agent]) ?? []
          );
          return {
            ...current,
            mapAgents: current.mapAgents.map(
              (agent) => positions.get(agent.agent_id) ?? agent
            )
          };
        });
      }
      if (frame.kind === 90050 && frame.tick !== undefined) {
        setData((current) =>
          current
            ? {
                ...current,
                freshness: {
                  ...current.freshness,
                  as_of_tick: frame.tick ?? current.freshness.as_of_tick,
                  engine: {
                    ...current.freshness.engine,
                    tick: frame.tick,
                    fresh: true,
                    projection_lag_ticks: 0
                  }
                }
              }
            : current
        );
      }
    };
    return () => socket.close();
  }, [data?.run.run_id]);

  useEffect(() => {
    if (!data || !selected || view !== "inspector") return;
    let cancelled = false;
    setTrace(null);
    polisApi
      .inspector(data.run.run_id, selected, data.freshness.as_of_tick)
      .then((value) => {
        if (!cancelled) setTrace(value);
      })
      .catch(() => {
        if (!cancelled) setTrace(null);
      });
    return () => {
      cancelled = true;
    };
  }, [data?.run.run_id, data?.freshness.as_of_tick, selected, view]);

  const selectedAgent =
    data?.agents.find((agent) => agent.agent_id === selected) ?? data?.agents[0] ?? null;

  const inspect = (agentId: string) => {
    setSelected(agentId);
    setView("inspector");
  };

  return (
    <div className="live-shell">
      <header className="live-topbar">
        <div>
          <span className="live-kicker">Living Systems Atlas · M1</span>
          <h1>POLIS Observatory</h1>
        </div>
        <div className="live-run-state">
          <span className={`live-dot ${socketState}`} />
          <div>
            <strong>{data?.run.name ?? "No run"}</strong>
            <small>
              {socketState === "live" ? "WebSocket connected" : "Stored projection"}
            </small>
          </div>
          <code>T{data?.freshness.as_of_tick ?? 0}</code>
          <code>S{data?.freshness.as_of_seq ?? 0}</code>
        </div>
      </header>
      <nav className="live-nav" aria-label="Observatory views">
        {liveViews.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              type="button"
              className={view === item.id ? "active" : ""}
              onClick={() => setView(item.id)}
            >
              <Icon size={17} />
              {item.label}
            </button>
          );
        })}
        <div className="live-nav-divider" />
        {futureViews.map((item) => {
          const Icon = item.icon;
          return (
            <button key={item.label} type="button" disabled title="Available in M6">
              <Icon size={17} />
              {item.label}
              <small>M6</small>
            </button>
          );
        })}
      </nav>
      <main className="live-main">
        <div className="live-context-bar">
          <span>
            <Wifi size={14} /> {status}
          </span>
          <span>
            lag {data?.freshness.engine.projection_lag_ticks ?? 0} ticks
          </span>
          <span>{data?.run.scale.toLocaleString() ?? 0} citizens</span>
        </div>

        {!data ? (
          <section className="live-callout">
            <CircleAlert size={20} />
            <div>
              <h2>Backend data is required</h2>
              <p>{status}</p>
              <p>
                Start it with <code>uv run polis observe --config configs/smoke.yaml</code>,
                or open <a href="?demo=1">the labelled development demo</a>.
              </p>
            </div>
          </section>
        ) : null}

        {data && view === "map" ? (
          <section className="live-workspace">
            <div className="live-panel live-map-panel">
              <div className="live-panel-title">
                <div>
                  <span className="live-kicker">Current projection</span>
                  <h2>City map</h2>
                </div>
                <span>{data.mapAgents.length.toLocaleString()} located agents</span>
              </div>
              <CityMap
                districts={data.districts}
                places={data.places}
                agents={data.mapAgents}
                selected={selected}
                onSelect={setSelected}
              />
            </div>
            <aside className="live-panel live-selection">
              <span className="live-kicker">Selected citizen</span>
              <h2>{selectedAgent?.display_name ?? "Select a map point"}</h2>
              {selectedAgent ? (
                <>
                  <dl>
                    <dt>District</dt><dd>{selectedAgent.district_id}</dd>
                    <dt>Mode</dt><dd>{selectedAgent.cognition_mode}</dd>
                    <dt>Wellbeing</dt><dd>{selectedAgent.state.wellbeing?.toFixed(1) ?? "—"}</dd>
                    <dt>Education</dt><dd>{selectedAgent.education_level}</dd>
                  </dl>
                  <button type="button" className="live-primary" onClick={() => inspect(selectedAgent.agent_id)}>
                    <Bot size={16} /> Inspect evidence
                  </button>
                </>
              ) : null}
            </aside>
          </section>
        ) : null}

        {data && view === "charts" ? (
          <section>
            <div className="live-view-heading">
              <span className="live-kicker">Recorded research metrics</span>
              <h2>System indicators</h2>
              <p>Definitions and units come from the backend metric catalogue.</p>
            </div>
            <div className="live-chart-grid">
              {data.metrics.map((series) => <Sparkline key={series.metric} series={series} />)}
            </div>
            <div className="live-unavailable">
              Economy-only metrics unavailable in M1: {data.unavailable.join(", ")}.
            </div>
          </section>
        ) : null}

        {data && view === "agents" ? (
          <section>
            <div className="live-view-heading">
              <span className="live-kicker">Read-only projection</span>
              <h2>Citizens</h2>
              <p>{data.agents.length.toLocaleString()} records loaded at tick {data.freshness.as_of_tick}.</p>
            </div>
            <div className="live-table-wrap">
              <table className="live-table">
                <thead>
                  <tr><th>Citizen</th><th>Age</th><th>District</th><th>Education</th><th>Mode</th><th>Wellbeing</th></tr>
                </thead>
                <tbody>
                  {data.agents.map((agent) => (
                    <tr key={agent.agent_id} onClick={() => inspect(agent.agent_id)}>
                      <td><strong>{agent.display_name}</strong><small>{agent.agent_id}</small></td>
                      <td>{agent.age_years}</td>
                      <td>{agent.district_id}</td>
                      <td>{agent.education_level}</td>
                      <td><span className={`live-mode mode-${agent.cognition_mode}`}>{agent.cognition_mode}</span></td>
                      <td>{agent.state.wellbeing?.toFixed(1) ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ) : null}

        {data && view === "inspector" ? (
          <section>
            <div className="live-view-heading">
              <span className="live-kicker">Sampled cognition trace</span>
              <h2>{selectedAgent?.display_name ?? "Citizen inspector"}</h2>
              <p>
                Perception → salience → memory → prompt → response → action → outcome,
                as recorded at tick {data.freshness.as_of_tick}.
              </p>
            </div>
            {!trace ? <div className="live-empty">Loading trace…</div> : (
              <div className="live-trace-grid">
                {(["perception", "salience", "retrieval", "prompt", "response", "action", "validation", "outcome"] as const).map((step, index) => (
                  <article key={step} className="live-trace-card">
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <h3>{step}</h3>
                    <TraceValue value={trace[step]} />
                  </article>
                ))}
              </div>
            )}
          </section>
        ) : null}
      </main>
    </div>
  );
}
