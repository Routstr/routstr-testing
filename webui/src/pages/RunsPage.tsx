import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '@/lib/api';
import { Run, TargetProfile } from '@/lib/types';
import { StatusBadge } from '@/components/StatusBadge';

type ProfileFilter = 'all' | TargetProfile;

// Node billing is sub-sat, so render the precise millisat spend: whole sats
// once it reaches 1 sat, otherwise milli-sats (avoids the misleading "0 sats").
export function formatSpend(msats: number, sats: number): string {
  const m = msats || (sats || 0) * 1000;
  if (m <= 0) return '0 sats';
  if (m >= 1000) {
    const s = m / 1000;
    return `${Number.isInteger(s) ? s : s.toFixed(3)} sats`;
  }
  return `${m} msat`;
}

function TargetProfileBadge({ profile }: { profile: TargetProfile }) {
  const tone =
    profile === 'remote'
      ? 'border-amber-300 bg-amber-50 text-amber-800'
      : 'border-slate-300 bg-slate-50 text-slate-700';
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${tone}`}
    >
      {profile}
    </span>
  );
}

function UpstreamBadge({ profile }: { profile: string }) {
  const real = profile && profile !== 'mock';
  const tone = real
    ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
    : 'border-slate-300 bg-slate-50 text-slate-500';
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${tone}`}
      title={real ? 'Real upstream provider' : 'In-compose mock-openai'}
    >
      {profile || 'mock'}
    </span>
  );
}

export function RunsPage() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<ProfileFilter>('all');

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const data = await api.listRuns(
          filter === 'all' ? undefined : { targetProfile: filter }
        );
        if (active) setRuns(data);
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (active) setLoading(false);
      }
    };

    void load();
    const id = window.setInterval(() => void load(), 3000);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, [filter]);

  return (
    <div className='rounded-xl border border-slate-200 bg-white p-4'>
      <div className='mb-4 flex items-center justify-between'>
        <h2 className='text-lg font-semibold'>Runs</h2>
        <div className='flex items-center gap-2 text-xs text-slate-600'>
          <span>Target profile:</span>
          <select
            value={filter}
            onChange={(event) => setFilter(event.target.value as ProfileFilter)}
            className='rounded-md border border-slate-300 px-2 py-1'
          >
            <option value='all'>all</option>
            <option value='local'>local</option>
            <option value='remote'>remote</option>
          </select>
        </div>
      </div>
      {error ? <p className='mb-3 text-xs text-red-600'>{error}</p> : null}
      {loading ? <p className='text-sm text-slate-500'>Loading…</p> : null}
      <table className='w-full border-collapse text-sm'>
        <thead>
          <tr className='border-b border-slate-200 text-left text-slate-600'>
            <th className='py-2'>Status</th>
            <th className='py-2'>Scenario</th>
            <th className='py-2'>Profile</th>
            <th className='py-2'>Upstream</th>
            <th className='py-2'>Started</th>
            <th className='py-2'>Finished</th>
            <th className='py-2'>Token spent</th>
            <th className='py-2'>Detail</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id} className='border-b border-slate-100'>
              <td className='py-2'>
                <StatusBadge status={run.status} />
              </td>
              <td className='py-2'>{run.scenario_id}</td>
              <td className='py-2'>
                <TargetProfileBadge profile={run.target_profile} />
              </td>
              <td className='py-2'>
                <UpstreamBadge profile={run.upstream_profile} />
              </td>
              <td className='py-2'>
                {new Date(run.started_at).toLocaleString()}
              </td>
              <td className='py-2'>
                {run.finished_at
                  ? new Date(run.finished_at).toLocaleString()
                  : '—'}
              </td>
              <td className='py-2 text-xs text-slate-600'>
                {formatSpend(run.token_consumed_msats, run.token_consumed_sats)}
              </td>
              <td className='py-2'>
                <Link
                  to={`/runs/${run.id}`}
                  className='text-slate-900 underline'
                >
                  Open
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
