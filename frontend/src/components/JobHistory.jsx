import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import { Link } from 'react-router-dom';

// Polling cadence: fast while a job is actively PROCESSING; slow otherwise.
// Pauses entirely when the tab is hidden.
const POLL_ACTIVE_MS = 5000;
const POLL_IDLE_MS = 30000;

const JobListSkeleton = () => (
    <ul className="divide-y divide-gray-200 dark:divide-gray-700 animate-pulse">
        {[...Array(3)].map((_, i) => (
            <li key={i} className="px-4 py-4 sm:px-6">
                <div className="flex items-center justify-between">
                    <div className="h-4 w-48 bg-gray-200 dark:bg-gray-700 rounded" />
                    <div className="h-5 w-20 bg-gray-200 dark:bg-gray-700 rounded-full" />
                </div>
                <div className="mt-3 h-3 w-32 bg-gray-100 dark:bg-gray-700 rounded" />
            </li>
        ))}
    </ul>
);

const EmptyState = () => (
    <div className="px-6 py-16 text-center">
        <div className="mx-auto h-16 w-16 rounded-full bg-orange-50 dark:bg-orange-900/30 flex items-center justify-center text-3xl mb-4">
            🕸️
        </div>
        <h4 className="text-base font-semibold text-gray-900 dark:text-gray-100">No jobs yet</h4>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400 max-w-sm mx-auto">
            Start a new run from the home page to see it appear here once processed.
        </p>
    </div>
);

const JobHistory = () => {
    const [jobs, setJobs] = useState([]);
    const [loadingJobs, setLoadingJobs] = useState(true);
    const jobsRef = useRef(jobs);
    jobsRef.current = jobs;

    const fetchJobs = useCallback(async () => {
        try {
            const response = await axios.get('/api/jobs');
            setJobs(response.data);
        } catch (error) {
            console.error('Error fetching jobs:', error);
        } finally {
            setLoadingJobs(false);
        }
    }, []);

    const handleDelete = async (jobId) => {
        if (!window.confirm("Are you sure you want to delete this job?")) return;
        try {
            await axios.delete(`/api/jobs/${jobId}`);
            fetchJobs();
        } catch (error) {
            console.error('Error deleting job:', error);
        }
    };

    useEffect(() => {
        let timerId = null;

        const hasProcessing = () =>
            jobsRef.current.some((j) => j.status === 'PROCESSING' || j.status === 'PENDING');

        const schedule = () => {
            if (document.hidden) return; // tab hidden — don't queue another tick
            const delay = hasProcessing() ? POLL_ACTIVE_MS : POLL_IDLE_MS;
            timerId = setTimeout(async () => {
                await fetchJobs();
                schedule();
            }, delay);
        };

        const onVisibilityChange = () => {
            if (document.hidden) {
                if (timerId) {
                    clearTimeout(timerId);
                    timerId = null;
                }
            } else {
                fetchJobs().then(schedule);
            }
        };

        fetchJobs().then(schedule);
        document.addEventListener('visibilitychange', onVisibilityChange);

        return () => {
            if (timerId) clearTimeout(timerId);
            document.removeEventListener('visibilitychange', onVisibilityChange);
        };
    }, [fetchJobs]);

    return (
        <div>
            <div className="mb-6">
                <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Job History</h1>
                <p className="text-sm text-gray-500 dark:text-gray-400">Every DataStage, SSIS, or Informatica export processed so far.</p>
            </div>

            <div className="bg-white dark:bg-gray-800 shadow overflow-hidden sm:rounded-md">
                <div className="px-4 py-5 sm:px-6 border-b border-gray-200 dark:border-gray-700">
                    <h3 className="text-lg leading-6 font-medium text-gray-900 dark:text-gray-100">Processed Jobs</h3>
                </div>

                {loadingJobs ? (
                    <JobListSkeleton />
                ) : jobs.length === 0 ? (
                    <EmptyState />
                ) : (
                    <ul className="divide-y divide-gray-200 dark:divide-gray-700">
                        {jobs.map((job) => (
                            <li key={job.id}>
                                <Link to={`/jobs/${job.id}`} className="block hover:bg-gray-50 dark:hover:bg-gray-700/50">
                                    <div className="px-4 py-4 sm:px-6">
                                        <div className="flex items-center justify-between">
                                            <p className="text-sm font-medium text-indigo-600 dark:text-indigo-400 truncate">{job.filename}</p>
                                            <div className="ml-2 flex-shrink-0 flex">
                                                <p className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full
                        ${job.status === 'COMPLETED' ? 'bg-green-100 dark:bg-green-900/40 text-green-800 dark:text-green-300' :
                                                        job.status === 'PROCESSING' ? 'bg-yellow-100 dark:bg-yellow-900/40 text-yellow-800 dark:text-yellow-300' :
                                                            job.status === 'FAILED' ? 'bg-red-100 dark:bg-red-900/40 text-red-800 dark:text-red-300' : 'bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-300'}`}>
                                                    {job.status}
                                                </p>
                                            </div>
                                        </div>
                                        <div className="mt-2 sm:flex sm:justify-between">
                                            <div className="sm:flex">
                                                <p className="flex items-center text-sm text-gray-500 dark:text-gray-400">
                                                    Uploaded: {new Date(job.created_at).toLocaleString()}
                                                </p>
                                            </div>
                                            <div className="mt-2 flex items-center text-sm text-gray-500 sm:mt-0">
                                                <button
                                                    onClick={(e) => {
                                                        e.preventDefault();
                                                        handleDelete(job.id);
                                                    }}
                                                    className="text-red-600 hover:text-red-900 ml-4"
                                                >
                                                    Delete
                                                </button>
                                            </div>
                                        </div>
                                    </div>
                                </Link>
                            </li>
                        ))}
                    </ul>
                )}
            </div>
        </div>
    );
};

export default JobHistory;
