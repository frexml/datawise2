import React, { useState } from 'react';
import { useAuth } from '../context/AuthContext';

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');

  const handleLogin = (e) => {
    e.preventDefault();
    const userEmail = email || 'user@company.com';
    const userName = name || userEmail.split('@')[0];
    login(userEmail, userName);
  };

  return (
    <div className="min-h-screen flex bg-gradient-to-br from-indigo-50 via-white to-violet-50 dark:from-gray-900 dark:via-gray-900 dark:to-gray-950">
      {/* Left - Welcome */}
      <div className="hidden lg:flex lg:w-[52%] flex-col justify-between p-12">
        <div>
          <div className="flex items-center gap-3">
            <img src="/logo.png" alt="ML arteka" className="h-9 w-9 rounded-lg ring-1 ring-gray-200" />
            <span className="text-lg font-semibold text-gray-900 dark:text-white">Data<span className="text-orange-600">Wise</span></span>
          </div>
          <div className="mt-16 max-w-md">
            <h1 className="text-4xl font-bold text-gray-900 dark:text-white leading-tight">
              Welcome to<br />
              <span className="text-indigo-600">DataWise</span>
            </h1>
            <p className="mt-4 text-gray-600 dark:text-gray-300 leading-relaxed">
              Point at any on-prem estate and watch it map itself. Live Todo ticks as we discover systems, parse views, and build your knowledge graph.
            </p>
            <div className="mt-8 grid grid-cols-1 gap-3">
              <div className="flex items-center gap-3 p-3 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm">
                <div className="h-8 w-8 rounded-lg bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center text-emerald-600">✦</div>
                <div><div className="text-sm font-medium text-gray-900 dark:text-white">Live system survey</div><div className="text-xs text-gray-500">8 steps, ticking in real time</div></div>
              </div>
              <div className="flex items-center gap-3 p-3 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm">
                <div className="h-8 w-8 rounded-lg bg-indigo-100 dark:bg-indigo-900/30 flex items-center justify-center text-indigo-600">◈</div>
                <div><div className="text-sm font-medium text-gray-900 dark:text-white">Banking + Telecom</div><div className="text-xs text-gray-500">Same spine, two estates - pick your wedge</div></div>
              </div>
              <div className="flex items-center gap-3 p-3 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm">
                <div className="h-8 w-8 rounded-lg bg-violet-100 dark:bg-violet-900/30 flex items-center justify-center text-violet-600">⬣</div>
                <div><div className="text-sm font-medium text-gray-900 dark:text-white">Knowledge graph + ledger</div><div className="text-xs text-gray-500">Every AI action is hash-chained and gated</div></div>
              </div>
            </div>
            <div className="mt-8 text-xs text-gray-400">Trusted by data teams to map, govern, and migrate critical estates.</div>
          </div>
        </div>
        <div className="text-xs text-gray-400">Built by ML arteka · Toronto · 2026-10-08</div>
      </div>

      {/* Right - Login */}
      <div className="flex-1 flex items-center justify-center p-6 sm:p-8">
        <div className="w-full max-w-md">
          <div className="lg:hidden flex items-center gap-2 mb-8">
            <img src="/logo.png" alt="ML arteka" className="h-8 w-8 rounded-md" />
            <span className="font-semibold text-gray-900 dark:text-white">Data<span className="text-orange-600">Wise</span></span>
          </div>

          <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-lg border border-gray-200 dark:border-gray-700 p-8">
            <h2 className="text-xl font-semibold text-gray-900 dark:text-white">Sign in to DataWise</h2>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">Welcome back — please sign in to continue to your estates.</p>

            <form onSubmit={handleLogin} className="space-y-4 mt-6">
              <div>
                <label className="text-xs font-medium text-gray-700 dark:text-gray-300">Work email</label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@company.com"
                  className="mt-1 w-full px-3 py-2.5 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-sm text-gray-900 dark:text-white placeholder:text-gray-400 focus:outline-none focus:ring-4 focus:ring-indigo-100 dark:focus:ring-indigo-900/30 focus:border-indigo-300 dark:focus:border-indigo-700"
                  required
                />
              </div>
              <div>
                <label className="text-xs font-medium text-gray-700 dark:text-gray-300">Password</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="mt-1 w-full px-3 py-2.5 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-sm text-gray-900 dark:text-white placeholder:text-gray-400 focus:outline-none focus:ring-4 focus:ring-indigo-100 dark:focus:ring-indigo-900/30 focus:border-indigo-300 dark:focus:border-indigo-700"
                  required
                />
              </div>
              <div className="flex items-center justify-between text-xs">
                <label className="flex items-center gap-2 text-gray-600 dark:text-gray-400">
                  <input type="checkbox" className="rounded border-gray-300" /> Remember me
                </label>
                <a href="#" className="text-indigo-600 hover:text-indigo-700 font-medium">Forgot password?</a>
              </div>
              <button type="submit" className="w-full py-3 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-medium shadow-sm transition">
                Sign in
              </button>
              <div className="text-center">
                <span className="text-xs text-gray-500">Don't have an account? </span>
                <a href="#" className="text-xs text-indigo-600 hover:text-indigo-700 font-medium">Contact your administrator</a>
              </div>
            </form>
          </div>

          <div className="mt-4 text-center text-xs text-gray-400">
            Protected by enterprise SSO · Encrypted session
          </div>
        </div>
      </div>
    </div>
  );
}
