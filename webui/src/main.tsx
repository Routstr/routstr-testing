import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from '@/components/AppShell';
import { ScenariosPage } from '@/pages/ScenariosPage';
import { RunsPage } from '@/pages/RunsPage';
import { RunDetailPage } from '@/pages/RunDetailPage';
import './styles.css';

function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path='/' element={<Navigate to='/scenarios' replace />} />
          <Route path='/scenarios' element={<ScenariosPage />} />
          <Route path='/runs' element={<RunsPage />} />
          <Route path='/runs/:runId' element={<RunDetailPage />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
