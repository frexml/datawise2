import React, { useState } from 'react';
import axios from 'axios';

const FileUpload = ({ onUploadSuccess }) => {
    const [file, setFile] = useState(null);
    const [uploading, setUploading] = useState(false);
    const [message, setMessage] = useState('');

    const handleFileChange = (e) => {
        setFile(e.target.files[0]);
        setMessage('');
    };

    const handleUpload = async () => {
        if (!file) return;

        const formData = new FormData();
        formData.append('file', file);

        setUploading(true);
        try {
            const response = await axios.post('/api/upload', formData, {
                headers: {
                    'Content-Type': 'multipart/form-data',
                },
            });
            setMessage('File uploaded successfully! Processing started.');
            setFile(null);
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
            <h3 className="text-lg leading-6 font-medium text-gray-900 dark:text-gray-100 mb-4">Upload DSX File</h3>
            <div className="flex items-center space-x-4">
                <input
                    type="file"
                    accept=".dsx"
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
            {message && <p className="mt-2 text-sm text-gray-600 dark:text-gray-400">{message}</p>}
        </div>
    );
};

export default FileUpload;
