import React, { useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import { Link } from 'react-router-dom';
import Markdown from 'react-markdown';

const STATUS_BADGE = {
    pending_review: 'bg-yellow-100 dark:bg-yellow-900/40 text-yellow-800 dark:text-yellow-300',
    rejected: 'bg-red-100 dark:bg-red-900/40 text-red-800 dark:text-red-300',
    regenerating: 'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-800 dark:text-indigo-300',
};

// Matches the backend's _REVIEW_SLA_HOURS (endpoints.py) — kept as a
// frontend-only computation since it's purely a display concern here.
const REVIEW_SLA_HOURS = 48;
const isOverdue = (review) =>
    ['pending_review', 'rejected', 'regenerating'].includes(review.status) &&
    (Date.now() - new Date(review.created_at).getTime()) / (1000 * 60 * 60) > REVIEW_SLA_HOURS;

const PendingReviews = () => {
    const [reviews, setReviews] = useState([]);
    const [loading, setLoading] = useState(true);
    const [reviewerName, setReviewerName] = useState('');
    const [submittingId, setSubmittingId] = useState(null);
    const [expandedId, setExpandedId] = useState(null);

    // Reject-with-reason
    const [rejectingId, setRejectingId] = useState(null);
    const [rejectReason, setRejectReason] = useState('');

    // Edit-in-place
    const [editingId, setEditingId] = useState(null);
    const [editTechnical, setEditTechnical] = useState('');
    const [editBusiness, setEditBusiness] = useState('');
    const [loadingEditText, setLoadingEditText] = useState(false);

    const reviewsRef = useRef(reviews);
    reviewsRef.current = reviews;

    const fetchReviews = useCallback(async () => {
        try {
            const res = await axios.get('/api/reviews'); // defaults to status=open
            setReviews(res.data);
        } catch (err) {
            console.error('Failed to load pending reviews:', err);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchReviews();
        let id;
        const schedule = () => {
            const hasRegenerating = reviewsRef.current.some((r) => r.status === 'regenerating');
            const delay = hasRegenerating ? 3000 : 15000;
            id = setTimeout(async () => {
                if (!document.hidden) await fetchReviews();
                schedule();
            }, delay);
        };
        schedule();
        return () => clearTimeout(id);
    }, [fetchReviews]);

    const requireReviewer = () => {
        if (!reviewerName.trim()) {
            alert('Enter a reviewer name before approving, rejecting, or editing.');
            return false;
        }
        return true;
    };

    const approve = async (reviewId) => {
        if (!requireReviewer()) return;
        setSubmittingId(reviewId);
        try {
            await axios.post(`/api/reviews/${reviewId}`, { reviewer: reviewerName, decision: 'approved' });
            setReviews((prev) => prev.filter((r) => r.id !== reviewId));
        } catch (err) {
            console.error('Failed to approve review:', err);
            alert('Failed to approve review.');
        } finally {
            setSubmittingId(null);
        }
    };

    const confirmReject = async (reviewId) => {
        if (!requireReviewer()) return;
        if (!rejectReason.trim()) {
            alert('A reason is required to reject a summary.');
            return;
        }
        setSubmittingId(reviewId);
        try {
            const res = await axios.post(`/api/reviews/${reviewId}`, {
                reviewer: reviewerName,
                decision: 'rejected',
                feedback: rejectReason,
            });
            setReviews((prev) => prev.map((r) => (r.id === reviewId ? res.data : r)));
            setRejectingId(null);
            setRejectReason('');
        } catch (err) {
            console.error('Failed to reject review:', err);
            alert(err.response?.data?.detail || 'Failed to reject review.');
        } finally {
            setSubmittingId(null);
        }
    };

    const rerun = async (reviewId) => {
        setSubmittingId(reviewId);
        try {
            const res = await axios.post(`/api/reviews/${reviewId}/rerun`);
            setReviews((prev) => prev.map((r) => (r.id === reviewId ? res.data : r)));
        } catch (err) {
            console.error('Failed to re-run review:', err);
            alert('Failed to re-run review.');
        } finally {
            setSubmittingId(null);
        }
    };

    const startEdit = async (review) => {
        setEditingId(review.id);
        setEditTechnical('');
        setEditBusiness('');
        setLoadingEditText(true);
        try {
            const res = await axios.get(`/api/results/${review.job_id}`);
            setEditTechnical(res.data.llm_explanation || '');
            setEditBusiness(res.data.business_summary || '');
        } catch (err) {
            console.error('Failed to load current summary for editing:', err);
            alert('Failed to load current summary.');
            setEditingId(null);
        } finally {
            setLoadingEditText(false);
        }
    };

    const saveEdit = async (reviewId) => {
        if (!requireReviewer()) return;
        setSubmittingId(reviewId);
        try {
            await axios.post(`/api/reviews/${reviewId}/edit`, {
                reviewer: reviewerName,
                technical_summary: editTechnical,
                business_summary: editBusiness,
            });
            setReviews((prev) => prev.filter((r) => r.id !== reviewId));
            setEditingId(null);
        } catch (err) {
            console.error('Failed to save edited summary:', err);
            alert('Failed to save edited summary.');
        } finally {
            setSubmittingId(null);
        }
    };

    return (
        <div>
            <div className="mb-6">
                <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Pending Reviews</h1>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                    Every AI-generated summary is queued here until a named reviewer approves it —
                    nothing reaches the governed layer without sign-off. Rejections need a reason, and
                    can be fixed by hand-editing or re-running with that feedback fed back to the LLM.
                </p>
            </div>

            <div className="mb-4 max-w-xs">
                <input
                    type="text"
                    placeholder="Your name (required to act on a review)"
                    value={reviewerName}
                    onChange={(e) => setReviewerName(e.target.value)}
                    className="w-full border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-3 py-2 text-sm"
                />
            </div>

            <div className="bg-white dark:bg-gray-800 shadow overflow-hidden sm:rounded-md">
                {loading ? (
                    <div className="p-6 space-y-4 animate-pulse">
                        {[...Array(3)].map((_, i) => (
                            <div key={i} className="h-20 bg-gray-100 dark:bg-gray-700 rounded" />
                        ))}
                    </div>
                ) : reviews.length === 0 ? (
                    <div className="px-6 py-16 text-center">
                        <div className="mx-auto h-16 w-16 rounded-full bg-green-50 dark:bg-green-900/30 flex items-center justify-center text-3xl mb-4">
                            ✅
                        </div>
                        <h4 className="text-base font-semibold text-gray-900 dark:text-gray-100">Queue is clear</h4>
                        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">No summaries are waiting on review right now.</p>
                    </div>
                ) : (
                    <ul className="divide-y divide-gray-200 dark:divide-gray-700">
                        {reviews.map((review) => (
                            <li key={review.id} className="px-4 py-4 sm:px-6">
                                <div className="flex items-center justify-between gap-4">
                                    <div className="min-w-0">
                                        <div className="flex items-center gap-2">
                                            <Link
                                                to={`/jobs/${review.job_id}`}
                                                className="text-sm font-medium text-indigo-600 dark:text-indigo-400 hover:underline truncate"
                                            >
                                                {review.job_filename}
                                            </Link>
                                            <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${STATUS_BADGE[review.status] || ''}`}>
                                                {review.status.replace(/_/g, ' ')}
                                            </span>
                                            {isOverdue(review) && (
                                                <span className="px-2 py-0.5 rounded-full text-xs font-semibold bg-red-100 dark:bg-red-900/40 text-red-800 dark:text-red-300" title={`Open longer than ${REVIEW_SLA_HOURS}h`}>
                                                    ⏰ Overdue
                                                </span>
                                            )}
                                        </div>
                                        <div className="text-xs text-gray-500 dark:text-gray-400">
                                            {review.target_type.replace(/_/g, ' ')} · queued {new Date(review.created_at).toLocaleString()}
                                        </div>
                                    </div>

                                    {review.status === 'pending_review' && rejectingId !== review.id && (
                                        <div className="flex items-center gap-2 shrink-0">
                                            <button
                                                onClick={() => setExpandedId(expandedId === review.id ? null : review.id)}
                                                className="px-3 py-1 text-sm text-gray-600 dark:text-gray-300 hover:underline"
                                            >
                                                {expandedId === review.id ? 'Hide preview' : 'Preview'}
                                            </button>
                                            <button
                                                onClick={() => approve(review.id)}
                                                disabled={submittingId === review.id}
                                                className="px-3 py-1 bg-green-600 text-white text-sm rounded hover:bg-green-700 disabled:bg-gray-400"
                                            >
                                                Approve
                                            </button>
                                            <button
                                                onClick={() => { setRejectingId(review.id); setRejectReason(''); }}
                                                disabled={submittingId === review.id}
                                                className="px-3 py-1 bg-red-600 text-white text-sm rounded hover:bg-red-700 disabled:bg-gray-400"
                                            >
                                                Reject
                                            </button>
                                        </div>
                                    )}

                                    {review.status === 'rejected' && editingId !== review.id && (
                                        <div className="flex items-center gap-2 shrink-0">
                                            <button
                                                onClick={() => startEdit(review)}
                                                disabled={submittingId === review.id}
                                                className="px-3 py-1 bg-indigo-600 text-white text-sm rounded hover:bg-indigo-700 disabled:bg-gray-400"
                                            >
                                                ✏️ Edit
                                            </button>
                                            <button
                                                onClick={() => rerun(review.id)}
                                                disabled={submittingId === review.id}
                                                className="px-3 py-1 bg-emerald-700 text-white text-sm rounded hover:bg-emerald-800 disabled:bg-gray-400"
                                            >
                                                🔁 Re-run
                                            </button>
                                        </div>
                                    )}
                                </div>

                                {/* Reject reason prompt */}
                                {rejectingId === review.id && (
                                    <div className="mt-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded p-3">
                                        <label className="block text-xs font-semibold text-red-800 dark:text-red-300 uppercase mb-1">
                                            Why is this being rejected?
                                        </label>
                                        <textarea
                                            value={rejectReason}
                                            onChange={(e) => setRejectReason(e.target.value)}
                                            rows={2}
                                            placeholder="e.g. Doesn't mention the reject-file handling; overstates confidence in the SCD logic..."
                                            className="w-full border border-red-300 dark:border-red-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-3 py-2 text-sm"
                                        />
                                        <div className="mt-2 flex gap-2">
                                            <button
                                                onClick={() => confirmReject(review.id)}
                                                disabled={submittingId === review.id}
                                                className="px-3 py-1 bg-red-600 text-white text-sm rounded hover:bg-red-700 disabled:bg-gray-400"
                                            >
                                                Confirm Reject
                                            </button>
                                            <button
                                                onClick={() => { setRejectingId(null); setRejectReason(''); }}
                                                className="px-3 py-1 text-sm text-gray-600 dark:text-gray-300 hover:underline"
                                            >
                                                Cancel
                                            </button>
                                        </div>
                                    </div>
                                )}

                                {/* Rejected: show reason + edit/rerun affordances */}
                                {review.status === 'rejected' && editingId !== review.id && (
                                    <div className="mt-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded p-3 text-sm text-red-800 dark:text-red-300">
                                        <span className="font-semibold">Rejected{review.reviewer ? ` by ${review.reviewer}` : ''}:</span>{' '}
                                        {review.feedback}
                                    </div>
                                )}

                                {/* Regenerating */}
                                {review.status === 'regenerating' && (
                                    <div className="mt-3 bg-indigo-50 dark:bg-indigo-900/20 border border-indigo-200 dark:border-indigo-800 rounded p-3 text-sm text-indigo-800 dark:text-indigo-300 flex items-center gap-2">
                                        <span className="inline-block h-3 w-3 rounded-full bg-indigo-500 animate-pulse" />
                                        Regenerating with your feedback: "{review.feedback}"
                                    </div>
                                )}

                                {/* Edit form */}
                                {editingId === review.id && (
                                    <div className="mt-3 bg-indigo-50 dark:bg-indigo-900/20 border border-indigo-200 dark:border-indigo-800 rounded p-3">
                                        {loadingEditText ? (
                                            <p className="text-sm text-indigo-700 dark:text-indigo-300">Loading current summary…</p>
                                        ) : (
                                            <>
                                                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                                    <div>
                                                        <label className="block text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase mb-1">Technical</label>
                                                        <textarea
                                                            value={editTechnical}
                                                            onChange={(e) => setEditTechnical(e.target.value)}
                                                            rows={6}
                                                            className="w-full border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-3 py-2 text-sm font-mono"
                                                        />
                                                    </div>
                                                    <div>
                                                        <label className="block text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase mb-1">Business</label>
                                                        <textarea
                                                            value={editBusiness}
                                                            onChange={(e) => setEditBusiness(e.target.value)}
                                                            rows={6}
                                                            className="w-full border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-3 py-2 text-sm font-mono"
                                                        />
                                                    </div>
                                                </div>
                                                <div className="mt-2 flex gap-2">
                                                    <button
                                                        onClick={() => saveEdit(review.id)}
                                                        disabled={submittingId === review.id}
                                                        className="px-3 py-1 bg-indigo-600 text-white text-sm rounded hover:bg-indigo-700 disabled:bg-gray-400"
                                                    >
                                                        Save &amp; Approve
                                                    </button>
                                                    <button
                                                        onClick={() => setEditingId(null)}
                                                        className="px-3 py-1 text-sm text-gray-600 dark:text-gray-300 hover:underline"
                                                    >
                                                        Cancel
                                                    </button>
                                                </div>
                                            </>
                                        )}
                                    </div>
                                )}

                                {expandedId === review.id && review.status === 'pending_review' && (
                                    <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-3">
                                        <div className="bg-gray-50 dark:bg-gray-900/40 border border-gray-200 dark:border-gray-700 rounded p-3">
                                            <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase mb-1">Technical</div>
                                            <div className="prose prose-sm max-w-none text-sm text-gray-700 dark:text-gray-300">
                                                {review.technical_preview ? <Markdown>{review.technical_preview}</Markdown> : '—'}
                                            </div>
                                        </div>
                                        <div className="bg-gray-50 dark:bg-gray-900/40 border border-gray-200 dark:border-gray-700 rounded p-3">
                                            <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase mb-1">Business</div>
                                            <div className="prose prose-sm max-w-none text-sm text-gray-700 dark:text-gray-300">
                                                {review.business_preview ? <Markdown>{review.business_preview}</Markdown> : '—'}
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </li>
                        ))}
                    </ul>
                )}
            </div>
        </div>
    );
};

export default PendingReviews;
