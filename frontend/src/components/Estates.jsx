import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';

const TEMPLATES = {
  banking: {
    name: 'NORTHSTAR',
    display_name: 'NorthStar Banking Estate',
    estate_type: 'banking',
    description: 'SAP ECC + Salesforce + Flat Files -> Oracle DW (126 nodes)',
    host: 'oracle-prod.bank.local',
    port: 1521,
    user: 'ESTATE_RO',
    db_type: 'oracle',
    icon: '🏦',
  },
  telecom: {
    name: 'TELCO_CORE',
    display_name: 'TelcoCore Telecom Estate',
    estate_type: 'telecom',
    description: 'BSS/OSS/CRM + Network CDR -> DW_TELCO (62 tables, PowerBI)',
    host: 'oracle-prod.telco.local',
    port: 1521,
    user: 'ESTATE_RO',
    db_type: 'oracle',
    icon: '📡',
  },
};

function TodoList({ todo, currentStage, onApprove, isPendingApproval }) {
  if (!todo || todo.length === 0) return null;
  const done = todo.filter((t) => t.status === 'done').length;
  const isPending = isPendingApproval || currentStage === 'pending_approval';
  return (
    <div className="mt-4 border rounded-xl overflow-hidden">
      <div className="px-4 py-2 bg-gray-50 dark:bg-gray-800 flex justify-between items-center">
        <span className="text-xs font-medium text-gray-700 dark:text-gray-300">{isPending ? 'Ready for approval' : `${done}/${todo.length} steps done`}</span>
        <span className="text-xs text-gray-500">{currentStage}</span>
      </div>
      {isPending && (
        <div className="px-4 py-2 bg-amber-50 dark:bg-amber-900/20 border-b border-amber-200 dark:border-amber-800 flex justify-between items-center">
          <span className="text-xs text-amber-800 dark:text-amber-200">Review the 8 steps below, then approve to start discovery.</span>
          <button onClick={onApprove} className="px-3 py-1 bg-indigo-600 text-white rounded text-xs hover:bg-indigo-700">Approve & Run →</button>
        </div>
      )}
      <div className="divide-y divide-gray-100 dark:divide-gray-700">
        {todo.map((item) => (
          <div key={item.key} className="px-4 py-2.5 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className={`h-5 w-5 rounded-full flex items-center justify-center text-xs ${item.status === 'done' ? 'bg-emerald-500 text-white' : item.status === 'running' ? 'bg-indigo-500 text-white animate-pulse' : 'bg-gray-200 dark:bg-gray-700 text-gray-500'}`}>
                {item.status === 'done' ? '✓' : item.status === 'running' ? '◐' : '○'}
              </span>
              <span className="text-sm text-gray-900 dark:text-white">{item.label}</span>
            </div>
            <span className="text-xs text-gray-500 dark:text-gray-400">{item.count != null ? `${item.count}` : ''} {item.detail && `· ${item.detail}`}</span>
          </div>
        ))}
      </div>
      <div className="h-1 bg-gray-100 dark:bg-gray-700">
        <div className="h-full bg-indigo-500 transition-all" style={{ width: `${(done / todo.length) * 100}%` }} />
      </div>
    </div>
  );
}

