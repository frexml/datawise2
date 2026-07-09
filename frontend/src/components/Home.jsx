import React, { useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';
import FileUpload from './FileUpload';

const KpiTile = ({ label, value, accent, icon }) => (
    <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-sm p-4 flex items-center gap-3">
        <div className={`h-10 w-10 rounded-lg flex items-center justify-center text-lg shrink-0 ${accent}`}>
            {icon}
        </div>
        <div className="min-w-0">
            <div className="text-2xl font-bold text-gray-900 dark:text-gray-100 leading-tight">{value}</div>
            <div className="text-xs text-gray-500 dark:text-gray-400 truncate">{label}</div>
        </div>
    </div>
);

// Ordered to match Job.current_stage values set in worker.py. current_stage
// reflects the LAST stage that fully completed, not the one in progress —
// so the "active" step in the UI is always the one immediately AFTER the
// matched index, not the matched stage itself.
const PIPELINE_STAGES = [
    { key: 'parsing', label: 'Parsing ETL export', icon: '🔬' },
    { key: 'analyzing', label: 'Analyzing structure', icon: '🧩' },
    { key: 'mapping_lineage', label: 'Mapping lineage', icon: '🗺️' },
    { key: 'generating_summaries', label: 'Generating summaries', icon: '🧠' },
    { key: 'saving_results', label: 'Saving results', icon: '💾' },
    { key: 'detecting_inefficiencies', label: 'Detecting inefficiencies', icon: '⚡' },
];

const RunProgress = ({ job }) => {
    const doneIndex = job.status === 'COMPLETED' || job.current_stage === 'completed'
        ? PIPELINE_STAGES.length - 1
        : PIPELINE_STAGES.findIndex((s) => s.key === job.current_stage);

    return (
        <div className="bg-white dark:bg-gray-800 shadow sm:rounded-lg p-6">
            <h3 className="text-lg font-medium text-gray-900 dark:text-gray-100 mb-1">
                Processing <span className="font-mono text-indigo-600 dark:text-indigo-400">{job.filename}</span>
            </h3>
            <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">
                Watch it move through the pipeline in real time — this is live backend state, not a simulated timer.
            </p>
            <ol className="flex items-start overflow-x-auto pb-1">
                {PIPELINE_STAGES.map((stage, i) => {
                    const isDone = i <= doneIndex;
                    const isActive = i === doneIndex + 1 && job.status === 'PROCESSING';
                    const isLast = i === PIPELINE_STAGES.length - 1;

                    // The connector to the RIGHT of this node, leading to the next one.
                    const connectorIsDone = i < doneIndex;
                    const connectorIsFlowing = i === doneIndex && job.status === 'PROCESSING';

                    return (
                        <React.Fragment key={stage.key}>
                            <li className="flex flex-col items-center w-20 sm:w-24 shrink-0 text-center">
                                <div
                                    className={
                                        'h-10 w-10 rounded-full flex items-center justify-center text-base shrink-0 transition-all duration-500 ' +
                                        (isDone
                                            ? 'bg-green-500 text-white shadow'
                                            : isActive
                                            ? 'bg-indigo-600 text-white shadow-lg shadow-indigo-300 dark:shadow-indigo-900 ring-4 ring-indigo-100 dark:ring-indigo-900/50 animate-pulse'
                                            : 'bg-gray-100 dark:bg-gray-700 text-gray-400 dark:text-gray-500')
                                    }
                                >
                                    {isDone ? '✓' : stage.icon}
                                </div>
                                <span
                                    className={
                                        'mt-2 text-xs leading-tight transition-colors duration-500 ' +
                                        (isDone
                                            ? 'text-gray-900 dark:text-gray-100 font-medium'
                                            : isActive
                                            ? 'text-indigo-700 dark:text-indigo-300 font-semibold'
                                            : 'text-gray-400 dark:text-gray-500')
                                    }
                                >
                                    {stage.label}
                                    {isActive && <span>…</span>}
                                </span>
                            </li>

                            {!isLast && (
                                <li className="relative flex-1 min-w-[24px] h-0.5 mt-5 overflow-visible">
                                    <div
                                        className={
                                            'absolute inset-0 rounded-full transition-colors duration-500 ' +
                                            (connectorIsDone
                                                ? 'bg-green-400 dark:bg-green-600'
                                                : connectorIsFlowing
                                                ? 'animate-march-x'
                                                : 'bg-gray-200 dark:bg-gray-700')
                                        }
                                        style={
                                            connectorIsFlowing
                                                ? { backgroundImage: 'repeating-linear-gradient(to right, #6366f1 0 6px, transparent 6px 12px)' }
                                                : undefined
                                        }
                                    />
                                    {connectorIsFlowing && (
                                        <span className="absolute left-1/2 -top-[7px] -translate-x-1/2 text-indigo-500 dark:text-indigo-400 text-sm animate-arrow-travel-x">
                                            ▸
                                        </span>
                                    )}
                                </li>
                            )}
                        </React.Fragment>
                    );
                })}
            </ol>
        </div>
    );
};

const Home = () => {
    const navigate = useNavigate();
    const [stats, setStats] = useState(null);
    const [showUpload, setShowUpload] = useState(false);
    const [activeJob, setActiveJob] = useState(null);
    const pollRef = useRef(null);

    const fetchStats = useCallback(async () => {
        try {
            const response = await axios.get('/api/stats');
            setStats(response.data);
        } catch (error) {
            console.error('Error fetching stats:', error);
        }
    }, []);

    useEffect(() => {
        fetchStats();
        const id = setInterval(() => {
            if (!document.hidden) fetchStats();
        }, 30000);
        return () => clearInterval(id);
    }, [fetchStats]);

    useEffect(() => () => {
        if (pollRef.current) clearTimeout(pollRef.current);
    }, []);

    const onUploadSuccess = ({ job_id }) => {
        setShowUpload(false);
        setActiveJob({ id: job_id, filename: 'your file', status: 'PENDING', current_stage: null });

        const poll = async () => {
            try {
                const res = await axios.get(`/api/jobs/${job_id}`);
                setActiveJob(res.data);
                if (res.data.status === 'COMPLETED') {
                    fetchStats();
                    setTimeout(() => navigate(`/jobs/${job_id}`), 600); // brief pause on the checkmark
                    return;
                }
                if (res.data.status === 'FAILED') {
                    fetchStats();
                    return; // stay put, show the failure state
                }
                pollRef.current = setTimeout(poll, 2000);
            } catch (err) {
                console.error('Error polling job status:', err);
                pollRef.current = setTimeout(poll, 3000);
            }
        };
        poll();
    };

    return (
        <div className="space-y-8">
            {/* Hero */}
            {!activeJob && (
                <div className="relative overflow-hidden bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-2xl shadow-sm px-8 py-16 text-center">
                    <div className="absolute inset-0 bg-gradient-to-br from-orange-50 via-transparent to-indigo-50 dark:from-orange-900/10 dark:to-indigo-900/10 pointer-events-none" />
                    <div className="relative">
                        <div className="text-5xl mb-4">🕸️</div>
                        <h1 className="text-3xl sm:text-4xl font-extrabold text-gray-900 dark:text-gray-100 tracking-tight">
                            Turn legacy ETL jobs into<br className="hidden sm:block" />
                            <span className="text-orange-600">governed documentation</span> — automatically
                        </h1>
                        <p className="mt-4 text-base text-gray-600 dark:text-gray-300 max-w-xl mx-auto">
                            Upload a DataStage (`.dsx`), SSIS (`.dtsx`), or Informatica (`.xml`) export and watch
                            column-level lineage, technical &amp; business summaries, and inefficiency findings
                            get extracted in minutes — every summary human-reviewed before it's governed.
                        </p>

                        {!showUpload ? (
                            <button
                                onClick={() => setShowUpload(true)}
                                className="mt-8 inline-flex items-center gap-2 px-6 py-3 bg-orange-600 text-white text-base font-semibold rounded-lg shadow hover:bg-orange-700 transition-colors"
                            >
                                🚀 Start New Run
                            </button>
                        ) : (
                            <div className="mt-8 max-w-lg mx-auto text-left">
                                <FileUpload onUploadSuccess={onUploadSuccess} />
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Active run — animated progress */}
            {activeJob && (
                <div>
                    <RunProgress job={activeJob} />
                    {activeJob.status === 'FAILED' && (
                        <div className="mt-4 flex items-center justify-between bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-4 py-3">
                            <span className="text-sm text-red-700 dark:text-red-300">
                                This run failed. Check the job history for details.
                            </span>
                            <button
                                onClick={() => setActiveJob(null)}
                                className="text-sm font-medium text-red-700 dark:text-red-300 hover:underline"
                            >
                                Start another run
                            </button>
                        </div>
                    )}
                </div>
            )}

            {/* KPI tiles */}
            {stats && (
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
                    <KpiTile label="Jobs Processed" value={stats.total_jobs} accent="bg-indigo-50 dark:bg-indigo-900/40" icon="📦" />
                    <KpiTile label="Completed" value={stats.completed_jobs} accent="bg-green-50 dark:bg-green-900/40" icon="✅" />
                    <KpiTile label="Pending Review" value={stats.pending_reviews} accent="bg-yellow-50 dark:bg-yellow-900/40" icon="📝" />
                    <KpiTile label="Inefficiencies Flagged" value={stats.inefficiencies_count} accent="bg-red-50 dark:bg-red-900/40" icon="⚡" />
                    <KpiTile label="Review Coverage" value={`${stats.review_coverage_pct}%`} accent="bg-blue-50 dark:bg-blue-900/40" icon="🛡️" />
                </div>
            )}
        </div>
    );
};

export default Home;
