/**
 * Minimal typed fetch wrapper.
 *
 * Milestone 1 only needs a single GET call for the health endpoint.
 * As real resources are added (Projects, Targets, Scans...), this file
 * should stay a thin `request<T>()` helper — resource-specific calls
 * belong in `src/features/<resource>/api.ts`, each with its own React
 * Query hooks, not piled up in this shared client.
 */

const DEFAULT_HEADERS: HeadersInit = {
  "Content-Type": "application/json",
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function request<TResponse>(path: string, init?: RequestInit): Promise<TResponse> {
  const response = await fetch(path, {
    ...init,
    headers: { ...DEFAULT_HEADERS, ...init?.headers },
  });

  if (!response.ok) {
    throw new ApiError(response.status, `Request to ${path} failed with status ${response.status}`);
  }

  return (await response.json()) as TResponse;
}
