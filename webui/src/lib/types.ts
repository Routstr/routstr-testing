export type RunStatus = 'running' | 'passed' | 'failed' | 'error';

export type TargetProfile = 'local' | 'remote';

export interface ScenarioSummary {
  id: string;
  name: string;
  description: string;
  expected_cost_sats: number;
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
  target_profile: TargetProfile;
  remote_node_urls: string[] | null;
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

export interface RunRequest {
  cashuToken: string;
  targetProfile: TargetProfile;
  /** Required when targetProfile === 'remote'. */
  remoteNodes?: RemoteNodeConfig[];
}
