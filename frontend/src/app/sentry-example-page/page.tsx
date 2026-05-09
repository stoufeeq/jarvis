"use client";

/**
 * Sentry verification page — visit /sentry-example-page in production
 * to fire a test error and confirm the SDK is reporting to the
 * correct Next.js project.
 *
 * Safe to leave in the codebase; only fires errors when you click the button.
 */
export default function SentryExamplePage() {
  return (
    <div className="p-8 max-w-xl mx-auto space-y-4">
      <h1 className="text-2xl font-bold">Sentry test page</h1>
      <p className="text-sm text-muted-foreground">
        Click the button to fire an unhandled error. It should appear in the
        Next.js Sentry project within a few seconds.
      </p>
      <button
        onClick={() => {
          // Reference an undefined function — triggers a real runtime error
          // that the global error handler will catch and Sentry will capture.
          // @ts-expect-error — intentional reference to undefined function
          myUndefinedFunction();
        }}
        className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium"
      >
        Trigger test error
      </button>
    </div>
  );
}
