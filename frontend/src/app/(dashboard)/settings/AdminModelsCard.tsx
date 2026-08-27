"use client";

/**
 * Admin-only AI model selection card.
 *
 * Three tasks (news / briefing / chat) each pick a model from the
 * server's curated catalog. Changes persist to system_settings and
 * take effect on the next AI call — no restart.
 *
 * Rendered by the parent Settings page ONLY when user.is_admin is true.
 * The backend also enforces via require_admin — this is UI polish, not
 * the security boundary.
 */

import { useState, useEffect, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { settingsApi, type ModelCatalogEntry, type ModelSettings, type ModelSettingsUpdate } from "@/lib/api";

type TaskKey = "news_model" | "briefing_model" | "chat_model";

const TASKS: Array<{ key: TaskKey; label: string; hint: string }> = [
  {
    key: "news_model",
    label: "News sentiment scoring",
    hint: "High volume (100+ calls/day, batched). Prefer cheap/fast models.",
  },
  {
    key: "briefing_model",
    label: "Daily briefing",
    hint: "Runs 1×/day, benefits from stronger reasoning.",
  },
  {
    key: "chat_model",
    label: "AI advisor chat",
    hint: "Interactive, low volume. Reasoning + response quality both matter.",
  },
];

const TIER_BADGE: Record<ModelCatalogEntry["tier"], string> = {
  free: "bg-emerald-500/20 text-emerald-400",
  cheap: "bg-blue-500/20 text-blue-400",
  premium: "bg-amber-500/20 text-amber-400",
};

export function AdminModelsCard() {
  const qc = useQueryClient();

  const { data: catalog, isLoading: catalogLoading } = useQuery({
    queryKey: ["settings", "catalog"],
    queryFn: () => settingsApi.catalog().then((r) => r.data),
    staleTime: 60 * 60 * 1000, // catalog is basically static; refresh hourly at most
  });
  const { data: models, isLoading: modelsLoading } = useQuery({
    queryKey: ["settings", "models"],
    queryFn: () => settingsApi.models().then((r) => r.data),
  });

  // Local edit buffer — flushed on Save. Keyed by TaskKey.
  const [draft, setDraft] = useState<Partial<Record<TaskKey, string>>>({});

  useEffect(() => {
    // Whenever a fresh server state arrives, discard local edits.
    if (models) setDraft({});
  }, [models]);

  const catalogById = useMemo(() => {
    const m = new Map<string, ModelCatalogEntry>();
    (catalog ?? []).forEach((e) => m.set(e.id, e));
    return m;
  }, [catalog]);

  const mutation = useMutation({
    mutationFn: (payload: ModelSettingsUpdate) => settingsApi.updateModels(payload).then((r) => r.data),
    onSuccess: (fresh) => {
      qc.setQueryData(["settings", "models"], fresh);
      setDraft({});
      toast.success("Model settings saved");
    },
    onError: (err: unknown) => {
      // Backend rejects unusable model choices with 422 + a detail message
      // (e.g. "Model 'deepseek/…:free' rejected by provider: HTTP 404 …").
      // Surface that instead of a generic "failed" toast — the user needs
      // to see the provider's actual complaint to pick a working model.
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(detail ?? "Failed to save model settings", { duration: 10_000 });
    },
  });

  if (catalogLoading || modelsLoading) {
    return (
      <section className="rounded-xl border border-border bg-card p-6">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
          AI Models (admin)
        </h2>
        <p className="mt-4 text-sm text-muted-foreground">Loading model catalog…</p>
      </section>
    );
  }

  if (!catalog || !models) {
    return (
      <section className="rounded-xl border border-border bg-card p-6">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
          AI Models (admin)
        </h2>
        <p className="mt-4 text-sm text-red-400">
          Failed to load. Check backend logs.
        </p>
      </section>
    );
  }

  function currentValue(key: TaskKey): string {
    return draft[key] ?? models![key];
  }

  function sourceLabel(key: TaskKey): "override" | "env" {
    const src = key === "news_model" ? "news_model_source"
              : key === "briefing_model" ? "briefing_model_source"
              : "chat_model_source";
    return models![src];
  }

  const dirty = Object.keys(draft).length > 0;

  function handleSave() {
    // Only send fields the admin actually changed.
    const payload: ModelSettingsUpdate = {};
    (Object.keys(draft) as TaskKey[]).forEach((k) => {
      payload[k] = draft[k]!;
    });
    mutation.mutate(payload);
  }

  return (
    <section className="rounded-xl border border-border bg-card p-6 space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
          AI Models (admin)
        </h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Pick which model serves each AI task. Changes are global — every user&apos;s
          calls will use the model you choose here, billed to the OpenRouter / Gemini
          account whose key is on the server.
        </p>
      </div>

      {TASKS.map((task) => {
        const value = currentValue(task.key);
        const currentEntry = catalogById.get(value);
        const isDraft = draft[task.key] !== undefined;
        const src = sourceLabel(task.key);

        return (
          <div key={task.key} className="space-y-1.5">
            <div className="flex items-baseline justify-between gap-2">
              <label className="text-sm font-medium">{task.label}</label>
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {isDraft ? "unsaved" : src === "override" ? "custom" : "default"}
              </span>
            </div>
            <p className="text-xs text-muted-foreground">{task.hint}</p>
            <select
              value={value}
              onChange={(e) => setDraft({ ...draft, [task.key]: e.target.value })}
              className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {/* Group by tier for scan-ability */}
              {(["free", "cheap", "premium"] as const).map((tier) => {
                const tierModels = catalog.filter((m) => m.tier === tier);
                if (tierModels.length === 0) return null;
                const tierLabel = tier === "free" ? "Free" : tier === "cheap" ? "Cheap" : "Premium";
                return (
                  <optgroup key={tier} label={tierLabel}>
                    {tierModels.map((m) => {
                      // Provider prefix so admin knows which key/billing account
                      // will foot the bill: [Gemini] vs [OpenRouter] on every row.
                      const providerTag = m.provider === "gemini" ? "[Gemini]" : "[OpenRouter]";
                      return (
                        <option key={m.id} value={m.id} disabled={!m.available}>
                          {providerTag} {m.label} — {m.price_hint}{m.available ? "" : "  (API key missing)"}
                        </option>
                      );
                    })}
                  </optgroup>
                );
              })}
            </select>
            {currentEntry && (
              <p className="text-[11px] text-muted-foreground/80 leading-snug">
                <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] mr-1.5 ${TIER_BADGE[currentEntry.tier]}`}>
                  {currentEntry.tier}
                </span>
                <span
                  className={`inline-block px-1.5 py-0.5 rounded text-[10px] mr-1.5 ${
                    currentEntry.provider === "gemini"
                      ? "bg-blue-500/20 text-blue-300"
                      : "bg-purple-500/20 text-purple-300"
                  }`}
                  title={
                    currentEntry.provider === "gemini"
                      ? "Billed to your Gemini API credits (GEMINI_API_KEY on server)"
                      : "Billed to your OpenRouter credits (OPENROUTER_API_KEY on server)"
                  }
                >
                  via {currentEntry.provider}
                </span>
                {currentEntry.notes}
              </p>
            )}
          </div>
        );
      })}

      <div className="flex items-center gap-3 pt-2">
        <button
          onClick={handleSave}
          disabled={!dirty || mutation.isPending}
          className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
        >
          {mutation.isPending ? "Saving…" : "Save changes"}
        </button>
        {dirty && (
          <button
            onClick={() => setDraft({})}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            Discard
          </button>
        )}
      </div>
    </section>
  );
}
