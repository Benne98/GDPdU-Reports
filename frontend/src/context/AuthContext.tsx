/**
 * AuthContext — JWT bearer auth for the GDPdU frontend.
 *
 * Token is stored in localStorage (prototype only).
 * TODO(security): Replace localStorage with httpOnly-cookie + CSP in production.
 *
 * Provides: user, token, isAdmin, login(), logout()
 */

import { createContext, useCallback, useContext, useEffect, useState } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AuthUser {
  user_id: number;
  email: string;
  display_name: string;
  is_admin: boolean;
  /** Role page_keys returned by /api/v1/auth/me (optional — absent = no restriction, fail-open). */
  page_keys?: string[];
}

interface AuthContextValue {
  user: AuthUser | null;
  token: string | null;
  isAdmin: boolean;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const AuthContext = createContext<AuthContextValue | null>(null);

const TOKEN_KEY = "gdpdu_access_token";
const USER_KEY = "gdpdu_user";

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(() =>
    localStorage.getItem(TOKEN_KEY)
  );
  const [user, setUser] = useState<AuthUser | null>(() => {
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as AuthUser;
    } catch {
      return null;
    }
  });
  const [loading, setLoading] = useState<boolean>(Boolean(token && !user));

  // On first mount, if we have a stored token but no user, validate via /me.
  useEffect(() => {
    if (!token) {
      setLoading(false);
      return;
    }
    if (user) {
      setLoading(false);
      return;
    }
    // Validate token and restore user
    setLoading(true);
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 15_000);
    fetch("/api/v1/auth/me", {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    })
      .then(async (res) => {
        if (res.ok) {
          const u = (await res.json()) as AuthUser;
          setUser(u);
          localStorage.setItem(USER_KEY, JSON.stringify(u));
        } else {
          // Token invalid — clear it
          localStorage.removeItem(TOKEN_KEY);
          localStorage.removeItem(USER_KEY);
          setToken(null);
          setUser(null);
        }
      })
      .catch(() => {
        // Network error / timeout — keep token in hope backend will come back, but unblock UI
      })
      .finally(() => {
        clearTimeout(timer);
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    let res: Response;
    try {
      res = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
    } catch {
      throw new Error(
        "Cannot reach the API. Start the test backend (port 8009) and open http://localhost:5175."
      );
    }
    if (res.status === 401) {
      throw new Error("Invalid email address or password.");
    }
    if (!res.ok) {
      throw new Error("Sign-in failed. Please try again later.");
    }
    const data = (await res.json()) as {
      access_token: string;
      token_type: string;
      user: AuthUser;
    };
    localStorage.setItem(TOKEN_KEY, data.access_token);
    localStorage.setItem(USER_KEY, JSON.stringify(data.user));
    setToken(data.access_token);
    setUser(data.user);
  }, []);

  const logout = useCallback(async () => {
    if (token) {
      try {
        await fetch("/api/v1/auth/logout", {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        });
      } catch {
        // ignore network errors — we clear locally regardless
      }
    }
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
  }, [token]);

  const isAdmin = user?.is_admin ?? false;

  return (
    <AuthContext.Provider value={{ user, token, isAdmin, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
