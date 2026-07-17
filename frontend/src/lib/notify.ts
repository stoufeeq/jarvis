/**
 * Toast helpers + a monkey-patch that makes every `toast.error(...)`
 * call site record its message to the persistent error log (surfaced
 * in the header's alert icon). Installed once via installErrorLogging()
 * from the dashboard layout so we don't have to edit 50 call sites.
 *
 * `errorToast(msg)` is available for explicit use when you want to be
 * clear the message will be recorded; existing `toast.error(msg)`
 * calls also work — the patch handles them.
 */
import toast from "react-hot-toast";
import type { Renderable, ValueOrFunction, ToastOptions } from "react-hot-toast";

import { useErrorLogStore } from "@/store/errorLog";

let installed = false;

export function installErrorLogging() {
  if (installed || typeof window === "undefined") return;
  installed = true;
  const original = toast.error.bind(toast);
  toast.error = ((message: ValueOrFunction<Renderable, unknown>, opts?: ToastOptions) => {
    // Only log if the message is a plain string. Renderable can also be
    // a JSX element or a function — those are rare here and don't
    // serialise cleanly into the log.
    if (typeof message === "string") {
      useErrorLogStore.getState().logError(message);
    }
    return original(message, opts);
  }) as typeof toast.error;
}

export function errorToast(message: string) {
  useErrorLogStore.getState().logError(message);
  return toast.error(message);
}

export function successToast(message: string) {
  return toast.success(message);
}

export function warningToast(message: string) {
  return toast(message, { icon: "⚠️" });
}
