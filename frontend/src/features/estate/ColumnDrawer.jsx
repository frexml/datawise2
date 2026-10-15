import React, { useState } from 'react';

export default function ColumnDrawer({ fqn, kind, columns, onClose, onSelectColumn }) {
  const [tab, setTab] = useState('columns');
  const [search, setSearch] = useState('');
  const [selectedCol, setSelectedCol] = useState(null);

  if (!fqn) return null;

  const filtered = columns.filter((c) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return c.target_column.toLowerCase().includes(q) || c.source_column.toLowerCase().includes(q) || (c.transform_expr && c.transform_expr.toLowerCase().includes(q));
  });

  const handleSelect = (col) => {
    setSelectedCol(col.target_column);
    onSelectColumn && onSelectColumn(col);
  };

  return (
    <div className="w-[380px] shrink-0 bg-white dark:bg-gray-800 border-l border-gray-200 dark:border-gray-700 flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
        <div className="min-w-0">
          <div className="text-xs text-gray-500 dark:text-gray-400 capitalize">{kind}</div>
          <div className="text-sm font-semibold text-gray-900 dark:text-white truncate" title={fqn}>{fqn}</div>
          <div className="text-xs text-gray-500">
            {columns.length} columns · {columns.filter(c => c.transform_expr).length} with transforms
            {columns.some(c => c.unresolved) && <span className="text-red-600 dark:text-red-400"> · {columns.filter(c => c.unresolved).length} unresolved</span>}
          </div>
        </div>
        <button onClick={onClose} className="h-7 w-7 flex items-center justify-center rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500">✕</button>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-200 dark:border-gray-700">
        <button onClick={() => setTab('columns')} className={`flex-1 px-3 py-2 text-xs font-medium border-b-2 ${tab==='columns' ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>Columns</button>
        <button onClick={() => setTab('graph')} className={`flex-1 px-3 py-2 text-xs font-medium border-b-2 ${tab==='graph' ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>Column Graph</button>
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
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {filtered.length === 0 ? (
                  <tr><td colSpan={3} className="px-3 py-6 text-center text-gray-400">No columns — leaf table or unresolved view</td></tr>
                ) : filtered.map((c) => (
                  <tr
                    key={`${c.target_table}.${c.target_column}-${c.source_table}.${c.source_column}`}
                    onClick={() => !c.unresolved && handleSelect(c)}
                    className={`hover:bg-gray-50 dark:hover:bg-gray-700/50 ${c.unresolved ? 'bg-red-50/60 dark:bg-red-950/20' : 'cursor-pointer'} ${selectedCol === c.target_column ? 'bg-indigo-50 dark:bg-indigo-900/30' : ''}`}
                  >
                    <td className="px-3 py-2 font-mono font-medium text-gray-900 dark:text-white">{c.target_column}</td>
                    <td className="px-3 py-2 font-mono text-gray-600 dark:text-gray-300">{c.unresolved ? <span className="text-gray-400 italic font-sans">—</span> : `${c.source_table.split('.').pop()}.${c.source_column}`}</td>
                    <td className="px-3 py-2">
                      {c.unresolved ? (
                        <span className="inline-flex px-1.5 py-0.5 rounded bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 text-[11px]" title={c.unresolved_reason}>
                          unresolved: {c.unresolved_reason?.length > 30 ? c.unresolved_reason.slice(0, 30) + '…' : c.unresolved_reason}
                        </span>
                      ) : c.transform_expr ? (
                        <span className="inline-flex px-1.5 py-0.5 rounded bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-300 font-mono text-[11px]" title={c.transform_expr}>
                          {c.transform_expr.length > 40 ? c.transform_expr.slice(0, 40) + '…' : c.transform_expr}
                        </span>
                      ) : (
                        <span className="text-gray-400">direct</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="p-3 border-t border-gray-200 dark:border-gray-700 flex gap-2">
            <button
              onClick={() => {
                const csv = ['target_column,source_table,source_column,transform,confidence', ...filtered.map(c => `${c.target_column},${c.source_table},${c.source_column},"${(c.transform_expr || '').replace(/"/g, '""')}",${c.confidence}`)].join('\n');
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
      ) : (
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          <div className="text-xs text-gray-500">Column lineage graph — each target column and its sources. Click a column above to highlight its path on the main graph.</div>
          {filtered.slice(0, 12).map((c) => (
            <div key={`${c.target_column}-${c.source_column}`} onClick={() => !c.unresolved && handleSelect(c)} className={`p-3 rounded-xl border ${c.unresolved ? 'border-red-200 dark:border-red-800 bg-red-50/60 dark:bg-red-950/20' : 'cursor-pointer ' + (selectedCol === c.target_column ? 'border-indigo-300 bg-indigo-50 dark:bg-indigo-900/20' : 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 hover:border-gray-300')}`}>
              {c.unresolved ? (
                <>
                  <div className="text-xs font-mono"><span className="text-gray-400">?</span> <span className="text-gray-400">→</span> <span className="px-2 py-1 rounded bg-gray-500 text-white">{c.target_table.split('.').pop()}.{c.target_column}</span></div>
                  <div className="mt-2 text-xs text-red-700 dark:text-red-300">unresolved: {c.unresolved_reason}</div>
                </>
              ) : (
                <>
                  <div className="flex items-center gap-2 text-xs">
                    <span className="px-2 py-1 rounded bg-white dark:bg-gray-800 border font-mono text-gray-700 dark:text-gray-300">{c.source_table.split('.').pop()}.{c.source_column}</span>
                    <span className="text-gray-400">→</span>
                    <span className="px-2 py-1 rounded bg-indigo-600 text-white font-mono">{c.target_table.split('.').pop()}.{c.target_column}</span>
                  </div>
                  {c.transform_expr && <div className="mt-2 text-xs font-mono text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/20 px-2 py-1 rounded border border-amber-200 dark:border-amber-800">transform: {c.transform_expr}</div>}
                  <div className="mt-1 text-[11px] text-gray-500">confidence {(c.confidence * 100).toFixed(0)}% · {c.transform_expr ? 'transformed' : 'direct'}</div>
                </>
              )}
            </div>
          ))}
          {filtered.length > 12 && <div className="text-xs text-gray-400 text-center">+{filtered.length - 12} more — search to filter</div>}
          {filtered.length === 0 && <div className="text-xs text-gray-400 text-center py-8">No column lineage for this node</div>}
        </div>
      )}
    </div>
  );
}
