import React from 'react';

const healthBg = { emerald: 'bg-emerald-500', amber: 'bg-amber-500', red: 'bg-red-500' };
const healthText = { emerald: 'text-emerald-600', amber: 'text-amber-600', red: 'text-red-600' };
const healthLight = { emerald: 'bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800', amber: 'bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-800', red: 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800' };

export default function AnalyticsPanel({ data, recommendation }) {
  if (!data) return <div className="text-sm text-gray-500 p-4">Loading analytics…</div>;

  const health = data.estate_health ?? 76;
  const healthLabel = data.health_label ?? (health >= 80 ? 'Healthy' : health >= 60 ? 'Moderate Risk' : 'At Risk');
  const hColor = data.health_color ?? (health >= 80 ? 'emerald' : health >= 60 ? 'amber' : 'red');
  const readiness = data.migration_readiness ?? Math.round((1 - (data.unresolved_count / Math.max(data.total_nodes, 1))) * data.coverage_pct);
  const orphanCost = data.orphan_cost_label ?? `$${(data.orphan_count * 900).toLocaleString()}/yr`;
  const blast = data.blast_stats ?? { p50: 0, p90: 0, max: 0, mean: 0 };
  const depthHist = data.lineage_depth?.histogram ?? { 1: 0, 2: 0, 3: 0 };
  const maxDepth = data.lineage_depth?.max_depth ?? 0;
  const quadrant = data.quadrant ?? [];

  // Quick Wins / Wedge / Watchlist derived
  const quickWins = data.orphan_tables?.slice(0, 3) ?? [];
  const watchUnresolved = data.unresolved?.slice(0, 3) ?? [];

  // Wedge card - driven by the real /bridge/recommend call (recommend_wedge), not static text.
  const wedgeScope = recommendation?.scope_fqns ?? [];
  const wedgeScopeSet = new Set(wedgeScope);
  const wedgeDashboards = (data.dashboard_lineage ?? []).filter((dl) =>
    dl.upstream_tables?.some((t) => wedgeScopeSet.has(t))
  );
  const wedgeDashboardNames = wedgeDashboards.map((dl) => dl.dashboard);

  return (
    <div className="space-y-6">
      {/* Hero: Estate Health */}
      <div className={`rounded-2xl border p-5 sm:p-6 ${healthLight[hColor]}`}>
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <div className={`h-14 w-14 rounded-2xl flex items-center justify-center text-white font-bold text-lg ${healthBg[hColor]}`}>{health}</div>
            <div>
              <div className="text-sm font-semibold text-gray-900 dark:text-white">Estate Health - {healthLabel}</div>
              <div className="text-xs text-gray-600 dark:text-gray-300 mt-0.5">
                {data.total_nodes} objects · {data.unresolved_count} unresolved (EXECUTE IMMEDIATE) · {data.circular_count} circular · {data.orphan_count} orphans ({data.orphan_pct ?? 0}%)
                {health < 80 && ' - You can safely cut 10% of the estate before you move it.'}
              </div>
            </div>
          </div>
          <div className="flex gap-3">
            <div className="text-center px-4 py-2 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700">
              <div className="text-xs text-gray-500">Readiness</div>
              <div className={`text-lg font-bold ${readiness >= 70 ? 'text-emerald-600' : readiness >= 50 ? 'text-amber-600' : 'text-red-600'}`}>{readiness}%</div>
            </div>
            <div className="text-center px-4 py-2 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700">
              <div className="text-xs text-gray-500">Orphan cost</div>
              <div className="text-sm font-bold text-gray-900 dark:text-white">{orphanCost}</div>
            </div>
          </div>
        </div>
        <div className="mt-4 h-2 bg-white/60 dark:bg-gray-800 rounded-full overflow-hidden flex">
          <div className={`h-full ${healthBg[hColor]} transition-all`} style={{ width: `${health}%` }} />
        </div>
        <div className="mt-1 flex justify-between text-[11px] text-gray-500 dark:text-gray-400">
          <span>Coverage {data.coverage_pct}% · {data.nodes_with_edges}/{data.total_nodes} with lineage</span>
          <span>Blast p50 {blast.p50} · p90 {blast.p90} · max {blast.max}</span>
        </div>
      </div>

      {/* Risk vs Value Quadrant + Prioritized Cards */}
      <div className="grid lg:grid-cols-5 gap-6">
        {/* Quadrant */}
        <div className="lg:col-span-3 bg-white dark:bg-gray-800 border rounded-2xl p-5">
          <div className="flex items-center justify-between mb-3">
            <h4 className="font-semibold text-sm text-gray-900 dark:text-white">Risk vs Value - where to act</h4>
            <span className="text-[11px] text-gray-500">size = blast radius</span>
          </div>
          <div className="relative h-[220px] bg-gray-50 dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-700 p-3 overflow-hidden">
            {/* Axes */}
            <div className="absolute inset-3 flex">
              <div className="flex-1 border-r border-dashed border-gray-300 dark:border-gray-600" />
              <div className="flex-1" />
            </div>
            <div className="absolute inset-3 flex flex-col">
              <div className="flex-1 border-b border-dashed border-gray-300 dark:border-gray-600" />
              <div className="flex-1" />
            </div>
            <div className="absolute top-2 left-1/2 -translate-x-1/2 text-[10px] text-gray-400">Value -></div>
            <div className="absolute left-1 top-1/2 -translate-y-1/2 -rotate-90 text-[10px] text-gray-400">Risk -></div>
            <div className="absolute top-4 left-4 text-[10px] px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300">High Risk, Low Value - delete</div>
            <div className="absolute top-4 right-4 text-[10px] px-1.5 py-0.5 rounded bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300">High Risk, High Value - prove last</div>
            <div className="absolute bottom-4 left-4 text-[10px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">Low Risk, Low Value - ignore</div>
            <div className="absolute bottom-4 right-4 text-[10px] px-1.5 py-0.5 rounded bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300">Low Risk, High Value - wedge</div>
            {/* Dots */}
            {quadrant.slice(0, 12).map((q) => {
              const x = Math.min(92, Math.max(8, (q.value / Math.max(5, Math.max(...quadrant.map((x) => x.value)) || 5)) * 80 + 10));
              const y = Math.min(92, Math.max(8, (q.risk / 4) * 80 + 10));
              const size = Math.min(14, 6 + (q.fan_in || 0) * 1.2);
              const color = q.risk >= 3 ? 'bg-red-500' : q.risk >= 2 ? 'bg-amber-500' : q.value >= 4 ? 'bg-emerald-500' : 'bg-gray-400';
              return (
                <div key={q.fqn} title={`${q.fqn} - value ${q.value}, risk ${q.risk}`} className={`absolute rounded-full ${color} border-2 border-white dark:border-gray-800 shadow-sm flex items-center justify-center text-[7px] text-white font-bold`} style={{ left: `${x}%`, top: `${y}%`, width: size, height: size, transform: 'translate(-50%,-50%)' }} />
              );
            })}
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {quadrant.slice(0, 4).map((q) => (
              <span key={q.fqn} className="px-2 py-1 rounded-full bg-gray-100 dark:bg-gray-700 text-[11px] font-mono">{q.fqn} <span className="text-gray-500">v{q.value} r{q.risk}</span></span>
            ))}
          </div>
        </div>

        {/* Three prioritized cards */}
        <div className="lg:col-span-2 space-y-3">
          <div className="bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 rounded-2xl p-4">
            <div className="text-xs font-semibold text-emerald-800 dark:text-emerald-200">Quick Wins - Cut before you move</div>
            <div className="text-xs text-emerald-700 dark:text-emerald-300 mt-1">{data.orphan_count} orphans ({data.orphan_pct ?? 0}% of estate) -> {orphanCost} waste. Zero reads, 0 dashboards.</div>
            <ul className="mt-2 space-y-1">
              {quickWins.map((f) => <li key={f} className="text-xs font-mono bg-white dark:bg-gray-800 px-2 py-1 rounded border truncate">{f}</li>)}
              {quickWins.length === 0 && <li className="text-xs text-gray-500">No orphans - estate is lean</li>}
            </ul>
            <div className="mt-2 text-[11px] text-emerald-700 dark:text-emerald-300">-> Generate DROP script (saves {quickWins.length} edges)</div>
          </div>
          <div className="bg-indigo-50 dark:bg-indigo-900/20 border border-indigo-200 dark:border-indigo-800 rounded-2xl p-4">
            <div className="text-xs font-semibold text-indigo-800 dark:text-indigo-200">Wedge - recommended first cutover</div>
            {recommendation ? (
              <>
                <div className="text-xs text-indigo-700 dark:text-indigo-300 mt-1">
                  {wedgeScope.length} objects, {recommendation.unresolved_count} unresolved
                  {wedgeDashboardNames.length > 0 && (
                    <> · {wedgeDashboardNames.length} dashboard{wedgeDashboardNames.length !== 1 ? 's' : ''} ({wedgeDashboardNames.slice(0, 2).join(', ')}{wedgeDashboardNames.length > 2 ? `, +${wedgeDashboardNames.length - 2} more` : ''})</>
                  )}
                  .
                </div>
                <div className="mt-2 flex items-center gap-2 text-[11px]">
                  <span className="px-2 py-1 rounded bg-white dark:bg-gray-800 border">Est. {recommendation.estimated_days} days</span>
                  <span className={`px-2 py-1 rounded text-white ${recommendation.risk_level === 'high' ? 'bg-red-500' : recommendation.risk_level === 'medium' ? 'bg-amber-500' : 'bg-emerald-500'}`}>risk {recommendation.risk_level}</span>
                  <span className="px-2 py-1 rounded bg-white dark:bg-gray-800 border capitalize">{recommendation.target_platform}</span>
                </div>
              </>
            ) : (
              <div className="text-xs text-indigo-700 dark:text-indigo-300 mt-1">Computing recommendation…</div>
            )}
          </div>
          <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-2xl p-4">
            <div className="text-xs font-semibold text-red-800 dark:text-red-200">Watchlist - Fix first or block cutover</div>
            <ul className="mt-1 space-y-1 text-xs">
              {watchUnresolved.map((u) => <li key={u.source} className="truncate"><span className="font-mono">{u.source}</span> -> {u.reason}</li>)}
              {data.circular_count > 0 && <li className="text-red-700 dark:text-red-300">⚠️ {data.circular_count} circular: {data.circular_dependencies?.[0]?.join(' -> ')}</li>}
              {watchUnresolved.length === 0 && data.circular_count === 0 && <li className="text-gray-500">No blocking issues</li>}
            </ul>
          </div>
        </div>
      </div>

      {/* Supporting visuals */}
      <div className="grid md:grid-cols-3 gap-6">
        <div className="bg-white dark:bg-gray-800 border rounded-2xl p-4">
          <h4 className="font-medium text-sm mb-3">Hot Tables - fan-in</h4>
          <div className="space-y-2">
            {data.hot_tables?.slice(0, 5).map((h) => {
              const max = Math.max(...(data.hot_tables?.map((x) => x.fan_in) ?? [1]));
              const w = (h.fan_in / max) * 100;
              return (
                <div key={h.fqn} className="flex items-center gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-mono truncate">{h.fqn}</div>
                    <div className="h-1.5 bg-gray-100 dark:bg-gray-700 rounded-full mt-1"><div className="h-full bg-indigo-500 rounded-full" style={{ width: `${w}%` }} /></div>
                  </div>
                  <div className="text-xs font-medium text-gray-600 dark:text-gray-300">{h.fan_in}</div>
                </div>
              );
            })}
          </div>
        </div>
        <div className="bg-white dark:bg-gray-800 border rounded-2xl p-4">
          <h4 className="font-medium text-sm mb-3">Orphan vs Active</h4>
          <div className="flex items-center gap-4">
            <div className="h-20 w-20 rounded-full border-4 border-gray-100 dark:border-gray-700 flex items-center justify-center" style={{ borderTopColor: '#10b981', borderRightColor: '#10b981', borderBottomColor: data.orphan_count ? '#f59e0b' : '#10b981', borderLeftColor: '#10b981' }}>
              <div className="text-center">
                <div className="text-sm font-bold text-gray-900 dark:text-white">{data.orphan_pct ?? 0}%</div>
                <div className="text-[10px] text-gray-500">orphan</div>
              </div>
            </div>
            <div className="text-xs space-y-1">
              <div><span className="inline-block h-2 w-2 rounded-full bg-emerald-500 mr-1" /> Active {data.nodes_with_edges}</div>
              <div><span className="inline-block h-2 w-2 rounded-full bg-amber-500 mr-1" /> Orphan {data.orphan_count}</div>
              <div className="text-gray-500">{data.total_tables} tables · {data.total_views} views · {data.total_procedures} procs</div>
            </div>
          </div>
        </div>
        <div className="bg-white dark:bg-gray-800 border rounded-2xl p-4">
          <h4 className="font-medium text-sm mb-2">Lineage Depth</h4>
          <div className="text-xs text-gray-500 mb-2">max depth {maxDepth} - deeper = more fragile</div>
          <div className="flex items-end gap-2 h-16">
            {[1, 2, 3].map((d) => {
              const cnt = depthHist[d] ?? 0;
              const max = Math.max(...Object.values(depthHist), 1);
              const h = (cnt / max) * 100;
              return (
                <div key={d} className="flex-1 flex flex-col items-center gap-1">
                  <div className="w-full bg-indigo-500 rounded-t" style={{ height: `${Math.max(8, h)}%` }} />
                  <div className="text-[11px] font-medium">d{d}</div>
                  <div className="text-[10px] text-gray-500">{cnt}</div>
                </div>
              );
            })}
          </div>
          <div className="text-[11px] text-gray-500 mt-1">d1 table-only · d2 view->table · d3 view->view->table</div>
        </div>
      </div>

      {/* Dashboards criticality */}
      <div className="bg-white dark:bg-gray-800 border rounded-2xl p-4">
        <h4 className="font-medium text-sm mb-2">Dashboards - criticality</h4>
        <div className="grid sm:grid-cols-2 gap-2">
          {data.dashboard_lineage?.map((d) => (
            <div key={d.dashboard} className={`p-2 rounded-lg border text-xs ${d.criticality === 'regulatory' ? 'border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20' : d.criticality === 'revenue' ? 'border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20' : 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900'}`}>
              <div className="font-medium flex items-center gap-1.5">
                {d.dashboard} <span className={`px-1 py-0.5 rounded text-[10px] text-white ${d.criticality === 'regulatory' ? 'bg-red-500' : d.criticality === 'revenue' ? 'bg-amber-500' : 'bg-gray-500'}`}>{d.criticality}</span>
                <span className="text-gray-400 font-normal">· {d.last_run}</span>
              </div>
              <div className="text-gray-600 dark:text-gray-300 truncate">{d.source_view} -> {d.upstream_tables?.slice(0, 2).join(', ')}{d.upstream_tables?.length > 2 ? ` +${d.upstream_tables.length - 2}` : ''}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
