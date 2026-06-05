"use client";

import Image from "next/image";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { authApi } from "@/lib/api";

export default function ForgotPasswordPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await authApi.forgotPassword(email);
      // Always show the generic confirmation — we never reveal whether
      // the email is registered.
      setSent(true);
    } catch {
      // Same outcome on error — the backend may have surfaced 500 due to
      // SMTP, but for the user-facing flow we keep the response uniform.
      setSent(true);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-background">
      <div className="w-full max-w-md p-8 rounded-xl border border-border bg-card space-y-6">
        <div className="flex flex-col items-center gap-3">
          <Image
            src="/logo.png"
            alt="Jarvis"
            width={180}
            height={50}
            className="object-contain"
            priority
          />
          <p className="text-muted-foreground text-sm">Reset your password</p>
        </div>

        {sent ? (
          <div className="space-y-4 text-sm">
            <p className="text-foreground">
              If <strong>{email}</strong> is a registered account, we&apos;ve sent it
              a 6-digit reset code. The code expires in 15 minutes.
            </p>
            <p className="text-muted-foreground">
              Enter the code on the next page along with your new password.
            </p>
            <div className="flex gap-2 pt-2">
              <button
                type="button"
                onClick={() => router.push(`/reset-password?email=${encodeURIComponent(email)}`)}
                className="flex-1 py-2 rounded-md bg-primary text-primary-foreground font-medium hover:opacity-90"
              >
                Enter code
              </button>
              <button
                type="button"
                onClick={() => router.push("/login")}
                className="flex-1 py-2 rounded-md bg-secondary text-foreground font-medium hover:bg-secondary/80"
              >
                Back to sign in
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium mb-1">Email</label>
              <input
                type="email"
                required
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full px-3 py-2 rounded-md border border-border bg-input text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
            <button
              type="submit"
              disabled={loading || !email}
              className="w-full py-2 rounded-md bg-primary text-primary-foreground font-medium hover:opacity-90 disabled:opacity-50 transition"
            >
              {loading ? "Sending…" : "Send reset code"}
            </button>
            <p className="text-sm text-muted-foreground text-center">
              <a href="/login" className="text-foreground underline underline-offset-2">
                Back to sign in
              </a>
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
