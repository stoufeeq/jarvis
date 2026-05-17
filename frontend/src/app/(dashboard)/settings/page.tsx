"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { authApi } from "@/lib/api";
import { useAuthStore } from "@/store/auth";
import { useSettingsStore } from "@/store/settings";
import { useThemeStore, THEMES } from "@/store/theme";
import toast from "react-hot-toast";

export default function SettingsPage() {
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const theme = useThemeStore((s) => s.theme);
  const setTheme = useThemeStore((s) => s.setTheme);
  const halalMode = useSettingsStore((s) => s.halalMode);
  const setHalalMode = useSettingsStore((s) => s.setHalalMode);
  const halalOnlyFilter = useSettingsStore((s) => s.halalOnlyFilter);
  const setHalalOnlyFilter = useSettingsStore((s) => s.setHalalOnlyFilter);

  // Profile form
  const [name, setName] = useState(user?.full_name ?? "");

  // Telegram form
  const [telegramChatId, setTelegramChatId] = useState(user?.telegram_chat_id ?? "");

  // Password form
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const profileMutation = useMutation({
    mutationFn: () => authApi.updateMe({ full_name: name }),
    onSuccess: (res) => {
      setUser(res.data);
      toast.success("Name updated");
    },
    onError: () => toast.error("Failed to update name"),
  });

  const testEmailMutation = useMutation({
    mutationFn: () => authApi.testEmail(),
    onSuccess: (res) => {
      if (res.data.ok) toast.success(res.data.detail);
      else toast.error(res.data.detail);
    },
    onError: () => toast.error("Test email request failed"),
  });

  const telegramMutation = useMutation({
    mutationFn: () => authApi.updateMe({ telegram_chat_id: telegramChatId.trim() }),
    onSuccess: (res) => {
      setUser(res.data);
      toast.success(telegramChatId.trim() ? "Telegram chat ID saved" : "Telegram chat ID cleared");
    },
    onError: () => toast.error("Failed to save Telegram chat ID"),
  });

  const testTelegramMutation = useMutation({
    mutationFn: () => authApi.testTelegram(),
    onSuccess: (res) => {
      if (res.data.ok) toast.success(res.data.detail);
      else toast.error(res.data.detail);
    },
    onError: () => toast.error("Test Telegram request failed"),
  });

  const passwordMutation = useMutation({
    mutationFn: () =>
      authApi.updateMe({ current_password: currentPassword, password: newPassword }),
    onSuccess: () => {
      toast.success("Password changed");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    },
    onError: (err: unknown) => {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        "Failed to change password";
      toast.error(msg);
    },
  });

  function handlePasswordSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (newPassword !== confirmPassword) {
      toast.error("New passwords do not match");
      return;
    }
    if (newPassword.length < 8) {
      toast.error("New password must be at least 8 characters");
      return;
    }
    passwordMutation.mutate();
  }

  return (
    <div className="max-w-lg space-y-8">
      <h1 className="text-2xl font-bold">Settings</h1>

      {/* Appearance */}
      <section className="rounded-xl border border-border bg-card p-6 space-y-4">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
          Appearance
        </h2>
        <p className="text-xs text-muted-foreground">Choose a colour theme. Light themes automatically switch to dark text for readability.</p>
        <div className="grid grid-cols-4 gap-3">
          {THEMES.map((t) => (
            <button
              key={t.key}
              onClick={() => setTheme(t.key)}
              className={`flex flex-col items-center gap-1.5 rounded-lg p-2 border-2 transition-colors ${
                theme === t.key ? "border-primary" : "border-border hover:border-muted-foreground"
              }`}
              title={t.label}
            >
              {/* Swatch */}
              <span
                className="w-8 h-8 rounded-full border border-black/10 flex items-center justify-center text-xs font-bold"
                style={{
                  background: t.preview,
                  color: t.dark ? "#f1f5f9" : "#1e293b",
                }}
              >
                {theme === t.key ? "✓" : ""}
              </span>
              <span className="text-xs text-muted-foreground leading-tight text-center">{t.label}</span>
            </button>
          ))}
        </div>
      </section>

      {/* Investing Preferences */}
      <section className="rounded-xl border border-border bg-card p-6 space-y-4">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
          Investing Preferences
        </h2>
        <p className="text-xs text-muted-foreground">
          Sharia compliance screening (Tier 1 — AAOIFI 33% ratios, curated halal-ETF whitelist).
          Verdicts: <strong>Halal</strong> / <strong>Not halal</strong> / <strong>Unknown</strong>{" "}
          (most ETFs and tickers with missing financials).
        </p>

        <label className="flex items-start justify-between gap-4 cursor-pointer">
          <div>
            <p className="text-sm font-medium">Show halal status badges</p>
            <p className="text-xs text-muted-foreground">
              Tiny ✓ / ✗ / ? icon next to ticker names. Tooltip explains the verdict.
            </p>
          </div>
          <input
            type="checkbox"
            checked={halalMode}
            onChange={(e) => setHalalMode(e.target.checked)}
            className="mt-1 h-4 w-4 shrink-0 accent-primary"
          />
        </label>

        <label className="flex items-start justify-between gap-4 cursor-pointer">
          <div>
            <p className="text-sm font-medium">Hide non-compliant on Watchlist</p>
            <p className="text-xs text-muted-foreground">
              Filters out tickers verdicted <strong>Not halal</strong>. Unknown and compliant entries still show.
            </p>
          </div>
          <input
            type="checkbox"
            checked={halalOnlyFilter}
            onChange={(e) => setHalalOnlyFilter(e.target.checked)}
            className="mt-1 h-4 w-4 shrink-0 accent-primary"
          />
        </label>
      </section>

      {/* Profile */}
      <section className="rounded-xl border border-border bg-card p-6 space-y-4">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
          Profile
        </h2>
        <div>
          <label className="block text-xs text-muted-foreground mb-1">Email</label>
          <p className="text-sm text-foreground">{user?.email}</p>
        </div>
        <div>
          <label className="block text-xs text-muted-foreground mb-1">Display name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Your name"
            className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <button
          onClick={() => profileMutation.mutate()}
          disabled={profileMutation.isPending || name === (user?.full_name ?? "")}
          className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
        >
          {profileMutation.isPending ? "Saving…" : "Save name"}
        </button>
      </section>

      {/* Email notifications */}
      <section className="rounded-xl border border-border bg-card p-6 space-y-3">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
          Email Notifications
        </h2>
        <p className="text-sm text-muted-foreground">
          When you create an alert and select <strong>In-app + Email</strong>, Jarvis will
          send an email to <strong>{user?.email}</strong> when the condition triggers.
        </p>
        <div className="rounded-md bg-secondary/50 border border-border px-4 py-3 text-xs text-muted-foreground space-y-1">
          <p className="font-medium text-foreground">Setup required on the server</p>
          <p>Add these to your <code className="bg-black/20 px-1 rounded">.env</code> file and restart the backend:</p>
          <pre className="mt-2 text-xs leading-relaxed overflow-x-auto">{`SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=<app-password>
ALERT_FROM_EMAIL=you@gmail.com`}</pre>
          <p className="mt-1">For Gmail: enable 2-Step Verification → Google Account → Security → App Passwords → generate one.</p>
        </div>
        <button
          onClick={() => testEmailMutation.mutate()}
          disabled={testEmailMutation.isPending}
          className="px-4 py-2 rounded-md bg-secondary text-sm font-medium hover:bg-secondary/80 disabled:opacity-50"
        >
          {testEmailMutation.isPending ? "Sending…" : "Send test email"}
        </button>
      </section>

      {/* Telegram notifications */}
      <section className="rounded-xl border border-border bg-card p-6 space-y-3">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
          Telegram Notifications
        </h2>
        <p className="text-sm text-muted-foreground">
          Push triggered alerts (with <strong>Telegram</strong> in their channels) and the
          daily briefing summary to your Telegram chat with the Jarvis bot.
        </p>
        <div className="rounded-md bg-secondary/50 border border-border px-4 py-3 text-xs text-muted-foreground space-y-1.5">
          <p className="font-medium text-foreground">How to set up</p>
          <ol className="list-decimal list-inside space-y-1">
            <li>Open Telegram and search for the Jarvis bot (the one whose token is set on the server).</li>
            <li>Send <code className="bg-black/20 px-1 rounded">/start</code> to begin a conversation.</li>
            <li>
              Visit{" "}
              <code className="bg-black/20 px-1 rounded">
                https://api.telegram.org/bot&lt;TOKEN&gt;/getUpdates
              </code>{" "}
              in your browser to find your numeric chat ID
              (or use a helper bot like <code>@userinfobot</code>).
            </li>
            <li>Paste the chat ID below and save, then send a test message.</li>
          </ol>
        </div>
        <div>
          <label className="block text-xs text-muted-foreground mb-1">Telegram chat ID</label>
          <input
            type="text"
            value={telegramChatId}
            onChange={(e) => setTelegramChatId(e.target.value)}
            placeholder="e.g. 123456789"
            className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => telegramMutation.mutate()}
            disabled={telegramMutation.isPending || telegramChatId === (user?.telegram_chat_id ?? "")}
            className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
          >
            {telegramMutation.isPending ? "Saving…" : "Save chat ID"}
          </button>
          <button
            onClick={() => testTelegramMutation.mutate()}
            disabled={testTelegramMutation.isPending || !user?.telegram_chat_id}
            className="px-4 py-2 rounded-md bg-secondary text-sm font-medium hover:bg-secondary/80 disabled:opacity-50"
          >
            {testTelegramMutation.isPending ? "Sending…" : "Send test message"}
          </button>
        </div>
      </section>

      {/* Change password */}
      <section className="rounded-xl border border-border bg-card p-6 space-y-4">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
          Change Password
        </h2>
        <form onSubmit={handlePasswordSubmit} className="space-y-4">
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Current password</label>
            <input
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              autoComplete="current-password"
              className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">New password</label>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
              className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Confirm new password</label>
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
              className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <button
            type="submit"
            disabled={passwordMutation.isPending || !currentPassword || !newPassword || !confirmPassword}
            className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
          >
            {passwordMutation.isPending ? "Updating…" : "Change password"}
          </button>
        </form>
      </section>
    </div>
  );
}
