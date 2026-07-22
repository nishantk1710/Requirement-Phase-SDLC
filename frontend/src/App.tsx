import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Landing } from "./Landing";
import {
  acceptAll,
  addRequirement,
  applyRecommendedDecisions,
  autoAccept,
  deleteProject,
  getArtifact,
  getCalibration,
  getConfig,
  getCorpora,
  getDecisions,
  getGate,
  getGenerateStatus,
  getRequirements,
  getSpotCheck,
  getStatus,
  getTechStack,
  postGenerate,
  postReview,
  resetStore,
  resolveDecision,
  reviewBulk,
  runPipeline,
  selectTechStack,
  uploadDocs,
  type Decision,
  type GenStatus,
  type JobStatus,
  type Requirement,
  type ReviewAction,
  type TechAspect,
  type TechStackResponse,
} from "./api";

// triage comes from the backend (agents/triage.py) so the UI and pipeline agree on risk.
const triageLevel = (r: Requirement): string => r.triage?.level ?? "review";
type Filter = "all" | "attention" | "review" | "routine";
type Phase = "input" | "run" | "review" | "srs";

const DEFAULT_PROJECT = "P-ELAMS";

export function App() {
  const [entered, setEntered] = useState(false);
  const [phase, setPhase] = useState<Phase>("input");
  const [pid, setPid] = useState(DEFAULT_PROJECT);
  const [corpus, setCorpus] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [filter, setFilter] = useState<Filter>("attention");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [generating, setGenerating] = useState(false);
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);

  const cfg = useQuery({ queryKey: ["config"], queryFn: getConfig });
  const corpora = useQuery({ queryKey: ["corpora"], queryFn: getCorpora });
  const list = useQuery({ queryKey: ["reqs", pid], queryFn: () => getRequirements(pid) });
  const gate = useQuery({ queryKey: ["gate", pid], queryFn: () => getGate(pid) });

  // default the corpus selection to the first available one
  useEffect(() => {
    if (!corpus && corpora.data?.corpora.length) setCorpus(corpora.data.corpora[0].path);
  }, [corpora.data, corpus]);

  // reload recovery (F8): on first mount, resume an in-flight or completed run/generation so a page
  // refresh doesn't strand the user on Input while work is running — or finished — on the server.
  const recovered = useRef(false);
  useEffect(() => {
    if (recovered.current) return;
    recovered.current = true;
    void (async () => {
      const s = await getStatus(pid).catch(() => null);
      const g = await getGenerateStatus(pid).catch(() => null);
      if (s?.state === "running") { qc.setQueryData(["status", pid], s); setRunning(true); setPhase("run"); }
      else if (g?.state === "running") { qc.setQueryData(["genstatus", pid], g); setGenerating(true); setPhase("srs"); }
      else if (g?.state === "done") { qc.setQueryData(["genstatus", pid], g); setPhase("srs"); }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["reqs", pid] });
    void qc.invalidateQueries({ queryKey: ["gate", pid] });
    void qc.invalidateQueries({ queryKey: ["decisions", pid] });  // decisions depend on req state
    setSelected(new Set());
  };

  // poll run status while a pipeline run is in progress
  const status = useQuery({
    queryKey: ["status", pid],
    queryFn: () => getStatus(pid),
    enabled: running,
    refetchInterval: running ? 1200 : false,
  });
  useEffect(() => {
    const st = status.data?.state;
    if (running && (st === "done" || st === "error")) {
      setRunning(false);
      invalidate();
      if (st === "done") setPhase("review");  // auto-advance to the review phase
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status.data?.state, running]);

  const run = useMutation({
    mutationFn: () => runPipeline(pid, corpus),
    onSuccess: () => { setRunning(true); setPhase("run"); },
  });
  const upload = useMutation({
    mutationFn: (files: FileList) => uploadDocs(pid, files),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["corpora"] });
      setCorpus(r.corpus);
    },
  });
  const review = useMutation({
    mutationFn: (v: { id: string; action: ReviewAction; edits?: Record<string, unknown> }) =>
      postReview(v.id, v.action, v.edits),
    onSuccess: invalidate,
  });
  const generate = useMutation({ mutationFn: () => postGenerate(pid), onSuccess: () => { setGenerating(true); setPhase("srs"); } });
  const genStatus = useQuery({
    queryKey: ["genstatus", pid],
    queryFn: () => getGenerateStatus(pid),
    enabled: generating,
    refetchInterval: generating ? 1200 : false,
  });
  useEffect(() => {
    const st = genStatus.data?.state;
    if (generating && (st === "done" || st === "error")) {
      setGenerating(false);
      if (st === "done") invalidate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [genStatus.data?.state, generating]);
  const autoAcc = useMutation({ mutationFn: () => autoAccept(pid, 0.75), onSuccess: invalidate });
  const acceptAllM = useMutation({ mutationFn: () => acceptAll(pid), onSuccess: invalidate });
  const bulk = useMutation({
    mutationFn: (v: { ids: string[]; action: ReviewAction }) => reviewBulk(pid, v.ids, v.action),
    onSuccess: invalidate,
  });
  const resetProject = useMutation({ mutationFn: () => deleteProject(pid), onSuccess: invalidate });
  const wipe = useMutation({ mutationFn: () => resetStore(), onSuccess: () => window.location.reload() });

  const provider = cfg.data?.provider ?? "…";
  const mockProvider = provider === "mock";

  // review triage: filter tabs + selection
  const allReqs = list.data?.requirements ?? [];
  const isPending = (r: Requirement) => r.status === "candidate" || r.status === "needs_review";
  const pending = allReqs.filter(isPending);
  const lvlCount = (lvl: string) => pending.filter((r) => triageLevel(r) === lvl).length;
  const attentionCount = lvlCount("attention");
  const reviewCount = lvlCount("review");
  const routineCount = lvlCount("routine");
  const visible = allReqs.filter((r) => {
    if (filter === "all") return true;
    return isPending(r) && triageLevel(r) === filter;
  });
  const selectable = visible.filter(isPending);
  const allSelected = selectable.length > 0 && selectable.every((r) => selected.has(r.id));
  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  const toggleAll = () =>
    setSelected(allSelected ? new Set() : new Set(selectable.map((r) => r.id)));
  const busy = review.isPending || bulk.isPending || autoAcc.isPending || acceptAllM.isPending;

  if (!entered) return <Landing onEnter={() => setEntered(true)} />;

  return (
    <div className="page">
      <header className="topbar">
        <div className="brand" onClick={() => setEntered(false)} title="Back to home">◆ RGA</div>
        <span className={`model ${mockProvider ? "warn" : ""}`}>model: {provider}</span>
        <span className="grow" />
        <label className="proj">
          Project&nbsp;
          <input
            value={pid}
            disabled={running || generating}
            title={running || generating ? "finish or leave the current run before switching project" : "project id"}
            onChange={(e) => setPid(e.target.value)}
          />
        </label>
        <div className="dangerzone">
          <button
            className="ghost"
            disabled={running || resetProject.isPending}
            title="Delete all data for this project"
            onClick={() => {
              if (window.confirm(`Delete ALL data for project "${pid}"? This cannot be undone.`))
                resetProject.mutate();
            }}
          >
            {resetProject.isPending ? "Resetting…" : "Reset project"}
          </button>
          <button
            className="danger"
            disabled={running || wipe.isPending}
            title="Wipe the entire database and restart"
            onClick={() => {
              if (window.confirm("Wipe the ENTIRE database (all projects) and restart the app? This cannot be undone."))
                wipe.mutate();
            }}
          >
            {wipe.isPending ? "Wiping…" : "Wipe all"}
          </button>
        </div>
      </header>

      <div className="titlerow">
        <h1>Requirement Gathering &amp; Analysis</h1>
      </div>
      <p className="sub">
        Input documents, run the agents, review by decision, then generate the SRS &amp; RTM —
        all here. Nothing reaches the generators until a human approves it.
      </p>
      <div>
        {resetProject.isSuccess && (
          <p className="muted">Cleared {resetProject.data.requirements_removed} requirement(s) from {pid}.</p>
        )}
      </div>

      {/* ---- PHASE NAV ---- */}
      <ol className="stepper">
        <li className={phase === "input" ? "on" : ""}><button className="stepbtn" onClick={() => setPhase("input")}>1 · Input</button></li>
        <li className={phase === "run" ? "on" : ""}><button className="stepbtn" disabled={!running && !status.data} onClick={() => (running || status.data) && setPhase("run")}>2 · Run agents</button></li>
        <li className={phase === "review" ? "on" : ""}><button className="stepbtn" disabled={allReqs.length === 0} onClick={() => allReqs.length && setPhase("review")}>3 · Review</button></li>
        <li className={phase === "srs" ? "on" : ""}><button className="stepbtn" disabled={!(gate.data?.ready || genStatus.data?.state === "done")} onClick={() => (gate.data?.ready || genStatus.data?.state === "done") && setPhase("srs")}>4 · SRS</button></li>
      </ol>

      <div className="phaseview" key={phase}>
      {/* ---- PHASE 1 · INPUT ---- */}
      {phase === "input" && (
      <section className="panel">
        <h2>1 · Input your documents</h2>
        <p className="muted">Pick a prepared document set or upload your own (.docx / .pdf / .txt / .csv), then run the agent pipeline.</p>
        <div className="piperow">
          <label>
            Document set&nbsp;
            <select value={corpus} onChange={(e) => setCorpus(e.target.value)}>
              {(corpora.data?.corpora ?? []).map((c) => (
                <option key={c.path} value={c.path}>
                  {c.id} ({c.kind}, {c.docs.length} docs)
                </option>
              ))}
            </select>
          </label>

          <span className="or">or</span>

          <input
            ref={fileRef}
            type="file"
            multiple
            onChange={(e) => e.target.files && e.target.files.length && upload.mutate(e.target.files)}
          />
          {upload.isPending && <span className="muted">uploading…</span>}
          {upload.isSuccess && <span className="ok">uploaded {upload.data.docs.length} doc(s)</span>}
        </div>
        <div className="phase-cta">
          <button
            className="btn-primary lg"
            disabled={!corpus || running || mockProvider}
            title={mockProvider ? "start the server with --provider foundry to run extraction" : corpus}
            onClick={() => run.mutate()}
          >
            {running ? "Starting…" : "Run the agent pipeline →"}
          </button>
        </div>
        {run.isError && <p className="gate-blocked">⛔ Could not start the run: {(run.error as Error).message}</p>}
        {mockProvider && (
          <p className="gate-blocked">
            The server is in <b>mock</b> mode, so the agents can't extract. Restart with
            <code> python -m rga serve --provider foundry </code> to run the real pipeline.
          </p>
        )}
      </section>
      )}

      {/* ---- PHASE 2 · RUN ---- */}
      {phase === "run" && (
      <section className="panel">
        <h2>2 · Running the agent pipeline</h2>
        <p className="muted">Extraction, verification, consolidation and coverage — a few minutes on the first run (cached after, so re-runs are fast).</p>
        <PipelineProgress status={status.data} running={running} />
        {status.data?.state === "error" && <p className="gate-blocked">⛔ {status.data.message}</p>}
        {status.data?.state === "done" && (
          <div className="phase-cta"><button className="btn-primary lg" onClick={() => setPhase("review")}>Continue to review →</button></div>
        )}
      </section>
      )}

      {/* ---- PHASE 3 · REVIEW ---- */}
      {phase === "review" && (
      <section className="panel">
        <div className="reviewhead">
          <h2>3 · Review by decision</h2>
          {gate.data && (
            <span className="counts">
              {Object.entries(gate.data.counts).filter(([, n]) => n > 0).map(([k, n]) => `${k}: ${n}`).join("  ·  ")}
            </span>
          )}
          <button
            className="generate"
            disabled={!gate.data?.ready || generating}
            title={gate.data?.reason}
            onClick={() => generate.mutate()}
          >
            {generating ? "Generating…" : "Generate SRS / RTM"}
          </button>
        </div>
        {gate.data && !gate.data.ready && <p className="gate-blocked">🔒 {gate.data.reason}</p>}
        {gate.data?.ready && list.data && allReqs.length > 0 && (
          <p className="ready-note">✅ Every requirement is triaged — you can generate the SRS &amp; RTM now.</p>
        )}

        {/* clear the remaining pending requirements so you can proceed to generation */}
        {list.data && pending.length > 0 && (
          <div className="reviewbar">
            <span className="muted small">
              {pending.length} requirement(s) still need a decision — resolve the decisions below, or clear the rest here:
            </span>
            <div className="bulkbar">
              <button
                className="btn-primary sm"
                disabled={busy || routineCount === 0}
                title="Approve the clean, high-confidence, routine requirements (each logged)"
                onClick={() => autoAcc.mutate()}
              >
                {autoAcc.isPending ? "Approving…" : `Auto-accept routine (${routineCount})`}
              </button>
              <button
                disabled={busy || pending.length === 0}
                title="Approve every remaining pending requirement (each logged)"
                onClick={() => {
                  if (window.confirm(`Approve ALL ${pending.length} remaining requirement(s) without individual review?`))
                    acceptAllM.mutate();
                }}
              >
                {acceptAllM.isPending ? "Approving…" : `Approve all remaining (${pending.length})`}
              </button>
            </div>
          </div>
        )}
        {(acceptAllM.isError || autoAcc.isError) && (
          <p className="gate-blocked">⛔ {((acceptAllM.error || autoAcc.error) as Error).message}</p>
        )}

        {list.isLoading && <p>Loading…</p>}
        {list.data && allReqs.length === 0 && (
          <p className="muted">No requirements yet — go back to <b>Input</b> and run the pipeline.</p>
        )}

        {/* PRIMARY surface: review by DECISION (clustered, owner-routed, propose-don't-ask) */}
        {list.data && allReqs.length > 0 && <Decisions pid={pid} reqs={allReqs} onResolved={invalidate} />}

        {/* Technology Stack (SRS §7): pick one candidate per aspect (recommended = default) */}
        {list.data && allReqs.length > 0 && <TechStack pid={pid} />}

        {list.data && allReqs.length > 0 && (
          <>
            <details className="reqdetail">
              <summary>All requirements ({allReqs.length}) — detail &amp; manual override</summary>
            {/* triage toolbar: cut the manual work */}
            <div className="triage">
              <div className="tabs">
                <button className={filter === "all" ? "on" : ""} onClick={() => setFilter("all")}>All ({allReqs.length})</button>
                <button className={filter === "attention" ? "on" : ""} onClick={() => setFilter("attention")}>🔴 Attention ({attentionCount})</button>
                <button className={filter === "review" ? "on" : ""} onClick={() => setFilter("review")}>🟡 Review ({reviewCount})</button>
                <button className={filter === "routine" ? "on" : ""} onClick={() => setFilter("routine")}>🟢 Routine ({routineCount})</button>
              </div>
              <div className="bulkbar">
                <button
                  className="primary"
                  disabled={busy || routineCount === 0}
                  title="Approve every 'routine' candidate (clear, testable, high-confidence, precisely traced) at once — each logged"
                  onClick={() => autoAcc.mutate()}
                >
                  {autoAcc.isPending ? "Accepting…" : `Auto-accept routine (${routineCount})`}
                </button>
                <button
                  disabled={busy || pending.length === 0}
                  title="Approve ALL pending requirements without individual review (each logged)"
                  onClick={() => {
                    if (window.confirm(`Accept ALL ${pending.length} pending requirement(s) without individual review?`))
                      acceptAllM.mutate();
                  }}
                >
                  {acceptAllM.isPending ? "Accepting…" : `Accept all (${pending.length})`}
                </button>
                <button disabled={busy || selected.size === 0} onClick={() => bulk.mutate({ ids: [...selected], action: "accept" })}>
                  Accept selected ({selected.size})
                </button>
                <button className="danger" disabled={busy || selected.size === 0} onClick={() => bulk.mutate({ ids: [...selected], action: "reject" })}>
                  Reject selected ({selected.size})
                </button>
              </div>
            </div>
            {autoAcc.isSuccess && (
              <p className="muted">Auto-accepted {autoAcc.data.accepted}; {autoAcc.data.remaining_for_review} left for you to review.</p>
            )}
            {acceptAllM.isSuccess && <p className="muted">Accepted all {acceptAllM.data.accepted} pending requirement(s).</p>}
            {(acceptAllM.isError || autoAcc.isError || bulk.isError) && (
              <p className="gate-blocked">
                ⛔ Action failed: {((acceptAllM.error || autoAcc.error || bulk.error) as Error).message}.
                If this is a 404, restart the server (`python -m rga serve --provider foundry`) so it has the latest endpoints.
              </p>
            )}

            <table>
              <thead>
                <tr>
                  <th><input type="checkbox" checked={allSelected} onChange={toggleAll} title="select all shown" /></th>
                  <th>Status</th><th>Triage</th><th>Why</th><th>Requirement</th><th>Owner</th><th>Type</th><th>Priority</th><th>Conf.</th>
                  <th>Source quote</th><th>Flags</th><th>Decision</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((r) => (
                  <Row
                    key={r.id}
                    r={r}
                    busy={busy}
                    selected={selected.has(r.id)}
                    selectable={isPending(r)}
                    onToggle={() => toggle(r.id)}
                    onReview={(action, edits) => review.mutate({ id: r.id, action, edits })}
                  />
                ))}
              </tbody>
            </table>
            </details>
            <SpotCheck pid={pid} />
          </>
        )}
      </section>
      )}

      {/* ---- PHASE 4 · SRS ---- */}
      {phase === "srs" && (
      <section className="panel">
        <div className="reviewhead">
          <h2>4 · Generated documents</h2>
          <button className="ghost" onClick={() => setPhase("review")}>← Back to review</button>
        </div>
        {generate.isError && <p className="gate-blocked">⛔ {(generate.error as Error).message}</p>}
        {genStatus.data && genStatus.data.state !== "done" && (
          <div className={`runstatus ${genStatus.data.state}`}>
            <span className="spinner" data-on={generating} /><b>Generation</b>
            <span className="muted">{genStatus.data.message}</span>
          </div>
        )}
        {genStatus.data?.state === "done"
          ? <Results pid={pid} status={genStatus.data} />
          : !generating && <p className="muted">No documents yet — go to Review and click <b>Generate SRS / RTM</b>.</p>}
      </section>
      )}
      </div>
    </div>
  );
}

