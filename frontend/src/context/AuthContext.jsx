import React, { createContext, useContext, useState, useEffect } from 'react';
import { api } from '../lib/api';

// Mock IdP for POC - in prod this would be Azure AD / Entra via MSAL
// Provides actor_id + actor_email_hash for ledger audit (P0 fix)
const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(() => {
    try {
      const raw = localStorage.getItem('estate_user');
      return raw ? JSON.parse(raw) : null;
    } catch { return null; }
  });

  useEffect(() => {
    if (user) localStorage.setItem('estate_user', JSON.stringify(user));
    else localStorage.removeItem('estate_user');
  }, [user]);

  const login = (email, name) => {
    const u = { email, name, id: `mock-${email}`, verified: true };
    setUser(u);
    localStorage.setItem('estate_token', `mock-${email}`);
    return u;
  };
  const logout = () => {
    setUser(null);
    localStorage.removeItem('estate_token');
  };
  const isAuthenticated = !!user;

  return (
    <AuthContext.Provider value={{ user, login, logout, isAuthenticated }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be inside AuthProvider');
  return ctx;
};
