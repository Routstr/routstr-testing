import { CSSProperties, useMemo, useState } from 'react';

import { RemoteNodeConfig, RunRequest, TargetProfile } from '@/lib/types';

interface Props {
  title: string;
  open: boolean;
  scenarioName?: string;
  estimatedCostSats?: number;
  onClose: () => void;
  onSubmit: (req: RunRequest) => Promise<void>;
}

const MASKED: CSSProperties = { WebkitTextSecurity: 'disc' } as CSSProperties;

export function RunTokenModal({
  title,
  open,
  scenarioName,
  estimatedCostSats,
  onClose,
  onSubmit,
}: Props) {
  const [token, setToken] = useState('');
  const [targetProfile, setTargetProfile] = useState<TargetProfile>('local');
  const [remoteUrlsRaw, setRemoteUrlsRaw] = useState('');
  // adminTokens is positional with parsedUrls (admin[i] applies to url[i]).
  const [adminTokens, setAdminTokens] = useState<Record<number, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parsedUrls = useMemo(
    () =>
      remoteUrlsRaw
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean),
    [remoteUrlsRaw]
  );

  if (!open) return null;

  const submitDisabled =
    !token.trim() ||
    submitting ||
    (targetProfile === 'remote' && parsedUrls.length === 0);

  return (
    <div className='fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4'>
      <div className='w-full max-w-xl rounded-xl bg-white p-5 shadow-xl'>
        <h3 className='text-lg font-semibold'>{title}</h3>
        {scenarioName ? (
          <p className='mt-1 text-sm text-slate-600'>Scenario: {scenarioName}</p>
        ) : null}
        <p className='mt-4 text-sm text-slate-600'>
          Estimated cost:{' '}
          {typeof estimatedCostSats === 'number'
            ? `${estimatedCostSats} sats`
            : '—'}
        </p>

        <div className='mt-4 space-y-1'>
          <label
            htmlFor='target-profile'
            className='text-xs font-semibold uppercase tracking-wide text-slate-600'
          >
            Target profile
          </label>
          <select
            id='target-profile'
            value={targetProfile}
            onChange={(event) => {
              const next = event.target.value as TargetProfile;
              setTargetProfile(next);
              if (next === 'local') {
                setRemoteUrlsRaw('');
                setAdminTokens({});
              }
            }}
            className='w-full rounded-md border border-slate-300 px-3 py-2 text-sm'
          >
            <option value='local'>local — build node-a/node-b from vendor</option>
            <option value='remote'>remote — point at deployed nodes</option>
          </select>
          <p className='text-xs text-slate-500'>
            {targetProfile === 'remote'
              ? 'Destructive tests will be skipped automatically. Admin-token-gated tests skip when no admin token is provided.'
              : 'Builds and brings up the in-compose node-a/node-b, then runs the full suite.'}
          </p>
        </div>

        {targetProfile === 'remote' ? (
          <div className='mt-4 space-y-2'>
            <label
              htmlFor='remote-urls'
              className='text-xs font-semibold uppercase tracking-wide text-slate-600'
            >
              Routstr node URLs (one per line)
            </label>
            <textarea
              id='remote-urls'
              value={remoteUrlsRaw}
              onChange={(event) => setRemoteUrlsRaw(event.target.value)}
              placeholder={'https://node1.example\nhttps://node2.example'}
              className='h-20 w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-xs'
              autoComplete='off'
              spellCheck={false}
            />
            {parsedUrls.length > 0 ? (
              <div className='space-y-2'>
                <p className='text-xs font-semibold uppercase tracking-wide text-slate-600'>
                  Admin tokens (optional, per node)
                </p>
                {parsedUrls.map((url, idx) => (
                  <div key={`${url}:${idx}`} className='flex items-center gap-2'>
                    <span
                      className='w-1/2 truncate text-xs text-slate-600'
                      title={url}
                    >
                      {url}
                    </span>
                    <input
                      type='text'
                      value={adminTokens[idx] ?? ''}
                      onChange={(event) =>
                        setAdminTokens((current) => ({
                          ...current,
                          [idx]: event.target.value,
                        }))
                      }
                      placeholder='Admin token (write-only)'
                      className='flex-1 rounded-md border border-slate-300 px-2 py-1 font-mono text-xs'
                      autoComplete='off'
                      spellCheck={false}
                      style={MASKED}
                    />
                  </div>
                ))}
                <p className='text-xs text-slate-500'>
                  Admin tokens are never persisted — same contract as the cashu token.
                </p>
              </div>
            ) : null}
          </div>
        ) : null}

        <div className='mt-4 space-y-1'>
          <label
            htmlFor='cashu-token'
            className='text-xs font-semibold uppercase tracking-wide text-slate-600'
          >
            Cashu token (funds routstrd)
          </label>
          <textarea
            id='cashu-token'
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder='Paste cashu token'
            className='h-28 w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-xs tracking-tight'
            autoComplete='off'
            spellCheck={false}
            style={MASKED}
          />
          <p className='text-xs text-slate-500'>
            Token is never rendered back in the UI.
          </p>
        </div>

        {error ? <p className='mt-3 text-xs text-red-600'>{error}</p> : null}

        <div className='mt-4 flex justify-end gap-2'>
          <button
            className='rounded-md border border-slate-300 px-3 py-2 text-sm'
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className='rounded-md bg-slate-900 px-3 py-2 text-sm text-white disabled:opacity-50'
            disabled={submitDisabled}
            onClick={async () => {
              setSubmitting(true);
              setError(null);
              try {
                const remoteNodes: RemoteNodeConfig[] = parsedUrls.map(
                  (url, idx) => ({
                    url,
                    adminToken: adminTokens[idx] ?? '',
                  })
                );
                await onSubmit({
                  cashuToken: token,
                  targetProfile,
                  ...(targetProfile === 'remote' ? { remoteNodes } : {}),
                });
                setToken('');
                setRemoteUrlsRaw('');
                setAdminTokens({});
              } catch (err) {
                setError(err instanceof Error ? err.message : String(err));
              } finally {
                setSubmitting(false);
              }
            }}
          >
            {submitting ? 'Submitting…' : 'Start run'}
          </button>
        </div>
      </div>
    </div>
  );
}
