import React, { useState } from 'react';
import axios from 'axios';

export default function LedgerPanel({ ledger, estateId }) {
  const [verifyResult, setVerifyResult] = useState(null);
  const [verifying, setVerifying] = useState(false);
  const [humanView, setHumanView] = useState(true);
  const [audit, setAudit] = useState([]);

  const verifyChain = async () => {
    setVerifying(true);
    setVerifyResult(null);
    try {
      const r = await axios.get(`/api/estates/${estateId}/ledger/verify`);
      setVerifyResult(r.data);
    } catch (e) {
      setVerifyResult({ verified: false, reason: e.response?.data?.detail || e.message });
    } finally {
      setVerifying(false);
    }
  };

  React.useEffect(() => {
    if (humanView && estateId) {
      axios.get(`/api/estates/${estateId}/audit`).then((r) => setAudit(r.data)).catch(() => {});
    }
  }, [humanView, estateId, ledger]);

  return (
    <div className="bg-white dark:bg-gray-800 border rounded-lg p-4">
      <div className="flex justify-between items-start gap-3 mb-2">
        <div>
          <h4 className="font-medium text-sm">Evidence Ledger {humanView ? '— Human Audit' : '— Raw (hash-chained, append-only)'}</h4>
          <p className="text-xs text-gray-500 mt-1">
            {humanView ? 'What changed · Who · When · Relationship to lineage' : 'Each event is `sha256(prev_hash + canonical_payload_hash)`; `actor_id` is UUIDv5(email).'}
          </p>
        </div>
        <div className="flex gap-2">
          <div className="flex rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
            <button onClick={() => setHumanView(true)} className={`px-3 py-1.5 text-xs ${humanView ? 'bg-indigo-600 text-white' : 'bg-white dark:bg-gray-800 text-gray-600'}`}>Human audit</button>
            <button onClick={() => setHumanView(false)} className={`px-3 py-1.5 text-xs ${!humanView ? 'bg-indigo-600 text-white' : 'bg-white dark:bg-gray-800 text-gray-600'}`}>Raw ledger</button>
          </div>
          <button onClick={verifyChain} disabled={verifying} className="shrink-0 px-3 py-1.5 bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded text-xs hover:bg-gray-50 disabled:opacity-50">
            {verifying ? 'Verifying…' : 'Verify Chain'}
          </button>
        </div>
      </div>
      {verifyResult && (
        <div className={`mb-3 p-2 rounded text-xs ${verifyResult.verified ? 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800' : 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 border border-red-200 dark:border-red-800'}`}>
          {verifyResult.verified ? `✅ Hash chain verified - ${ledger.length} events, unbroken.` : `❌ Verification failed - ${verifyResult.reason}`}
        </div>
      )}
      {humanView ? (
        <div className="space-y-1 max-h-[600px] overflow-y-auto">
          {audit.map((a) => (
            <div key={a.id} className="text-xs p-3 border rounded bg-gray-50 dark:bg-gray-900/50">
              <div className="flex justify-between items-start gap-2">
                <span className="font-medium text-gray-900 dark:text-white">{a.what}</span>
                <span className="text-[11px] text-gray-500">{a.when_relative}</span>
              </div>
              <div className="mt-1 flex flex-wrap gap-2 text-[11px]">
                <span className="px-1.5 py-0.5 rounded bg-indigo-100 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300">Who: {a.who}</span>
                <span className="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">When: {a.when ? new Date(a.when).toLocaleString() : a.when_relative}</span>
                <span className="px-1.5 py-0.5 rounded bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-300">Relationship: {a.relationship}</span>
                {a.hops && <span className="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700">Hops: {a.hops}</span>}
              </div>
              <div className="mt-1 text-[11px] font-mono text-gray-500 truncate" title={a.full_path}>{a.full_path || ''}</div>
            </div>
          ))}
          {audit.length === 0 && <div className="text-xs text-gray-400">No audit entries yet — survey an estate to generate lineage.</div>}
        </div>
      ) : (
        <div className="space-y-1 max-h-[600px] overflow-y-auto">
          {ledger.map((ev) => (
            <div key={ev.id} className="text-xs p-2 border rounded flex gap-3 items-center">
              <span className="text-gray-400">#{ev.id}</span>
              <span className={`px-1.5 py-0.5 rounded text-white ${ev.event_type === 'promoted' ? 'bg-emerald-600' : ev.event_type.includes('approved') ? 'bg-indigo-600' : 'bg-gray-500'}`}>{ev.event_type}</span>
              <span className="truncate flex-1">{JSON.stringify(ev.payload).slice(0, 120)}</span>
              <span className="text-gray-400 truncate" title={ev.event_hash}>{ev.event_hash?.slice(0, 8)}…</span>
              <span className="text-gray-500">{ev.actor}{ev.actor_id ? ` (${ev.actor_id.slice(0, 8)})` : ''}</span>
              {ev.idp_verified && <span className="text-emerald-600" title="IdP verified">✓</span>}
            </div>
          ))}
          {ledger.length === 0 && <div className="text-xs text-gray-400">No ledger events yet.</div>}
        </div>
      )}
    </div>
  );
}
