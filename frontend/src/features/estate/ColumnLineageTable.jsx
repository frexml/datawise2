import React, { useState } from 'react';

export default function ColumnLineageTable({ columnLineage }) {
  const [search, setSearch] = useState('');
  const [filterTransform, setFilterTransform] = useState(false);

  if (!columnLineage) return <div className="text-sm text-gray-500 p-4">Loading column lineage…</div>;

  const unresolvedCount = columnLineage.filter((c) => c.unresolved).length;

  const filtered = columnLineage.filter((c) => {
    if (filterTransform && !c.transform_expr) return false;
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      c.target_table.toLowerCase().includes(q) ||
      c.target_column.toLowerCase().includes(q) ||
      c.source_table.toLowerCase().includes(q) ||
      c.source_column.toLowerCase().includes(q) ||
      (c.transform_expr && c.transform_expr.toLowerCase().includes(q)) ||
      (c.unresolved_reason && c.unresolved_reason.toLowerCase().includes(q))
    );
  });

  return (
    <div className="bg-white dark:bg-gray-800 border rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex flex-col sm:flex-row gap-3 sm:items-center justify-between">
        <div>
          <h4 className="text-sm font-semibold text-gray-900 dark:text-white">Column Lineage — what changed per column</h4>
          <p className="text-xs text-gray-500">
            Search any table/column or transform. "What changed" shows the SQL transform or `direct`.
            {unresolvedCount > 0 && <span className="text-amber-600 dark:text-amber-400"> · {unresolvedCount} unresolved</span>}
          </p>
        </div>
        <div className="flex gap-2">
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search target, source, or transform…" className="px-3 py-2 border rounded-lg text-xs w-64 dark:bg-gray-900 dark:border-gray-700 dark:text-white" />
          <label className="flex items-center gap-1 text-xs px-2">
            <input type="checkbox" checked={filterTransform} onChange={(e) => setFilterTransform(e.target.checked)} /> transformed only
          </label>
        </div>
      </div>
      <div className="overflow-x-auto max-h-[500px] overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-gray-50 dark:bg-gray-900 text-gray-500">
            <tr>
              <th className="text-left px-3 py-2">Target (Schema → Table → Column)</th>
              <th className="text-left px-3 py-2">Source (Schema → Table → Column)</th>
              <th className="text-left px-3 py-2">What changed</th>
              <th className="text-left px-3 py-2">Hops</th>
              <th className="text-left px-3 py-2">Confidence</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
            {filtered.slice(0, 200).map((c, i) => (
              <tr key={`${c.target_table}.${c.target_column}-${c.source_table}.${c.source_column}-${i}`} className={`hover:bg-gray-50 dark:hover:bg-gray-700/30 ${c.unresolved ? 'bg-red-50/60 dark:bg-red-950/20' : ''}`} title={c.full_path || `${c.source_table}.${c.source_column} -> ${c.target_table}.${c.target_column} (${c.hops || 1} hops)`}>
                <td className="px-3 py-2 font-mono">
                  <div className="font-medium text-gray-900 dark:text-white">{c.target_schema || c.target_table.split('.')[0]} → {c.target_table.split('.').pop()}.{c.target_column}</div>
                  <div className="text-[10px] text-gray-400">{c.target_table}</div>
                </td>
                <td className="px-3 py-2 font-mono text-gray-600 dark:text-gray-300">
                  {c.unresolved ? (
                    <span className="inline-flex px-1.5 py-0.5 rounded bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300 text-[11px]">Attention Required</span>
                  ) : (
                    <div>
                      <div>{(c.source_schema || c.source_table.split('.')[0])} → {c.source_table.split('.').pop()}.{c.source_column}</div>
                      <div className="text-[10px] text-gray-400">{c.source_table}</div>
                    </div>
                  )}
                </td>
                <td className="px-3 py-2">
                  {c.unresolved ? (
                    <span className="inline-flex px-1.5 py-0.5 rounded bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 text-[11px] max-w-[280px] truncate" title={c.unresolved_reason}>
                      No clear lineage
                    </span>
                  ) : c.transform_expr ? (
                    <span className="inline-flex px-1.5 py-0.5 rounded bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-300 font-mono text-[11px] max-w-[280px] truncate" title={c.transform_expr}>
                      {c.transform_expr}
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
                <td className="px-3 py-2">
                  <span className={`px-1.5 py-0.5 rounded text-[11px] ${c.unresolved ? 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300' : c.confidence >= 0.9 ? 'bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300' : 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300'}`}>
                    {(c.confidence * 100).toFixed(0)}%
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length === 0 && <div className="p-6 text-center text-sm text-gray-400">No matching columns</div>}
        {filtered.length > 200 && <div className="p-2 text-xs text-gray-500 text-center">Showing 200 of {filtered.length} — search to filter</div>}
      </div>
      <div className="px-4 py-3 border-t border-gray-200 dark:border-gray-700 flex justify-between items-center">
        <span className="text-xs text-gray-500">{filtered.length} column lineage rows (of {columnLineage.length})</span>
        <button
          onClick={() => {
            const csv = ['target_schema,target_table,target_column,source_schema,source_table,source_column,transform,hops,full_path,confidence', ...filtered.map((c) => `${c.target_schema || c.target_table.split('.')[0] || ''},${c.target_table},${c.target_column},${c.source_schema || c.source_table.split('.')[0] || ''},${c.source_table},${c.source_column},"${(c.transform_expr || '').replace(/"/g, '""')}",${c.hops || 1},"${(c.full_path || '').replace(/"/g, '""')}",${c.confidence}`)].join('\n');
            const blob = new Blob([csv], { type: 'text/csv' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = 'column_lineage.csv'; a.click();
          }}
          className="px-3 py-1.5 bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-xs font-medium hover:bg-gray-50"
        >
          Export CSV
        </button>
      </div>
    </div>
  );
}
