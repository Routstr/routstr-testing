import {
  Run,
  RunCreated,
  RunDetail,
  RunRequest,
  ScenarioDetail,
  ScenarioSummary,
  TargetProfile,
} from '@/lib/types';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed: ${response.status}`);
  }

  return (await response.json()) as T;
}

function buildRunBody(scenarioId: string, req: RunRequest) {
  const remoteNodes = req.remoteNodes ?? [];
  const remoteUrls = remoteNodes
    .map((entry) => entry.url.trim())
    .filter(Boolean);
  // Admin tokens are positional with the URL list — keep the slot even when
  // a given node has no token, so the server can correlate index → URL.
  const adminTokens = remoteNodes.map((entry) => entry.adminToken ?? '');
  return {
    scenario_id: scenarioId,
    cashu_token: req.cashuToken,
    target_profile: req.targetProfile,
    ...(req.targetProfile === 'remote'
      ? {
          remote_node_urls: remoteUrls,
          // Only send admin_tokens if at least one is non-empty; the server
          // treats undefined as "no admin auth", which is the safer default.
          ...(adminTokens.some((t) => t)
            ? { remote_admin_tokens: adminTokens }
            : {}),
        }
      : {}),
  };
}

export const api = {
  listScenarios: () => request<ScenarioSummary[]>('/api/scenarios'),
  getScenario: (scenarioId: string) =>
    request<ScenarioDetail>(`/api/scenarios/${scenarioId}`),
  createScenario: (payload: { id: string; yaml: string }) =>
    request<ScenarioDetail>('/api/scenarios', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateScenario: (scenarioId: string, payload: { yaml: string }) =>
    request<ScenarioDetail>(`/api/scenarios/${scenarioId}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  runScenario: (scenarioId: string, req: RunRequest) =>
    request<RunCreated>('/api/runs', {
      method: 'POST',
      body: JSON.stringify(buildRunBody(scenarioId, req)),
    }),
  listRuns: (filter?: { targetProfile?: TargetProfile }) => {
    const params = new URLSearchParams();
    if (filter?.targetProfile) params.set('target_profile', filter.targetProfile);
    const qs = params.toString();
    return request<Run[]>(`/api/runs${qs ? `?${qs}` : ''}`);
  },
  getRun: (runId: number) => request<RunDetail>(`/api/runs/${runId}`),
  rerunScenario: (scenarioId: string, req: RunRequest) =>
    request<RunCreated>('/api/runs', {
      method: 'POST',
      body: JSON.stringify(buildRunBody(scenarioId, req)),
    }),
};
