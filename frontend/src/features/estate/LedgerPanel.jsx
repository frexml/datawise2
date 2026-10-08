import React, { useState } from 'react';
import axios from 'axios';

export default function LedgerPanel({ ledger, estateId }) {
  const [verifyResult, setVerifyResult] = useState(null);
  const [verifying, setVerifying] = useState(false);

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

  return (
    <div className="bg-white dark:bg-gray-800 border rounded-lg p-4">
      <div className="flex justify-between items-start gap-3 mb-2">
        <div>
          <h4 className="font-medium text-sm">Evidence Ledger (hash-chained, append-only) - actor_id + canonical hash</h4>
          <p className="text-xs text-gray-500 mt-1">Each event is `sha256(prev_hash + canonical_payload_hash)`; `actor_id` is UUIDv5(email) for audit. Tamper breaks the chain below.</p>
        </div>
        <button onClick={verifyChain} disabled={verifying} className="shrink-0 px-3 py-1.5 bg-indigo-600 text-white rounded text-xs hover:bg-indigo-700 disabled:opacity-50">
          {verifying ? 'Verifying…' : 'Verify Chain'}
        </button>
      </div>
      {verifyResult && (
        <div className={`mb-3 p-2 rounded text-xs ${verifyResult.verified ? 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800' : 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 border border-red-200 dark:border-red-800'}`}>
          {verifyResult.verified ? `✅ Hash chain verified - ${ledger.length} events, unbroken.` : `❌ Verification failed - ${verifyResult.reason}`}
        </div>
      )}
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
    </div>
  );
}
