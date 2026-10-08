import React from 'react';

export default function LedgerPanel({ ledger }) {
  return (
    <div className="bg-white dark:bg-gray-800 border rounded-lg p-4">
      <h4 className="font-medium text-sm mb-2">Evidence Ledger (hash-chained, append-only) - actor_id + canonical hash</h4>
      <p className="text-xs text-gray-500 mb-3">Each event is `sha256(prev_hash + canonical_payload_hash)`; `actor_id` is UUIDv5(email) for audit. Tamper breaks `GET /ledger/verify`.</p>
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
