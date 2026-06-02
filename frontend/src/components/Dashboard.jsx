import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Link } from 'react-router-dom';
import FileUpload from './FileUpload';

const Dashboard = () => {
    const [jobs, setJobs] = useState([]);

    const fetchJobs = async () => {
        try {
            const response = await axios.get('/api/jobs');
            setJobs(response.data);
        } catch (error) {
            console.error('Error fetching jobs:', error);
        }
    };

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
        fetchJobs();
        const interval = setInterval(fetchJobs, 5000); // Poll every 5 seconds
        return () => clearInterval(interval);
    }, []);

    return (
        <div>
            <FileUpload onUploadSuccess={fetchJobs} />

            <div className="bg-white shadow overflow-hidden sm:rounded-md">
                <div className="px-4 py-5 sm:px-6">
                    <h3 className="text-lg leading-6 font-medium text-gray-900">Processed Jobs</h3>
                </div>
                <ul className="divide-y divide-gray-200">
                    {jobs.map((job) => (
                        <li key={job.id}>
                            <Link to={`/jobs/${job.id}`} className="block hover:bg-gray-50">
                                <div className="px-4 py-4 sm:px-6">
                                    <div className="flex items-center justify-between">
                                        <p className="text-sm font-medium text-indigo-600 truncate">{job.filename}</p>
                                        <div className="ml-2 flex-shrink-0 flex">
                                            <p className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full 
                        ${job.status === 'COMPLETED' ? 'bg-green-100 text-green-800' :
                                                    job.status === 'PROCESSING' ? 'bg-yellow-100 text-yellow-800' :
                                                        job.status === 'FAILED' ? 'bg-red-100 text-red-800' : 'bg-gray-100 text-gray-800'}`}>
                                                {job.status}
                                            </p>
                                        </div>
                                    </div>
                                    <div className="mt-2 sm:flex sm:justify-between">
                                        <div className="sm:flex">
                                            <p className="flex items-center text-sm text-gray-500">
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
                    {jobs.length === 0 && (
                        <li className="px-4 py-4 sm:px-6 text-center text-gray-500">No jobs found. Upload a file to get started.</li>
                    )}
                </ul>
            </div>
        </div>
    );
};

export default Dashboard;