// Cinematic pipeline progress — an ordered stage tracker with done / active / pending states.
const PIPELINE_STAGES: [string, string][] = [
  ["ingesting", "Reading & structuring the documents"],
  ["extracting", "Extraction + Critic agents"],
  ["deduping", "De-duplicating near-paraphrases"],
  ["consolidating", "Consolidating to a canonical set"],
  ["reconciling", "Reconciling scope"],
  ["conflicts", "Detecting conflicts"],
  ["verifying", "Second-opinion verification"],
  ["completeness", "Completeness / gap check"],
  ["coverage", "Coverage floor — nothing missed"],
  ["analyzing", "Clarity, priority & auto-approve"],
];
function PipelineProgress({ status, running }: { status?: JobStatus; running: boolean }) {
  const cur = status?.stage ?? (running ? "ingesting" : "");
  const done = status?.state === "done";
  let idx = PIPELINE_STAGES.findIndex(([k]) => k === cur);
  // an unknown/transient stage ("starting") must not reset every step to pending — hold at the
  // first stage while the run is active so accumulated checkmarks don't disappear (F9)
  if (idx < 0 && (running || status?.state === "running")) idx = 0;
  return (
    <div className="pipeline">
      {PIPELINE_STAGES.map(([key, label], i) => {
        const state = done || (idx >= 0 && i < idx) ? "done" : i === idx ? "active" : "pending";
        return (
          <div key={key} className={`pstage ${state}`}>
            <span className="pdot">{state === "done" ? "✓" : ""}</span>
            <span className="plabel">{label}</span>
            {state === "active" && status?.message && <span className="pmsg muted small">{status.message}</span>}
          </div>
        );
      })}
    </div>
  );
}

