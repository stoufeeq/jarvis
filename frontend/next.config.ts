import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const nextConfig: NextConfig = {
  output: "standalone",
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8002",
  },
  devIndicators: false,
};

// Wrap with Sentry. The wrapper is a no-op when no DSN is configured,
// so it's safe to leave on for everyone.
// withSentryConfig auto-injects sentry.client.config.ts and the
// instrumentation.ts hook into the build.
export default withSentryConfig(nextConfig, {
  silent: true,
  // Suppress uploading source maps — needs SENTRY_AUTH_TOKEN which we
  // don't currently set. Errors will still report with minified stack
  // traces. Enable later by adding SENTRY_AUTH_TOKEN as a GitHub secret
  // and removing the line below.
  sourcemaps: { disable: true },
  hideSourceMaps: true,
  disableLogger: true,
});
