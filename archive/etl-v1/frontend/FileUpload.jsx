import React, { useState, useEffect } from 'react';
import axios from 'axios';

const FileUpload = ({ onUploadSuccess }) => {
    const [file, setFile] = useState(null);
    const [domain, setDomain] = useState('');
    const [wave, setWave] = useState('');
    const [priority, setPriority] = useState(false);
    const [knownDomains, setKnownDomains] = useState([]);
    const [knownWaves, setKnownWaves] = useState([]);
    const [uploading, setUploading] = useState(false);
    const [message, setMessage] = useState('');

    useEffect(() => {
        axios.get('/api/portfolio')
            .then((res) => {
                setKnownDomains(res.data.domains || []);
                setKnownWaves(res.data.waves || []);
            })
            .catch((err) => console.error('Failed to load domain/wave suggestions:', err));
    }, []);

    const handleFileChange = (e) => {
        setFile(e.target.files[0]);
        setMessage('');
    };

    const handleUpload = async () => {
        if (!file) return;

        const formData = new FormData();
        formData.append('file', file);
        if (domain.trim()) formData.append('domain', domain.trim());
        if (wave.trim()) formData.append('wave', wave.trim());
        formData.append('priority', priority);

        setUploading(true);
        try {
            const response = await axios.post('/api/upload', formData, {
                headers: {
                    'Content-Type': 'multipart/form-data',
                },
            });
            setMessage('File uploaded successfully! Processing started.');
            setFile(null);
            setPriority(false);
            if (onUploadSuccess) onUploadSuccess(response.data);
        } catch (error) {
            console.error('Error uploading file:', error);
            setMessage('Error uploading file.');
        } finally {
            setUploading(false);
        }
    };

    return (
        <div className="bg-white dark:bg-gray-800 shadow sm:rounded-lg p-6">
            <h3 className="text-lg leading-6 font-medium text-gray-900 dark:text-gray-100 mb-4">Upload ETL Export</h3>
            <div className="flex items-center space-x-4">
                <input
                    type="file"
                    accept=".dsx,.dtsx,.xml"
                    onChange={handleFileChange}
                    className="block w-full text-sm text-gray-500 dark:text-gray-400
            file:mr-4 file:py-2 file:px-4
            file:rounded-full file:border-0
            file:text-sm file:font-semibold
            file:bg-indigo-50 file:text-indigo-700
            hover:file:bg-indigo-100"
                />
                <button
                    onClick={handleUpload}
                    disabled={!file || uploading}
                    className={`inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 ${(!file || uploading) ? 'opacity-50 cursor-not-allowed' : ''
                        }`}
                >
                    {uploading ? 'Uploading...' : 'Upload'}
                </button>
            </div>
            <div className="mt-3 flex items-center gap-3">
                <input
                    type="text"
                    list="domain-suggestions"
                    placeholder="Domain (optional, e.g. Fees)"
                    value={domain}
                    onChange={(e) => setDomain(e.target.value)}
                    className="flex-1 text-sm border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-3 py-1.5"
                />
                <datalist id="domain-suggestions">
                    {knownDomains.map((d) => <option key={d} value={d} />)}
                </datalist>
                <input
                    type="text"
                    list="wave-suggestions"
                    placeholder="Wave (optional, e.g. Wave 1)"
                    value={wave}
                    onChange={(e) => setWave(e.target.value)}
                    className="flex-1 text-sm border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-3 py-1.5"
                />
                <datalist id="wave-suggestions">
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
                ⭐ Priority job
            </label>
            {message && <p className="mt-2 text-sm text-gray-600 dark:text-gray-400">{message}</p>}
        </div>
    );
};

export default FileUpload;
