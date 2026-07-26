export type Freshness = {
  as_of_tick: number;
  as_of_seq: number;
  engine: {
    tick?: number;
    fresh: boolean;
    projection_lag_ticks: number;
    heartbeat_age_s?: number | null;
  };
};

export type RunRecord = {
  run_id: string;
  name: string;
  status: string;
  last_tick: number;
  scale: number;
  started_at: string;
  ended_at: string | null;
  terminal_hash: string | null;
  tags: string[];
};

export type DistrictRecord = {
  district_id: string;
  name: string;
  polygon: { bbox: [number, number, number, number] };
  properties: Record<string, unknown>;
};

export type PlaceRecord = {
  place_id: string;
  district_id: string;
  type: string;
  name: string;
  x: number;
  y: number;
  capacity: number;
};

export type MapAgent = {
  agent_id: string;
  current_place_id: string;
  x: number;
  y: number;
  mode: "reflex" | "deliberate" | "reflect";
  district_id: string;
  salience?: number;
};

export type AgentRecord = {
  agent_id: string;
  display_name: string;
  age_years: number;
  district_id: string;
  current_place_id: string;
  education_level: string;
  employment_status: string;
  wealth_cents: number;
  health: number;
  cognition_mode: "reflex" | "deliberate" | "reflect";
  state: { wellbeing?: number; identity_summary?: string };
  as_of_tick: number;
  as_of_seq: number;
};

export type MetricDefinition = {
  id: string;
  unit: string;
  cadence: string;
  definition: string;
};

export type MetricSeries = Freshness & {
  metric: string;
  definition: string;
  unit: string;
  cadence: string;
  points: Array<{ tick: number; value: number; as_of_seq: number }>;
};

export type InspectorTrace = Freshness & {
  recording: "sampled" | "not recorded";
  perception: unknown;
  salience: unknown;
  retrieval: unknown;
  prompt: unknown;
  response: unknown;
  action: unknown;
  validation: unknown;
  outcome: unknown;
};

const API_ROOT = (import.meta.env.VITE_POLIS_API_BASE as string | undefined) ?? "";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${detail}`);
  }
  return response.json() as Promise<T>;
}

export const polisApi = {
  runs: () =>
    getJson<{ items: RunRecord[] } & Freshness>("/api/v1/runs"),
  mapStatic: (runId: string) =>
    getJson<
      {
        districts: DistrictRecord[];
        places: PlaceRecord[];
        tile_raster_rle: Array<[number, number]>;
      } & Freshness
    >(`/api/v1/runs/${runId}/map/static`),
  map: (runId: string) =>
    getJson<{ tick: number; agents: MapAgent[] } & Freshness>(
      `/api/v1/runs/${runId}/map`
    ),
  agents: (runId: string, cursor?: string) =>
    getJson<{ items: AgentRecord[]; next_cursor: string | null } & Freshness>(
      `/api/v1/runs/${runId}/agents?limit=500${
        cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""
      }`
    ),
  metricCatalogue: (runId: string) =>
    getJson<
      {
        items: MetricDefinition[];
        unavailable_in_m1: string[];
      } & Freshness
    >(`/api/v1/runs/${runId}/metrics/catalogue`),
  metric: (runId: string, metric: string) =>
    getJson<MetricSeries>(
      `/api/v1/runs/${runId}/metrics?metric=${encodeURIComponent(metric)}`
    ),
  inspector: (runId: string, agentId: string, tick: number) =>
    getJson<InspectorTrace>(
      `/api/v1/runs/${runId}/agents/${agentId}/tick/${tick}`
    )
};

export async function allAgents(
  runId: string
): Promise<{ items: AgentRecord[] } & Freshness> {
  const items: AgentRecord[] = [];
  let cursor: string | undefined;
  let freshness: Freshness | undefined;
  do {
    const page = await polisApi.agents(runId, cursor);
    items.push(...page.items);
    freshness = page;
    cursor = page.next_cursor ?? undefined;
  } while (cursor);
  if (!freshness) throw new Error("Agent projection returned no page.");
  return { ...freshness, items };
}

export function liveSocketUrl(runId: string): string {
  const configured = import.meta.env.VITE_POLIS_WS_BASE as string | undefined;
  const base =
    configured ??
    `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`;
  return `${base}/api/v1/ws/live?run_id=${encodeURIComponent(runId)}`;
}
