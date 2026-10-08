import axios from 'axios';

// Centralized axios client - baseURL is /api via Vite proxy (dev) or nginx/Caddy (prod)
// Interceptors add auth header (AuthContext) and global error handling.
const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
});

// Attach auth token if present (mock IdP for POC)
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('estate_token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (res) => res,
  (err) => {
    const detail = err.response?.data?.detail || err.message;
    // Centralized logging - would ship to LangSmith in prod
    console.error(`[api] ${err.config?.method?.toUpperCase()} ${err.config?.url} ->`, detail);
    return Promise.reject(err);
  }
);

export default api;
export { api };
