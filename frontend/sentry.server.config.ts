// Sentry server-side initialization (Next.js App Router server code).
// Same env-gated activation as client.
import * as Sentry from "@sentry/nextjs";

const dsn = process.env.SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.APP_ENV ?? "development",
    release: process.env.GIT_SHA?.slice(0, 8),
    tracesSampleRate: 0.1,
  });
}
