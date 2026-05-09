// Next.js 13+ instrumentation hook — runs once when the server starts.
// Sentry v8 uses this instead of the older sentry.server.config.ts pattern
// for server-side init.
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("./sentry.server.config");
  }

  if (process.env.NEXT_RUNTIME === "edge") {
    await import("./sentry.edge.config");
  }
}
