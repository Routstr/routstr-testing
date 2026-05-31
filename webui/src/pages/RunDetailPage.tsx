import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '@/lib/api';
import { RunDetail } from '@/lib/types';
import { StatusBadge } from '@/components/StatusBadge';
import { RunTokenModal } from '@/components/RunTokenModal';

export function RunDetailPage() {
  const { runId = '' } = useParams();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [showRerun, setShowRerun] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const runIdNumber = Number.parseInt(runId, 10);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const data = await api.getRun(runIdNumber);
        if (active) setRun(data);
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : String(err));
      }
    };
    if (Number.isFinite(runIdNumber)) {
      void load();
      const id = window.setInterval(() => void load(), 3000);
      return () => {
        active = false;
        window.clearInterval(id);
      };
    }
    return;
  }, [runId]);

  if (!run) {
    return (
      <div className='rounded-xl border border-slate-200 bg-white p-4'>
        <p className='text-sm text-slate-500'>{error ?? 'Loading run…'}</p>
      </div>
    );
  }

  const vendorCommits = Object.entries(run.vendor_commits ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const rawLogs = run.test_results
    .filter((test) => Boolean(test.log_filename))
    .map((test) => ({
      testName: test.test_name,
      logFilename: test.log_filename,
    }));

  const profileBadgeTone =
    run.target_profile === 'remote'
      ? 'border-amber-300 bg-amber-50 text-amber-800'
      : 'border-slate-300 bg-slate-50 text-slate-700';

  return (
    <div className='rounded-xl border border-slate-200 bg-white p-4'>
      <div className='mb-4 flex items-center justify-between'>
        <div>
          <Link to='/runs' className='text-sm underline'>Back to runs</Link>
          <h2 className='mt-2 text-lg font-semibold'>Run {run.id}</h2>
          <div className='mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-600'>
            <span>Scenario: <span className='font-medium text-slate-900'>{run.scenario_id}</span></span>
            <span
              className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${profileBadgeTone}`}
              title={run.target_profile === 'remote' ? 'External deployment' : 'Local compose stack'}
            >
              {run.target_profile}
            </span>
            {run.target_profile === 'remote' && run.remote_node_urls?.length ? (
              <span className='flex flex-wrap items-center gap-1'>
                <span>nodes:</span>
                {run.remote_node_urls.map((url) => (
                  <code
                    key={url}
                    className='rounded bg-slate-100 px-1 py-0.5 text-[11px] text-slate-800'
                    title={url}
                  >
                    {url}
                  </code>
                ))}
              </span>
            ) : null}
            <span
              className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${
                run.upstream_profile && run.upstream_profile !== 'mock'
                  ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
                  : 'border-slate-300 bg-slate-50 text-slate-500'
              }`}
              title='Upstream provider profile'
            >
              upstream: {run.upstream_profile || 'mock'}
            </span>
            {run.upstream_profile && run.upstream_profile !== 'mock' ? (
              <span className='text-slate-600'>
                est ${Number(run.upstream_estimated_cost_usd ?? 0).toFixed(4)}
                {run.upstream_actual_cost_usd != null
                  ? ` · actual $${Number(run.upstream_actual_cost_usd).toFixed(6)}`
                  : ' · actual n/a'}
              </span>
            ) : null}
          </div>
        </div>
        <StatusBadge status={run.status} />
      </div>

      <div className='mb-4'>
        <button className='rounded-md bg-slate-900 px-3 py-2 text-sm text-white' onClick={() => setShowRerun(true)}>
          Re-run with new token
        </button>
      </div>

      <div className='mb-4 rounded-md border border-slate-200 p-3'>
        <h3 className='mb-2 text-sm font-semibold text-slate-900'>Vendor commits</h3>
        {vendorCommits.length ? (
          <ul className='space-y-1 text-sm text-slate-700'>
            {vendorCommits.map(([vendor, commit]) => (
              <li key={vendor}>
                <span className='font-medium'>{vendor}</span>: <code className='rounded bg-slate-100 px-1 py-0.5 text-xs'>{commit}</code>
              </li>
            ))}
          </ul>
        ) : (
          <p className='text-sm text-slate-500'>No vendor commit metadata recorded.</p>
        )}
      </div>

      <div className='mb-4 rounded-md border border-slate-200 p-3'>
        <h3 className='mb-2 text-sm font-semibold text-slate-900'>Raw logs</h3>
        {rawLogs.length ? (
          <ul className='space-y-1 text-sm'>
            {rawLogs.map(({ testName, logFilename }) => (
              <li key={`${testName}:${logFilename}`}>
                <a href={`${import.meta.env.VITE_API_BASE_URL ?? ''}/api/runs/${run.id}/logs/${encodeURIComponent(logFilename)}`} target='_blank' rel='noreferrer' className='underline'>
                  {testName}
                </a>
              </li>
            ))}
          </ul>
        ) : (
          <p className='text-sm text-slate-500'>No raw log files available for this run.</p>
        )}
      </div>

      <div className='space-y-3'>
        {run.test_results.map((test) => (
          <details key={test.test_name} className='rounded-md border border-slate-200 p-3'>
            <summary className='flex cursor-pointer items-center justify-between gap-2'>
              <span className='font-medium'>{test.test_name}</span>
              <span className='flex items-center gap-2'>
                <StatusBadge status={test.outcome} />
                <span className='text-xs text-slate-500'>{test.duration_ms}ms</span>
              </span>
            </summary>
            {test.error_excerpt ? <pre className='mt-3 overflow-x-auto rounded bg-slate-100 p-2 text-xs'>{test.error_excerpt}</pre> : null}
            {test.log_filename ? (
              <a href={`${import.meta.env.VITE_API_BASE_URL ?? ''}/api/runs/${run.id}/logs/${encodeURIComponent(test.log_filename)}`} target='_blank' rel='noreferrer' className='mt-2 inline-block text-sm underline'>
                Open raw logs
              </a>
            ) : null}
          </details>
        ))}
      </div>

      <RunTokenModal
        title='Re-run with new token'
        open={showRerun}
        scenarioName={run.scenario_id}
        onClose={() => setShowRerun(false)}
        onSubmit={async (req) => {
          await api.rerunScenario(run.scenario_id, req);
          setShowRerun(false);
        }}
      />
    </div>
  );
}
