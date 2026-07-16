import { useHealth } from "@/features/health/api";

/**
 * Minimal status page proving the full stack is wired end-to-end:
 * React -> Vite dev proxy -> FastAPI -> Postgres connectivity check.
 *
 * This page is intentionally bare — the real Dashboard/Projects/Scans
 * pages from SRS §9 are built starting in later milestones once
 * auth, RBAC, and domain models exist.
 */
export function HealthPage() {
  const { data, isLoading, isError, error } = useHealth();

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-slate-950 text-slate-100">
      <h1 className="mb-6 text-3xl font-semibold tracking-tight">SPECTER_AI</h1>
      <p className="mb-8 text-slate-400">Autonomous Offensive Security Platform</p>

      <div className="w-full max-w-md rounded-lg border border-slate-800 bg-slate-900 p-6">
        <h2 className="mb-4 text-sm font-medium uppercase tracking-wide text-slate-500">
          System Health
        </h2>

        {isLoading && <p className="text-slate-400">Checking backend...</p>}

        {isError && (
          <p className="text-red-400">
            Could not reach the API: {error instanceof Error ? error.message : "unknown error"}
          </p>
        )}

        {data && (
          <div className="space-y-2">
            <StatusRow label="Overall status" value={data.status} />
            <StatusRow label="Environment" value={data.environment} />
            {data.components.map((component) => (
              <StatusRow
                key={component.name}
                label={component.name}
                value={component.healthy ? "healthy" : "unhealthy"}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function StatusRow({ label, value }: { label: string; value: string }) {
  const isGood = value === "ok" || value === "healthy";
  const isBad = value === "unhealthy" || value === "degraded";

  return (
    <div className="flex items-center justify-between border-b border-slate-800 py-2 last:border-none">
      <span className="capitalize text-slate-400">{label}</span>
      <span className={isGood ? "text-emerald-400" : isBad ? "text-red-400" : "text-slate-200"}>
        {value}
      </span>
    </div>
  );
}
