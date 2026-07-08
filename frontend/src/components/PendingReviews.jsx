import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { Link } from 'react-router-dom';

const PendingReviews = () => {
    const [reviews, setReviews] = useState([]);
    const [loading, setLoading] = useState(true);
    const [reviewerName, setReviewerName] = useState('');
    const [submittingId, setSubmittingId] = useState(null);
    const [expandedId, setExpandedId] = useState(null);

    const fetchReviews = useCallback(async () => {
        try {
            const res = await axios.get('/api/reviews'); // defaults to status=pending_review
            setReviews(res.data);
        } catch (err) {
            console.error('Failed to load pending reviews:', err);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchReviews();
        const id = setInterval(() => {
            if (!document.hidden) fetchReviews();
        }, 15000);
        return () => clearInterval(id);
    }, [fetchReviews]);

    const submitReview = async (reviewId, decision) => {
        if (!reviewerName.trim()) {
            alert('Enter a reviewer name before approving or rejecting.');
            return;
        }
        setSubmittingId(reviewId);
        try {
            await axios.post(`/api/reviews/${reviewId}`, { reviewer: reviewerName, decision });
            setReviews((prev) => prev.filter((r) => r.id !== reviewId));
        } catch (err) {
            console.error('Failed to submit review:', err);
            alert('Failed to submit review.');
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
                    nothing reaches the governed layer without sign-off.
                </p>
            </div>

            <div className="mb-4 max-w-xs">
                <input
                    type="text"
                    placeholder="Your name (required to approve/reject)"
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
                                        <Link
                                            to={`/jobs/${review.job_id}`}
                                            className="text-sm font-medium text-indigo-600 dark:text-indigo-400 hover:underline truncate"
                                        >
                                            {review.job_filename}
                                        </Link>
                                        <div className="text-xs text-gray-500 dark:text-gray-400">
                                            {review.target_type.replace(/_/g, ' ')} · queued {new Date(review.created_at).toLocaleString()}
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-2 shrink-0">
                                        <button
                                            onClick={() => setExpandedId(expandedId === review.id ? null : review.id)}
                                            className="px-3 py-1 text-sm text-gray-600 dark:text-gray-300 hover:underline"
                                        >
                                            {expandedId === review.id ? 'Hide preview' : 'Preview'}
                                        </button>
                                        <button
                                            onClick={() => submitReview(review.id, 'approved')}
                                            disabled={submittingId === review.id}
                                            className="px-3 py-1 bg-green-600 text-white text-sm rounded hover:bg-green-700 disabled:bg-gray-400"
                                        >
                                            Approve
                                        </button>
                                        <button
                                            onClick={() => submitReview(review.id, 'rejected')}
                                            disabled={submittingId === review.id}
                                            className="px-3 py-1 bg-red-600 text-white text-sm rounded hover:bg-red-700 disabled:bg-gray-400"
                                        >
                                            Reject
                                        </button>
                                    </div>
                                </div>

                                {expandedId === review.id && (
                                    <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-3">
                                        <div className="bg-gray-50 dark:bg-gray-900/40 border border-gray-200 dark:border-gray-700 rounded p-3">
                                            <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase mb-1">Technical</div>
                                            <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">
                                                {review.technical_preview || '—'}
                                            </p>
                                        </div>
                                        <div className="bg-gray-50 dark:bg-gray-900/40 border border-gray-200 dark:border-gray-700 rounded p-3">
                                            <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase mb-1">Business</div>
                                            <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">
                                                {review.business_preview || '—'}
                                            </p>
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
