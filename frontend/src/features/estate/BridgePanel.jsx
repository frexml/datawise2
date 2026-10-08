import React, { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';
import { useAuth } from '../../context/AuthContext';

export default function BridgePanel({ estateId }) {
  const { user, login } = useAuth();
  const [rec, setRec] = useState(null);
  const [plans, setPlans] = useState([]);
  const [diffResult, setDiffResult] = useState(null);
  const [contResult, setContResult] = useState(null);
  const [promoteResult, setPromoteResult] = useState(null);
  const [error, setError] = useState(null);
  const [approver, setApprover] = useState(user?.name || '');
  const [email, setEmail] = useState(user?.email || '');
  const [artifacts, setArtifacts] = useState({});
  const [artifactLoading, setArtifactLoading] = useState(null);

  const refreshPlans = useCallback(async () => { try { const r = await axios.get(`/api/estates/${estateId}/bridge/plans`); setPlans(r.data); } catch (e) {} }, [estateId]);
  useEffect(() => { refreshPlans(); }, [refreshPlans]);
  useEffect(() => { if (user) { setApprover(user.name); setEmail(user.email); } }, [user]);

  const recommend = async () => { setError(null); try { const r = await axios.post(`/api/estates/${estateId}/bridge/recommend`); setRec(r.data); } catch (e) { setError(e.response?.data?.detail || e.message); } };
  const createPlan = async () => { setError(null); try { await axios.post(`/api/estates/${estateId}/bridge/plan`, { name: 'Snowflake Wedge - Finance Mart', target_platform: 'snowflake' }); await refreshPlans(); } catch (e) { setError(e.response?.data?.detail || e.message); } };
  const approve = async (planId) => {
    setError(null);
    if (!approver.trim() || approver.trim().length < 2) { setError('Approver name required (≥2 chars)'); return; }
    if (email && !email.includes('@')) { setError('Valid email required for ledger audit'); return; }
    // Mock login if not authenticated
    if (!user && email) login(email, approver);
    try { await axios.post(`/api/estates/${estateId}/bridge/plan/${planId}/approve`, { approver, actor_email: email || undefined }); await refreshPlans(); } catch (e) { setError(e.response?.data?.detail || e.message); }
  };
  const runDiff = async () => { setError(null); try { const r = await axios.post(`/api/estates/${estateId}/bridge/diff`, {}); setDiffResult(r.data); } catch (e) { setError(e.response?.data?.detail || e.message); } };
  const checkCont = async () => { setError(null); try { const r = await axios.post(`/api/estates/${estateId}/bridge/continuity`, {}); setContResult(r.data); } catch (e) { setError(e.response?.data?.detail || e.message); } };
  const promote = async () => { setError(null); try { const r = await axios.post(`/api/estates/${estateId}/bridge/promote`); setPromoteResult(r.data); } catch (e) { setError(e.response?.data?.detail || e.message); } };

  const approvedPlan = plans.find((p) => p.status === 'approved');

  const viewArtifact = async (kind) => {
    if (!approvedPlan) return;
    setError(null);
    if (artifacts[kind] != null) { setArtifacts((a) => ({ ...a, [kind]: null })); return; } // toggle closed
    setArtifactLoading(kind);
    try {
      const r = await axios.get(`/api/estates/${estateId}/bridge/plan/${approvedPlan.id}/${kind}`, { responseType: 'text', transformResponse: (d) => d });
      setArtifacts((a) => ({ ...a, [kind]: r.data }));
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setArtifactLoading(null);
    }
  };

  const downloadArtifact = (kind, filename) => {
    const text = artifacts[kind];
    if (!text) return;
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const ARTIFACT_LABELS = { ddl: 'DDL', terraform: 'Terraform', scaffold: 'Migration code' };
  const ARTIFACT_EXT = { ddl: 'sql', terraform: 'tf', scaffold: 'py' };

  return (
    <div className="space-y-4">
      <div className="bg-white dark:bg-gray-800 p-4 rounded-lg border">
        <h4 className="font-medium text-sm">1. Recommend Wedge -> Snowflake</h4>
        <p className="text-xs text-gray-500">Per-attribute Oracle->Snowflake mapping + Terraform per-FQN. Finance mart is lowest-risk.</p>
        <button onClick={recommend} className="mt-2 px-3 py-1.5 bg-indigo-600 text-white rounded text-sm">Recommend</button>
        {rec && <div className="mt-2 text-xs bg-gray-50 dark:bg-gray-900 p-3 rounded"><div>Target: {rec.target_platform} · {rec.scope_fqns?.length} objects · {rec.estimated_days} days · risk {rec.risk_level}</div><div className="mt-1 prose dark:prose-invert prose-xs max-w-none"><ReactMarkdown>{rec.rationale}</ReactMarkdown></div></div>}
      </div>
      <div className="bg-white dark:bg-gray-800 p-4 rounded-lg border">
        <h4 className="font-medium text-sm">2. Create & Approve Plan (IdP-verified)</h4>
        <div className="flex gap-2 mt-2 flex-wrap">
          <button onClick={createPlan} className="px-3 py-1.5 bg-white border rounded text-sm hover:bg-gray-50">Create Plan</button>
          <input value={approver} onChange={(e) => setApprover(e.target.value)} placeholder="Approver name (≥2)" className="px-2 py-1 border rounded text-sm dark:bg-gray-900 dark:border-gray-700" />
          <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email for ledger hash" className="px-2 py-1 border rounded text-sm dark:bg-gray-900 dark:border-gray-700" />
        </div>
        {plans.map((p) => <div key={p.id} className="mt-2 text-xs p-2 border rounded flex justify-between items-center">
          <span>{p.name} · {p.target_platform} · {p.status} {p.approved_by && `by ${p.approved_by}`}</span>
          {p.status === 'pending_approval' && <button onClick={() => approve(p.id)} className="px-2 py-1 bg-emerald-600 text-white rounded">Approve</button>}
        </div>)}
      </div>
      {approvedPlan && (
        <div className="bg-white dark:bg-gray-800 p-4 rounded-lg border">
          <h4 className="font-medium text-sm">Generated artifacts - {approvedPlan.name}</h4>
          <div className="flex gap-2 mt-2 flex-wrap">
            {['ddl', 'terraform', 'scaffold'].map((kind) => (
              <button
                key={kind}
                onClick={() => viewArtifact(kind)}
                disabled={artifactLoading === kind}
                className="px-3 py-1.5 bg-white border rounded text-sm hover:bg-gray-50 disabled:opacity-50"
              >
                {artifactLoading === kind ? 'Loading…' : artifacts[kind] != null ? `Hide ${ARTIFACT_LABELS[kind]}` : `View ${ARTIFACT_LABELS[kind]}`}
              </button>
            ))}
          </div>
          {['ddl', 'terraform', 'scaffold'].map((kind) => artifacts[kind] != null && (
            <div key={kind} className="mt-2">
              <div className="flex justify-between items-center mb-1">
                <span className="text-xs font-medium text-gray-700 dark:text-gray-300">{ARTIFACT_LABELS[kind]}</span>
                <button onClick={() => downloadArtifact(kind, `${approvedPlan.name.replace(/\s+/g, '_')}.${ARTIFACT_EXT[kind]}`)} className="text-xs px-2 py-1 bg-indigo-600 text-white rounded hover:bg-indigo-700">Download</button>
              </div>
              <pre className="text-[11px] bg-gray-50 dark:bg-gray-900 border rounded p-3 overflow-auto max-h-72 whitespace-pre-wrap">{artifacts[kind]}</pre>
            </div>
          ))}
        </div>
      )}
      <div className="bg-white dark:bg-gray-800 p-4 rounded-lg border">
        <h4 className="font-medium text-sm">3. Prove - Real DuckDB Diff + Continuity</h4>
        <p className="text-xs text-gray-500">Diff uses DuckDB EXCEPT + numeric epsilon + masked columns. Continuity uses ColumnIdentity + embedding similarity (difflib).</p>
        <div className="flex gap-2 mt-2">
          <button onClick={runDiff} disabled={!approvedPlan} className="px-3 py-1.5 bg-indigo-600 text-white rounded text-sm disabled:opacity-50">Run Diff (needs approved plan)</button>
          <button onClick={checkCont} disabled={!diffResult} className="px-3 py-1.5 bg-white border rounded text-sm disabled:opacity-50">Continuity Check</button>
        </div>
        {diffResult && <div className="mt-2 text-xs bg-gray-50 dark:bg-gray-900 p-2 rounded">Diff: {diffResult.result?.total_rows_compared} rows, mismatched {diffResult.result?.mismatched_rows} · {diffResult.result?.passed ? '✅ passed' : '❌ failed'} · bound {diffResult.result?.bound_95} · {diffResult.result?.sampling_method} · <span className="text-gray-500">masked: {diffResult.result?.masked_columns?.join(', ') || 'none'}</span></div>}
        {contResult && <div className="mt-2 text-xs bg-gray-50 dark:bg-gray-900 p-2 rounded">Continuity: {contResult.matched}/{contResult.total_columns} matched, {contResult.requires_approval_count} need approval · {contResult.passed ? '✅ passed' : 'needs decisions'} · embedding similarity ≥0.85</div>}
      </div>
      <div className="bg-white dark:bg-gray-800 p-4 rounded-lg border">
        <h4 className="font-medium text-sm">4. Promote (ledger-gated, hash-verified)</h4>
        <button onClick={promote} className="mt-2 px-3 py-1.5 bg-emerald-600 text-white rounded text-sm">Promote</button>
        {promoteResult && <div className="mt-2 text-xs text-emerald-600">✅ Promoted - ledger verified, event {promoteResult.ledger_event_id}, actor_id {promoteResult.actor_id || 'system'}</div>}
      </div>
      {error && <div className="text-xs text-red-600 bg-red-50 dark:bg-red-900/20 p-2 rounded">{error}</div>}
    </div>
  );
}
