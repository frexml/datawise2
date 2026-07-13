import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { BrowserRouter as Router, Routes, Route, Link, NavLink } from 'react-router-dom';
import Home from './components/Home';
import JobHistory from './components/JobHistory';
import JobDetails from './components/JobDetails';
import PendingReviews from './components/PendingReviews';
import Portfolio from './components/Portfolio';

const ThemeToggle = () => {
    const [isDark, setIsDark] = useState(() => document.documentElement.classList.contains('dark'));

    useEffect(() => {
        document.documentElement.classList.toggle('dark', isDark);
        localStorage.setItem('theme', isDark ? 'dark' : 'light');
    }, [isDark]);

    return (
        <button
            onClick={() => setIsDark((v) => !v)}
            title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
            className="h-8 w-8 flex items-center justify-center rounded-md text-gray-500 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
        >
            {isDark ? '☀️' : '🌙'}
        </button>
    );
};

const navLinkClass = ({ isActive }) =>
    `hidden sm:inline font-medium transition-colors ${isActive
        ? 'text-orange-600'
        : 'text-gray-600 dark:text-gray-300 hover:text-orange-600'
    }`;

function App() {
    const [pendingReviews, setPendingReviews] = useState(0);

    useEffect(() => {
        const fetchPending = async () => {
            try {
                const res = await axios.get('/api/stats');
                setPendingReviews(res.data.pending_reviews || 0);
            } catch {
                // non-critical — nav badge just stays at its last known value
            }
        };
        fetchPending();
        const id = setInterval(() => {
            if (!document.hidden) fetchPending();
        }, 20000);
        return () => clearInterval(id);
    }, []);

    return (
        <Router>
            <div className="min-h-screen flex flex-col bg-gradient-to-b from-gray-50 to-gray-100 dark:from-gray-900 dark:to-gray-950 transition-colors">
                {/* Top nav */}
                <nav className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 shadow-sm sticky top-0 z-20">
                    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                        <div className="flex justify-between h-16 items-center">
                            <Link to="/" className="flex items-center gap-3 group">
                                <img
                                    src="/logo.png"
                                    alt="ML arteka"
                                    className="h-9 w-9 rounded-md ring-1 ring-gray-200 transition-transform group-hover:scale-105"
                                />
                                <div className="flex flex-col leading-tight">
                                    <span className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                                        Data<span className="text-orange-600">Wise</span>
                                    </span>
                                    <span className="text-[10px] uppercase tracking-wider text-gray-500 dark:text-gray-400">
                                        Lineage Explorer
                                    </span>
                                </div>
                            </Link>

                            <div className="flex items-center gap-5 text-sm">
                                <NavLink to="/" end className={navLinkClass}>
                                    Home
                                </NavLink>
                                <NavLink to="/history" className={navLinkClass}>
                                    History
                                </NavLink>
                                <NavLink to="/portfolio" className={navLinkClass}>
                                    Portfolio
                                </NavLink>
                                <NavLink to="/reviews" className={navLinkClass}>
                                    <span className="inline-flex items-center gap-1.5">
                                        Reviews
                                        {pendingReviews > 0 && (
                                            <span className="inline-flex items-center justify-center h-5 min-w-[1.25rem] px-1 rounded-full bg-orange-600 text-white text-[11px] font-semibold">
                                                {pendingReviews}
                                            </span>
                                        )}
                                    </span>
                                </NavLink>
                                <ThemeToggle />
                            </div>
                        </div>
                    </div>
                </nav>

                {/* Page content */}
                <main className="flex-1 py-10">
                    <div className="max-w-7xl mx-auto sm:px-6 lg:px-8">
                        <Routes>
                            <Route path="/" element={<Home />} />
                            <Route path="/history" element={<JobHistory />} />
                            <Route path="/portfolio" element={<Portfolio />} />
                            <Route path="/jobs/:jobId" element={<JobDetails />} />
                            <Route path="/reviews" element={<PendingReviews />} />
                        </Routes>
                    </div>
                </main>

                {/* Footer with ML arteka attribution */}
                <footer className="bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 mt-auto">
                    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-5 flex flex-col sm:flex-row items-center justify-between gap-3">
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                            DataWise · IBM DataStage lineage extraction & analysis
                        </p>
                        <a
                            href="https://ML arteka.ca"
                            target="_blank"
                            rel="noreferrer"
                            className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400 hover:text-orange-600 transition-colors"
                            title="Built by ML arteka"
                        >
                            <span>Built by</span>
                            <img src="/logo.png" alt="ML arteka" className="h-5 w-5 rounded" />
                            <span className="font-medium">ML arteka</span>
                        </a>
                    </div>
                </footer>
            </div>
        </Router>
    );
}

export default App;
