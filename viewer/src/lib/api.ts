// Name-DB API client. The viewer runs in the browser; by default it targets the service on
// VITE_API_PORT (default 15000) of the same host it was served from (CORS is open on the
// service). Override the whole base with VITE_API_BASE at build time if it lives elsewhere.

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

export type AssetRow = {
  asset_id: number;
  stash_id: string | null;
  path: string | null;
  basename: string | null;
  thumb_stash_id: string | null;
  resource_type: string;
  child_count: number;
  active: { name_id: number; name: string; source_level: string } | null;
};

export type AssetPage = { total: number; assets: AssetRow[] };
export type NamePage = { total: number; names: NameRow[] };
export type Scope = "gallery" | "folder" | "file";

// --- Enrichment ---

export type EnrichSource = { id: string; label: string; metered: boolean };

// Standalone performer fields shown/applied in the resolve modal (only populated ones appear).
export const PROFILE_FIELDS = [
  "name", "disambiguation", "aliases", "gender", "birthdate", "death_date", "ethnicity",
  "country", "hair_color", "eye_color", "height", "weight", "measurements", "fake_tits",
  "penis_length", "circumcised", "career_start", "career_end", "tattoos", "piercings",
  "details", "urls",
] as const;
export type ProfileField = (typeof PROFILE_FIELDS)[number];
export const LIST_FIELDS = new Set(["aliases", "urls", "images"]);

export type PerformerData = {
  source: string;
  source_entity_id: string;
  name: string;
  aliases?: string[];
  urls?: string[];
  images?: string[];
  score?: number | null;
  [k: string]: unknown; // the scalar bio fields
};
export type Candidate = {
  source: string;
  source_entity_id: string;
  score: number | null;
  data: PerformerData;
};
export type EnrichProfile = ({ field_sources: Record<string, string> } & Record<string, unknown>) | null;
export type CandidatesResp = {
  name_id: number;
  source: string;
  cached: boolean;
  error: string | null;
  candidates: Candidate[];
};
export type BatchResult = { name_id: number; error: string | null; count?: number; applied?: number };
export type FieldApply = { value: unknown; source: string };

type NameQuery = {
  status?: string;
  q?: string;
  sort?: string;
  order?: string;
  limit?: number;
  offset?: number;
};
type AssetQuery = {
  type: Scope;
  q?: string;
  sort?: string;
  order?: string;
  assigned?: string;
  limit?: number;
  offset?: number;
};

// `||` (not `??`): the Docker build bakes these as "" (empty), which is not nullish — so `??`
// would keep "" and (for the base) make fetches relative to the viewer's own origin. Empty must
// fall through to the derived default (<served-host>:VITE_API_PORT).
const API_PORT: string = import.meta.env.VITE_API_PORT || "15000";
const API_BASE: string =
  import.meta.env.VITE_API_BASE || `${location.protocol}//${location.hostname}:${API_PORT}`;

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
  listNames: (o: NameQuery = {}) => {
    const p = new URLSearchParams({
      sort: o.sort ?? "name",
      order: o.order ?? "asc",
      limit: String(o.limit ?? 100),
      offset: String(o.offset ?? 0),
    });
    if (o.status) p.set("status", o.status);
    if (o.q) p.set("q", o.q);
    return req<NamePage>(`/names?${p.toString()}`);
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
  listAssets: (o: AssetQuery) => {
    const p = new URLSearchParams({
      type: o.type,
      sort: o.sort ?? "path",
      order: o.order ?? "asc",
      limit: String(o.limit ?? 100),
      offset: String(o.offset ?? 0),
    });
    if (o.q) p.set("q", o.q);
    if (o.assigned) p.set("assigned", o.assigned);
    return req<AssetPage>(`/assets?${p.toString()}`);
  },
  activate: (assetId: number, nameId: number, sourceLevel: Scope) =>
    req<{ ok: boolean; affected: number }>(`/assets/${assetId}/activate`, {
      method: "POST",
      body: JSON.stringify({ name_id: nameId, source_level: sourceLevel }),
    }),
  deactivate: (assetId: number) =>
    req<{ ok: boolean; affected: number }>(`/assets/${assetId}/activation`, { method: "DELETE" }),

  // --- Enrichment ---
  enrichSources: () => req<{ sources: EnrichSource[] }>("/enrichment/sources"),
  // The search interface for one (name, source): cache-first on the server, uniform response
  // whether the data is cached or freshly fetched. `refresh` forces a live call.
  enrichSearch: (nameId: number, source: string, refresh = false) => {
    const p = new URLSearchParams({ name_id: String(nameId), source });
    if (refresh) p.set("refresh", "true");
    return req<CandidatesResp>(`/enrichment/search?${p.toString()}`);
  },
  enrichProfile: (nameId: number) =>
    req<{ profile: EnrichProfile }>(`/enrichment/profile?name_id=${nameId}`),
  enrichProfileStatus: (nameIds: number[]) =>
    req<{ profiles: Record<number, { fields: number; sources: string[] }> }>(
      `/enrichment/profiles?name_ids=${nameIds.join(",")}`,
    ),
  applyProfile: (nameId: number, fields: Record<string, FieldApply>) =>
    req<{ profile: EnrichProfile }>("/enrichment/profile", {
      method: "POST",
      body: JSON.stringify({ name_id: nameId, fields }),
    }),
  searchBatch: (nameIds: number[], source: string) =>
    req<{ source: string; results: BatchResult[] }>("/enrichment/search-batch", {
      method: "POST",
      body: JSON.stringify({ name_ids: nameIds, source }),
    }),
  updateBatch: (nameIds: number[], source: string, excludeFields: string[] = []) =>
    req<{ source: string; results: BatchResult[] }>("/enrichment/update-batch", {
      method: "POST",
      body: JSON.stringify({ name_ids: nameIds, source, exclude_fields: excludeFields }),
    }),
  enrichCredits: () =>
    req<Record<string, { spent: number; budget: number }>>("/enrichment/credits"),
};
