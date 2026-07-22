// Cinematic landing page — the entry into the RGA workspace. Clean, modern, Notion/Apple-inspired.

const PHASES = [
  { icon: "📥", title: "Input", body: "Drop in BRDs, call transcripts, emails, backlogs and forms (.docx / .pdf / .txt / .csv). Document structure is preserved." },
  { icon: "🔎", title: "Extract & analyze", body: "Agents extract every requirement with a verbatim source quote, verify grounding, consolidate duplicates, and flag conflicts & gaps." },
  { icon: "🧭", title: "Review by decision", body: "Clean items auto-approve. You confirm a handful of owner-routed decisions with recommendations — not hundreds of rows." },
  { icon: "📄", title: "Generate", body: "A traceable IEEE-830 SRS, RTM, seed models and an open-questions appendix — from approved requirements only." },
];

const GUARANTEES = [
  { title: "Grounded", body: "No verbatim quote, no requirement. Every line traces back to the source." },
  { title: "Nothing missed", body: "A coverage floor accounts for every source chunk; possible misses surface — they never silently drop." },
  { title: "Consolidated", body: "Same-obligation duplicates merge with full provenance. No over-extraction, no bloat." },
  { title: "Human-gated", body: "Nothing ships until a person confirms the decisions that actually matter." },
];

export function Landing({ onEnter }: { onEnter: () => void }) {
  return (
    <div className="landing">
      <div className="landing-bg" aria-hidden />

      <header className="landing-nav">
        <div className="brand">◆ RGA</div>
        <nav className="landing-links">
          <a href="#how">How it works</a>
          <a href="#trust">Guarantees</a>
          <button className="btn-primary sm" onClick={onEnter}>Launch</button>
        </nav>
      </header>

      <section className="hero">
        <div className="eyebrow">SDLC · Requirement Gathering &amp; Analysis</div>
        <h1 className="hero-title">From scattered inputs<br />to a review-ready SRS.</h1>
        <p className="hero-sub">
          Ingest messy BRDs, discovery calls, emails and backlogs. Extract, verify and consolidate
          every requirement — then review a handful of decisions, not hundreds of rows.
        </p>
        <div className="hero-cta">
          <button className="btn-primary lg" onClick={onEnter}>Launch workspace →</button>
          <a className="btn-ghost lg" href="#how">See how it works</a>
        </div>
        <div className="hero-stats">
          <div><b>100%</b><span>traceable to source</span></div>
          <div><b>zero</b><span>requirements dropped</span></div>
          <div><b>~17</b><span>decisions, not 300 rows</span></div>
        </div>
      </section>

      <section id="how" className="section">
        <h2 className="section-title">Four phases, one clean flow</h2>
        <div className="cards">
          {PHASES.map((p, i) => (
            <div className="card" key={p.title}>
              <div className="card-num">0{i + 1}</div>
              <div className="card-icon">{p.icon}</div>
              <h3>{p.title}</h3>
              <p>{p.body}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="trust" className="section">
        <h2 className="section-title">Built on guarantees, not vibes</h2>
        <div className="cards">
          {GUARANTEES.map((g) => (
            <div className="card soft" key={g.title}>
              <h3>{g.title}</h3>
              <p>{g.body}</p>
            </div>
          ))}
        </div>

        <div className="cta-band">
          <h2>Ready to turn documents into a specification?</h2>
          <button className="btn-primary lg" onClick={onEnter}>Launch workspace →</button>
        </div>
      </section>

      <footer className="landing-foot">
        Requirement Gathering &amp; Analysis · synthetic-data proof-of-concept · powered by Claude
      </footer>
    </div>
  );
}
