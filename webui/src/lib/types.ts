export type RunStatus = 'running' | 'passed' | 'failed' | 'error';

export type TargetProfile = 'local' | 'remote';

export interface ScenarioSummary {
  id: string;
  name: string;
  description: string;
  expected_cost_sats: number;
  upstream_profile: string;
  estimated_upstream_cost_usd: number;
  stats: Record<string, unknown>;
}

export interface ScenarioDetail extends ScenarioSummary {
  yaml: string;
}

export interface Run {
  id: number;
  scenario_id: string;
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
  token_consumed_sats: number;
  token_consumed_msats: number;
  target_profile: TargetProfile;
  remote_node_urls: string[] | null;
  upstream_profile: string;
  upstream_estimated_cost_usd: number | null;
  upstream_actual_cost_usd: number | null;
}

export interface RunTestOutcome {
  id: string;
  test_name: string;
  outcome: RunStatus;
  duration_ms: number;
  error_excerpt: string | null;
  log_filename: string;
}

export interface RunDetail extends Run {
  vendor_commits: Record<string, string>;
  test_results: RunTestOutcome[];
}

export interface RunCreated {
  run_id: number;
  scenario_id: string;
}

export interface RemoteNodeConfig {
  /** Routstr node base URL — must include scheme. */
  url: string;
  /** Optional admin token; write-only, never persisted. */
  adminToken: string;
}

/** Sentinel upstream profile: the in-compose mock-openai container. */
export const MOCK_UPSTREAM = 'mock';

export interface ProviderRequiredEnv {
  name: string;
  secret: boolean;
  has_default: boolean;
}

export interface ProviderModel {
  id: string;
  name: string;
}

export interface Provider {
  id: string;
  name: string;
  upstream_base_url: string;
  api_key_env: string;
  required_env: ProviderRequiredEnv[];
  models: ProviderModel[];
  notes: string;
}

export interface RunRequest {
  cashuToken: string;
  targetProfile: TargetProfile;
  /** Required when targetProfile === 'remote'. */
  remoteNodes?: RemoteNodeConfig[];
  /** `mock` (default) or a providers/<id>.yaml id. */
  upstreamProfile?: string;
  /** Per-provider env (e.g. { OPENAI_API_KEY }); write-only, never persisted. */
  upstreamEnv?: Record<string, string>;
  /** Per-run cost ceiling override (USD). */
  upstreamMaxUsd?: number;
}
