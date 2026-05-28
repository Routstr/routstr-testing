import { FlaskConical, PlayCircle } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

const linkClass = ({ isActive }: { isActive: boolean }) =>
  [
    'flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors',
    isActive ? 'bg-slate-900 text-white' : 'text-slate-700 hover:bg-slate-200',
  ].join(' ');

export function AppShell({ children }: Props) {
  return (
    <div className='mx-auto min-h-screen max-w-7xl px-4 py-6 sm:px-6 lg:px-8'>
      <header className='mb-6 flex items-center justify-between'>
        <div>
          <h1 className='text-2xl font-semibold tracking-tight'>Routstr Runs UI</h1>
          <p className='text-sm text-slate-600'>Scenario authoring and test execution</p>
        </div>
      </header>
      <div className='grid gap-6 md:grid-cols-[220px_1fr]'>
        <aside className='h-fit rounded-xl border border-slate-200 bg-white p-3'>
          <nav className='space-y-2'>
            <NavLink to='/scenarios' className={linkClass}>
              <FlaskConical size={16} /> Scenarios
            </NavLink>
            <NavLink to='/runs' className={linkClass}>
              <PlayCircle size={16} /> Runs
            </NavLink>
          </nav>
        </aside>
        <main>{children}</main>
      </div>
    </div>
  );
}
