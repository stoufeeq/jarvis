"use client";

import { useState, useRef, useEffect } from "react";
import { usePrivacyStore } from "@/store/privacy";
import { useAuthStore } from "@/store/auth";
import { authApi } from "@/lib/api";

export function PrivacyToggle() {
  const { isPrivate, setPrivate } = usePrivacyStore();
  const user = useAuthStore((s) => s.user);

  const [showModal, setShowModal] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (showModal) {
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [showModal]);

  const handleToggle = () => {
    if (isPrivate) {
      // Currently hidden → ask for password to reveal
      setShowModal(true);
    } else {
      // Currently visible → hide immediately
      setPrivate(true);
    }
  };

  const handleReveal = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!user?.email || !password) return;
    setLoading(true);
    setError("");
    try {
      await authApi.login(user.email, password);
      setPrivate(false);
      setShowModal(false);
      setPassword("");
    } catch {
      setError("Incorrect password.");
    } finally {
      setLoading(false);
    }
  };

  const handleCancel = () => {
    setShowModal(false);
    setPassword("");
    setError("");
  };

  return (
    <>
      <button
        onClick={handleToggle}
        title={isPrivate ? "Reveal values" : "Hide values"}
        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors border ${
          isPrivate
            ? "border-amber-500/40 text-amber-400 bg-amber-500/10 hover:bg-amber-500/20"
            : "border-border text-muted-foreground bg-secondary hover:bg-secondary/80"
        }`}
      >
        {isPrivate ? (
          <>
            <EyeOffIcon />
            <span className="hidden sm:inline">Show $</span>
          </>
        ) : (
          <>
            <EyeIcon />
            <span className="hidden sm:inline">Hide $</span>
          </>
        )}
      </button>

      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-card border border-border rounded-xl p-6 w-full max-w-sm mx-4 shadow-xl">
            <h2 className="text-base font-semibold mb-1">Confirm your identity</h2>
            <p className="text-sm text-muted-foreground mb-4">
              Enter your password to reveal financial values.
            </p>
            <form onSubmit={handleReveal} className="space-y-3">
              <input
                ref={inputRef}
                type="password"
                value={password}
                onChange={(e) => { setPassword(e.target.value); setError(""); }}
                placeholder="Password"
                className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
              {error && <p className="text-xs text-red-400">{error}</p>}
              <div className="flex gap-2 pt-1">
                <button
                  type="submit"
                  disabled={!password || loading}
                  className="flex-1 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
                >
                  {loading ? "Verifying…" : "Reveal"}
                </button>
                <button
                  type="button"
                  onClick={handleCancel}
                  className="flex-1 px-4 py-2 rounded-md bg-secondary text-sm font-medium hover:bg-secondary/80"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}

function EyeIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function EyeOffIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
      <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
      <line x1="1" y1="1" x2="23" y2="23" />
    </svg>
  );
}
