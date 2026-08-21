// Typed client for the P6 review API. Same-origin when served by FastAPI; the Vite dev
// server proxies /api to :8000 (see vite.config.ts), so these paths work in both modes.

export interface Source {
  doc_id: string;
  source_type: string;
  location: string;
  quote: string;
  start: number | null;
  end: number | null;
}

export interface Quality {
  ambiguity_flags: string[];
  ambiguity_explanation: string | null;
  suggested_rewrite: string | null;
  testable: boolean | null;
  completeness_notes: string | null;
  score: number | null;
}

export interface Triage {
  level: "attention" | "review" | "routine";
  score: number;
  reasons: string[];
}

export interface Requirement {
  id: string;
  statement: string;
  rtype: string;
  feature: string | null;
  nfr_category: string | null;
  priority: string | null;
  status: string;
  inferred: boolean;
  confidence: number;
  rationale: string;
  quality: Quality;
  conflicts_with: string[];
  owner: string;
  triage: Triage;
  sources: Source[];
}

export interface Decision {
  id: string;
  kind: string;
  question: string;
  recommended: string;
  options: string[];
  evidence: string[];
  affected: string[];
  owner: string;
  tier: string;
  reason: string;
  resolved?: boolean;   // persisted server-side (survives reload)
}
export interface DecisionsResponse {
  decisions: Decision[];
  open?: number;
  resolved?: number;
  summary: { total: number; by_owner: Record<string, number>; by_tier: Record<string, number> };
}
export interface SpotCheck { auto_approved: number; sample_rate: number; sample: Requirement[]; }
export interface Calibration {
  bands: Record<string, { accepted: number; decided: number; acceptance_rate: number | null }>;
  suggested_auto_approve_bar: number;
}

export interface Listing {
  project_id: string;
  counts: Record<string, number>;
  triage: Record<string, number>;
  requirements: Requirement[];
}

export interface Gate {
  ready: boolean;
  reason: string;
  counts: Record<string, number>;
}

export type ReviewAction = "accept" | "edit" | "reject";

async function unwrap<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((body as { detail?: string }).detail ?? res.statusText);
  }
  return body as T;
}

export const getRequirements = (pid: string): Promise<Listing> =>
  fetch(`/api/projects/${pid}/requirements`).then(unwrap<Listing>);

export const getGate = (pid: string): Promise<Gate> =>
  fetch(`/api/projects/${pid}/gate`).then(unwrap<Gate>);

export const getDecisions = (pid: string): Promise<DecisionsResponse> =>
  fetch(`/api/projects/${pid}/decisions`).then(unwrap<DecisionsResponse>);

export const getSpotCheck = (pid: string, rate = 0.05): Promise<SpotCheck> =>
  fetch(`/api/projects/${pid}/spot-check?rate=${rate}`).then(unwrap<SpotCheck>);

export const getCalibration = (pid: string): Promise<Calibration> =>
  fetch(`/api/projects/${pid}/calibration`).then(unwrap<Calibration>);

export const postReview = (
  id: string,
  action: ReviewAction,
  edits?: Record<string, unknown>,
): Promise<unknown> =>
  fetch(`/api/requirements/${id}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, edits }),
  }).then(unwrap<unknown>);

export interface GenStatus {
  state: string;   // idle | running | done | error
  stage: string;   // starting | assembling | writing | done | error
  message: string;
  count?: number;
  files?: string[];
  out_dir?: string;
  manifest?: Record<string, unknown>;
}

// generation runs in the background: start it, poll status, then fetch the artifacts.
export const postGenerate = (pid: string): Promise<{ started: boolean }> =>
  fetch(`/api/projects/${pid}/generate`, { method: "POST" }).then(unwrap<{ started: boolean }>);

export const getGenerateStatus = (pid: string): Promise<GenStatus> =>
  fetch(`/api/projects/${pid}/generate-status`).then(unwrap<GenStatus>);

export const getArtifact = async (pid: string, name: string): Promise<string> => {
  const res = await fetch(`/api/projects/${pid}/artifacts/${name}`);
  const text = await res.text();
  if (!res.ok) {
    // a failed fetch must NOT be rendered as the document body — surface the error instead
    let detail = res.statusText;
    try { detail = (JSON.parse(text) as { detail?: string }).detail ?? detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return text;
};

// --- local file browser + handoff ZIP --------------------------------------
// The backend runs on the user's machine, so we browse the LOCAL filesystem and select files by
// path (no browser upload — some orgs block that). The backend reads the chosen files off disk.
export interface FsEntry { name: string; path: string; is_dir: boolean; size: string; is_image: boolean; }
export const getFsRoots = (): Promise<{ roots: { name: string; path: string }[]; cwd: string }> =>
  fetch("/api/fs/roots").then(unwrap<{ roots: { name: string; path: string }[]; cwd: string }>);
export const getFsList = (path: string): Promise<{ path: string; parent: string | null; entries: FsEntry[] }> =>
  fetch(`/api/fs/list?path=${encodeURIComponent(path)}`).then(unwrap<{ path: string; parent: string | null; entries: FsEntry[] }>);

// Send the selected local paths; get a ZIP back and save it (a download, not an upload).
export const downloadHandoffZip = async (pid: string, asset1: string[], asset2: string[]): Promise<void> => {
  const res = await fetch(`/api/projects/${pid}/handoff-zip`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ asset1, asset2 }),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (JSON.parse(await res.text()) as { detail?: string }).detail ?? detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `${pid}_handoff.zip`; a.click();
  URL.revokeObjectURL(url);
};

// --- pipeline (input → run → progress) --------------------------------------
export interface CorpusFile {
  doc_id: string;
  name: string;
  type: string;
  ext: string;
  size_bytes: number | null;
  size: string;
}
export interface Corpus { id: string; path: string; docs: string[]; files?: CorpusFile[]; kind: string }
export interface JobStatus {
  state: string;   // idle | running | done | error
  stage: string;   // starting | ingesting | extracting | analyzing | done | error
  message: string;
  counts?: Record<string, number>;
  n_chunks?: number;
  n_extracted?: number;
}

export const getConfig = (): Promise<{ provider: string; default_project: string }> =>
  fetch("/api/config").then(unwrap<{ provider: string; default_project: string }>);

export const getCorpora = (): Promise<{ corpora: Corpus[] }> =>
  fetch("/api/corpora").then(unwrap<{ corpora: Corpus[] }>);

export const uploadDocs = (pid: string, files: FileList): Promise<{ corpus: string; docs: string[] }> => {
  const fd = new FormData();
  Array.from(files).forEach((f) => fd.append("files", f));
  return fetch(`/api/projects/${pid}/upload`, { method: "POST", body: fd }).then(unwrap<{ corpus: string; docs: string[] }>);
};

export const runPipeline = (pid: string, corpus: string): Promise<{ started: boolean }> =>
  fetch(`/api/projects/${pid}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ corpus }),
  }).then(unwrap<{ started: boolean }>);

