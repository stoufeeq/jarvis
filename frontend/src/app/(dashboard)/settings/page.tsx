"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { authApi } from "@/lib/api";
import { useAuthStore } from "@/store/auth";
import toast from "react-hot-toast";

export default function SettingsPage() {
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);

  // Profile form
  const [name, setName] = useState(user?.full_name ?? "");

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
