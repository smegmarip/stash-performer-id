// Name-DB API client. The viewer runs in the browser; by default it targets the service on
// port 15000 of the same host it was served from (CORS is open on the service). Override with
// VITE_API_BASE at build time if the service lives elsewhere.

export type NameRow = {
  id: number;
  name: string;
  disambiguation: string;
  valid: boolean; // valid by default; triage invalidates the junk
  edited_at: string | null;
};

export type Summary = {
  assets: number;
  relationships: number;
  candidates: number;
  candidates_by_source: Record<string, number>;
  distinct_names: number;
};

const API_BASE: string =
  import.meta.env.VITE_API_BASE ?? `${location.protocol}//${location.hostname}:15000`;

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

type NamePatch = Partial<Pick<NameRow, "valid" | "name" | "disambiguation">>;

export const api = {
  base: API_BASE,
  summary: () => req<Summary>("/audit/summary"),
  listNames: (status?: string, limit = 500, offset = 0) => {
    const q = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (status) q.set("status", status);
    return req<NameRow[]>(`/names?${q.toString()}`);
  },
  updateName: (id: number, patch: NamePatch) =>
    req<NameRow>(`/names/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  setValidBulk: (ids: number[], valid: boolean) =>
    req<{ updated: number }>("/names/set-valid", {
      method: "POST",
      body: JSON.stringify({ ids, valid }),
    }),
  addName: (name: string, disambiguation = "") =>
    req<NameRow>("/names", {
      method: "POST",
      body: JSON.stringify({ name, disambiguation }),
    }),
  harvestGalleries: () =>
    req<{ galleries: number; new_names: number }>("/harvest/galleries", { method: "POST" }),
};
