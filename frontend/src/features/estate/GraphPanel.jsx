import React, { useState } from 'react';
import ReactFlow, { Background, Controls, MiniMap } from 'reactflow';
import 'reactflow/dist/style.css';

export default function GraphPanel({ nodes, edges, onNodesChange, onEdgesChange, onNodeClick, selected, blast, unresolvedCount }) {
  const [expanded, setExpanded] = useState(false);

  // Reset expanded when selection changes
  React.useEffect(() => { setExpanded(false); }, [selected]);

  const blastList = blast?.blast_radius || [];
  const count = blast?.count ?? blastList.length;
  const showEllipsis = count > 5 && !expanded;
  const visible = expanded ? blastList : blastList.slice(0, 5);
  const remaining = count - 5;

  return (
    <div className="bg-white dark:bg-gray-800 border rounded-lg">
      <div style={{ height: 650 }}>
        <ReactFlow nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onNodeClick={onNodeClick} fitView>
          <Background />
          <Controls />
          <MiniMap />
        </ReactFlow>
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
          </div>
        </div>
      )}
      {unresolvedCount > 0 && (
        <div className="p-2 text-xs text-amber-600 bg-amber-50 dark:bg-amber-900/20">⚠️ {unresolvedCount} unresolved edge(s) flagged - requires ledger decision before promote</div>
      )}
    </div>
  );
}