function download(name: string, content: string) {
  const blob = new Blob([content], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

type ArtifactTab = "SRS.md" | "RTM.md";   // pack = SRS + RTM only; Appendix C lives inside the SRS (Part J)
function Results({ pid, status }: { pid: string; status: GenStatus }) {
  const [tab, setTab] = useState<ArtifactTab>("SRS.md");
  const doc = useQuery({ queryKey: ["artifact", pid, tab], queryFn: () => getArtifact(pid, tab) });
  const traceable = (status.manifest as { traceability_complete?: boolean } | undefined)?.traceability_complete;
  return (
    <section className="panel">
      <div className="reviewhead">
        <h2>4 · Generated documents</h2>
        {traceable
          ? <span className="ok">✅ {status.count} approved requirement(s) · full traceability</span>
          : <span className="gate-blocked">⚠️ {status.count} approved · traceability INCOMPLETE — a requirement is missing its source</span>}
      </div>
      <div className="tabs">
        <button className={tab === "SRS.md" ? "on" : ""} onClick={() => setTab("SRS.md")}>SRS (IEEE-830)</button>
        <button className={tab === "RTM.md" ? "on" : ""} onClick={() => setTab("RTM.md")}>Traceability Matrix</button>
        <button className="ghost" disabled={!doc.data} onClick={() => doc.data && download(tab, doc.data)}>Download .md</button>
        <a className="dl-docx" href={`/api/projects/${pid}/artifacts/${tab.replace(".md", ".docx")}`}>Download Word (.docx)</a>
        <span className="muted">saved to handoff/{pid}/ (.md + .docx)</span>
      </div>
      <pre className="doc">
        {doc.isLoading
          ? "Loading…"
          : doc.isError
            ? `⛔ Could not load ${tab}: ${(doc.error as Error).message}`
            : doc.data}
      </pre>
    </section>
  );
}

function Row({
  r, busy, selected, selectable, onToggle, onReview,
}: {
  r: Requirement;
  busy: boolean;
  selected: boolean;
  selectable: boolean;
  onToggle: () => void;
  onReview: (action: ReviewAction, edits?: Record<string, unknown>) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(r.statement);
  const flags = r.quality.ambiguity_flags;
  useEffect(() => { if (!editing) setDraft(r.statement); }, [r.statement, editing]);

  return (
    <tr className={`status-${r.status}`}>
      <td>{selectable && <input type="checkbox" checked={selected} onChange={onToggle} />}</td>
      <td><span className={`badge ${r.status}`}>{r.status}</span></td>
      <td>
        <span className={`badge triage-${r.triage?.level ?? "review"}`} title={(r.triage?.reasons ?? []).join("; ") || "clear, grounded, high-confidence"}>
          {r.triage?.level ?? "—"}
        </span>
      </td>
      <td className="why muted">{(r.triage?.reasons ?? []).join("; ") || "—"}</td>
      <td className="statement">
        {editing ? (
          <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} />
        ) : (
          <>
            <span>{r.statement}</span>
            {r.inferred && <span className="tag inferred">inferred</span>}
          </>
        )}
      </td>
      <td className="muted">{r.owner}</td>
      <td>{r.rtype}</td>
      <td>{r.priority ? <span className={`tag prio-${r.priority}`}>{r.priority}</span> : <span className="ok">—</span>}</td>
      <td className="conf" title="recorded for calibration — not used to gate">{r.confidence.toFixed(2)}</td>
      <td className="quotes">
        {r.sources.map((s, i) => (
          <blockquote key={i} title={`${s.doc_id} · ${s.location}`}>“{s.quote}”</blockquote>
        ))}
      </td>
      <td className="flags">
        {r.conflicts_with?.length > 0 && <span className="tag flag" title={`conflicts with ${r.conflicts_with.join(", ")}`}>conflict</span>}
        {flags.map((f) => <span key={f} className="tag flag">{f}</span>)}
        {flags.length === 0 && !r.conflicts_with?.length && <span className="ok">—</span>}
      </td>
      <td className="actions">
        {editing ? (
          <>
            <button disabled={busy || !draft.trim()} onClick={() => { onReview("edit", { statement: draft }); setEditing(false); }}>
              Save &amp; approve
            </button>
            <button className="ghost" onClick={() => { setDraft(r.statement); setEditing(false); }}>Cancel</button>
          </>
        ) : (
          <>
            <button disabled={busy} onClick={() => onReview("accept")}>Accept</button>
            <button disabled={busy} className="ghost" onClick={() => setEditing(true)}>Edit</button>
            <button disabled={busy} className="danger" onClick={() => onReview("reject")}>Reject</button>
          </>
        )}
      </td>
    </tr>
  );
}

// Review by DECISION: clustered, owner-routed, propose-don't-ask. Resolving one propagates to
// every requirement it affects (via bulk review of the affected ids).
const KIND_LABEL: Record<string, string> = {
  conflict: "Conflict", possible_miss: "Possible miss", gap: "Coverage gap",
  out_of_scope: "Out of scope", disputed: "Disputed", undecided: "Undecided", deferred: "Deferred",
};
const recommendsExclude = (d: Decision) => /exclude|defer|out of scope|drop/i.test(d.recommended);
// the suggested requirement text for gap/possible-miss decisions (strip the "…: " prefix)
const suggestionText = (d: Decision) => (d.evidence[0] ?? d.question).replace(/^[^:]{3,40}:\s*/, "").trim();

function Decisions({ pid, reqs, onResolved }: { pid: string; reqs: Requirement[]; onResolved: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["decisions", pid], queryFn: () => getDecisions(pid) });
  const [owner, setOwner] = useState<string>("all");
  const [busy, setBusy] = useState<string>("");                       // id of the decision being acted on
  const [addDraft, setAddDraft] = useState<Record<string, string>>({}); // decision id -> edited requirement text
  const [applyMsg, setApplyMsg] = useState<string>("");

  // map affected requirement ids -> their statements, so a card can SHOW what it will change (F4)
  const stmtOf = useMemo(() => {
    const m = new Map<string, string>();
    reqs.forEach((r) => m.set(r.id, r.statement));
    return m;
  }, [reqs]);

  const refreshDecisions = () => void qc.invalidateQueries({ queryKey: ["decisions", pid] });
  // persist the resolution server-side so it survives a reload, then refresh reqs/gate + decisions
  const persistResolve = async (d: Decision, action: string) => {
    try { await resolveDecision(pid, d.id, { kind: d.kind, recommended: d.recommended, action }); }
    finally { onResolved(); refreshDecisions(); }
  };
  const keep = async (d: Decision, keepId: string, dropId: string, label: string) => {
    setBusy(d.id);
    try {
      await reviewBulk(pid, [keepId], "accept");
      await reviewBulk(pid, [dropId], "reject");
      await persistResolve(d, label);
    } finally { setBusy(""); }
  };
  const bulk = async (d: Decision, ids: string[], action: ReviewAction, label: string) => {
    setBusy(d.id);
    try { if (ids.length) await reviewBulk(pid, ids, action); await persistResolve(d, label); }
    finally { setBusy(""); }
  };
  const draftFor = (d: Decision) => addDraft[d.id] ?? suggestionText(d);
  const add = async (d: Decision) => {
    const text = draftFor(d).trim();
    if (!text) return;
    setBusy(d.id);
    try { await addRequirement(pid, text, KIND_LABEL[d.kind] ?? d.kind); await persistResolve(d, "added"); }
    finally { setBusy(""); }
  };
  const dismiss = async (d: Decision) => {
    setBusy(d.id);
    try { await persistResolve(d, "dismissed"); } finally { setBusy(""); }
  };
  const applyAll = useMutation({
    mutationFn: () => applyRecommendedDecisions(pid),
    onSuccess: (r) => {
      setApplyMsg(
        `Applied recommended verdicts — ${r.applied.conflicts} conflict(s), ${r.applied.included} included, ` +
        `${r.applied.excluded} excluded${r.applied.to_author ? `, ${r.applied.to_author} need you to author them` : ""}.`,
      );
      onResolved();
      refreshDecisions();
    },
  });

  if (q.isLoading) return <p className="muted">Loading decisions…</p>;
  const decisions: Decision[] = q.data?.decisions ?? [];
  const open = decisions.filter((d) => !d.resolved);       // SERVER-side resolved flag (durable, F3)
  const resolvedCount = decisions.length - open.length;
  const byOwner = q.data?.summary.by_owner ?? {};
  const owners = ["all", ...Object.keys(byOwner)];
  const shown = owner === "all" ? open : open.filter((d) => d.owner === owner);

  // a plain JSX-returning helper (NOT a nested component — that would remount the textarea and
  // drop focus on each keystroke). Edit-before-add for gap/possible-miss (F5).
  const addBox = (d: Decision) => (
    <div className="addbox">
      <textarea
        rows={2}
        value={draftFor(d)}
        onChange={(e) => setAddDraft((m) => ({ ...m, [d.id]: e.target.value }))}
        placeholder="Edit the requirement wording before adding…"
      />
      <div>
        <button className="btn-primary sm" disabled={busy === d.id || !draftFor(d).trim()} onClick={() => add(d)}>
          {busy === d.id ? "Adding…" : "Add as requirement"}
        </button>
        <button disabled={busy === d.id} onClick={() => dismiss(d)}>Dismiss</button>
      </div>
    </div>
  );

  return (
    <div className="decisions">
      <div className="reviewhead">
        <h3>Decisions to make</h3>
        <span className="dcount">{open.length} open · {resolvedCount} resolved</span>
        <button
          className="btn-primary sm"
          disabled={applyAll.isPending || open.length === 0}
          title="Apply every open decision's recommended verdict at once (saved server-side)"
          onClick={() => {
            if (window.confirm(`Apply the recommended verdict to all ${open.length} open decision(s)?`))
              applyAll.mutate();
          }}
        >
          {applyAll.isPending ? "Applying…" : `Apply all recommended (${open.length})`}
        </button>
      </div>
      <p className="muted small">
        Confirm the recommendation or override — resolving a decision applies to every requirement it
        affects, and is saved (it won't reappear on reload).
      </p>
      {applyMsg && <p className="muted small">{applyMsg}</p>}
      {applyAll.isError && <p className="gate-blocked">⛔ {(applyAll.error as Error).message}</p>}

      {decisions.length > 0 && (
        <div className="tabs owners">
          {owners.map((o) => (
            <button key={o} className={owner === o ? "on" : ""} onClick={() => setOwner(o)}>
              {o === "all" ? `All owners (${open.length})` : `${o} (${open.filter((d) => d.owner === o).length})`}
            </button>
          ))}
        </div>
      )}

      {open.length === 0 && (
        <p className="ok done-note">✅ All decisions resolved — the clean requirements were auto-approved, and you've resolved the rest.</p>
      )}

      {shown.map((d) => {
        const b = busy === d.id;
        const addKind = d.kind === "gap" || d.kind === "possible_miss";
        const affectedStmts = d.affected.map((id) => stmtOf.get(id)).filter(Boolean) as string[];
        return (
          <div key={d.id} className={`decision tier-${d.tier}`}>
            <div className="dhead">
              <span className={`tag kind-${d.kind}`}>{KIND_LABEL[d.kind] ?? d.kind}</span>
              <span className="tag owner">{d.owner}</span>
              {d.affected.length > 0 && <span className="daffects">affects {d.affected.length}</span>}
            </div>
            <div className="dq">{d.question}</div>
            <div className="drec"><span className="reclabel">Recommended</span>{d.recommended}</div>
            <div className="dwhy">Why surfaced — {d.reason}</div>
            {affectedStmts.length > 0 && (
              <details className="daffected">
                <summary>Affected requirements ({affectedStmts.length})</summary>
                <ul>{affectedStmts.map((s, i) => <li key={i}>{s}</li>)}</ul>
              </details>
            )}
            <div className="dactions">
              {d.kind === "conflict" && d.affected.length === 2 ? (
                <>
                  <button className={recommendsExclude(d) ? "" : "btn-primary sm"} disabled={b}
                    onClick={() => keep(d, d.affected[0], d.affected[1], "kept-a")}>Keep A · reject B</button>
                  <button disabled={b} onClick={() => keep(d, d.affected[1], d.affected[0], "kept-b")}>Keep B · reject A</button>
                </>
              ) : addKind ? (
                addBox(d)
              ) : d.affected.length > 0 ? (
                recommendsExclude(d) ? (
                  <>
                    <button className="btn-primary sm danger-p" disabled={b}
                      onClick={() => bulk(d, d.affected, "reject", "excluded")}>Exclude — reject {d.affected.length}</button>
                    <button disabled={b} onClick={() => bulk(d, d.affected, "accept", "included")}>Include — accept {d.affected.length}</button>
                  </>
                ) : (
                  <>
                    <button className="btn-primary sm" disabled={b}
                      onClick={() => bulk(d, d.affected, "accept", "included")}>Include — accept {d.affected.length}</button>
                    <button disabled={b} onClick={() => bulk(d, d.affected, "reject", "excluded")}>Exclude — reject {d.affected.length}</button>
                  </>
                )
              ) : (
                addBox(d)
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// QA: spot-check a 5% sample of auto-approved requirements + acceptance calibration (loaded on demand).
// Technology-stack review (SRS §7): the run proposes popular candidates per aspect (one recommended);
// the reviewer picks one per aspect. A stack stated in the inputs is adopted and shown read-only.
function TechStack({ pid }: { pid: string }) {
  const qc = useQueryClient();
  const q = useQuery<TechStackResponse>({ queryKey: ["techstack", pid], queryFn: () => getTechStack(pid) });
  const [busy, setBusy] = useState<string>("");                     // aspect key currently saving
  const [otherOpen, setOtherOpen] = useState<Record<string, boolean>>({});  // "Other" typebar shown
  const [otherText, setOtherText] = useState<Record<string, string>>({});   // typed custom value

  const pick = async (aspect: string, candidate: string, custom = false) => {
    setBusy(aspect);
    try {
      await selectTechStack(pid, aspect, candidate, custom);
      if (!custom) setOtherOpen((s) => ({ ...s, [aspect]: false }));  // a normal pick closes the typebar
      await qc.invalidateQueries({ queryKey: ["techstack", pid] });
    } finally { setBusy(""); }
  };
  const submitOther = (aspect: string) => {
    const val = (otherText[aspect] ?? "").trim();
    if (val) void pick(aspect, val, true);
  };

  if (q.isLoading || q.isError) return null;
  const ts = q.data?.tech_stack;
  if (!ts || !ts.aspects?.length) return null;   // no analysis yet (e.g. an older run) — hide quietly
  const selections = q.data?.selections ?? {};
  const stated = ts.stated_in_inputs;
  const chosenName = (a: TechAspect): string | undefined =>
    selections[a.key] ?? a.candidates.find((c) => c.recommended)?.name ?? a.candidates[0]?.name;

  return (
    <section className="techstack">
      <div className="reviewhead">
        <h3>Technology Stack — SRS §7</h3>
        <span className="counts">
          {stated ? "adopted from inputs" : `${ts.aspects.length} aspects · pick one per aspect`}
        </span>
      </div>
      {stated
        ? <p className="ready-note">✅ A technology stack is stated in the source inputs — adopted as-is; no selection needed.</p>
        : <p className="muted small">Candidates are simple, widely-used choices. The ★ recommended option is the default — change any, or pick <b>Other</b> to type your own, then generate.</p>}
      {ts.basis && <p className="muted small">{ts.basis}</p>}
      <div className="ts-aspects">
        {ts.aspects.map((a) => {
          const chosen = chosenName(a);
          const isCustom = !!chosen && !a.candidates.some((c) => c.name === chosen);
          const showOther = !stated && (otherOpen[a.key] || isCustom);
          return (
            <div className="ts-aspect" key={a.key}>
              <div className="ts-aspect-head">
                <b>{a.title}</b>{a.rationale && <span className="muted small"> — {a.rationale}</span>}
              </div>
              <div className="ts-cands">
                {a.candidates.map((c) => (
                  <label className={`ts-cand ${c.name === chosen ? "on" : ""}`} key={c.name}>
                    <input
                      type="radio"
                      name={`ts:${pid}:${a.key}`}
                      checked={c.name === chosen}
                      disabled={stated || busy === a.key}
                      onChange={() => pick(a.key, c.name)}
                    />
                    <span className="ts-name">
                      {c.name}{c.recommended && <em className="rec"> ★ Recommended</em>}
                    </span>
                    {c.reason && <span className="ts-reason muted small">{c.reason}</span>}
                  </label>
                ))}
                {/* "Other" — reviewer types their own technology for this aspect */}
                {!stated && (
                  <label className={`ts-cand ${isCustom ? "on" : ""}`}>
                    <input
                      type="radio"
                      name={`ts:${pid}:${a.key}`}
                      checked={isCustom}
                      disabled={busy === a.key}
                      onChange={() => {
                        setOtherOpen((s) => ({ ...s, [a.key]: true }));
                        setOtherText((s) => ({ ...s, [a.key]: isCustom ? (chosen ?? "") : (s[a.key] ?? "") }));
                      }}
                    />
                    <span className="ts-name">Other{isCustom && chosen ? `: ${chosen}` : " — type your own"}</span>
                  </label>
                )}
                {showOther && (
                  <div className="ts-other">
                    <input
                      className="ts-other-input"
                      type="text"
                      autoFocus
                      placeholder="Type a technology, e.g. Python + FastAPI, MongoDB, JWT…"
                      value={otherText[a.key] ?? (isCustom ? (chosen ?? "") : "")}
                      disabled={busy === a.key}
                      onChange={(e) => setOtherText((s) => ({ ...s, [a.key]: e.target.value }))}
                      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); submitOther(a.key); } }}
                    />
                    <button
                      className="ghost"
                      disabled={busy === a.key || !(otherText[a.key] ?? "").trim()}
                      onClick={() => submitOther(a.key)}
                    >Use this</button>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
      <p className="muted small">These choices populate §7 of the generated SRS.</p>
    </section>
  );
}

function SpotCheck({ pid }: { pid: string }) {
  const spot = useQuery({ queryKey: ["spotcheck", pid], queryFn: () => getSpotCheck(pid), enabled: false });
  const cal = useQuery({ queryKey: ["calibration", pid], queryFn: () => getCalibration(pid), enabled: false });
  return (
    <details className="spotcheck">
      <summary>QA — spot-check auto-approved &amp; calibration</summary>
      <div className="bulkbar">
        <button className="ghost" onClick={() => void spot.refetch()}>Load 5% spot-check</button>
        <button className="ghost" onClick={() => void cal.refetch()}>Load calibration</button>
      </div>
      {spot.data && (
        <p className="muted">{spot.data.auto_approved} auto-approved · showing {spot.data.sample.length} sampled for QA.</p>
      )}
      {spot.data?.sample.map((r) => (
        <div key={r.id} className="spotrow">
          <div>{r.statement}</div>
          <div className="muted small">conf {r.confidence.toFixed(2)}{r.sources[0]?.quote ? ` · “${r.sources[0].quote}”` : ""}</div>
        </div>
      ))}
      {cal.data && (
        <p className="muted small">
          Acceptance by confidence band — {Object.entries(cal.data.bands).map(([b, v]) => `${b}: ${v.acceptance_rate ?? "—"} (${v.decided})`).join("  ·  ")}.
          Suggested auto-approve bar: <b>{cal.data.suggested_auto_approve_bar}</b>.
        </p>
      )}
    </details>
  );
}
