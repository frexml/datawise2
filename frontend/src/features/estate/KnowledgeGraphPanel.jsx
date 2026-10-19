import React, { useEffect, useState } from 'react';
import axios from 'axios';

const COLORS = {
  purple: 'bg-purple-100 text-purple-700 border-purple-200 dark:bg-purple-900/30 dark:text-purple-300 dark:border-purple-800',
  orange: 'bg-orange-100 text-orange-700 border-orange-200 dark:bg-orange-900/30 dark:text-orange-300 dark:border-orange-800',
  teal: 'bg-teal-100 text-teal-700 border-teal-200 dark:bg-teal-900/30 dark:text-teal-300 dark:border-teal-800',
  green: 'bg-emerald-100 text-emerald-700 border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-300 dark:border-emerald-800',
  red: 'bg-red-100 text-red-700 border-red-200 dark:bg-red-900/30 dark:text-red-300 dark:border-red-800',
  cyan: 'bg-cyan-100 text-cyan-700 border-cyan-200 dark:bg-cyan-900/30 dark:text-cyan-300 dark:border-cyan-800',
  blue: 'bg-blue-100 text-blue-700 border-blue-200 dark:bg-blue-900/30 dark:text-blue-300 dark:border-blue-800',
};

export default function KnowledgeGraphPanel({ estateId, selectedFqn }) {
  const [kg, setKg] = useState(null);
  const [error, setError] = useState(null);
  const [mode, setMode] = useState('estate'); // estate | overview
  const [fqn, setFqn] = useState(selectedFqn || '');

  const load = async (q) => {
    setError(null);
    try {
      const params = q ? `?fqn=${encodeURIComponent(q)}` : '';
      const r = await axios.get(`/api/estates/${estateId}/knowledge-graph${params}`);
      setKg(r.data);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    }
  };

  useEffect(() => { load(selectedFqn || ''); setFqn(selectedFqn || ''); }, [estateId, selectedFqn]);
  useEffect(() => { if (mode === 'overview') load(fqn); }, [mode]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 items-center justify-between">
        <div className="flex rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <button onClick={() => setMode('estate')} className={`px-3 py-1.5 text-xs ${mode === 'estate' ? 'bg-indigo-600 text-white' : 'bg-white dark:bg-gray-800 text-gray-600'}`}>Estate</button>
          <button onClick={() => setMode('overview')} className={`px-3 py-1.5 text-xs ${mode === 'overview' ? 'bg-indigo-600 text-white' : 'bg-white dark:bg-gray-800 text-gray-600'}`}>KG Overview (6 subgraphs)</button>
        </div>
        {mode === 'overview' && (
          <div className="flex gap-2 items-center">
            <input value={fqn} onChange={(e) => setFqn(e.target.value)} placeholder="Filter by FQN (optional)" className="px-2 py-1 border rounded text-xs dark:bg-gray-900 dark:border-gray-700 w-64" />
            <button onClick={() => load(fqn)} className="px-2 py-1 text-xs rounded bg-white border hover:bg-gray-50">Apply</button>
            {fqn && <button onClick={() => { setFqn(''); load(''); }} className="px-2 py-1 text-xs rounded bg-white border hover:bg-gray-50">Clear</button>}
          </div>
        )}
      </div>
      {error && <div className="text-xs text-red-600 bg-red-50 p-2 rounded">{error}</div>}
      {!kg ? (
        <div className="text-xs text-gray-400">Loading knowledge graph…</div>
      ) : mode === 'estate' ? (
        <div className="text-xs text-gray-500 bg-gray-50 dark:bg-gray-900 border rounded-lg px-3 py-2">
          Estate view is the Lineage Graph tab. Switch to <span className="font-medium">KG Overview</span> to collapse {kg.facts || 0} facts into 6 subgraphs{fqn || kg.fqn ? ` filtered by ${fqn || kg.fqn}` : ''}.
        </div>
      ) : (
        <div>
          <div className="text-xs text-gray-500 mb-2">Central: {kg.central} · {kg.facts} facts · {kg.unknowns} unknowns{kg.fqn ? ` · filtered by ${kg.fqn}` : ''}</div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {(kg.subgraphs || []).map((s) => (
              <div key={s.name} className={`p-3 rounded-xl border ${COLORS[s.color] || ''}`}>
                <div className="text-xs font-semibold">{s.name}</div>
                <div className="text-lg font-bold">{s.count}</div>
              </div>
            ))}
          </div>
          {(kg.subgraphs || []).length !== 6 && (
            <div className="mt-2 text-xs text-amber-600">Expected 6 subgraphs, got {(kg.subgraphs || []).length}</div>
          )}
        </div>
      )}
    </div>
  );
}
