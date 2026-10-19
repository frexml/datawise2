import React from 'react';

const PERSPECTIVES = [
  { key: 'end_to_end', label: 'End-to-end', desc: 'Complete flow: Source → ETL → DW → BI', count: null },
  { key: 'table', label: 'Table', desc: 'Table ↔ View only', count: null },
  { key: 'job', label: 'Job', desc: 'ETL → Table + Schedule → Job', count: null },
  { key: 'schema', label: 'Schema', desc: 'Group by schema → BI', count: null },
  { key: 'column', label: 'Column', desc: '87 columns', count: null },
];

export default function LineagePerspectives({ active, onChange, counts }) {
  return (
    <div className="flex flex-wrap gap-1.5 p-2 bg-gray-50 dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-700">
      {PERSPECTIVES.map((p) => {
        const c = counts?.[p.key];
        const label = c != null ? `${p.label} · ${c}` : p.label;
        return (
          <button
            key={p.key}
            onClick={() => onChange(p.key)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition ${active === p.key ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 border-gray-200 dark:border-gray-600 hover:border-gray-300'}`}
            title={p.desc}
          >
            {label}
          </button>
        );
      })}
      {active === 'column' && (
        <div className="ml-auto flex gap-1">
          <button className="px-2 py-1 text-xs rounded bg-white dark:bg-gray-800 border">Complete</button>
          <button className="px-2 py-1 text-xs rounded bg-red-50 dark:bg-red-900/20 border border-red-200 text-red-700">Attention Required</button>
          <button className="px-2 py-1 text-xs rounded bg-amber-50 dark:bg-amber-900/20 border border-amber-200 text-amber-700">Changed + audit</button>
        </div>
      )}
    </div>
  );
}
