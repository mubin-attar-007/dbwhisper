"use client";

import { useState } from "react";
import { ApiError } from "@/src/lib/api";
import { useAuth } from "./AuthProvider";

export function AuthBar() {
  const { user, loading, login, register, logout } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (loading) {
    return (
      <div className="border-b border-slate-800 px-4 py-2 text-xs text-slate-500">
        Checking session…
      </div>
    );
  }

  if (user) {
    return (
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-2 text-sm">
        <span className="text-slate-400">
          Signed in as <span className="text-slate-200">{user.email}</span>
        </span>
        <button
          onClick={() => void logout()}
          className="rounded-md border border-slate-700 px-3 py-1 text-xs text-slate-300 transition hover:bg-slate-800"
        >
          Sign out
        </button>
      </div>
    );
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") await login(email, password);
      else await register(email, password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Authentication failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="border-b border-slate-800 px-4 py-2">
      <form onSubmit={submit} className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-slate-400">
          Querying the public demo. {mode === "login" ? "Sign in" : "Create an account"} to use
          your own databases.
        </span>
        <input
          type="email"
          required
          placeholder="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100"
        />
        <input
          type="password"
          required
          minLength={8}
          placeholder="password (8+ chars)"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100"
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-md bg-indigo-600 px-3 py-1 text-white transition hover:bg-indigo-500 disabled:opacity-50"
        >
          {busy ? "…" : mode === "login" ? "Sign in" : "Sign up"}
        </button>
        <button
          type="button"
          onClick={() => {
            setMode(mode === "login" ? "register" : "login");
            setError(null);
          }}
          className="text-xs text-indigo-400 hover:underline"
        >
          {mode === "login" ? "Need an account?" : "Have an account?"}
        </button>
        {error && <span className="text-xs text-red-400">{error}</span>}
      </form>
    </div>
  );
}
