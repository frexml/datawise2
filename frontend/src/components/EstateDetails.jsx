import React, { useEffect, useState, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import axios from 'axios';
import { useNodesState, useEdgesState, MarkerType } from 'reactflow';
import GraphPanel from '../features/estate/GraphPanel';
import AnalyticsPanel from '../features/estate/AnalyticsPanel';
import ChatPanel from '../features/estate/ChatPanel';
import BridgePanel from '../features/estate/BridgePanel';
import LedgerPanel from '../features/estate/LedgerPanel';
import { useAuth } from '../context/AuthContext';

const TAB_LABELS = [
  { key: 'graph', label: 'Lineage Graph' },
  { key: 'analytics', label: 'Analytics' },
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
  const [selected, setSelected] = useState(null);
  const [blast, setBlast] = useState(null);
  const [surveyTodo, setSurveyTodo] = useState([]);
  const [surveyStage, setSurveyStage] = useState('');
  const [displayTodo, setDisplayTodo] = useState([]);
  const [approver, setApprover] = useState(user?.name || '');
  const [approveError, setApproveError] = useState(null);

  useEffect(() => { if (user?.name) setApprover(user.name); }, [user]);

  const loadAll = async (showGraph = true) => {
    const r = await axios.get(`/api/estates/${estateId}`);
    setEstate(r.data);
    setSurveyTodo(r.data.survey_todo || []);
    setDisplayTodo(r.data.survey_todo || []);
    setSurveyStage(r.data.current_stage || '');
    if (showGraph && r.data.status === 'ACTIVE') {
      const g = await axios.get(`/api/estates/${estateId}/graph`);
      setGraph(g.data);
      const rfNodes = g.data.nodes.map((n, i) => ({
        id: n.id, position: n.position || { x: (i % 6) * 220, y: Math.floor(i / 6) * 80 },
        data: { label: n.label }, type: 'default',
        style: { background: n.type === 'table' ? '#EEF2FF' : n.type === 'view' ? (n.unresolved ? '#FEF3C7' : '#ECFDF5') : n.type === 'procedure' ? (n.has_dynamic ? '#FEE2E2' : '#F3E8FF') : n.type === 'dashboard' ? '#FFF7ED' : '#F1F5F9', border: '1px solid #CBD5E1', fontSize: '10px', padding: '6px', borderRadius: '6px', width: 170 }
      }));
      const rfEdges = g.data.edges.map((e) => ({ id: e.id, source: e.source, target: e.target, animated: true, style: { stroke: e.unresolved ? '#F59E0B' : '#6366F1' }, markerEnd: { type: MarkerType.ArrowClosed, color: e.unresolved ? '#F59E0B' : '#6366F1' }, label: e.type }));
      setNodes(rfNodes); setEdges(rfEdges);
    }
    if (r.data.status === 'ACTIVE') {
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
    try { const r = await axios.get(`/api/estates/${estateId}/blast-radius`, { params: { fqn: node.id } }); setBlast(r.data); } catch (e) { setBlast({ count: 0, blast_radius: [] }); }
  };

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

      {activeTab === 'graph' && <GraphPanel nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onNodeClick={onNodeClick} selected={selected} blast={blast} unresolvedCount={graph?.estate?.unresolved} />}
      {activeTab === 'analytics' && <AnalyticsPanel data={analytics} recommendation={recommendation} />}
      {activeTab === 'chat' && <ChatPanel estateId={estateId} />}
      {activeTab === 'bridge' && <BridgePanel estateId={estateId} />}
      {activeTab === 'ledger' && <LedgerPanel ledger={ledger} estateId={estateId} />}
    </div>
  );
}
