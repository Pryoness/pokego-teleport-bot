import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { MonitorShell } from "@/components/dashboard/monitor-shell";

export const Route = createFileRoute("/monitor")({ component: MonitorPage });

function MonitorPage() {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // Set monitor cookie for read-only API access
    fetch("/api/monitor-login", { method: "POST" })
      .catch(() => {})
      .finally(() => setReady(true));
  }, []);

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <p className="text-sm text-muted">Loading monitor…</p>
      </div>
    );
  }
  return <MonitorShell />;
}
