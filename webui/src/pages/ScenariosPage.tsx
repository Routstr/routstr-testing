import { useEffect, useMemo, useState } from 'react';
import Editor from '@monaco-editor/react';
import { api } from '@/lib/api';
import { ScenarioDetail, ScenarioSummary } from '@/lib/types';
import { RunTokenModal } from '@/components/RunTokenModal';

export function ScenariosPage() {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [scenarioDetail, setScenarioDetail] = useState<ScenarioDetail | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [saving, setSaving] = useState(false);
  const [newScenarioId, setNewScenarioId] = useState('');
  const [runOpen, setRunOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = useMemo(
    () => scenarios.find((scenario) => scenario.id === selectedId) ?? null,
    [scenarios, selectedId]
  );

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const data = await api.listScenarios();
        if (cancelled) return;
        setScenarios(data);
        setSelectedId((existing) => existing ?? data[0]?.id ?? null);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadDetail = async () => {
      if (!selectedId) {
        setScenarioDetail(null);
        return;
      }
      setLoadingDetail(true);
      try {
        const detail = await api.getScenario(selectedId);
        if (!cancelled) setScenarioDetail(detail);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoadingDetail(false);
      }
    };
    void loadDetail();
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const createScenario = async () => {
    const id = newScenarioId.trim();
    if (!id) {
      setError('Scenario id is required.');
      return;
    }
    const created = await api.createScenario({
      id,
      yaml: 'name: new-scenario\nsteps: []\n',
    });
    const refreshed = await api.listScenarios();
    setScenarios(refreshed);
    setSelectedId(created.id);
    setNewScenarioId('');
  };

  return (
    <div className='rounded-xl border border-slate-200 bg-white p-4'>
      <div className='mb-4 flex items-center justify-between'>
        <h2 className='text-lg font-semibold'>Scenarios</h2>
        <div className='flex items-center gap-2'>
          <input
            value={newScenarioId}
            onChange={(event) => setNewScenarioId(event.target.value)}
            placeholder='scenario_id (A-Za-z0-9_-)'
            className='rounded-md border border-slate-300 px-2 py-1 text-xs'
          />
          <button
            className='rounded-md bg-slate-900 px-3 py-2 text-sm text-white'
            onClick={() => void createScenario()}
          >
          New scenario
          </button>
        </div>
      </div>
      {error ? <p className='mb-3 text-xs text-red-600'>{error}</p> : null}
      <div className='grid gap-4 lg:grid-cols-[260px_1fr]'>
        <div className='space-y-2'>
          {loading ? <p className='text-sm text-slate-500'>Loading…</p> : null}
          {scenarios.map((scenario) => (
            <button
              key={scenario.id}
              className={`w-full rounded-md border px-3 py-2 text-left ${selectedId === scenario.id ? 'border-slate-900 bg-slate-100' : 'border-slate-200'}`}
              onClick={() => setSelectedId(scenario.id)}
            >
              <p className='font-medium'>{scenario.name}</p>
              <p className='line-clamp-2 text-xs text-slate-600'>{scenario.description || 'No description'}</p>
              <p className='mt-1 text-[11px] text-slate-500'>Est. {scenario.expected_cost_sats} sats</p>
            </button>
          ))}
        </div>

        {selected && scenarioDetail ? (
          <div className='space-y-3'>
            <p className='text-sm text-slate-700'>
              Editing YAML for <span className='font-semibold'>{selected.id}</span>
            </p>
            <div className='overflow-hidden rounded-md border border-slate-300'>
              <Editor
                height='360px'
                defaultLanguage='yaml'
                value={scenarioDetail.yaml}
                onChange={(value) =>
                  setScenarioDetail((current) =>
                    current ? { ...current, yaml: value ?? '' } : current
                  )
                }
                options={{ minimap: { enabled: false }, fontSize: 13 }}
              />
            </div>
            <div className='flex gap-2'>
              <button
                className='rounded-md border border-slate-300 px-3 py-2 text-sm'
                disabled={saving}
                onClick={async () => {
                  setSaving(true);
                  try {
                    await api.updateScenario(selected.id, { yaml: scenarioDetail.yaml });
                  } finally {
                    setSaving(false);
                  }
                }}
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
              <button className='rounded-md bg-slate-900 px-3 py-2 text-sm text-white' onClick={() => setRunOpen(true)}>
                Run
              </button>
            </div>
          </div>
        ) : (
          <p className='text-sm text-slate-500'>
            {loadingDetail ? 'Loading scenario detail…' : 'Select a scenario to edit.'}
          </p>
        )}
      </div>

      <RunTokenModal
        title='Run scenario'
        open={runOpen}
        scenarioName={selected?.name}
        estimatedCostSats={selected?.expected_cost_sats}
        estimatedUpstreamCostUsd={selected?.estimated_upstream_cost_usd}
        scenarioUpstreamProfile={selected?.upstream_profile}
        onClose={() => setRunOpen(false)}
        onSubmit={async (req) => {
          if (!selected) return;
          await api.runScenario(selected.id, req);
          setRunOpen(false);
        }}
      />
    </div>
  );
}
