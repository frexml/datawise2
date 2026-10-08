import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { BrowserRouter as Router, Routes, Route, Link, NavLink, Navigate, useNavigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Estates from './components/Estates';
import EstateDetails from './components/EstateDetails';
import Login from './components/Login';
import { AuthProvider, useAuth } from './context/AuthContext';

const EstateSwitcher = () => {
  const [estates, setEstates] = useState([]);
  const navigate = useNavigate();
  useEffect(() => {
    axios.get('/api/estates').then((r) => setEstates(r.data)).catch(() => {});
  }, []);
  if (estates.length === 0) return null;
  return (
    <select
      onChange={(e) => { if (e.target.value) navigate(`/estates/${e.target.value}`); }}
      defaultValue=""
      className="hidden sm:block text-xs border border-gray-200 dark:border-gray-700 rounded-md px-2 py-1.5 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300"
    >
      <option value="" disabled>Switch estate…</option>
      {estates.map((e) => (
        <option key={e.id} value={e.id}>{e.estate_type === 'telecom' ? '📡' : '🏦'} {e.name} · {e.estate_type}</option>
      ))}
    </select>
  );
};

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30000, retry: 1, refetchOnWindowFocus: false } },
});

const ThemeToggle = () => {
    const [isDark, setIsDark] = useState(() => document.documentElement.classList.contains('dark'));

    useEffect(() => {
        document.documentElement.classList.toggle('dark', isDark);
        localStorage.setItem('theme', isDark ? 'dark' : 'light');
    }, [isDark]);

    return (
        <button
            onClick={() => setIsDark((v) => !v)}
            title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
            className="h-8 w-8 flex items-center justify-center rounded-md text-gray-500 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
        >
            {isDark ? '☀️' : '🌙'}
        </button>
    );
};

const navLinkClass = ({ isActive }) =>
    `hidden sm:inline font-medium transition-colors ${isActive
        ? 'text-orange-600'
        : 'text-gray-600 dark:text-gray-300 hover:text-orange-600'
    }`;

const AppInner = () => {
  const { isAuthenticated, user, logout } = useAuth();
  if (!isAuthenticated) return <Login />;
  return (
    <Router>
      <div className="min-h-screen flex flex-col bg-gradient-to-b from-gray-50 to-gray-100 dark:from-gray-900 dark:to-gray-950 transition-colors">
        {/* Top nav */}
        <nav className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 shadow-sm sticky top-0 z-20">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="flex justify-between h-16 items-center">
              <Link to="/" className="flex items-center gap-3 group">
                <img src="/logo.png" alt="ML arteka" className="h-9 w-9 rounded-md ring-1 ring-gray-200 transition-transform group-hover:scale-105" />
                <div className="flex flex-col leading-tight">
                  <span className="text-lg font-semibold text-gray-900 dark:text-gray-100">Data<span className="text-orange-600">Wise</span></span>
                  <span className="text-[10px] uppercase tracking-wider text-gray-500 dark:text-gray-400">Knowledge Graph + Ledger</span>
                </div>
              </Link>
              <div className="flex items-center gap-4 text-sm">
                <div className="hidden sm:flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
                  <span className="h-7 w-7 rounded-full bg-indigo-600 text-white flex items-center justify-center text-xs font-medium">{user?.name?.[0] || '?'}</span>
                  <span>{user?.name}</span>
                  <button onClick={logout} className="ml-2 px-2 py-1 rounded-md border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 text-xs">Logout</button>
                </div>
                <EstateSwitcher />
                <NavLink to="/estates" className={navLinkClass}>Estates</NavLink>
                <ThemeToggle />
              </div>
            </div>
          </div>
        </nav>
        <main className="flex-1 py-10">
          <div className="max-w-7xl mx-auto sm:px-6 lg:px-8">
            <Routes>
              <Route path="/" element={<Navigate to="/estates" replace />} />
              <Route path="/estates" element={<Estates />} />
              <Route path="/estates/:estateId" element={<EstateDetails />} />
              <Route path="*" element={<Navigate to="/estates" replace />} />
            </Routes>
          </div>
        </main>
        <footer className="bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 mt-auto">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-5 flex flex-col sm:flex-row items-center justify-between gap-3">
            <p className="text-xs text-gray-500 dark:text-gray-400">DataWise · On-prem estate lineage, analytics, chat & migration bridge</p>
            <a href="https://ML arteka.ca" target="_blank" rel="noreferrer" className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400 hover:text-orange-600 transition-colors" title="Built by ML arteka">
              <span>Built by</span><img src="/logo.png" alt="ML arteka" className="h-5 w-5 rounded" /><span className="font-medium">ML arteka</span>
            </a>
          </div>
        </footer>
      </div>
    </Router>
  );
};

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <AppInner />
      </AuthProvider>
    </QueryClientProvider>
  );
}

export default App;
