import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Landing } from "./Landing";
import {
  acceptAll,
  addRequirement,
  applyRecommendedDecisions,
  attachCodebase,
  attachCodebaseZip,
  autoAccept,
  deleteProject,
  downloadHandoffZip,
  generateChangePack,
  getArtifact,
  getChanges,
  getCodebase,
  getConfig,
  getCorpora,
  getFsList,
  getFsRoots,
  getDecisions,
  getGate,
  getGenerateStatus,
  getRequirements,
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
type Mode = "fresh" | "existing";   // greenfield (full SRS) vs brownfield (change-only SRS for a repo)

const DEFAULT_PROJECT = "P-ELAMS";

export function App() {
  const [entered, setEntered] = useState(false);
  const [phase, setPhase] = useState<Phase>("input");
  const [pid, setPid] = useState(DEFAULT_PROJECT);
  // the project input is a DRAFT until blur/Enter — otherwise every keystroke changes `pid` (the
  // query key) and fires a storm of API calls for each partial name, and trailing spaces leak in.
  const [pidDraft, setPidDraft] = useState(pid);
  useEffect(() => { setPidDraft(pid); }, [pid]);
  const commitPid = () => {
    const v = pidDraft.trim();
    if (v && v !== pid) setPid(v); else setPidDraft(pid);
  };
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

  // the currently-selected document set — so the user can SEE exactly what they are ingesting
  const selectedCorpus = (corpora.data?.corpora ?? []).find((c) => c.path === corpus);

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
    void qc.invalidateQueries({ queryKey: ["changes", pid] });    // delta-vs-baseline depends on req state
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
  const generate = useMutation({
    mutationFn: () => postGenerate(pid),
    onSuccess: () => {
      // clear any previous "done" status so re-generation doesn't look already-finished (which
      // would freeze the badge on the OLD version while the doc shows the new one).
      qc.setQueryData(["genstatus", pid], { state: "running", stage: "starting", message: "Starting generation…" });
      setGenerating(true);
      setPhase("srs");
    },
  });
  // --- mode (fresh vs existing-codebase) --------------------------------------
  const codebaseQ = useQuery({ queryKey: ["codebase", pid], queryFn: () => getCodebase(pid) });
  const codebaseAttached = !!codebaseQ.data?.attached;
  const [mode, setMode] = useState<Mode>("fresh");
  useEffect(() => {
    // an attached codebase means brownfield (server truth); otherwise restore the saved choice.
    if (codebaseAttached) { setMode("existing"); return; }
    try { const s = localStorage.getItem(`rga:mode:${pid}`); if (s === "existing" || s === "fresh") setMode(s as Mode); } catch { /* ignore */ }
  }, [pid, codebaseAttached]);
  const chooseMode = (m: Mode) => { setMode(m); try { localStorage.setItem(`rga:mode:${pid}`, m); } catch { /* ignore */ } };
  const changeGen = useMutation({
    mutationFn: () => generateChangePack(pid),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["artifact", pid] }); setPhase("srs"); },
  });
  // in existing mode the run corpus IS the synthetic requirements doc — restore it on reload
  useEffect(() => {
    if (mode === "existing" && codebaseQ.data?.corpus) setCorpus(codebaseQ.data.corpus);
  }, [mode, codebaseQ.data?.corpus]);
  const genStatus = useQuery({
    queryKey: ["genstatus", pid],
    queryFn: () => getGenerateStatus(pid),
    // Poll off the ACTUAL backend state, not just the local `generating` flag, and keep polling in a
    // backgrounded tab — otherwise a slow generation that finishes while the tab is unfocused leaves
    // the page stuck on a stale "assembling" message (it never sees "done").
    refetchInterval: (q) => {
      const st = q.state.data?.state;
      // poll while a generation is actually in flight — but a stale "done"/"error" must NOT stop a
      // freshly-kicked-off one (`generating`), or the badge freezes on the previous version.
      return (st === "running" || (generating && st !== "done" && st !== "error")) ? 1200 : false;
    },
    refetchIntervalInBackground: true,
  });
  // agile: has an SRS baseline been generated yet? drives the post-generation "revise" affordances
  // (shares the ["changes", pid] cache with ChangesPanel; false until the first SRS exists).
  const changesQ = useQuery({ queryKey: ["changes", pid], queryFn: () => getChanges(pid) });
  const hasBaseline = changesQ.data?.has_baseline ?? false;
  // the "revise requirements" section auto-opens ONCE when a baseline first appears, then respects
  // the user's own collapse/expand (so it never fights a re-render that refetches the delta).
  const [reqOpen, setReqOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);   // "+ Add requirement" inline form
  useEffect(() => { if (hasBaseline) setReqOpen(true); }, [hasBaseline]);
  const reqSectionRef = useRef<HTMLDetailsElement>(null);
  // "Revise" jumps to Review with the editable requirements list OPEN, UNFILTERED, and in view.
  // The triage tabs (attention/review/routine) only show pending items, so once everything is
  // approved they are empty — resetting the filter to "all" is what makes approved rows editable.
  const goRevise = () => {
    setFilter("all");
    setReqOpen(true);
    setPhase("review");
    window.setTimeout(() => reqSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
  };
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
  const featureList = [...new Set(allReqs.map((r) => r.feature).filter(Boolean) as string[])].sort();
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
  // Every visible requirement can be selected for a bulk action — including approved ones, so a
  // reviewer can bulk-reject requirements while revising (not only clear pending ones).
  const selectableRows = visible;
  const allSelected = selectableRows.length > 0 && selectableRows.every((r) => selected.has(r.id));
  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  const toggleAll = () =>
    setSelected(allSelected ? new Set() : new Set(selectableRows.map((r) => r.id)));
  const busy = review.isPending || bulk.isPending || autoAcc.isPending || acceptAllM.isPending;

  if (!entered) return <Landing onEnter={() => setEntered(true)} />;

  return (
    <div className="page">
      <header className="topbar">
        <div className="brand" onClick={() => setEntered(false)} title="Back to home">RGA</div>
        <span className={`model ${mockProvider ? "warn" : ""}`}>model: {provider}</span>
        <span className="grow" />
        <label className="proj">
          Project&nbsp;
          <input
            value={pidDraft}
            disabled={running || generating}
            title={running || generating ? "finish or leave the current run before switching project" : "project id (press Enter to switch)"}
            onChange={(e) => setPidDraft(e.target.value)}
            onBlur={commitPid}
            onKeyDown={(e) => { if (e.key === "Enter") { commitPid(); (e.target as HTMLInputElement).blur(); } }}
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
        <li className={phase === "srs" ? "on" : ""}><button className="stepbtn" disabled={!(gate.data?.ready || genStatus.data?.state === "done" || changeGen.isSuccess)} onClick={() => (gate.data?.ready || genStatus.data?.state === "done" || changeGen.isSuccess) && setPhase("srs")}>4 · {mode === "existing" ? "Change docs" : "SRS"}</button></li>
      </ol>

      <div className="phaseview" key={phase}>
      {/* ---- PHASE 1 · INPUT ---- */}
      {phase === "input" && (
      <section className="panel">
        <h2>1 · Choose how you're building</h2>
        <div className="modepick">
          <button className={`modecard ${mode === "fresh" ? "on" : ""}`} onClick={() => chooseMode("fresh")}>
            <b>Net-new system</b>
            <span>Build from scratch — a full IEEE-830 <b>SRS &amp; RTM</b> elicited from your business &amp; requirement documents.</span>
          </button>
          <button className={`modecard ${mode === "existing" ? "on" : ""}`} onClick={() => chooseMode("existing")}>
            <b>Existing codebase</b>
            <span>Evolve a live repository — RGA analyzes the source, reconstructs its requirements, and produces a <b>scoped SRS &amp; RTM</b> mapped to the real code.</span>
          </button>
        </div>

        {mode === "existing" && (
          <div className="cb-step">
            <h3>Step A · Analyze the existing codebase</h3>
            <p className="muted small">Upload the existing repository as a <b>.zip</b> (or point to its folder). RGA scans it
              and synthesizes an <b>enhancement-requirements PDF</b> — proposing <b>new</b> requirements that are distinct
              from what the code already implements. That PDF (shown in Step B) is the input the pipeline extracts from,
              so the SRS/RTM specify the changes for the existing system.</p>
            <CodebaseAttach pid={pid} onAttached={(c) => setCorpus(c)} />
          </div>
        )}

        {mode === "existing" ? (
          <>
            <h3 className="cb-step-h">Step B · Requirements input document</h3>
            <p className="muted">RGA synthesized a PDF of proposed <b>enhancement requirements</b> (new, distinct from the
              existing code). Choose it below — or pick another document set — then run the pipeline to fetch the
              requirements, review, and generate the SRS &amp; RTM.</p>
            <div className="piperow">
              <label>
                Input document&nbsp;
                <select value={corpus} onChange={(e) => setCorpus(e.target.value)}>
                  {(corpora.data?.corpora ?? []).map((c) => (
                    <option key={c.path} value={c.path}>
                      {c.id} ({c.kind}, {c.docs.length} docs)
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </>
        ) : (
          <>
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
          </>
        )}

        {mode === "existing" && !selectedCorpus && codebaseAttached && (
          <p className="muted small">Preparing the synthetic requirements document…</p>
        )}

        {/* what's actually being ingested — the metadata of every file in the selected set */}
        {selectedCorpus && (selectedCorpus.files?.length ?? 0) > 0 && (
          <div className="filemeta">
            <div className="filemeta-head">
              <span>Documents in <b>{selectedCorpus.id}</b></span>
              <span className="muted small">{selectedCorpus.files!.length} file(s) · {selectedCorpus.kind}</span>
            </div>
            <div className="tablewrap">
              <table>
                <thead>
                  <tr><th>#</th><th>File</th><th>Format</th><th>Size</th></tr>
                </thead>
                <tbody>
                  {selectedCorpus.files!.map((f, i) => (
                    <tr key={`${f.doc_id}:${f.name}`}>
                      <td className="muted">{i + 1}</td>
                      <td className="fname">{f.name}</td>
                      <td className="fext">{f.ext}</td>
                      <td className="muted">{f.size}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <div className="phase-cta">
          <button
            className="btn-primary lg"
            disabled={!corpus || running || mockProvider || (mode === "existing" && !codebaseAttached)}
            title={mockProvider ? "start the server with --provider foundry to run extraction" : corpus}
            onClick={() => run.mutate()}
          >
            {running ? "Starting…" : "Run the agent pipeline →"}
          </button>
          {mode === "existing" && !codebaseAttached && (
            <p className="muted small">Attach the existing codebase (Step A) before running.</p>
          )}
        </div>
        {run.isError && <p className="gate-blocked">Could not start the run: {(run.error as Error).message}</p>}
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
        {status.data?.state === "error" && <p className="gate-blocked">{status.data.message}</p>}
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
        </div>
        <p className="muted">Work top to bottom: read the requirements, resolve the decisions, pick the technology stack — the actions to clear the rest and generate the SRS/RTM are at the bottom of the page.</p>

        {list.isLoading && <p>Loading…</p>}
        {list.data && allReqs.length === 0 && (
          <p className="muted">No requirements yet — go back to <b>Input</b> and run the pipeline.</p>
        )}

        {/* Agile: what changed since the last generated baseline (only shows after a first SRS) */}
        {list.data && allReqs.length > 0 && <ChangesPanel pid={pid} />}

        {/* PRIMARY surface: review by DECISION (clustered, owner-routed, propose-don't-ask) */}
        {list.data && allReqs.length > 0 && <Decisions pid={pid} reqs={allReqs} onResolved={invalidate} />}

        {/* Technology Stack (SRS §7): pick one candidate per aspect (recommended = default).
            Hidden for brownfield — the existing codebase already defines the stack. */}
        {mode !== "existing" && list.data && allReqs.length > 0 && <TechStack pid={pid} />}

        {list.data && allReqs.length > 0 && (
          <>
            <details className="reqdetail" ref={reqSectionRef} open={reqOpen}
              onToggle={(e) => setReqOpen((e.target as HTMLDetailsElement).open)}>
              <summary>{hasBaseline
                ? `Revise requirements (${allReqs.length}) — edit any approved requirement; an edit re-opens it for a quick re-approval, then regenerate for the next SRS version`
                : `All requirements (${allReqs.length}) — detail & manual override`}</summary>
            {/* triage toolbar: cut the manual work */}
            <div className="triage">
              <div className="tabs">
                <button className={filter === "all" ? "on" : ""} onClick={() => setFilter("all")}>All ({allReqs.length})</button>
                <button className={filter === "attention" ? "on" : ""} onClick={() => setFilter("attention")}><span className="tdot attention" />Attention ({attentionCount})</button>
                <button className={filter === "review" ? "on" : ""} onClick={() => setFilter("review")}><span className="tdot review" />Review ({reviewCount})</button>
                <button className={filter === "routine" ? "on" : ""} onClick={() => setFilter("routine")}><span className="tdot routine" />Routine ({routineCount})</button>
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
                Action failed: {((acceptAllM.error || autoAcc.error || bulk.error) as Error).message}.
                If this is a 404, restart the server (`python -m rga serve --provider foundry`) so it has the latest endpoints.
              </p>
            )}

            <div className="reqlist-bar">
              <label className="selall">
                <input type="checkbox" checked={allSelected} disabled={visible.length === 0} onChange={toggleAll} />
                Select all shown
              </label>
              <span className="reqlist-bar-right">
                <span className="muted small">Showing {visible.length} of {allReqs.length}</span>
                <button className="btn-ghost sm addreq-btn" onClick={() => setAddOpen((v) => !v)}>
                  {addOpen ? "Close" : "+ Add requirement"}
                </button>
              </span>
            </div>
            {addOpen && (
              <AddRequirementForm
                pid={pid}
                features={featureList}
                onClose={() => setAddOpen(false)}
                onAdded={() => { setFilter("all"); invalidate(); }}
              />
            )}
            <div className="reqlist">
              {visible.map((r) => (
                <Row
                  key={r.id}
                  r={r}
                  busy={busy}
                  selected={selected.has(r.id)}
                  selectable
                  hasBaseline={hasBaseline}
                  onToggle={() => toggle(r.id)}
                  onReview={(action, edits) => review.mutate({ id: r.id, action, edits })}
                />
              ))}
              {visible.length === 0 && (
                <div className="reqempty muted">
                  {filter === "all"
                    ? "No requirements yet."
                    : <>The “{filter}” tab only lists requirements still awaiting review — approved ones live under{" "}
                        <button className="linklike" onClick={() => setFilter("all")}>All ({allReqs.length})</button>, where you can edit any of them.</>}
                </div>
              )}
            </div>
            </details>

            {/* all page-level actions live at the BOTTOM — after reading the requirements + picking the stack */}
            <div className="reviewfooter">
              {gate.data && !gate.data.ready && <p className="gate-blocked">{gate.data.reason}</p>}
              {gate.data?.ready && <p className="ready-note">Every requirement is triaged — you can generate the SRS &amp; RTM now.</p>}
              {pending.length > 0 && (
                <p className="muted small">{pending.length} requirement(s) still awaiting review — resolve the decisions above, or clear the rest here before generating.</p>
              )}
              {(acceptAllM.isError || autoAcc.isError) && (
                <p className="gate-blocked">{((acceptAllM.error || autoAcc.error) as Error).message}</p>
              )}
              <div className="footer-actions">
                <div className="footer-left">
                  <button
                    className="btn-ghost"
                    disabled={busy || routineCount === 0}
                    title="Approve the clean, high-confidence, routine requirements (each logged)"
                    onClick={() => autoAcc.mutate()}
                  >
                    {autoAcc.isPending ? "Approving…" : `Auto-accept routine (${routineCount})`}
                  </button>
                  <button
                    className="btn-ghost"
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
                {mode === "existing" ? (
                  <button
                    className="btn-primary lg"
                    disabled={!gate.data?.ready || changeGen.isPending}
                    title={gate.data?.reason}
                    onClick={() => changeGen.mutate()}
                  >
                    {changeGen.isPending ? "Generating…" : "Generate Change SRS + RTM →"}
                  </button>
                ) : (
                  <button
                    className="btn-primary lg"
                    disabled={!gate.data?.ready || generating}
                    title={gate.data?.reason}
                    onClick={() => generate.mutate()}
                  >
                    {generating ? "Generating…" : "Generate SRS / RTM →"}
                  </button>
                )}
              </div>
              {changeGen.isError && <p className="gate-blocked">{(changeGen.error as Error).message}</p>}
            </div>
          </>
        )}
      </section>
      )}

      {/* ---- PHASE 4 · SRS ---- */}
      {phase === "srs" && (
      <section className="panel">
        {mode === "existing" ? (
          <>
            <div className="reviewhead">
              <h2>4 · Change documents</h2>
              {changeGen.isSuccess && <span className="ok">Change SRS + RTM generated</span>}
            </div>
            {changeGen.isError && <p className="gate-blocked">{(changeGen.error as Error).message}</p>}
            {changeGen.isSuccess
              ? <CodebaseResults pid={pid} />
              : <p className="muted">No change documents yet — go to <b>Review</b> and click <b>Generate Change SRS + RTM</b>.</p>}
          </>
        ) : (
        <>
        <div className="reviewhead">
          <h2>4 · Generated documents</h2>
          {genStatus.data?.state === "done" && (() => {
            const v = (genStatus.data.manifest as { srs_version?: string } | undefined)?.srs_version;
            return v ? <span className="ver-badge">SRS v{v}</span> : null;
          })()}
          {genStatus.data?.state === "done" && (
            (genStatus.data.manifest as { traceability_complete?: boolean } | undefined)?.traceability_complete
              ? <span className="ok">{genStatus.data.count} approved requirement(s) · full traceability</span>
              : <span className="gate-blocked">{genStatus.data.count} approved · traceability incomplete — a requirement is missing its source</span>
          )}
          {genStatus.data?.state === "done" && (() => {
            const fv = (genStatus.data.manifest as { format_validation?: { ok?: boolean; checks_passed?: number; checks_total?: number; summary?: string } } | undefined)?.format_validation;
            if (!fv) return null;
            return fv.ok
              ? <span className="ok" title={fv.summary}>format validated ✓ ({fv.checks_passed}/{fv.checks_total})</span>
              : <span className="gate-blocked" title={fv.summary}>format check failed — see manifest</span>;
          })()}
        </div>
        {generate.isError && <p className="gate-blocked">{(generate.error as Error).message}</p>}
        {genStatus.data?.state === "done" && (
          <div className="revise-cta">
            <div className="revise-cta-text">
              <b>A requirement changed after this SRS?</b>
              <p className="muted small">
                Revise it in Review and regenerate — the SRS becomes a new version with the change recorded in
                the Revision History, and unchanged requirements carry forward automatically. Editing an approved
                requirement re-opens it for a quick re-approval first.
              </p>
            </div>
            <button className="btn-primary" onClick={goRevise}>Revise requirements →</button>
          </div>
        )}
        {genStatus.data && genStatus.data.state !== "done" && genStatus.data.state !== "error" && (
          <div className={`runstatus ${genStatus.data.state}`}>
            <span className="spinner" data-on={generating} /><b>Generation</b>
            <span className="muted">{genStatus.data.message}</span>
            <p className="muted small genhint">Drafting the full SRS prose with the LLM is the longest step — it can take a minute or two. If the model is slow or unavailable it falls back automatically — this page updates on its own when it finishes (even in a background tab).</p>
          </div>
        )}
        {genStatus.data?.state === "error" && (
          <p className="gate-blocked">Generation failed: {genStatus.data.message}</p>
        )}
        {genStatus.data?.state === "done"
          ? <><Results pid={pid} /><AssetPicker pid={pid} /></>
          : !generating && <p className="muted">No documents yet — go to Review and click <b>Generate SRS / RTM</b>.</p>}
        </>
        )}
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
            <span className="pdot" />
            <span className="plabel">{label}</span>
            {state === "active" && status?.message && <span className="pmsg muted small">{status.message}</span>}
          </div>
        );
      })}
    </div>
  );
}

// --- minimal, dependency-free Markdown renderer for the generated SRS/RTM preview ----------------
// Renders the subset our generator emits (headings, bold/italic/code, bullet lists, GFM tables,
// blockquotes) and swallows the docx-only [[TITLEPAGE]] / [[TOC]] markers so they never show raw.
function mdInline(text: string, kb: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`]+`)/g;
  let last = 0, i = 0, m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("**")) out.push(<strong key={`${kb}-${i}`}>{t.slice(2, -2)}</strong>);
    else if (t.startsWith("*")) out.push(<em key={`${kb}-${i}`}>{t.slice(1, -1)}</em>);
    else out.push(<code key={`${kb}-${i}`}>{t.slice(1, -1)}</code>);
    last = m.index + t.length; i++;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function Markdown({ text }: { text: string }) {
  const out: ReactNode[] = [];
  let table: string[] = [], list: string[] = [], title: string[] | null = null, k = 0;

  const flushTable = () => {
    if (!table.length) return;
    const rows = table.map((r) =>
      r.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|")));
    table = [];
    const cells = rows.filter((r) => !r.every((c) => /^:?-{2,}:?$/.test(c) || c === ""));
    if (!cells.length) return;
    const [head, ...body] = cells;
    const kk = k++;
    out.push(
      <table key={`t${kk}`}>
        <thead><tr>{head.map((c, j) => <th key={j}>{mdInline(c, `th${kk}-${j}`)}</th>)}</tr></thead>
        <tbody>{body.map((row, ri) => (
          <tr key={ri}>{head.map((_h, j) => <td key={j}>{mdInline(row[j] ?? "", `td${kk}-${ri}-${j}`)}</td>)}</tr>
        ))}</tbody>
      </table>,
    );
  };
  const flushList = () => {
    if (!list.length) return;
    const items = list; list = [];
    const kk = k++;
    out.push(
      <ul key={`u${kk}`}>{items.map((it, idx) => {
        const indent = it.match(/^\s*/)?.[0].length ?? 0;
        const body = it.replace(/^\s*[-*]\s+/, "");
        return <li key={idx} style={indent >= 2 ? { marginLeft: (indent / 4) * 16 } : undefined}>{mdInline(body, `li${kk}-${idx}`)}</li>;
      })}</ul>,
    );
  };

  for (const raw of (text || "").split("\n")) {
    const s = raw.trim();
    if (s === "[[TITLEPAGE]]") { flushTable(); flushList(); title = []; continue; }
    if (s === "[[/TITLEPAGE]]") {
      const tb = title ?? []; title = null;
      out.push(
        <div className="titleblock" key={`tp${k++}`}>
          {tb.map((ln, idx) => ln.startsWith("#")
            ? <h1 key={idx}>{mdInline(ln.replace(/^#+\s*/, ""), `tph${idx}`)}</h1>
            : <p key={idx}>{mdInline(ln, `tpp${idx}`)}</p>)}
        </div>,
      );
      continue;
    }
    if (title !== null) { if (s) title.push(s); continue; }
    if (s === "[[TOC]]" || s === "[[/TOC]]") continue;

    if (s.startsWith("|")) { flushList(); table.push(raw); continue; }
    flushTable();
    if (/^\s*[-*]\s+/.test(raw)) { list.push(raw); continue; }
    flushList();
    if (!s || s === "---") continue;
    const h = s.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      const inner = mdInline(h[2], `h${k}`), kk = k++;
      out.push(h[1].length === 1 ? <h1 key={`h${kk}`}>{inner}</h1>
        : h[1].length === 2 ? <h2 key={`h${kk}`}>{inner}</h2>
        : h[1].length === 3 ? <h3 key={`h${kk}`}>{inner}</h3>
        : <h4 key={`h${kk}`}>{inner}</h4>);
      continue;
    }
    if (s.startsWith("> ")) { out.push(<blockquote key={`b${k++}`}>{mdInline(s.slice(2), `bq${k}`)}</blockquote>); continue; }
    out.push(<p key={`p${k++}`}>{mdInline(s, `p${k}`)}</p>);
  }
  flushTable(); flushList();
  return <div className="doc">{out}</div>;
}

// Minimal RFC-4180 CSV parser (quoted fields, "" escapes, embedded commas/newlines) for the RTM view.
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [], field = "", inQ = false, i = 0;
  while (i < text.length) {
    const c = text[i];
    if (inQ) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i += 2; continue; } inQ = false; i++; continue; }
      field += c; i++; continue;
    }
    if (c === '"') { inQ = true; i++; continue; }
    if (c === ",") { row.push(field); field = ""; i++; continue; }
    if (c === "\n") { row.push(field); rows.push(row); row = []; field = ""; i++; continue; }
    if (c === "\r") { i++; continue; }
    field += c; i++;
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.some((c) => c !== ""));
}

function CsvTable({ text }: { text: string }) {
  const rows = parseCsv(text);
  if (rows.length === 0) return <div className="doc muted">Empty.</div>;
  const [head, ...body] = rows;
  return (
    <div className="doc tablewrap">
      <table className="csvtable">
        <thead><tr>{head.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
        <tbody>
          {body.map((r, ri) => (
            <tr key={ri}>{head.map((_, ci) => <td key={ci}>{r[ci] ?? ""}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type ArtifactTab = "SRS.md" | "RTM.csv";   // SRS is Markdown/.docx; RTM is now a CSV
function Results({ pid }: { pid: string }) {
  const [tab, setTab] = useState<ArtifactTab>("SRS.md");
  const doc = useQuery({ queryKey: ["artifact", pid, tab], queryFn: () => getArtifact(pid, tab) });
  const isRtm = tab === "RTM.csv";
  const dlHref = isRtm ? `/api/projects/${pid}/artifacts/RTM.csv` : `/api/projects/${pid}/artifacts/SRS.docx`;
  return (
    <>
      <div className="tabs doctabs">
        <button className={tab === "SRS.md" ? "on" : ""} onClick={() => setTab("SRS.md")}>SRS (IEEE-830)</button>
        <button className={isRtm ? "on" : ""} onClick={() => setTab("RTM.csv")}>Traceability Matrix</button>
        <span className="muted small dl-note">saved to handoff/{pid}/ (SRS .md/.docx · RTM .csv)</span>
        <a className="dl-docx dl-right" href={dlHref} download>{isRtm ? "Download (.csv)" : "Download (.docx)"}</a>
      </div>
      {doc.isLoading
        ? <div className="doc">Loading…</div>
        : doc.isError
          ? <div className="doc gate-blocked">Could not load {tab}: {(doc.error as Error).message}</div>
          : isRtm
            ? <CsvTable text={doc.data ?? ""} />
            : <Markdown text={doc.data ?? ""} />}
    </>
  );
}

// An in-app file explorer that browses the LOCAL disk via the backend (no browser upload). The
// user navigates folders and ticks files; "Add" returns their absolute paths to the caller.
function FileExplorer({ target, onAdd, onClose }: {
  target: "asset1" | "asset2"; onAdd: (paths: string[]) => void; onClose: () => void;
}) {
  const [roots, setRoots] = useState<{ name: string; path: string }[]>([]);
  const [cwd, setCwd] = useState<string>("");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const listing = useQuery({ queryKey: ["fs", cwd], queryFn: () => getFsList(cwd), enabled: !!cwd });

  useEffect(() => {
    getFsRoots().then((r) => { setRoots(r.roots); setCwd(r.cwd || r.roots[0]?.path || ""); }).catch(() => { /* ignore */ });
  }, []);

  const go = (p: string) => { setSel(new Set()); setCwd(p); };
  const toggle = (p: string) => { const n = new Set(sel); if (n.has(p)) n.delete(p); else n.add(p); setSel(n); };
  const data = listing.data;

  return (
    <div className="fx-overlay" onClick={onClose}>
      <div className="fx-panel" onClick={(e) => e.stopPropagation()}>
        <div className="fx-head">
          <b>Select files — {target === "asset1" ? "Asset 1 (design elements)" : "Asset 2 (static images)"}</b>
          <button type="button" className="fx-close" title="close" onClick={onClose}>×</button>
        </div>
        <div className="fx-roots">
          {roots.map((r) => (
            <button type="button" key={r.path} className={data?.path?.startsWith(r.path) ? "on" : ""} onClick={() => go(r.path)}>{r.name}</button>
          ))}
        </div>
        <div className="fx-path">
          <button type="button" disabled={!data?.parent} onClick={() => data?.parent && go(data.parent)}>↑ Up</button>
          <span className="fx-cwd" title={data?.path ?? cwd}>{data?.path ?? cwd ?? "…"}</span>
        </div>
        <div className="fx-list">
          {listing.isLoading && <p className="muted">Loading…</p>}
          {listing.isError && <p className="gate-blocked">{(listing.error as Error).message}</p>}
          {data?.entries.map((e) => e.is_dir ? (
            <div key={e.path} className="fx-row fx-dir" onClick={() => go(e.path)} title={e.name}>
              <span className="fx-ic">📁</span><span className="fx-name">{e.name}</span>
            </div>
          ) : (
            <label key={e.path} className={`fx-row fx-file ${sel.has(e.path) ? "sel" : ""}`}>
              <input type="checkbox" checked={sel.has(e.path)} onChange={() => toggle(e.path)} />
              <span className="fx-ic">{e.is_image ? "🖼️" : "📄"}</span>
              <span className="fx-name" title={e.name}>{e.name}</span>
              <span className="muted small fx-size">{e.size}</span>
            </label>
          ))}
          {data && data.entries.length === 0 && <p className="muted">This folder is empty.</p>}
        </div>
        <div className="fx-foot">
          <span className="muted small">{sel.size} file(s) selected</span>
          <div className="fx-foot-actions">
            <button type="button" onClick={onClose}>Cancel</button>
            <button type="button" className="btn-primary sm" disabled={sel.size === 0}
                    onClick={() => { onAdd([...sel]); onClose(); }}>Add {sel.size} file(s)</button>
          </div>
        </div>
      </div>
    </div>
  );
}

// Browse the local disk and select files for each asset folder, then download the whole handoff as
// a ZIP (SRS + asset1/ + asset2/). Files are read locally by the backend — nothing is uploaded.
function AssetPicker({ pid }: { pid: string }) {
  const [a1, setA1] = useState<string[]>([]);
  const [a2, setA2] = useState<string[]>([]);
  const [explorer, setExplorer] = useState<null | "asset1" | "asset2">(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const baseName = (p: string) => p.split(/[\\/]/).pop() || p;
  const addPaths = (target: "asset1" | "asset2", paths: string[]) => {
    const setter = target === "asset1" ? setA1 : setA2;
    setter((cur) => Array.from(new Set([...cur, ...paths])));
  };
  const removeAt = (setter: React.Dispatch<React.SetStateAction<string[]>>, i: number) =>
    setter((cur) => cur.filter((_, idx) => idx !== i));

  const download = async () => {
    setBusy(true); setErr("");
    try { await downloadHandoffZip(pid, a1, a2); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  };

  const column = (
    files: string[], setter: React.Dispatch<React.SetStateAction<string[]>>,
    target: "asset1" | "asset2", title: string,
  ) => (
    <div className="asset-col">
      <div className="asset-col-head">
        <b>{title}</b><span className="muted small">{files.length} file(s)</span>
      </div>
      <button type="button" className="btn-ghost" onClick={() => setExplorer(target)}>Browse files…</button>
      {files.length === 0
        ? <p className="asset-empty muted small">No files chosen yet.</p>
        : <ul className="asset-files">
            {files.map((p, i) => (
              <li key={`${p}:${i}`}>
                <span className="asset-fname" title={p}>{baseName(p)}</span>
                <button type="button" className="asset-x" title="remove" onClick={() => removeAt(setter, i)}>×</button>
              </li>
            ))}
          </ul>}
    </div>
  );

  return (
    <div className="assetpicker">
      <div className="reviewhead">
        <h3>Handoff package — choose assets</h3>
        <span className="counts">Asset 1: {a1.length} · Asset 2: {a2.length}</span>
      </div>
      <p className="muted small">
        Browse your computer and pick files for each folder — nothing is uploaded; files are read
        locally and packaged into the ZIP.
      </p>
      <div className="asset-cols">
        {column(a1, setA1, "asset1", "Asset 1 — design elements")}
        {column(a2, setA2, "asset2", "Asset 2 — static images")}
      </div>
      {err && <p className="gate-blocked">{err}</p>}
      <div className="phase-cta">
        <button type="button" className="btn-primary lg" disabled={busy} onClick={() => void download()}>
          {busy ? "Packaging…" : "Download handoff package (.zip)"}
        </button>
      </div>
      {explorer && (
        <FileExplorer target={explorer} onClose={() => setExplorer(null)}
                      onAdd={(paths) => addPaths(explorer, paths)} />
      )}
    </div>
  );
}

// Manually author a new requirement (created APPROVED, human-sourced) — the "added" case of the
// agile loop. Reuses POST /projects/{pid}/requirements; the new requirement shows as NEW in the
// Changes panel and flows into the next SRS version.
function AddRequirementForm({ pid, features, onClose, onAdded }: {
  pid: string; features: string[]; onClose: () => void; onAdded: () => void;
}) {
  const [stmt, setStmt] = useState("");
  const [rtype, setRtype] = useState("functional");
  const [feature, setFeature] = useState("");
  const [priority, setPriority] = useState("");
  const [nfrCat, setNfrCat] = useState("");
  const m = useMutation({
    mutationFn: () => addRequirement(pid, stmt.trim(), "Added manually", {
      rtype,
      feature: rtype === "functional" && feature.trim() ? feature.trim() : undefined,
      priority: priority || undefined,
      nfr_category: rtype === "non_functional" && nfrCat ? nfrCat : undefined,
    }),
    onSuccess: () => { onAdded(); onClose(); },
  });
  return (
    <div className="addreq-card">
      <textarea className="reqedit" rows={2} autoFocus placeholder="The system shall…"
                value={stmt} onChange={(e) => setStmt(e.target.value)} />
      <div className="addreq-fields">
        <label>Type
          <select value={rtype} onChange={(e) => setRtype(e.target.value)}>
            <option value="functional">functional</option>
            <option value="non_functional">non-functional</option>
            <option value="business">business</option>
            <option value="constraint">constraint</option>
            <option value="assumption">assumption</option>
          </select>
        </label>
        {rtype === "functional" && (
          <label>Feature
            <input list="addreq-features" placeholder="(optional)" value={feature}
                   onChange={(e) => setFeature(e.target.value)} />
            <datalist id="addreq-features">{features.map((f) => <option key={f} value={f} />)}</datalist>
          </label>
        )}
        {rtype === "non_functional" && (
          <label>Category
            <select value={nfrCat} onChange={(e) => setNfrCat(e.target.value)}>
              <option value="">quality (§5.4)</option>
              <option value="performance">performance (§5.1)</option>
              <option value="safety">safety (§5.2)</option>
              <option value="security">security (§5.3)</option>
            </select>
          </label>
        )}
        <label>Priority
          <select value={priority} onChange={(e) => setPriority(e.target.value)}>
            <option value="">auto</option>
            <option value="must">must</option>
            <option value="should">should</option>
            <option value="could">could</option>
            <option value="wont">won&apos;t</option>
          </select>
        </label>
      </div>
      <div className="addreq-actions">
        <button className="btn-primary sm" disabled={!stmt.trim() || m.isPending} onClick={() => m.mutate()}>
          {m.isPending ? "Adding…" : "Add requirement"}
        </button>
        <button className="ghost sm" disabled={m.isPending} onClick={onClose}>Cancel</button>
        {m.isError && <span className="gate-blocked small">{(m.error as Error).message}</span>}
      </div>
    </div>
  );
}

function Row({
  r, busy, selected, selectable, hasBaseline, onToggle, onReview,
}: {
  r: Requirement;
  busy: boolean;
  selected: boolean;
  selectable: boolean;
  hasBaseline: boolean;
  onToggle: () => void;
  onReview: (action: ReviewAction, edits?: Record<string, unknown>) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(r.statement);
  const [showSrc, setShowSrc] = useState(false);
  const flags = r.quality.ambiguity_flags;
  const conflicts = r.conflicts_with ?? [];
  const warnCount = flags.length + conflicts.length;
  const warnTitle = [...flags, ...(conflicts.length ? [`conflicts with ${conflicts.join(", ")}`] : [])].join("; ");
  useEffect(() => { if (!editing) setDraft(r.statement); }, [r.statement, editing]);
  // editing an already-approved requirement that is in a frozen baseline re-opens it for
  // re-approval (change-control), so the button must not claim it stays approved.
  const willReReview = r.status === "approved" && hasBaseline;
  const stmtChanged = draft.trim() !== r.statement.trim();
  const lvl = r.triage?.level ?? "review";

  return (
    <div className={`reqcard status-${r.status}${selected ? " sel" : ""}`}>
      {selectable && (
        <input className="reqcheck" type="checkbox" checked={selected} onChange={onToggle}
               title="select for a bulk action" />
      )}
      <div className="reqcard-body">
        {editing ? (
          <textarea className="reqedit" value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} autoFocus />
        ) : (
          <div className="reqcard-stmt">
            {r.statement}
            {r.inferred && <span className="tag inferred">inferred</span>}
          </div>
        )}
        <div className="reqcard-meta">
          <span className={`tdot ${lvl}`}
                title={`triage: ${lvl} — ${(r.triage?.reasons ?? []).join("; ") || "clear, grounded, high-confidence"}`} />
          <span className={`badge ${r.status}`}>{r.status.replace(/_/g, " ")}</span>
          <span className="chip">{r.rtype.replace(/_/g, " ")}</span>
          {r.priority && <span className={`chip prio-${r.priority}`}>{r.priority}</span>}
          {warnCount > 0 && (
            <span className="chip warn" title={warnTitle}>⚠ {warnCount} flag{warnCount > 1 ? "s" : ""}</span>
          )}
          {r.sources.length > 0 && (
            <button className="src-toggle" onClick={() => setShowSrc((v) => !v)}>
              {showSrc ? "Hide source" : `Source (${r.sources.length})`}
            </button>
          )}
        </div>
        {showSrc && r.sources.length > 0 && (
          <div className="reqcard-src">
            {r.sources.map((s, i) => (
              <blockquote key={i} title={`${s.doc_id} · ${s.location}`}>“{s.quote}”</blockquote>
            ))}
          </div>
        )}
        {editing && willReReview && stmtChanged && (
          <p className="muted small reopen-hint">Changing an approved requirement re-opens it for re-approval before the next SRS.</p>
        )}
      </div>
      <div className="reqcard-actions">
        {editing ? (
          <>
            <button className="btn-primary sm" disabled={busy || !draft.trim()} onClick={() => { onReview("edit", { statement: draft }); setEditing(false); }}>
              {willReReview && stmtChanged ? "Save (needs re-approval)" : "Save & approve"}
            </button>
            <button className="ghost" onClick={() => { setDraft(r.statement); setEditing(false); }}>Cancel</button>
          </>
        ) : (
          <>
            {r.status !== "approved" && (
              <button className="btn-primary sm" disabled={busy} onClick={() => onReview("accept")}>
                {r.status === "rejected" ? "Restore" : "Accept"}
              </button>
            )}
            <button disabled={busy} className="ghost" onClick={() => setEditing(true)}>Edit</button>
            {r.status !== "rejected" && (
              <button disabled={busy} className="danger" onClick={() => onReview("reject")}>Reject</button>
            )}
          </>
        )}
      </div>
    </div>
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

// Brownfield: attach an EXISTING codebase, then generate a change-only SRS + RTM scoped to it.
// Collapsed by default (greenfield projects ignore it); opens once a codebase is attached.
// Attach + understand an existing codebase (path or .zip). Used in the Input phase's "Existing
// codebase" mode — Step A. Generation of the change pack happens later in the Review→SRS flow.
function CodebaseAttach({ pid, onAttached }: { pid: string; onAttached: (corpus: string) => void }) {
  const qc = useQueryClient();
  const cb = useQuery({ queryKey: ["codebase", pid], queryFn: () => getCodebase(pid) });
  const [path, setPath] = useState("");
  const done = (r: { corpus?: string }) => {
    void qc.invalidateQueries({ queryKey: ["codebase", pid] });
    void qc.invalidateQueries({ queryKey: ["corpora"] });
    if (r.corpus) onAttached(r.corpus);   // the synthetic requirements doc becomes the run corpus
  };
  const attach = useMutation({
    mutationFn: () => attachCodebase(pid, path.trim() || (cb.data?.root ?? "")),
    onSuccess: done,
  });
  const zip = useMutation({
    mutationFn: (f: File) => attachCodebaseZip(pid, f),
    onSuccess: done,
  });
  const info = cb.data;
  const attached = !!info?.attached;
  const busy = attach.isPending || zip.isPending;
  return (
    <div className="cb-attach-card">
      <div className="cb-attach-row">
        <label className="btn-primary cb-zip-btn">
          {zip.isPending ? "Uploading…" : attached ? "Replace .zip" : "Upload .zip"}
          <input type="file" accept=".zip,application/zip" hidden disabled={busy}
                 onChange={(e) => { const f = e.target.files?.[0]; if (f) zip.mutate(f); e.target.value = ""; }} />
        </label>
        <span className="muted small">or a local folder path:</span>
        <input type="text" placeholder={info?.root || "C:\\path\\to\\existing\\codebase"} value={path}
               onChange={(e) => setPath(e.target.value)} />
        <button className="btn-ghost sm" disabled={busy || (!path.trim() && !attached)} onClick={() => attach.mutate()}>
          {attach.isPending ? "Scanning…" : attached ? "Re-scan" : "Scan folder"}
        </button>
      </div>
      {(attach.isError || zip.isError) && (
        <p className="gate-blocked small">{((attach.error || zip.error) as Error).message}</p>
      )}
      {attached && info && (
        <div className="cb-attached">
          <p className="small"><span className="ok">✓ analyzed</span> · <b>{info.n_files}</b> source file(s) · <code>{info.root}</code>{info.truncated ? " (truncated)" : ""}</p>
          {info.doc && <p className="muted small">Synthesized an enhancement-requirements PDF (<code>{info.doc}</code>) — new requirements, distinct from the code → the input in Step B.</p>}
          {info.summary && <p className="muted">{info.summary}</p>}
          {(info.capabilities?.length ?? 0) > 0 && (
            <div className="tablewrap">
              <table className="cb-caps">
                <thead><tr><th>Module</th><th>Files</th><th>Language</th><th>Key components</th></tr></thead>
                <tbody>
                  {info.capabilities!.slice(0, 12).map((c) => (
                    <tr key={c.module}>
                      <td><code>{c.module}</code></td><td>{c.files}</td><td>{c.language}</td>
                      <td className="muted small">{c.key_symbols.slice(0, 6).join(", ") || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function CodebaseResults({ pid }: { pid: string }) {
  const [tab, setTab] = useState<"CHANGE_SRS.md" | "CHANGE_RTM.csv">("CHANGE_SRS.md");
  const doc = useQuery({ queryKey: ["artifact", pid, tab], queryFn: () => getArtifact(pid, tab) });
  const isRtm = tab === "CHANGE_RTM.csv";
  return (
    <div className="cb-results">
      <div className="tabs doctabs">
        <button className={!isRtm ? "on" : ""} onClick={() => setTab("CHANGE_SRS.md")}>Change SRS</button>
        <button className={isRtm ? "on" : ""} onClick={() => setTab("CHANGE_RTM.csv")}>Change RTM</button>
        <span className="muted small dl-note">saved to handoff/{pid}/</span>
        <a className="dl-docx dl-right" href={`/api/projects/${pid}/artifacts/${isRtm ? "CHANGE_RTM.csv" : "CHANGE_SRS.docx"}`} download>
          {isRtm ? "Download (.csv)" : "Download (.docx)"}
        </a>
      </div>
      {doc.isLoading ? <div className="doc">Loading…</div>
        : doc.isError ? <div className="doc gate-blocked">Could not load: {(doc.error as Error).message}</div>
        : isRtm ? <CsvTable text={doc.data ?? ""} /> : <Markdown text={doc.data ?? ""} />}
    </div>
  );
}

// Agile: the delta of the current approved set vs the last generated baseline (added / modified /
// removed). Only appears once a baseline exists — i.e. after the first SRS has been generated.
function ChangesPanel({ pid }: { pid: string }) {
  const q = useQuery({ queryKey: ["changes", pid], queryFn: () => getChanges(pid) });
  const d = q.data;
  if (!d || !d.has_baseline) return null;
  // The delta is computed against the APPROVED set, so it is only meaningful once review is complete
  // (nothing pending). Mid-review — e.g. right after a fresh run, with requirements still `candidate`
  // — un-reviewed items would misread as "removed", so hold the delta and explain why.
  if (!d.review_complete) {
    return (
      <div className="changes">
        <div className="reviewhead"><h3>Changes since Baseline v{d.baseline_version}</h3></div>
        <p className="muted small">Finish reviewing the requirements below — once nothing is left pending, the
          changes for the next SRS version (vs Baseline v{d.baseline_version}) appear here.</p>
      </div>
    );
  }
  const s = d.summary;
  return (
    <div className="changes">
      <div className="reviewhead">
        <h3>Changes since Baseline v{d.baseline_version}</h3>
        <span className="counts">{s.added} added · {s.modified} modified · {s.removed} removed · {d.unchanged} unchanged</span>
      </div>
      {d.total_changes === 0 ? (
        <p className="muted small">No changes since the last SRS — regenerating produces the same requirements.</p>
      ) : (
        <>
          <p className="muted small">
            These land in the next SRS version when you generate; unchanged approved requirements are carried forward automatically.
          </p>
          {d.added.map((c) => (
            <div key={c.id} className="chg">
              <div className="chg-head"><span className="chg-tag add">NEW</span><b>{c.srs_id}</b></div>
              <div className="chg-line add">+ {c.statement}</div>
            </div>
          ))}
          {d.modified.map((c) => (
            <div key={c.id} className="chg">
              <div className="chg-head"><span className="chg-tag mod">MODIFIED</span><b>{c.srs_id}</b></div>
              <div className="chg-line del">− {c.before}</div>
              <div className="chg-line add">+ {c.statement}</div>
            </div>
          ))}
          {d.removed.map((c) => (
            <div key={c.id} className="chg">
              <div className="chg-head"><span className="chg-tag rem">REMOVED</span><b>{c.srs_id}</b></div>
              <div className="chg-line rem">~ {c.statement}</div>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

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
      </div>
      <p className="muted small">
        Confirm the recommendation or override — resolving a decision applies to every requirement it
        affects, and is saved (it won't reappear on reload).
      </p>
      {applyMsg && <p className="muted small">{applyMsg}</p>}
      {applyAll.isError && <p className="gate-blocked">{(applyAll.error as Error).message}</p>}

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
        <p className="ok done-note">All decisions resolved — the clean requirements were auto-approved, and you've resolved the rest.</p>
      )}

      {shown.map((d) => {
        const b = busy === d.id;
        const addKind = d.kind === "gap" || d.kind === "possible_miss";
        const recB = /\bkeep\s*b\b|option\s*b\b/i.test(d.recommended);  // which side the recommendation favours
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
                  <button className={recB ? "" : "btn-primary sm"} disabled={b}
                    onClick={() => keep(d, d.affected[0], d.affected[1], "kept-a")}>Keep A</button>
                  <button className={recB ? "btn-primary sm" : ""} disabled={b}
                    onClick={() => keep(d, d.affected[1], d.affected[0], "kept-b")}>Keep B</button>
                </>
              ) : addKind ? (
                addBox(d)
              ) : d.affected.length > 0 ? (
                recommendsExclude(d) ? (
                  <>
                    <button className="btn-primary sm" disabled={b}
                      onClick={() => bulk(d, d.affected, "reject", "excluded")}>Exclude ({d.affected.length})</button>
                    <button disabled={b} onClick={() => bulk(d, d.affected, "accept", "included")}>Include ({d.affected.length})</button>
                  </>
                ) : (
                  <>
                    <button className="btn-primary sm" disabled={b}
                      onClick={() => bulk(d, d.affected, "accept", "included")}>Include ({d.affected.length})</button>
                    <button disabled={b} onClick={() => bulk(d, d.affected, "reject", "excluded")}>Exclude ({d.affected.length})</button>
                  </>
                )
              ) : (
                addBox(d)
              )}
            </div>
          </div>
        );
      })}
      {open.length > 0 && (
        <div className="decisions-footer">
          <span className="muted small">Reviewed everything? Apply the recommended verdict to all open decisions at once.</span>
          <button
            className="btn-primary"
            disabled={applyAll.isPending}
            title="Apply every open decision's recommended verdict at once (saved server-side)"
            onClick={() => {
              if (window.confirm(`Apply the recommended verdict to all ${open.length} open decision(s)?`))
                applyAll.mutate();
            }}
          >
            {applyAll.isPending ? "Applying…" : `Apply all recommended (${open.length})`}
          </button>
        </div>
      )}
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
        ? <p className="ready-note">A technology stack is stated in the source inputs — adopted as-is; no selection needed.</p>
        : <p className="muted small">Candidates are simple, widely-used choices. The recommended option is the default — change any, or pick <b>Other</b> to type your own, then generate.</p>}
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
                      {c.name}{c.recommended && <em className="rec"> Recommended</em>}
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
                      placeholder="Type a technology, then press Enter (e.g. Python + FastAPI, MongoDB, JWT)…"
                      value={otherText[a.key] ?? (isCustom ? (chosen ?? "") : "")}
                      disabled={busy === a.key}
                      onChange={(e) => setOtherText((s) => ({ ...s, [a.key]: e.target.value }))}
                      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); submitOther(a.key); } }}
                      onBlur={() => submitOther(a.key)}
                    />
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

