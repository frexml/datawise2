import React, { useState } from 'react';
import ReactFlow, { Background, Controls, MiniMap } from 'reactflow';
import 'reactflow/dist/style.css';

const SELECTED_STYLE = { background: '#4F46E5', color: '#fff', border: '2px solid #4338CA', opacity: 1 };
const NEIGHBOR_STYLE = { background: '#A7F3D0', color: '#065F46', border: '2px solid #059669', opacity: 1 };

export default function GraphPanel({ nodes, edges, onNodesChange, onEdgesChange, onNodeClick, onNodeMouseEnter, onNodeMouseLeave, selected, blast, unresolvedCount, hoverPreview, highlightCol, onClearHighlight, onPaneClick }) {
  const [expanded, setExpanded] = useState(false);

  React.useEffect(() => { setExpanded(false); }, [selected]);

  // Direct neighbors of the clicked node (1-hop, undirected). Transitive
  // connectivity would light up almost the whole estate (140/151 nodes form a
  // single component), which defeats the point of dimming the rest.
  const highlight = React.useMemo(() => {
    if (!selected) return null;
    const connected = new Set([selected]);
    for (const e of edges) {
      if (e.source === selected) connected.add(e.target);
      else if (e.target === selected) connected.add(e.source);
    }
    return connected;
  }, [selected, edges]);

  const viewNodes = React.useMemo(() => {
    if (!highlight) return nodes;
    return nodes.map((n) => {
      if (n.id === selected) return { ...n, style: { ...n.style, ...SELECTED_STYLE } };
      if (highlight.has(n.id)) return { ...n, style: { ...n.style, ...NEIGHBOR_STYLE } };
      return { ...n, style: { ...n.style, opacity: 0.15 } };
    });
  }, [nodes, highlight, selected]);

  const viewEdges = React.useMemo(() => {
    if (!highlight) return edges;
    return edges.map((e) => {
      const lit = highlight.has(e.source) && highlight.has(e.target);
      return lit
        ? { ...e, animated: true, style: { ...e.style, stroke: '#059669', strokeWidth: 2, opacity: 1 } }
        : { ...e, style: { ...e.style, opacity: 0.08 } };
    });
  }, [edges, highlight]);

  const blastList = blast?.blast_radius || [];
  const count = blast?.count ?? blastList.length;
  const showEllipsis = count > 5 && !expanded;
  const visible = expanded ? blastList : blastList.slice(0, 5);
  const remaining = count - 5;

  return (
    <div className="bg-white dark:bg-gray-800 border rounded-lg">
      <div style={{ height: 650 }} className="relative">
        <ReactFlow
          nodes={viewNodes}
          edges={viewEdges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onNodeMouseEnter={onNodeMouseEnter}
          onNodeMouseLeave={onNodeMouseLeave}
          onPaneClick={onPaneClick}
          fitView
        >
          <Background />
          <Controls />
          <MiniMap />
        </ReactFlow>
        {hoverPreview && (
          <div className="absolute top-2 left-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 shadow-lg text-xs pointer-events-none">
            <div className="font-mono font-medium text-gray-900 dark:text-white">{hoverPreview.fqn}</div>
            <div className="text-gray-500">{hoverPreview.count} columns, {hoverPreview.withTransform} with transforms - click to see what changed</div>
          </div>
        )}
        {highlightCol && (
          <div className="absolute top-2 right-2 bg-indigo-600 text-white text-xs px-2 py-1 rounded-full shadow">
            highlighting: {highlightCol.source_column} → {highlightCol.target_column}
            <button onClick={(e) => { e.stopPropagation(); onClearHighlight && onClearHighlight(); }} className="ml-2 underline">clear</button>
          </div>
        )}
      </div>
      {selected && (
        <div className="p-3 border-t text-xs leading-relaxed">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="font-medium text-gray-900 dark:text-white">{selected}</span>
            {blast && (
              <span className="text-gray-600 dark:text-gray-300">
                <span className="font-medium">blast radius {count}:</span>{' '}
                {visible.map((b, i) => (
                  <React.Fragment key={b.fqn}>
                    {i > 0 && ', '}
                    <span className="font-mono text-gray-700 dark:text-gray-200">{b.fqn}</span>
                  </React.Fragment>
                ))}
                {showEllipsis && (
                  <button
                    onClick={() => setExpanded(true)}
                    className="ml-1 inline-flex items-center px-1.5 py-0.5 rounded text-indigo-600 dark:text-indigo-400 bg-indigo-50 dark:bg-indigo-900/30 hover:bg-indigo-100 dark:hover:bg-indigo-900/50 border border-indigo-200 dark:border-indigo-800 text-[11px] font-medium transition"
                    title="Show full blast radius"
                  >
                    … +{remaining} more
                  </button>
                )}
                {expanded && count > 5 && (
                  <button onClick={() => setExpanded(false)} className="ml-2 text-indigo-600 dark:text-indigo-400 hover:underline text-[11px]">show less</button>
                )}
              </span>
            )}
            <span className="text-gray-400">· {Math.max((highlight?.size || 1) - 1, 0)} connected highlighted — click background to clear</span>
          </div>
        </div>
      )}
      {unresolvedCount > 0 && (
        <div className="p-2 text-xs text-amber-600 bg-amber-50 dark:bg-amber-900/20">⚠️ {unresolvedCount} unresolved edge(s) flagged - requires ledger decision before promote</div>
      )}
    </div>
  );
}