export const getStatus = (pid: string): Promise<JobStatus> =>
  fetch(`/api/projects/${pid}/status`).then(unwrap<JobStatus>);

// --- review-by-exception + bulk --------------------------------------------
export const autoAccept = (pid: string, minConfidence = 0.9): Promise<{ accepted: number; remaining_for_review: number }> =>
  fetch(`/api/projects/${pid}/auto-accept`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ min_confidence: minConfidence }),
  }).then(unwrap<{ accepted: number; remaining_for_review: number }>);

export const acceptAll = (pid: string): Promise<{ accepted: number }> =>
  fetch(`/api/projects/${pid}/accept-all`, { method: "POST" }).then(unwrap<{ accepted: number }>);

export const reviewBulk = (pid: string, ids: string[], action: ReviewAction): Promise<{ count: number }> =>
  fetch(`/api/projects/${pid}/review-bulk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids, action }),
  }).then(unwrap<{ count: number }>);

export const addRequirement = (pid: string, statement: string, reason = ""): Promise<{ added: boolean; id: string }> =>
  fetch(`/api/projects/${pid}/requirements`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ statement, reason }),
  }).then(unwrap<{ added: boolean; id: string }>);

// --- decision resolution ----------------------------------------------------
export interface ApplyRecommended {
  applied: { conflicts: number; excluded: number; included: number; to_author: number };
  resolved_total: number;
}
export const applyRecommendedDecisions = (pid: string): Promise<ApplyRecommended> =>
  fetch(`/api/projects/${pid}/decisions/apply-recommended`, { method: "POST" }).then(unwrap<ApplyRecommended>);

export const resolveDecision = (
  pid: string, decisionId: string, body: { kind: string; recommended: string; action: string },
): Promise<{ resolved: string; resolved_total: number }> =>
  fetch(`/api/projects/${pid}/decisions/${decisionId}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(unwrap<{ resolved: string; resolved_total: number }>);

// --- technology stack (SRS §7) — per-aspect human review --------------------
export interface TechCandidate { name: string; recommended: boolean; reason: string; }
export interface TechAspect { key: string; title: string; rationale: string; candidates: TechCandidate[]; }
export interface TechStackResult { stated_in_inputs: boolean; basis: string; aspects: TechAspect[]; }
export interface TechStackResponse { tech_stack: TechStackResult | null; selections: Record<string, string>; }

export const getTechStack = (pid: string): Promise<TechStackResponse> =>
  fetch(`/api/projects/${pid}/tech-stack`).then(unwrap<TechStackResponse>);

export const selectTechStack = (
  pid: string, aspect: string, candidate: string, custom = false,
): Promise<{ aspect: string; selected: string; recommended: string; custom: boolean }> =>
  fetch(`/api/projects/${pid}/tech-stack/select`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ aspect, candidate, custom }),
  }).then(unwrap<{ aspect: string; selected: string; recommended: string; custom: boolean }>);

// --- reset / delete ---------------------------------------------------------
export const deleteProject = (pid: string): Promise<{ deleted: boolean; requirements_removed: number }> =>
  fetch(`/api/projects/${pid}`, { method: "DELETE" }).then(unwrap<{ deleted: boolean; requirements_removed: number }>);

export const resetStore = (): Promise<{ reset: boolean }> =>
  fetch(`/api/reset`, { method: "POST" }).then(unwrap<{ reset: boolean }>);
