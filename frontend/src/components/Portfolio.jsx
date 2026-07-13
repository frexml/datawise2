import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Link } from 'react-router-dom';

const coverageColor = (pct) => {
    if (pct >= 90) return 'bg-green-500';
    if (pct >= 60) return 'bg-yellow-500';
    return 'bg-red-500';
};

const BreakdownSection = ({ title, icon, rows, linkParam }) => (
    <div className="bg-white dark:bg-gray-800 shadow sm:rounded-md">
        <div className="px-4 py-5 sm:px-6 border-b border-gray-200 dark:border-gray-700">
            <h3 className="text-lg leading-6 font-medium text-gray-900 dark:text-gray-100 flex items-center gap-2">
                <span>{icon}</span> {title}
            </h3>
        </div>
        {rows.length === 0 ? (
            <div className="px-6 py-10 text-center text-sm text-gray-500 dark:text-gray-400 italic">
                No jobs yet.
            </div>
        ) : (
            <ul className="divide-y divide-gray-200 dark:divide-gray-700">
                {rows.map((row) => (
                    <li key={row.name} className="px-4 py-4 sm:px-6">
                        <div className="flex items-center justify-between mb-1.5">
                            <Link
                                to={`/history?${linkParam}=${encodeURIComponent(row.name)}`}
                                className="text-sm font-medium text-indigo-600 dark:text-indigo-400 hover:underline"
                            >
                                {row.name} →
                            </Link>
                            <span className="text-xs text-gray-500 dark:text-gray-400">
                                {row.total_jobs} job{row.total_jobs === 1 ? '' : 's'} · {row.completed_jobs} completed
                            </span>
                        </div>
                        <div className="flex items-center gap-3">
                            <div className="flex-1 h-2 rounded-full bg-gray-100 dark:bg-gray-700 overflow-hidden">
                                <div
                                    className={`h-full ${coverageColor(row.coverage_pct)}`}
                                    style={{ width: `${row.coverage_pct}%` }}
                                />
                            </div>
                            <span className="text-xs font-semibold text-gray-700 dark:text-gray-300 w-28 text-right">
                                {row.coverage_pct}% ({row.approved_reviews}/{row.total_reviews} approved)
                            </span>
                        </div>
                        <div className="mt-2 flex items-center gap-x-3 gap-y-1 flex-wrap text-xs text-gray-500 dark:text-gray-400">
                            <span>⚡ {row.inefficiencies_count} inefficienc{row.inefficiencies_count === 1 ? 'y' : 'ies'}</span>
                            <span>📦 {row.scopeiq_total_days}d estimated ({row.scopeiq_estimated_jobs} job{row.scopeiq_estimated_jobs === 1 ? '' : 's'})</span>
                            <span>⏱ {row.avg_turnaround_days != null ? `${row.avg_turnaround_days}d avg to approve` : 'no approvals yet'}</span>
                            <span>📚 {row.catalog_pushed_count}/{row.total_jobs} pushed to catalog</span>
                            {row.overdue_reviews > 0 && (
                                <span className="text-red-600 dark:text-red-400 font-semibold">🚨 {row.overdue_reviews} overdue</span>
                            )}
                        </div>
                        {row.priority_total_jobs > 0 && (
                            <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                                ⭐ Priority: {row.priority_coverage_pct}% ({row.priority_approved_reviews}/{row.priority_total_reviews} approved, {row.priority_total_jobs} priority job{row.priority_total_jobs === 1 ? '' : 's'})
                            </div>
                        )}
                    </li>
                ))}
            </ul>
        )}
    </div>
);

const RecurringPatterns = ({ patterns, truncated }) => (
    <div className="bg-white dark:bg-gray-800 shadow sm:rounded-md">
        <div className="px-4 py-5 sm:px-6 border-b border-gray-200 dark:border-gray-700">
            <h3 className="text-lg leading-6 font-medium text-gray-900 dark:text-gray-100 flex items-center gap-2">
                <span>🔁</span> Recurring Patterns
            </h3>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                The same inefficiency pattern appearing in 2+ jobs — worth fixing once instead of replicating across the migration.
            </p>
        </div>
        {patterns.length === 0 ? (
            <div className="px-6 py-10 text-center text-sm text-gray-500 dark:text-gray-400 italic">
                No recurring patterns detected yet.
            </div>
        ) : (
            <ul className="divide-y divide-gray-200 dark:divide-gray-700">
                {patterns.map((p) => (
                    <li key={p.signature} className="px-4 py-4 sm:px-6">
                        <div className="flex items-center justify-between mb-1.5">
                            <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{p.pattern_type.replace(/_/g, ' ')}</span>
                            <span className="text-xs font-semibold text-orange-600 dark:text-orange-400">appears in {p.job_count} jobs</span>
                        </div>
                        <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">{p.sample_description}</p>
                        <div className="flex flex-wrap gap-1.5">
                            {p.jobs.map((j) => (
                                <Link
                                    key={j.id}
                                    to={`/jobs/${j.id}`}
                                    className="px-2 py-0.5 text-xs rounded bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600"
                                    title={[j.domain, j.wave].filter(Boolean).join(' · ')}
                                >
                                    {j.filename}
                                </Link>
                            ))}
                        </div>
                    </li>
                ))}
            </ul>
        )}
        {truncated && (
            <div className="px-4 py-2 sm:px-6 text-xs text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-700">
                Showing the top {patterns.length} patterns by job count — more exist.
            </div>
        )}
    </div>
);

const Portfolio = () => {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        axios.get('/api/portfolio')
            .then((res) => setData(res.data))
            .catch((err) => console.error('Failed to load portfolio breakdown:', err))
            .finally(() => setLoading(false));
    }, []);

    return (
        <div>
            <div className="mb-6">
                <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Portfolio</h1>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                    Documentation coverage broken down by domain and migration wave — for engagements tracking many jobs as a program, not one at a time.
                </p>
            </div>

            {loading ? (
                <div className="text-center py-16">
                    <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
                </div>
            ) : (
                <div className="space-y-6">
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                        <BreakdownSection title="By Domain" icon="🗂️" rows={data?.by_domain || []} linkParam="domain" />
                        <BreakdownSection title="By Wave" icon="🌊" rows={data?.by_wave || []} linkParam="wave" />
                    </div>
                    <RecurringPatterns
                        patterns={data?.recurring_patterns || []}
                        truncated={data?.recurring_patterns_truncated || false}
                    />
                </div>
            )}
        </div>
    );
};

export default Portfolio;
