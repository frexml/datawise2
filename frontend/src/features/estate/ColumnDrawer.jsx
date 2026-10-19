import React, { useState, useEffect } from 'react';
import axios from 'axios';

export default function ColumnDrawer({ fqn, kind, columns, estateId, onClose, onSelectColumn }) {
  const [tab, setTab] = useState('columns');
  const [search, setSearch] = useState('');
  const [selectedCol, setSelectedCol] = useState(null);
  const [facts, setFacts] = useState({ facts: [], unknowns: [] });
  const [semantic, setSemantic] = useState({ rules: [], implicit_contracts: [], similar: [], conflicting: [] });
  const [behavior, setBehavior] = useState({ failures: [], dependencies: [], rejects: [], operational: [] });

  // Per-node Facts: backend filters by ?fqn= (Structure/Semantic/Behavior).
  // Client re-filters as a fallback so stale/cached payloads never leak
  // other nodes' facts into this drawer.
  const matchesFqn = (evidenceFqn) => {
    if (!fqn) return true;
    const q = fqn.toUpperCase();
    const e = (evidenceFqn || '').toUpperCase();
    if (!e) return false;
    if (q.includes(e) || e.includes(q)) return true;
    const norm = e.replace('→', '->');
    if (norm.includes('->')) {
      return norm.split('->').some((p) => { const t = p.trim(); return q.includes(t) || t.includes(q); });
    }
    if (e.includes(',')) {
      return e.split(',').some((p) => { const t = p.trim(); return q.includes(t) || t.includes(q); });
    }
    return false;
  };

  useEffect(() => {
    if (!fqn || !estateId) return;
    const eid = estateId;
    const q = `?fqn=${encodeURIComponent(fqn)}`;
    axios.get(`/api/estates/${eid}/structure/facts${q}`).then((r) => setFacts(r.data)).catch(() => {});
    axios.get(`/api/estates/${eid}/semantic/rules${q}`).then((r) => setSemantic(r.data)).catch(() => {});
    axios.get(`/api/estates/${eid}/behavior/flows${q}`).then((r) => setBehavior(r.data)).catch(() => {});
  }, [fqn, estateId]);

  if (!fqn) return null;

  const filtered = columns.filter((c) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return c.target_column.toLowerCase().includes(q) || c.source_column.toLowerCase().includes(q) || (c.transform_expr && c.transform_expr.toLowerCase().includes(q));
  });

  const [journey, setJourney] = useState(null);

  const handleSelect = (col) => {
    setSelectedCol(col.target_column);
    onSelectColumn && onSelectColumn(col);
    // Fetch estate-wide journey for this column
    if (col && estateId) {
      const fqn = `${col.target_table}.${col.target_column}`;
      axios.get(`/api/estates/${estateId}/columns/${encodeURIComponent(fqn)}/lineage`).then((r) => setJourney(r.data.journey)).catch(() => setJourney(null));
    }
  };

  // Reset journey when fqn changes
  React.useEffect(() => { setJourney(null); setSelectedCol(null); }, [fqn]);

  return (
    <div className="w-[380px] shrink-0 bg-white dark:bg-gray-800 border-l border-gray-200 dark:border-gray-700 flex flex-col h-full">
      {/* Header — Schema → Table → Column + hops */}
      <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
        <div className="min-w-0">
          <div className="text-xs text-gray-500 dark:text-gray-400 capitalize">{kind} · Schema {fqn.split('.')[0] || '—'} → Table {fqn.split('.').pop()}</div>
          <div className="text-sm font-semibold text-gray-900 dark:text-white truncate" title={fqn}>{fqn}</div>
          <div className="text-xs text-gray-500">
            {columns.length} columns · {columns.filter(c => c.transform_expr).length} with transforms · Hops {columns.length ? Math.max(...columns.map(c => c.hops || 1)) : 1} max
            {columns.some(c => c.unresolved) && <span className="ml-1 px-1.5 py-0.5 rounded bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 font-medium">Attention Required · {columns.filter(c => c.unresolved).length} unresolved</span>}
          </div>
        </div>
        <button onClick={onClose} className="h-7 w-7 flex items-center justify-center rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500">✕</button>
      </div>

      {/* Tabs — 5: Columns + Graph + 3 Engines */}
      <div className="flex border-b border-gray-200 dark:border-gray-700 overflow-x-auto">
        <button onClick={() => setTab('columns')} className={`px-3 py-2 text-xs font-medium border-b-2 whitespace-nowrap ${tab==='columns' ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>Columns</button>
        <button onClick={() => setTab('graph')} className={`px-3 py-2 text-xs font-medium border-b-2 whitespace-nowrap ${tab==='graph' ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>Graph</button>
        <button onClick={() => setTab('structure')} className={`px-3 py-2 text-xs font-medium border-b-2 whitespace-nowrap ${tab==='structure' ? 'border-emerald-600 text-emerald-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>Structure</button>
        <button onClick={() => setTab('semantic')} className={`px-3 py-2 text-xs font-medium border-b-2 whitespace-nowrap ${tab==='semantic' ? 'border-violet-600 text-violet-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>Semantic</button>
        <button onClick={() => setTab('behavior')} className={`px-3 py-2 text-xs font-medium border-b-2 whitespace-nowrap ${tab==='behavior' ? 'border-amber-600 text-amber-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>Behavior</button>
      </div>

      {tab === 'columns' ? (
        <>
          <div className="p-3 border-b border-gray-200 dark:border-gray-700">
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search columns or transforms…" className="w-full px-3 py-2 border rounded-lg text-xs dark:bg-gray-900 dark:border-gray-700 dark:text-white" />
          </div>
          <div className="flex-1 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-gray-50 dark:bg-gray-900 text-gray-500">
                <tr>
                  <th className="text-left px-3 py-2 font-medium">Target Column</th>
                  <th className="text-left px-3 py-2 font-medium">Source</th>
                  <th className="text-left px-3 py-2 font-medium">What changed</th>
                  <th className="text-left px-3 py-2 font-medium">Hops</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {filtered.length === 0 ? (
                  <tr><td colSpan={4} className="px-3 py-6 text-center text-gray-400">No columns — leaf table or unresolved view</td></tr>
                ) : filtered.map((c) => (
                  <tr
                    key={`${c.target_table}.${c.target_column}-${c.source_table}.${c.source_column}`}
                    onClick={() => !c.unresolved && handleSelect(c)}
                    className={`hover:bg-gray-50 dark:hover:bg-gray-700/50 ${c.unresolved ? 'bg-red-50/60 dark:bg-red-950/20 border-l-2 border-red-400' : 'cursor-pointer'} ${selectedCol === c.target_column ? 'bg-indigo-50 dark:bg-indigo-900/30' : ''}`}
                    title={c.full_path || `${c.source_table}.${c.source_column} -> ${c.target_table}.${c.target_column} (${c.hops || 1} hops)`}
                  >
                    <td className="px-3 py-2 font-mono font-medium text-gray-900 dark:text-white">
                      <div>{c.target_column}</div>
                      <div className="text-[10px] text-gray-400">{c.target_schema || c.target_table.split('.')[0]}</div>
                    </td>
                    <td className="px-3 py-2 font-mono text-gray-600 dark:text-gray-300">
                      {c.unresolved ? (
                        <span className="inline-flex px-1.5 py-0.5 rounded bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300 text-[11px] font-medium">Attention Required</span>
                      ) : (
                        <div>
                          <div>{c.source_table.split('.').pop()}.{c.source_column}</div>
                          <div className="text-[10px] text-gray-400">{c.source_schema || c.source_table.split('.')[0]}</div>
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {c.unresolved ? (
                        <span className="inline-flex px-1.5 py-0.5 rounded bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 text-[11px]" title={c.unresolved_reason}>
                          No clear lineage
                        </span>
                      ) : c.transform_expr ? (
                        <span className="inline-flex px-1.5 py-0.5 rounded bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-300 font-mono text-[11px]" title={c.transform_expr}>
                          {c.transform_expr.length > 30 ? c.transform_expr.slice(0, 30) + '…' : c.transform_expr}
                        </span>
                      ) : (
                        <span className="text-gray-400">direct</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-center">
                      <span className="inline-flex px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 text-[11px] font-medium" title={c.full_path || `${c.hops || 1} hops`}>
                        {c.hops || 1} hops
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="p-3 border-t border-gray-200 dark:border-gray-700 flex gap-2">
            <button
              onClick={() => {
                const csv = ['target_schema,target_table,target_column,source_schema,source_table,source_column,transform,hops,full_path,confidence', ...filtered.map(c => `${c.target_schema || ''},${c.target_table},${c.target_column},${c.source_schema || ''},${c.source_table},${c.source_column},"${(c.transform_expr || '').replace(/"/g, '""')}",${c.hops || 1},"${(c.full_path || '').replace(/"/g, '""')}",${c.confidence}`)].join('\n');
                const blob = new Blob([csv], { type: 'text/csv' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url; a.download = `${fqn}_columns.csv`; a.click();
              }}
              className="flex-1 px-3 py-2 bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-xs font-medium hover:bg-gray-50 dark:hover:bg-gray-600"
            >
              Export CSV
            </button>
            <button onClick={onClose} className="flex-1 px-3 py-2 bg-indigo-600 text-white rounded-lg text-xs font-medium hover:bg-indigo-700">Close</button>
          </div>
        </>
      ) : tab === 'graph' ? (
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          <div className="text-xs text-gray-500">Column lineage graph — each target column and its sources. Click a column above to highlight its path on the main graph.</div>
          {filtered.slice(0, 12).map((c) => (
            <div key={`${c.target_column}-${c.source_column}`} onClick={() => !c.unresolved && handleSelect(c)} className={`p-3 rounded-xl border ${c.unresolved ? 'border-red-200 dark:border-red-800 bg-red-50/60 dark:bg-red-950/20' : 'cursor-pointer ' + (selectedCol === c.target_column ? 'border-indigo-300 bg-indigo-50 dark:bg-indigo-900/20' : 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 hover:border-gray-300')}`}>
              {c.unresolved ? (
                <>
                  <div className="text-xs font-mono"><span className="text-gray-400">?</span> <span className="text-gray-400">→</span> <span className="px-2 py-1 rounded bg-red-500 text-white">{c.target_table.split('.').pop()}.{c.target_column}</span> <span className="ml-2 px-1.5 py-0.5 rounded bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300 text-[11px]">Attention Required · {c.hops || 1} hops</span></div>
                  <div className="mt-2 text-xs text-red-700 dark:text-red-300">No clear lineage - {c.unresolved_reason}</div>
                  {c.full_path && <div className="mt-1 text-[11px] font-mono text-gray-500 truncate" title={c.full_path}>{c.full_path}</div>}
                </>
              ) : (
                <>
                  <div className="flex items-center gap-2 text-xs">
                    <span className="px-2 py-1 rounded bg-white dark:bg-gray-800 border font-mono text-gray-700 dark:text-gray-300">{c.source_schema || c.source_table.split('.')[0]}.{c.source_table.split('.').pop()}.{c.source_column}</span>
                    <span className="text-gray-400">→</span>
                    <span className="px-2 py-1 rounded bg-indigo-600 text-white font-mono">{c.target_schema || c.target_table.split('.')[0]}.{c.target_table.split('.').pop()}.{c.target_column}</span>
                    <span className="ml-auto px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 text-[11px]">{c.hops || 1} hops</span>
                  </div>
                  <div className="mt-1 text-[11px] font-mono text-gray-500 truncate" title={c.full_path || `${c.source_table}.${c.source_column} -> ${c.target_table}.${c.target_column}`}>{c.full_path || `${c.source_table}.${c.source_column} -> ${c.target_table}.${c.target_column}`}</div>
                  {c.transform_expr && <div className="mt-2 text-xs font-mono text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/20 px-2 py-1 rounded border border-amber-200 dark:border-amber-800">transform: {c.transform_expr}</div>}
                  <div className="mt-1 text-[11px] text-gray-500">confidence {(c.confidence * 100).toFixed(0)}% · {c.transform_expr ? 'transformed' : 'direct'}</div>
                  {selectedCol === c.target_column && journey && journey.fqn === `${c.target_table}.${c.target_column}` && (
                    <div className="mt-2 p-2 bg-white dark:bg-gray-800 border rounded-lg">
                      <div className="text-[11px] font-medium text-gray-700 dark:text-gray-300">Estate-wide journey — {journey.hops} hops</div>
                      <div className="text-[11px] font-mono text-gray-600 dark:text-gray-400 truncate" title={journey.full_path}>{journey.full_path}</div>
                      <div className="text-[11px] text-gray-500">Upstream: {journey.upstream?.length || 0} · Downstream: {journey.downstream?.length || 0}</div>
                      {journey.upstream?.length > 0 && <div className="text-[11px] text-gray-500">Up: {journey.upstream.slice(0, 3).map((u) => u.fqn).join(' → ')}</div>}
                      {journey.downstream?.length > 0 && <div className="text-[11px] text-gray-500">Down: {journey.downstream.slice(0, 3).map((d) => d.fqn).join(' → ')}</div>}
                    </div>
                  )}
                </>
              )}
            </div>
          ))}
          {filtered.length > 12 && <div className="text-xs text-gray-400 text-center">+{filtered.length - 12} more — search to filter</div>}
          {filtered.length === 0 && <div className="text-xs text-gray-400 text-center py-8">No column lineage for this node</div>}
        </div>
      ) : tab === 'structure' ? (
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          <div className="text-xs text-gray-500">Structure Engine — <span className="font-medium">Structure → Facts</span> · filtered by <span className="font-mono">{fqn}</span> · {(facts.facts || []).filter((f) => matchesFqn(f.evidence?.fqn)).length} facts, {(facts.unknowns || []).filter((u) => matchesFqn(u.evidence?.fqn)).length} unknowns</div>
          {(facts.facts || []).filter((f) => matchesFqn(f.evidence?.fqn)).map((f, i) => (
            <div key={i} className="p-3 rounded-xl border bg-emerald-50/50 dark:bg-emerald-900/10 border-emerald-200 dark:border-emerald-800">
              <div className="text-xs font-medium text-emerald-800 dark:text-emerald-200">{f.type}</div>
              <div className="text-xs font-mono text-gray-700 dark:text-gray-300 truncate" title={f.evidence?.fqn}>{f.evidence?.fqn}</div>
              <div className="text-xs text-gray-500">{f.evidence?.snippet}</div>
              <div className="text-[11px] text-gray-400">confidence {(f.confidence * 100).toFixed(0)}% · {f.source_ref}</div>
            </div>
          ))}
          {(facts.unknowns || []).filter((u) => matchesFqn(u.evidence?.fqn)).length > 0 && (
            <div className="pt-2 border-t">
              <div className="text-xs font-medium text-amber-700 dark:text-amber-300">Unknowns for {fqn} (extensible)</div>
              {(facts.unknowns || []).filter((u) => matchesFqn(u.evidence?.fqn)).map((u, i) => (
                <div key={i} className="mt-1 p-2 rounded bg-amber-50 dark:bg-amber-900/20 border border-amber-200 text-xs">
                  {u.evidence?.fqn} — {u.evidence?.snippet}
                </div>
              ))}
            </div>
          )}
          {(facts.facts || []).filter((f) => matchesFqn(f.evidence?.fqn)).length === 0 && <div className="text-xs text-gray-400 py-4">No structure facts for {fqn} — try selecting a table.</div>}
        </div>
      ) : tab === 'semantic' ? (
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          <div className="text-xs text-gray-500">Semantic AI Engine — <span className="font-medium">Facts + Context → Meaning</span> · filtered by <span className="font-mono">{fqn}</span>{selectedCol ? ` · column ${selectedCol}` : ''}</div>
          {(semantic.rules || []).filter((r) => matchesFqn(r.evidence?.fqn) && (!selectedCol || r.evidence?.fqn?.toUpperCase().includes(selectedCol.toUpperCase()))).map((r, i) => (
            <div key={i} className="p-3 rounded-xl border bg-violet-50/50 dark:bg-violet-900/10 border-violet-200 dark:border-violet-800">
              <div className="text-xs font-medium text-violet-800 dark:text-violet-200">{r.type}: {r.meaning || r.evidence?.snippet}</div>
              <div className="text-xs font-mono text-gray-600 dark:text-gray-300 truncate" title={r.evidence?.fqn}>{r.evidence?.fqn}</div>
              <div className="text-[11px] text-gray-400">confidence {(r.confidence * 100).toFixed(0)}% · {r.source_ref}</div>
            </div>
          ))}
          {(semantic.implicit_contracts || []).filter((r) => matchesFqn(r.evidence?.fqn)).map((r, i) => (
            <div key={`ic-${i}`} className="p-3 rounded-xl border bg-violet-50/30 dark:bg-violet-900/5 border-violet-200 dark:border-violet-800">
              <div className="text-xs font-medium text-violet-800 dark:text-violet-200">implicit_contract: {r.meaning || r.evidence?.snippet}</div>
              <div className="text-xs font-mono text-gray-600 dark:text-gray-300 truncate" title={r.evidence?.fqn}>{r.evidence?.fqn}</div>
            </div>
          ))}
          {(semantic.similar || []).filter((s) => matchesFqn(s.evidence?.fqn)).length > 0 && <div className="text-xs font-medium text-gray-700 dark:text-gray-300 mt-2">Similar logic ({(semantic.similar || []).filter((s) => matchesFqn(s.evidence?.fqn)).length} for {fqn})</div>}
          {(semantic.similar || []).filter((s) => matchesFqn(s.evidence?.fqn)).map((s, i) => <div key={i} className="text-xs p-2 border rounded bg-gray-50 dark:bg-gray-900">{s.evidence?.fqn} — {s.evidence?.snippet}</div>)}
          {(semantic.conflicting || []).filter((s) => matchesFqn(s.evidence?.fqn)).map((s, i) => <div key={`c-${i}`} className="text-xs p-2 border rounded bg-red-50 dark:bg-red-900/20 border-red-200">conflict: {s.evidence?.fqn} — {s.evidence?.snippet}</div>)}
          {(semantic.rules || []).filter((r) => matchesFqn(r.evidence?.fqn)).length === 0 && (semantic.implicit_contracts || []).filter((r) => matchesFqn(r.evidence?.fqn)).length === 0 && <div className="text-xs text-gray-400 py-4">No semantic rules for {fqn} — business rules like RISK_SCORE bands appear here.</div>}
        </div>
      ) : tab === 'behavior' ? (
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          <div className="text-xs text-gray-500">Behavior Engine — <span className="font-medium">Meaning + Context → Behavior</span> · filtered by <span className="font-mono">{fqn}</span></div>
          {[...(behavior.failures || []), ...(behavior.dependencies || []), ...(behavior.rejects || []), ...(behavior.data_quality || []), ...(behavior.operational || [])].filter((b) => matchesFqn(b.evidence?.fqn)).slice(0, 12).map((b, i) => (
            <div key={i} className="p-3 rounded-xl border bg-amber-50/50 dark:bg-amber-900/10 border-amber-200 dark:border-amber-800">
              <div className="text-xs font-medium text-amber-800 dark:text-amber-200">{b.type}: {b.meaning || b.evidence?.snippet}</div>
              <div className="text-xs font-mono text-gray-600 dark:text-gray-300" title={b.evidence?.fqn}>{b.evidence?.fqn}</div>
              <div className="text-[11px] text-gray-400">{b.source_ref}</div>
            </div>
          ))}
          {[...(behavior.failures || []), ...(behavior.dependencies || []), ...(behavior.rejects || []), ...(behavior.data_quality || []), ...(behavior.operational || [])].filter((b) => matchesFqn(b.evidence?.fqn)).length === 0 && <div className="text-xs text-gray-400 py-2">No behavior facts for {fqn} — runtime logs for PROC_RISK_ASSESSMENT retry will appear here when that node is selected.</div>}
        </div>
      ) : null}
    </div>
  );
}
