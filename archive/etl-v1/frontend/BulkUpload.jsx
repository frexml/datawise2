import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { Link } from 'react-router-dom';

const STATUS_STYLES = {
    PENDING: 'bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-300',
    PROCESSING: 'bg-yellow-100 dark:bg-yellow-900/40 text-yellow-800 dark:text-yellow-300',
    COMPLETED: 'bg-green-100 dark:bg-green-900/40 text-green-800 dark:text-green-300',
    FAILED: 'bg-red-100 dark:bg-red-900/40 text-red-800 dark:text-red-300',
    ERROR: 'bg-red-100 dark:bg-red-900/40 text-red-800 dark:text-red-300',
};

const BulkUpload = ({ onCancel }) => {
    const [files, setFiles] = useState([]);
    const [domain, setDomain] = useState('');
    const [wave, setWave] = useState('');
    const [priority, setPriority] = useState(false);
    const [knownDomains, setKnownDomains] = useState([]);
    const [knownWaves, setKnownWaves] = useState([]);
    const [batch, setBatch] = useState(null); // [{filename, job_id, status, error}]
    const [submitting, setSubmitting] = useState(false);
    const pollRef = useRef(null);

    useEffect(() => {
        axios.get('/api/portfolio')
            .then((res) => {
                setKnownDomains(res.data.domains || []);
                setKnownWaves(res.data.waves || []);
            })
            .catch((err) => console.error('Failed to load domain/wave suggestions:', err));
        return () => {
            if (pollRef.current) clearTimeout(pollRef.current);
        };
    }, []);

    const handleFilesChange = (e) => {
        setFiles(Array.from(e.target.files));
    };

    const startPolling = (jobIds) => {
        const pending = new Set(jobIds);
        const poll = async () => {
            try {
                const res = await axios.get('/api/jobs', { params: { limit: 1000 } });
                const byId = new Map(res.data.map((j) => [j.id, j]));
                setBatch((prev) => prev.map((row) => {
                    if (!row.job_id) return row;
                    const job = byId.get(row.job_id);
                    if (!job) return row;
                    if (job.status === 'COMPLETED' || job.status === 'FAILED') pending.delete(row.job_id);
                    return { ...row, status: job.status };
                }));
            } catch (err) {
                console.error('Failed to poll batch status:', err);
            }
            if (pending.size > 0) {
                pollRef.current = setTimeout(poll, 4000);
            }
        };
        poll();
    };

    const handleSubmit = async () => {
        if (files.length === 0) return;
        setSubmitting(true);
        const rows = files.map((f) => ({ filename: f.name, job_id: null, status: 'PENDING', error: null }));
        setBatch(rows);

        const jobIds = [];
        for (let i = 0; i < files.length; i++) {
            const formData = new FormData();
            formData.append('file', files[i]);
            if (domain.trim()) formData.append('domain', domain.trim());
            if (wave.trim()) formData.append('wave', wave.trim());
            formData.append('priority', priority);

            try {
                const res = await axios.post('/api/upload', formData, {
                    headers: { 'Content-Type': 'multipart/form-data' },
                });
                jobIds.push(res.data.job_id);
                setBatch((prev) => prev.map((row, idx) => idx === i ? { ...row, job_id: res.data.job_id, status: res.data.status } : row));
            } catch (err) {
                console.error(`Failed to upload ${files[i].name}:`, err);
                setBatch((prev) => prev.map((row, idx) => idx === i ? { ...row, status: 'ERROR', error: 'Upload failed' } : row));
            }
        }

        setSubmitting(false);
        if (jobIds.length > 0) startPolling(jobIds);
    };

    const counts = batch ? {
        total: batch.length,
        completed: batch.filter((r) => r.status === 'COMPLETED').length,
        failed: batch.filter((r) => r.status === 'FAILED' || r.status === 'ERROR').length,
        inFlight: batch.filter((r) => r.status === 'PENDING' || r.status === 'PROCESSING').length,
    } : null;

    const historyLink = domain.trim() || wave.trim()
        ? `/history?${domain.trim() ? `domain=${encodeURIComponent(domain.trim())}` : ''}${wave.trim() ? `${domain.trim() ? '&' : ''}wave=${encodeURIComponent(wave.trim())}` : ''}`
        : '/history';

    return (
        <div className="bg-white dark:bg-gray-800 shadow sm:rounded-lg p-6 text-left">
            <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-medium text-gray-900 dark:text-gray-100">Bulk Upload</h3>
                {!batch && (
                    <button onClick={onCancel} className="text-sm text-gray-500 dark:text-gray-400 hover:underline">✕ Cancel</button>
                )}
            </div>

            {!batch ? (
                <>
                    <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
                        Select multiple DataStage/SSIS/Informatica exports and tag the whole batch with a domain and/or wave at once.
                    </p>
                    <input
                        type="file"
                        multiple
                        accept=".dsx,.dtsx,.xml"
                        onChange={handleFilesChange}
                        className="block w-full text-sm text-gray-500 dark:text-gray-400
                            file:mr-4 file:py-2 file:px-4
                            file:rounded-full file:border-0
                            file:text-sm file:font-semibold
                            file:bg-indigo-50 file:text-indigo-700
                            hover:file:bg-indigo-100"
                    />
                    {files.length > 0 && (
                        <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">{files.length} file{files.length === 1 ? '' : 's'} selected</p>
                    )}

                    <div className="mt-3 flex items-center gap-3">
                        <input
                            type="text"
                            list="bulk-domain-suggestions"
                            placeholder="Domain for this batch (optional)"
                            value={domain}
                            onChange={(e) => setDomain(e.target.value)}
                            className="flex-1 text-sm border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-3 py-1.5"
                        />
                        <datalist id="bulk-domain-suggestions">
                            {knownDomains.map((d) => <option key={d} value={d} />)}
                        </datalist>
                        <input
                            type="text"
                            list="bulk-wave-suggestions"
                            placeholder="Wave for this batch (optional)"
                            value={wave}
                            onChange={(e) => setWave(e.target.value)}
                            className="flex-1 text-sm border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-3 py-1.5"
                        />
                        <datalist id="bulk-wave-suggestions">
                            {knownWaves.map((w) => <option key={w} value={w} />)}
                        </datalist>
                    </div>

                    <label className="mt-3 flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
                        <input
                            type="checkbox"
                            checked={priority}
                            onChange={(e) => setPriority(e.target.checked)}
                            className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500"
                        />
                        ⭐ Mark this whole batch as priority
                    </label>

                    <button
                        onClick={handleSubmit}
                        disabled={files.length === 0 || submitting}
                        className={`mt-4 inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-indigo-600 hover:bg-indigo-700 ${(files.length === 0 || submitting) ? 'opacity-50 cursor-not-allowed' : ''}`}
                    >
                        {submitting ? 'Uploading...' : `Upload ${files.length || ''} File${files.length === 1 ? '' : 's'}`}
                    </button>
                </>
            ) : (
                <>
                    <div className="flex items-center gap-4 mb-4 text-sm">
                        <span className="text-gray-700 dark:text-gray-300 font-medium">
                            {counts.completed}/{counts.total} completed
                        </span>
                        {counts.failed > 0 && <span className="text-red-600 dark:text-red-400">{counts.failed} failed</span>}
                        {counts.inFlight > 0 && (
                            <span className="inline-flex items-center gap-1.5 text-gray-500 dark:text-gray-400">
                                <span className="inline-block h-3 w-3 rounded-full border-2 border-indigo-500 border-t-transparent animate-spin" />
                                {counts.inFlight} in progress
                            </span>
                        )}
                    </div>
                    <ul className="divide-y divide-gray-200 dark:divide-gray-700 max-h-80 overflow-y-auto border border-gray-200 dark:border-gray-700 rounded">
                        {batch.map((row, idx) => (
                            <li key={idx} className="px-3 py-2 flex items-center justify-between text-sm">
                                <span className="truncate text-gray-800 dark:text-gray-200">{row.filename}</span>
                                <span className={`px-2 py-0.5 rounded-full text-xs font-semibold shrink-0 ml-2 ${STATUS_STYLES[row.status] || STATUS_STYLES.PENDING}`}>
                                    {row.status}
                                </span>
                            </li>
                        ))}
                    </ul>
                    {counts.inFlight === 0 && (
                        <div className="mt-4 flex items-center gap-4">
                            <Link to={historyLink} className="text-sm font-medium text-indigo-600 dark:text-indigo-400 hover:underline">View in History →</Link>
                            <Link to="/portfolio" className="text-sm font-medium text-indigo-600 dark:text-indigo-400 hover:underline">View Portfolio →</Link>
                            <button onClick={onCancel} className="text-sm text-gray-500 dark:text-gray-400 hover:underline">Upload another batch</button>
                        </div>
                    )}
                </>
            )}
        </div>
    );
};

export default BulkUpload;
