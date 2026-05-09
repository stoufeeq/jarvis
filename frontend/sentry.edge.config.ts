// Sentry edge runtime (middleware) initialization.
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
