import { RunStatus } from '@/lib/types';

const statusClasses: Record<RunStatus, string> = {
  running: 'bg-blue-100 text-blue-700',
  passed: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-red-100 text-red-700',
  error: 'bg-amber-100 text-amber-700',
};

export function StatusBadge({ status }: { status: RunStatus }) {
  return (
    <span className={`rounded-full px-2 py-1 text-xs font-semibold ${statusClasses[status]}`}>
      {status}
    </span>
  );
}
