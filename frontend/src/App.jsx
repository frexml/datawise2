import React from 'react';
import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom';
import Dashboard from './components/Dashboard';
import JobDetails from './components/JobDetails';

function App() {
    return (
        <Router>
            <div className="min-h-screen flex flex-col bg-gradient-to-b from-gray-50 to-gray-100">
                {/* Top nav */}
                <nav className="bg-white border-b border-gray-200 shadow-sm sticky top-0 z-20">
                    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                        <div className="flex justify-between h-16 items-center">
                            <Link to="/" className="flex items-center gap-3 group">
                                <img
                                    src="/logo.png"
                                    alt="mobileLIVE"
                                    className="h-9 w-9 rounded-md ring-1 ring-gray-200 transition-transform group-hover:scale-105"
                                />
                                <div className="flex flex-col leading-tight">
                                    <span className="text-lg font-semibold text-gray-900">
                                        Data<span className="text-orange-600">Wise</span>
                                    </span>
                                    <span className="text-[10px] uppercase tracking-wider text-gray-500">
                                        Lineage Explorer
                                    </span>
                                </div>
                            </Link>

                            <div className="hidden sm:flex items-center gap-4 text-sm">
                                <Link
                                    to="/"
                                    className="text-gray-600 hover:text-orange-600 font-medium transition-colors"
                                >
                                    Dashboard
                                </Link>
                                <a
                                    href="https://platform.openai.com/docs"
                                    target="_blank"
                                    rel="noreferrer"
                                    className="text-gray-400 hover:text-gray-600 font-medium transition-colors"
                                >
                                    Docs
                                </a>
                            </div>
                        </div>
                    </div>
                </nav>

                {/* Page content */}
                <main className="flex-1 py-10">
                    <div className="max-w-7xl mx-auto sm:px-6 lg:px-8">
                        <Routes>
                            <Route path="/" element={<Dashboard />} />
                            <Route path="/jobs/:jobId" element={<JobDetails />} />
                        </Routes>
                    </div>
                </main>

                {/* Footer with mobileLIVE attribution */}
                <footer className="bg-white border-t border-gray-200 mt-auto">
                    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-5 flex flex-col sm:flex-row items-center justify-between gap-3">
                        <p className="text-xs text-gray-500">
                            DataWise · IBM DataStage lineage extraction & analysis
                        </p>
                        <a
                            href="https://mobilelive.ca"
                            target="_blank"
                            rel="noreferrer"
                            className="flex items-center gap-2 text-xs text-gray-500 hover:text-orange-600 transition-colors"
                            title="Built by mobileLIVE"
                        >
                            <span>Built by</span>
                            <img src="/logo.png" alt="mobileLIVE" className="h-5 w-5 rounded" />
                            <span className="font-medium">mobileLIVE</span>
                        </a>
                    </div>
                </footer>
            </div>
        </Router>
    );
}

export default App;
