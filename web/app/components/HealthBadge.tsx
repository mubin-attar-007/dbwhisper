"use client";

import { useEffect, useState } from "react";
import { getHealth } from "@/src/lib/api";

type HealthState = "checking" | "online" | "offline";

export function HealthBadge() {
  const [state, setState] = useState<HealthState>("checking");
  const [title, setTitle] = useState<string>("Checking API health…");

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    getHealth(controller.signal)
      .then((health) => {
        if (!active) return;
        setState("online");
        setTitle(
          `online — ${health.message || "healthy"}${
            health.version ? ` (v${health.version})` : ""
          }`,
        );
      })
      .catch((err: unknown) => {
        if (!active) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        setState("offline");
        setTitle(
          err instanceof Error ? `offline — ${err.message}` : "offline",
        );
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, []);

  const config: Record<
    HealthState,
    { dot: string; text: string; label: string }
  > = {
    checking: {
      dot: "bg-slate-400 motion-safe:animate-pulse",
      text: "text-slate-300",
      label: "checking…",
    },
    online: {
      dot: "bg-emerald-400",
      text: "text-emerald-300",
      label: "online",
    },
    offline: {
      dot: "bg-rose-500",
      text: "text-rose-300",
      label: "offline",
    },
  };

  const c = config[state];

  return (
    <span
      title={title}
      role="status"
      aria-live="polite"
      className={`inline-flex items-center gap-2 rounded-full border border-slate-700 bg-slate-900/70 px-3 py-1 text-xs font-medium ${c.text}`}
    >
      <span className={`h-2 w-2 rounded-full ${c.dot}`} aria-hidden="true" />
      {c.label}
    </span>
  );
}
