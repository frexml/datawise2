import React, { useEffect, useState, useCallback, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import axios from 'axios';
import { useNodesState, useEdgesState, MarkerType } from 'reactflow';
import GraphPanel from '../features/estate/GraphPanel';
import AnalyticsPanel from '../features/estate/AnalyticsPanel';
import ChatPanel from '../features/estate/ChatPanel';
import BridgePanel from '../features/estate/BridgePanel';
import LedgerPanel from '../features/estate/LedgerPanel';
import ColumnDrawer from '../features/estate/ColumnDrawer';
import ColumnLineageTable from '../features/estate/ColumnLineageTable';
import KnowledgeGraphPanel from '../features/estate/KnowledgeGraphPanel';
import LineagePerspectives from '../features/estate/LineagePerspectives';
import UnresolvedPanel from '../features/estate/UnresolvedPanel';
import { useAuth } from '../context/AuthContext';

// BFS shortest path between two graph nodes - used to highlight a column's
// full lineage path, which may span 2+ view hops, not just a direct edge.
function findPath(edges, fromId, toId) {
  const empty = { nodes: new Set(), edgeIds: new Set() };
  if (!fromId || !toId) return empty;
  if (fromId === toId) return { nodes: new Set([fromId]), edgeIds: new Set() };

  const adjacency = new Map();
  for (const e of edges) {
    if (!adjacency.has(e.source)) adjacency.set(e.source, []);
    adjacency.get(e.source).push({ to: e.target, edgeId: e.id });
  }

  const visited = new Set([fromId]);
  const queue = [[fromId, [fromId], []]];
  while (queue.length > 0) {
    const [cur, nodePath, edgePath] = queue.shift();
    if (cur === toId) return { nodes: new Set(nodePath), edgeIds: new Set(edgePath) };
    for (const { to, edgeId } of adjacency.get(cur) || []) {
      if (!visited.has(to)) {
        visited.add(to);
        queue.push([to, [...nodePath, to], [...edgePath, edgeId]]);
      }
    }
  }
  return empty; // no path - e.g. source/target aren't connected in the loaded graph
}

// Column perspective draws at most this many lineage rows: each row becomes a
// source node + target node + edge, and the full 935-row list would overwhelm
// ReactFlow. The complete list lives in the "Column Lineage" table tab.
const MAX_COLUMN_EDGES = 160;

// Build the column perspective graph. Every edge endpoint must exist as a node
// (ReactFlow silently drops edges whose endpoints are missing) and node ids must
// be unique (duplicates collapse), so we dedupe through a Map and always emit
// both the source and target column nodes for each drawn row.
function buildColumnView(columnLineage, colCase) {
  let cols = columnLineage;
  if (colCase === 'attention') cols = cols.filter((c) => c.unresolved);
  else if (colCase === 'complete') cols = cols.filter((c) => !c.unresolved && !c.transform_expr);
  else if (colCase === 'changed') cols = cols.filter((c) => c.transform_expr);

  const edgeable = cols.filter((c) => c.source_table && c.source_column);
  const specs = edgeable.slice(0, MAX_COLUMN_EDGES);
  const orphanTargets = cols
    .filter((c) => !(c.source_table && c.source_column))
    .slice(0, MAX_COLUMN_EDGES - specs.length);

  const nodeMap = new Map();
  const columnNode = (fqn, color, isSource, index) => ({
    id: fqn,
    label: fqn.split('.').pop(),
    type: 'column',
    data: { label: fqn.split('.').pop(), fqn, kind: 'column' },
    position: {
      x: isSource ? (index % 3) * 190 : 900 + (index % 3) * 190,
      y: Math.floor(index / 3) * 60,
    },
    style: { background: color, border: '1px solid #CBD5E1', fontSize: '9px', padding: '4px', borderRadius: '4px', width: 150 },
  });

  const edges = [];
  specs.forEach((c, i) => {
    const sourceId = `${c.source_table}.${c.source_column}`;
    const targetId = `${c.target_table}.${c.target_column}`;
    if (!nodeMap.has(sourceId)) nodeMap.set(sourceId, columnNode(sourceId, '#F1F5F9', true, nodeMap.size));
    if (!nodeMap.has(targetId)) nodeMap.set(targetId, columnNode(targetId, c.unresolved ? '#FEE2E2' : c.transform_expr ? '#FEF3C7' : '#ECFDF5', false, i));
    edges.push({
      id: `${sourceId}->${targetId}:${i}`,
      source: sourceId,
      target: targetId,
      label: c.transform_expr ? 'transform' : 'direct',
      animated: !!c.transform_expr,
      style: { stroke: c.unresolved ? '#EF4444' : '#6366F1' },
      markerEnd: { type: MarkerType.ArrowClosed, color: c.unresolved ? '#EF4444' : '#6366F1' },
    });
  });
  orphanTargets.forEach((c, i) => {
    const targetId = `${c.target_table}.${c.target_column}`;
    if (!nodeMap.has(targetId)) nodeMap.set(targetId, columnNode(targetId, '#FEE2E2', false, i));
  });

  return { nodes: [...nodeMap.values()], edges };
}

const TAB_LABELS = [
  { key: 'graph', label: 'Lineage Graph' },
  { key: 'knowledge', label: 'Knowledge Graph' },
  { key: 'analytics', label: 'Analytics' },
  { key: 'unresolved', label: 'Unresolved' },
  { key: 'columns', label: 'Column Lineage' },
  { key: 'chat', label: 'Chat' },
  { key: 'bridge', label: 'Bridge' },
  { key: 'ledger', label: 'Ledger' },
];

// Thin orchestrator - was 650-line God component, now delegates to features/estate/* (P1 fix)
export default function EstateDetails() {
  const { estateId } = useParams();
  const { user } = useAuth();
  const [estate, setEstate] = useState(null);
  const [activeTab, setActiveTab] = useState('graph');
  const [graph, setGraph] = useState(null);
  const [analytics, setAnalytics] = useState(null);
  const [recommendation, setRecommendation] = useState(null);
  const [ledger, setLedger] = useState([]);
  const [error, setError] = useState(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  // Full (unfiltered) graph used to rebuild any perspective without refetching.
  const baseRef = useRef({ nodes: [], edges: [] });
  const [selected, setSelected] = useState(null);
  const [blast, setBlast] = useState(null);
  const [surveyTodo, setSurveyTodo] = useState([]);
  const [surveyStage, setSurveyStage] = useState('');
  const [displayTodo, setDisplayTodo] = useState([]);
  const [preview, setPreview] = useState(null);
  const [approver, setApprover] = useState(user?.name || '');
  const [approveError, setApproveError] = useState(null);
  const [ir, setIr] = useState(null);
  const [drawerFqn, setDrawerFqn] = useState(null);
  const [drawerKind, setDrawerKind] = useState(null);
  const [highlightCol, setHighlightCol] = useState(null);
  const [hoverPreview, setHoverPreview] = useState(null);
  const [perspective, setPerspective] = useState('end_to_end');
  const [columnCase, setColumnCase] = useState('all'); // all | complete | attention | changed

  useEffect(() => { if (user?.name) setApprover(user.name); }, [user]);

  const applyPerspective = (nodes, edges, pers, colCase, irData) => {
    if (pers === 'end_to_end') return { nodes, edges };
    if (pers === 'table') {
      const keepTypes = new Set(['table', 'view']);
      const keepIds = new Set(nodes.filter((n) => keepTypes.has(n.type)).map((n) => n.id));
      return { nodes: nodes.filter((n) => keepIds.has(n.id)), edges: edges.filter((e) => keepIds.has(e.source) && keepIds.has(e.target)) };
    }
    if (pers === 'job') {
      const keepTypes = new Set(['etl', 'schedule', 'table']);
      const keepIds = new Set(nodes.filter((n) => keepTypes.has(n.type) || n.data?.kind === 'procedure').map((n) => n.id));
      // keep ETL->Table and Schedule->Job
      return { nodes: nodes.filter((n) => keepIds.has(n.id) || n.type === 'table'), edges: edges.filter((e) => e.type === 'ETL_READS' || e.type === 'ETL_WRITES' || e.type === 'SCHEDULE_DEPENDS' || e.type === 'SP_READS' || e.type === 'SP_WRITES') };
    }
    if (pers === 'schema') {
      // Group by schema - keep all but add schema label; for now just show all with schema coloring
      return { nodes, edges };
    }
    if (pers === 'column') {
      if (!irData || !irData.column_lineage) return { nodes, edges };
      return buildColumnView(irData.column_lineage, colCase);
    }
    return { nodes, edges };
  };

  // Recompute the visible graph for a perspective from the cached base graph.
  const applyView = (pers, colCase, irData, base) => {
    const b = base || baseRef.current;
    const filtered = applyPerspective(b.nodes, b.edges, pers, colCase, irData);
    setNodes(filtered.nodes);
    setEdges(filtered.edges);
  };

  const loadAll = async (showGraph = true) => {
    const r = await axios.get(`/api/estates/${estateId}`);
    setEstate(r.data);
    setSurveyTodo(r.data.survey_todo || []);
    setDisplayTodo(r.data.survey_todo || []);
    setSurveyStage(r.data.current_stage || '');
    setPreview(r.data.survey_preview || null);
    if (r.data.status === 'ACTIVE') {
      // IR first: the column perspective needs column_lineage, so it must be
      // in hand before the graph view is built.
      let irData = ir;
      try {
        const irRes = await axios.get(`/api/estates/${estateId}/ir`);
        irData = irRes.data;
        setIr(irData);
      } catch { setIr(null); }
      if (showGraph) {
        const g = await axios.get(`/api/estates/${estateId}/graph`);
        setGraph(g.data);
        setEstate((prev) => prev && ({
          ...prev,
          node_count: g.data.estate?.counts?.nodes ?? prev.node_count,
          edge_count: g.data.estate?.counts?.edges ?? prev.edge_count,
          unresolved_count: g.data.estate?.counts?.unresolved ?? prev.unresolved_count,
        }));
        const rfNodes = g.data.nodes.map((n, i) => ({
          id: n.id, position: n.position || { x: (i % 6) * 220, y: Math.floor(i / 6) * 80 },
          data: { label: n.label, kind: n.type }, type: 'default',
          style: {
            background: n.type === 'table' ? '#EEF2FF' : n.type === 'view' ? (n.unresolved ? '#FEF3C7' : '#ECFDF5') : n.type === 'procedure' ? (n.has_dynamic ? '#FEE2E2' : '#F3E8FF') : n.type === 'dashboard' ? '#FFF7ED' : '#F1F5F9',
            border: '1px solid #CBD5E1', fontSize: '10px', padding: '6px', borderRadius: '6px', width: 170,
          }
        }));
        const rfEdges = g.data.edges.map((e) => ({ id: e.id, source: e.source, target: e.target, animated: true, style: { stroke: e.unresolved ? '#F59E0B' : '#6366F1' }, markerEnd: { type: MarkerType.ArrowClosed, color: e.unresolved ? '#F59E0B' : '#6366F1' }, label: e.type, type: e.type }));
        const base = { nodes: rfNodes, edges: rfEdges };
        baseRef.current = base;
        applyView(perspective, columnCase, irData, base);
      }
      const a = await axios.get(`/api/estates/${estateId}/analytics`);
      setAnalytics(a.data);
      try {
        const rec = await axios.post(`/api/estates/${estateId}/bridge/recommend`);
        setRecommendation(rec.data);
      } catch { setRecommendation(null); }
    }
    const l = await axios.get(`/api/estates/${estateId}/ledger`);
    setLedger(l.data);
  };

  useEffect(() => {
    const init = async () => {
      try { await loadAll(false); } catch (e) { setError(e.response?.data?.detail || e.message); }
      // If surveying, poll Todo (kept for real backend progress; displayTodo animation handles the 5s view)
      const poll = async () => {
        try {
          const r = await axios.get(`/api/estates/${estateId}/survey/todo`);
          setSurveyTodo(r.data.todo || []);
          setDisplayTodo(r.data.todo || []);
          setSurveyStage(r.data.current_stage || '');
          setPreview(r.data.survey_preview || null);
          setEstate((prev) => prev ? { ...prev, status: r.data.status, current_stage: r.data.current_stage, survey_todo: r.data.todo, node_count: r.data.counts?.nodes ?? prev.node_count, edge_count: r.data.counts?.edges ?? prev.edge_count } : prev);
          if (r.data.status === 'ACTIVE' && r.data.current_stage === 'done') {
            await loadAll(true);
          } else if (r.data.current_stage !== 'done' && r.data.status !== 'ACTIVE') {
            setTimeout(poll, 700);
          } else if (r.data.status === 'ACTIVE') {
            await loadAll(true);
          }
        } catch {}
      };
      // Start polling if not yet ACTIVE
      axios.get(`/api/estates/${estateId}`).then((r) => {
        if (r.data.status !== 'ACTIVE' || r.data.current_stage !== 'done') poll();
        else loadAll(true);
      }).catch(() => loadAll(true));
    };
    init();
  }, [estateId, setNodes, setEdges]);

  const onNodeClick = async (_, node) => {
    setSelected(node.id);
    const kind = node.data?.kind || node.type || 'table';
    setDrawerFqn(node.id);
    setDrawerKind(kind);
    setHighlightCol(null);
    try { const r = await axios.get(`/api/estates/${estateId}/blast-radius`, { params: { fqn: node.id } }); setBlast(r.data); } catch (e) { setBlast({ count: 0, blast_radius: [] }); }
  };

  const onNodeMouseEnter = (_, node) => {
    if (!ir || !ir.column_lineage) return;
    const cols = ir.column_lineage.filter((c) => c.target_table === node.id || c.source_table === node.id);
    if (cols.length > 0) {
      setHoverPreview({ fqn: node.id, count: cols.length, withTransform: cols.filter((c) => c.transform_expr).length });
    }
  };
  const onNodeMouseLeave = () => setHoverPreview(null);

  // Snapshot of the current edges, read (not reactive) by the highlight effect
  // below so it can run BFS without depending on `edges` itself - depending on
  // it directly would re-trigger every time this same effect calls setEdges.
  const edgesRef = useRef([]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);

  // Highlight a column's full lineage path on the graph - BFS over the real
  // edge list, not just a single direct edge, since column lineage commonly
  // spans 2+ view hops (e.g. table -> view -> view -> dashboard).
  useEffect(() => {
    if (!highlightCol) {
      setNodes((nds) => nds.map((n) => ({ ...n, style: { ...n.style, opacity: 1, border: '1px solid #CBD5E1' } })));
      setEdges((eds) => eds.map((e) => ({ ...e, style: { ...e.style, opacity: 1, stroke: e.unresolved ? '#F59E0B' : '#6366F1' } })));
      return;
    }
    const { nodes: pathNodes, edgeIds: pathEdgeIds } = findPath(edgesRef.current, highlightCol.source_table, highlightCol.target_table);
    setNodes((nds) =>
      nds.map((n) => {
        const isOnPath = pathNodes.has(n.id);
        return { ...n, style: { ...n.style, opacity: isOnPath ? 1 : 0.25, border: isOnPath ? '2px solid #6366F1' : '1px solid #CBD5E1' } };
      })
    );
    setEdges((eds) =>
      eds.map((e) => {
        const onPath = pathEdgeIds.has(e.id);
        return { ...e, style: { ...e.style, opacity: onPath ? 1 : 0.15, stroke: onPath ? '#6366F1' : '#CBD5E1' } };
      })
    );
  }, [highlightCol, setNodes, setEdges]);

  if (error) return <div className="p-8 text-red-600 text-sm">{error}</div>;
  if (!estate) return <div className="p-8 text-gray-500">Loading estate…</div>;

  const isSurveying = estate.status !== 'ACTIVE' || surveyStage !== 'done';
  const isPendingApproval = surveyStage === 'pending_approval' || estate.current_stage === 'pending_approval';
  const todoForDisplay = displayTodo.length > 0 ? displayTodo : surveyTodo;
  const doneCount = (todoForDisplay || []).filter((t) => t.status === 'done').length;
  const todoToShow = todoForDisplay.length > 0 ? todoForDisplay : surveyTodo;

  return (
    <div className="space-y-4">
      <div className="bg-white dark:bg-gray-800 shadow sm:rounded-lg p-4 flex justify-between items-center">
        <div>
          <Link to="/estates" className="text-xs text-indigo-600 hover:underline">← Estates</Link>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
            <span>{estate.estate_type === 'telecom' ? '📡' : '🏦'}</span> {estate.display_name}
            <span className={`text-xs px-2 py-0.5 rounded-full ${estate.estate_type === 'telecom' ? 'bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300' : 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'}`}>{estate.estate_type || 'banking'}</span>
          </h2>
          <p className="text-xs text-gray-500">{estate.name} · IR {estate.ir_version || 'pending'} · {estate.node_count || 0} nodes · {estate.edge_count || 0} edges {estate.unresolved_count > 0 && `· ${estate.unresolved_count} unresolved`} {estate.connection?.host && `· ${estate.connection.host}:${estate.connection.port} ${estate.connection.status === 'connected' ? '● Connected' : ''}`}</p>
        </div>
        <div className="text-xs text-gray-500">{isSurveying ? `Discovering… ${doneCount}/${surveyTodo.length || 8}` : 'Knowledge Graph + Evidence Ledger - system of record'}</div>
      </div>

      {isSurveying && todoToShow && todoToShow.length > 0 && (
        <div className="bg-white dark:bg-gray-800 border rounded-xl p-4">
          <div className="flex justify-between items-center mb-2">
            <span className="text-sm font-medium text-gray-900 dark:text-white">{isPendingApproval ? "Based on initial findings, here's the discovery plan awaiting your approval" : 'Discovering your estate - live progress'}</span>
            <span className="text-xs text-gray-500">{isPendingApproval ? `${todoToShow.length} steps proposed` : `${doneCount}/${todoToShow.length} done · ${surveyStage}`}</span>
          </div>
          {isPendingApproval && preview?.systems?.length > 0 && (
            <div className="mb-2 text-xs text-gray-500 dark:text-gray-400">
              Detected: {preview.systems.join(', ')}{preview.estimated_objects ? ` · ~${preview.estimated_objects.replace(/^~/, '')} objects estimated` : ''}
            </div>
          )}
          {isPendingApproval && (
            <div className="mb-3 p-2 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded flex flex-wrap gap-2 justify-between items-center">
              <span className="text-xs text-amber-800 dark:text-amber-200">Approve to begin discovery.</span>
              <div className="flex gap-2 items-center">
                {approveError && <span className="text-xs text-red-600">{approveError}</span>}
                <input
                  value={approver}
                  onChange={(e) => setApprover(e.target.value)}
                  placeholder="Approver name (≥2)"
                  className="px-2 py-1 border rounded text-xs dark:bg-gray-900 dark:border-gray-700 w-36"
                />
                <button
                  onClick={async () => {
                    const name = approver.trim();
                    if (name.length < 2) { setApproveError('Approver name required (≥2 chars)'); return; }
                    setApproveError(null);
                    await axios.post(`/api/estates/${estateId}/survey/approve`, { approver: name, actor_email: user?.email || undefined });
                    // Trigger poll
                    const poll = async () => {
                      const r = await axios.get(`/api/estates/${estateId}/survey/todo`);
                      setSurveyTodo(r.data.todo || []);
                      setDisplayTodo(r.data.todo || []);
                      setSurveyStage(r.data.current_stage || '');
                      if (r.data.status === 'ACTIVE' && r.data.current_stage === 'done') {
                        const g = await axios.get(`/api/estates/${estateId}/graph`);
                        setGraph(g.data);
                      } else {
                        setTimeout(poll, 500);
                      }
                    };
                    poll();
                  }}
                  className="px-3 py-1 bg-indigo-600 text-white rounded text-xs hover:bg-indigo-700"
                >
                  Approve & Run →
                </button>
              </div>
            </div>
          )}
          <div className="grid sm:grid-cols-2 gap-2">
            {todoToShow.map((item) => (
              <div key={item.key} className="flex items-center gap-2 text-xs px-3 py-2 rounded-lg border bg-gray-50 dark:bg-gray-900 dark:border-gray-700">
                <span className={`h-5 w-5 rounded-full flex items-center justify-center text-xs ${item.status === 'done' ? 'bg-emerald-500 text-white' : item.status === 'running' ? 'bg-indigo-500 text-white animate-pulse' : 'bg-gray-200 dark:bg-gray-700 text-gray-500'}`}>{item.status === 'done' ? '✓' : item.status === 'running' ? '◐' : '○'}</span>
                <span className="flex-1 font-medium text-gray-700 dark:text-gray-300">{item.label}</span>
                <span className="text-gray-500">{item.count != null ? item.count : ''}</span>
              </div>
            ))}
          </div>
          <div className="mt-3 h-1.5 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden">
            <div className="h-full bg-indigo-500 transition-all" style={{ width: `${(doneCount / Math.max(todoToShow.length, 1)) * 100}%` }} />
          </div>
        </div>
      )}

      <div className="flex gap-2 border-b dark:border-gray-700">
        {TAB_LABELS.map((t) => <button key={t.key} onClick={() => setActiveTab(t.key)} className={`px-4 py-2 text-sm border-b-2 ${activeTab === t.key ? 'border-orange-600 text-orange-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>{t.label}</button>)}
      </div>

      {activeTab === 'graph' && (
        <div className="space-y-3">
          <LineagePerspectives
            active={perspective}
            onChange={(p) => {
              const nextCase = p === 'column' && perspective !== 'column' ? 'all' : columnCase;
              setPerspective(p);
              if (nextCase !== columnCase) setColumnCase(nextCase);
              applyView(p, nextCase);
            }}
            counts={{
              end_to_end: graph?.nodes?.length,
              table: graph?.nodes?.filter((n) => n.type === 'table' || n.type === 'view').length,
              job: graph?.nodes?.filter((n) => n.type === 'etl' || n.type === 'schedule').length,
              schema: graph?.nodes?.length,
              column: ir?.column_lineage?.length || 0,
            }}
          />
          {(graph?.estate?.counts?.declared_unverified ?? 0) > 0 && perspective !== 'column' && (
            <div className="text-xs text-gray-500">
              Every drawn edge is evidenced by the captured artifacts. {graph.estate.counts.declared_unverified} Control-M declared references are hidden — the schedule names them but the job artifact never mentions them.
            </div>
          )}
          {perspective === 'column' && (
            <div className="flex items-center gap-1">
              <button onClick={() => { setColumnCase('all'); applyView('column', 'all'); }} className={`px-2 py-1 text-xs rounded ${columnCase === 'all' ? 'bg-indigo-600 text-white' : 'bg-white dark:bg-gray-800 border'}`}>All ({ir?.column_lineage?.length || 0})</button>
              <button onClick={() => { setColumnCase('complete'); applyView('column', 'complete'); }} className={`px-2 py-1 text-xs rounded ${columnCase === 'complete' ? 'bg-emerald-600 text-white' : 'bg-white dark:bg-gray-800 border'}`}>Complete</button>
              <button onClick={() => { setColumnCase('attention'); applyView('column', 'attention'); }} className={`px-2 py-1 text-xs rounded ${columnCase === 'attention' ? 'bg-red-600 text-white' : 'bg-red-50 dark:bg-red-900/20 border border-red-200 text-red-700'}`}>Attention Required ({(ir?.column_lineage || []).filter((c) => c.unresolved).length})</button>
              <button onClick={() => { setColumnCase('changed'); applyView('column', 'changed'); }} className={`px-2 py-1 text-xs rounded ${columnCase === 'changed' ? 'bg-amber-600 text-white' : 'bg-amber-50 dark:bg-amber-900/20 border border-amber-200 text-amber-700'}`}>Changed + audit</button>
              <span className="ml-2 text-xs text-gray-500">drawing {edges.length} of {(ir?.column_lineage || []).length} column rows — full list in Column Lineage tab</span>
            </div>
          )}
          <div className="flex gap-4">
            <div className="flex-1 min-w-0">
              <GraphPanel
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onNodeClick={onNodeClick}
                onNodeMouseEnter={onNodeMouseEnter}
                onNodeMouseLeave={onNodeMouseLeave}
                onPaneClick={() => { setSelected(null); setBlast(null); }}
                selected={selected}
                blast={blast}
                unresolvedCount={graph?.estate?.counts?.unresolved_edges ?? graph?.estate?.unresolved}
                hoverPreview={hoverPreview}
                highlightCol={highlightCol}
              />
              {hoverPreview && (
                <div className="mt-2 text-xs text-gray-500 bg-gray-50 dark:bg-gray-900 border rounded-lg px-3 py-2">
                  <span className="font-mono font-medium">{hoverPreview.fqn}</span> — {hoverPreview.count} columns, {hoverPreview.withTransform} with transforms. Click to see what changed.
                </div>
              )}
            </div>
          {drawerFqn && (
            <ColumnDrawer
              fqn={drawerFqn}
              kind={drawerKind}
              columns={(ir?.column_lineage || []).filter((c) => c.target_table === drawerFqn || c.source_table === drawerFqn)}
              estateId={estateId}
              onClose={() => { setDrawerFqn(null); setHighlightCol(null); }}
              onSelectColumn={(col) => setHighlightCol(col)}
            />
          )}
          </div>
        </div>
      )}
      {activeTab === 'knowledge' && <KnowledgeGraphPanel estateId={estateId} selectedFqn={drawerFqn} />}
      {activeTab === 'analytics' && <AnalyticsPanel data={analytics} recommendation={recommendation} />}
      {activeTab === 'unresolved' && <UnresolvedPanel estateId={estateId} />}
      {activeTab === 'columns' && <ColumnLineageTable columnLineage={ir?.column_lineage || []} />}
      {activeTab === 'chat' && <ChatPanel estateId={estateId} />}
      {activeTab === 'bridge' && <BridgePanel estateId={estateId} />}
      {activeTab === 'ledger' && <LedgerPanel ledger={ledger} estateId={estateId} />}
    </div>
  );
}