export default function Estates() {
  const [estates, setEstates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showModal, setShowModal] = useState(false);
  const [template, setTemplate] = useState('banking');
  const [conn, setConn] = useState({ host: TEMPLATES.banking.host, port: TEMPLATES.banking.port, user: TEMPLATES.banking.user, db_type: TEMPLATES.banking.db_type });
  const [creating, setCreating] = useState(false);
  const [surveying, setSurveying] = useState(null); // estate id being surveyed
  const [todo, setTodo] = useState([]);
  const [currentStage, setCurrentStage] = useState('');

  const fetch = async () => {
    try {
      const res = await axios.get('/api/estates');
      setEstates(res.data);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, []);

  useEffect(() => {
    if (template) {
      const t = TEMPLATES[template];
      setConn({ host: t.host, port: t.port, user: t.user, db_type: t.db_type });
    }
  }, [template]);

  const handleCreateAndSurvey = async () => {
    setCreating(true);
    setError(null);
    try {
      const tpl = TEMPLATES[template];
      // 1) Create estate PENDING
      const createRes = await axios.post('/api/estates', {
        name: tpl.name,
        display_name: tpl.display_name,
        source_type: 'synthetic',
        estate_type: tpl.estate_type,
        connection: { host: conn.host, port: conn.port, user: conn.user, db_type: conn.db_type, status: 'pending' },
      });
      const estateId = createRes.data.id;
      // 2) Test connection (mock)
      try {
        await axios.post(`/api/estates/${estateId}/test-connection`, conn);
      } catch (e) {
        setError(`Connection failed: ${e.response?.data?.detail || e.message}`);
        setCreating(false);
        return;
      }
      // 3) Start survey - creates Todo as pending_approval; user must approve before execution
      const surveyRes = await axios.post(`/api/estates/${estateId}/survey`, {});
      setTodo(surveyRes.data.todo || []);
      setCurrentStage('pending_approval');
      setSurveying(estateId);
      setShowModal(false);
      // Show Todo for 5s as requested, then enable Approve button (user must click)
      // No auto-approve - user approval is required before execution
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setCreating(false);
    }
  };

  // Poll surveying estate if any
  useEffect(() => {
    if (!surveying) return;
    const poll = async () => {
      try {
        const r = await axios.get(`/api/estates/${surveying}/survey/todo`);
        setTodo(r.data.todo || []);
        setCurrentStage(r.data.current_stage || '');
        if (r.data.status === 'ACTIVE') {
          setSurveying(null);
          fetch();
        }
      } catch {}
    };
    const id = setInterval(poll, 800);
    return () => clearInterval(id);
  }, [surveying]);

  if (loading) return <div className="p-8 text-center text-gray-500">Loading estates…</div>;

  return (
    <div className="space-y-6">
      <div className="bg-white dark:bg-gray-800 shadow sm:rounded-lg p-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold text-gray-900 dark:text-white">Estates</h2>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">On-prem data estates - knowledge graph + evidence ledger. Connect to any on-prem estate and see it mapped in real time.</p>
          </div>
          <button onClick={() => setShowModal(true)} className="px-4 py-2 bg-indigo-600 text-white rounded-md hover:bg-indigo-700 text-sm">+ Add Estate</button>
        </div>
        {error && <div className="mt-4 p-3 bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300 rounded text-sm">{error}</div>}
        {surveying && todo.length > 0 && (
          <div className="mt-4">
            <div className="text-sm font-medium text-indigo-600 dark:text-indigo-400">
              {currentStage === 'pending_approval' ? 'Todo ready for approval' : `Discovering estate ${surveying}… live progress`}
            </div>
            <TodoList
              todo={todo}
              currentStage={currentStage}
              isPendingApproval={currentStage === 'pending_approval'}
              onApprove={async () => {
                try {
                  await axios.post(`/api/estates/${surveying}/survey/approve`, {});
                  setCurrentStage('surveying');
                  const poll = async () => {
                    try {
                      const r = await axios.get(`/api/estates/${surveying}/survey/todo`);
                      setTodo(r.data.todo || []);
                      setCurrentStage(r.data.current_stage || '');
                      if (r.data.status === 'ACTIVE' && r.data.current_stage === 'done') {
                        setSurveying(null);
                        await fetch();
                        return;
                      }
                      setTimeout(poll, 500);
                    } catch {
                      setSurveying(null);
                    }
                  };
                  poll();
                } catch (e) {
                  setError(e.response?.data?.detail || e.message);
                }
              }}
            />
          </div>
        )}
      </div>

      {showModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setShowModal(false)}>
          <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-xl w-full max-w-lg p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-semibold text-gray-900 dark:text-white">Add Estate</h3>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">Choose a template and test your connection before surveying the estate.</p>
            <div className="mt-4 grid grid-cols-2 gap-3">
              {Object.entries(TEMPLATES).map(([key, t]) => (
                <button
                  key={key}
                  onClick={() => setTemplate(key)}
                  className={`p-4 rounded-xl border-2 text-left ${template === key ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-900/20' : 'border-gray-200 dark:border-gray-700 hover:border-gray-300'}`}
                >
                  <div className="text-2xl">{t.icon}</div>
                  <div className="text-sm font-medium text-gray-900 dark:text-white mt-1">{t.display_name}</div>
                  <div className="text-xs text-gray-500 dark:text-gray-400">{t.description}</div>
                  <div className="text-xs text-gray-400 mt-1 capitalize">{t.estate_type}</div>
                </button>
              ))}
            </div>
            <div className="mt-4 grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-gray-500">Host</label>
                <input value={conn.host} onChange={(e) => setConn({ ...conn, host: e.target.value })} className="mt-1 w-full px-3 py-2 border rounded-md text-sm dark:bg-gray-900 dark:border-gray-700" />
              </div>
              <div>
                <label className="text-xs text-gray-500">Port</label>
                <input type="number" value={conn.port} onChange={(e) => setConn({ ...conn, port: parseInt(e.target.value) || 1521 })} className="mt-1 w-full px-3 py-2 border rounded-md text-sm dark:bg-gray-900 dark:border-gray-700" />
              </div>
              <div>
                <label className="text-xs text-gray-500">User</label>
                <input value={conn.user} onChange={(e) => setConn({ ...conn, user: e.target.value })} className="mt-1 w-full px-3 py-2 border rounded-md text-sm dark:bg-gray-900 dark:border-gray-700" />
              </div>
              <div>
                <label className="text-xs text-gray-500">DB Type</label>
                <select value={conn.db_type} onChange={(e) => setConn({ ...conn, db_type: e.target.value })} className="mt-1 w-full px-3 py-2 border rounded-md text-sm dark:bg-gray-900 dark:border-gray-700">
                  <option value="oracle">Oracle</option>
                  <option value="teradata">Teradata</option>
                  <option value="postgres">Postgres</option>
                </select>
              </div>
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <button onClick={() => setShowModal(false)} className="px-4 py-2 text-sm border rounded-md dark:border-gray-700">Cancel</button>
              <button onClick={handleCreateAndSurvey} disabled={creating} className="px-4 py-2 bg-indigo-600 text-white rounded-md text-sm disabled:opacity-50">
                {creating ? 'Connecting…' : 'Test Connection & Discover ->'}
              </button>
            </div>
            <div className="mt-2 text-[11px] text-gray-400">Connection test validates reachability before surveying.</div>
          </div>
        </div>
      )}

      {estates.length === 0 ? (
        <div className="bg-white dark:bg-gray-800 shadow sm:rounded-lg p-12 text-center">
          <div className="text-5xl mb-4">🗺️</div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white">No estates yet</h3>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-2">Connect a data estate to get started.</p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {estates.map((e) => (
            <Link key={e.id} to={`/estates/${e.id}`} className="bg-white dark:bg-gray-800 shadow sm:rounded-lg p-6 hover:shadow-md transition-shadow border-l-4 border-indigo-500">
              <div className="flex items-center gap-2">
                <span className="text-lg">{e.estate_type === 'telecom' ? '📡' : '🏦'}</span>
                <h3 className="font-semibold text-gray-900 dark:text-white">{e.display_name || e.name}</h3>
                <span className={`ml-auto text-xs px-2 py-0.5 rounded-full ${e.estate_type === 'telecom' ? 'bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300' : 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'}`}>{e.estate_type || 'banking'}</span>
              </div>
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">{e.name} · {e.source_type} · IR {e.ir_version} · {e.status}</p>
              {e.connection?.host && <p className="text-xs text-gray-400 mt-1">{e.connection.host}:{e.connection.port} · {e.connection.user} {e.connection.status === 'connected' ? '● Connected' : ''}</p>}
              <div className="flex gap-4 mt-3 text-xs text-gray-600 dark:text-gray-300">
                <span>📦 {e.node_count || 0} nodes</span>
                <span>🔗 {e.edge_count || 0} edges</span>
                {e.unresolved_count > 0 && <span className="text-amber-600">⚠️ {e.unresolved_count} unresolved</span>}
                {e.current_stage && <span className="text-indigo-600">· {e.current_stage}</span>}
              </div>
              {e.survey_todo && e.survey_todo.length > 0 && (
                <div className="mt-3">
                  <div className="h-1.5 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden">
                    <div className="h-full bg-indigo-500" style={{ width: `${(e.survey_todo.filter((t) => t.status === 'done').length / e.survey_todo.length) * 100}%` }} />
                  </div>
                  <div className="text-[11px] text-gray-500 mt-1">{e.survey_todo.filter((t) => t.status === 'done').length}/{e.survey_todo.length} survey done</div>
                </div>
              )}
              <div className="mt-3 text-xs text-indigo-600 dark:text-indigo-400">View estate -></div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
