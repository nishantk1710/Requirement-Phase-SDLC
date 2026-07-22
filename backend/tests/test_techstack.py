"""Technology-stack agent (SRS §7): PER-ASPECT candidates for human review. Adopt a source-stated
stack, else propose popular candidates per aspect with exactly one recommended. Deterministic
without a provider. §7 renders the chosen (or recommended-default) candidate per aspect."""

from __future__ import annotations

import json as _json

from rga.agents.techstack import analyze_tech_stack, detect_stated_tokens
from rga.generate.srs import generate_srs
from rga.llm.mock import MockProvider
from rga.models import Chunk, Requirement, RType, SourceRef, Status


def _chunk(text, doc="brd", i=0):
    return Chunk(doc_id=doc, project_id="P", source_type="brd", index=i, location="1",
                 text=text, start=0, end=len(text))


def _req(rid, stmt, rtype=RType.functional, feature=None):
    return Requirement(id=rid, project_id="P", statement=stmt, rtype=rtype, feature=feature,
                       source_refs=[SourceRef(doc_id="brd", source_type="brd", location="1",
                                              raw_quote=stmt, start=0, end=len(stmt))])


def _approved(rid, stmt):
    r = _req(rid, stmt)
    r.status = Status.approved
    return r


def _one_rec_per_aspect(res):
    return all(sum(c.recommended for c in a.candidates) == 1 for a in res.aspects)


# --- agent -----------------------------------------------------------------------------------------
def test_stated_stack_is_detected_and_adopted_per_aspect():
    chunks = [_chunk("The platform is built on the MERN stack — MongoDB, Express, React and Node.js.")]
    res = analyze_tech_stack(None, [_req("R1", "The system shall let a user log in.")], chunks)
    assert res.stated_in_inputs is True and res.aspects
    assert all(len(a.candidates) == 1 and a.candidates[0].recommended for a in res.aspects)  # locked


def test_not_stated_proposes_candidates_per_aspect_one_recommended():
    chunks = [_chunk("The system shall let customers browse products and check out. Tech is open.")]
    reqs = [_approved("R1", "The system shall let a customer place an order and pay by card.")]
    res = analyze_tech_stack(None, reqs, chunks)
    assert res.stated_in_inputs is False
    keys = {a.key for a in res.aspects}
    assert {"frontend", "backend", "database", "auth"} <= keys                 # core aspects present
    assert "payments" not in keys and "hosting" not in keys                    # switched off (commented out)
    assert all(len(a.candidates) >= 2 for a in res.aspects)
    assert _one_rec_per_aspect(res)


def test_payments_and_hosting_aspects_are_excluded():
    """Payments and Hosting / Infrastructure are switched off in the generator (both the fallback
    blocks and via EXCLUDED_ASPECT_KEYS) — they must not appear on any path."""
    chunks = [_chunk("No tech mandated; customers browse, search, pay by card, and get email alerts.")]
    reqs = [_approved("R1", "The system shall let a customer search the catalogue, pay by card, and be notified.")]
    res = analyze_tech_stack(None, reqs, chunks)
    keys = {a.key for a in res.aspects}
    assert "payments" not in keys and "hosting" not in keys
    titles = " ".join(a.title.lower() for a in res.aspects)
    assert "payment" not in titles and "hosting" not in titles and "infrastructure" not in titles
    assert {"frontend", "backend", "database", "auth"} <= keys                 # the rest still present


def test_renderer_excludes_payments_and_hosting_from_stored_data():
    """Even a STORED/older run that still contains Payments/Hosting must not render them in §7 —
    the renderer drops switched-off aspects, so a regenerate removes them without re-analysis."""
    from rga.generate.tech_stack import tech_stack_markdown
    stored = {"stated_in_inputs": False, "basis": "b", "aspects": [
        {"key": "backend", "title": "Backend / API", "rationale": "r",
         "candidates": [{"name": "Node.js + Express", "recommended": True, "reason": "x"}]},
        {"key": "payments", "title": "Payments", "rationale": "r",
         "candidates": [{"name": "Stripe", "recommended": True, "reason": "x"}]},
        {"key": "hosting", "title": "Hosting / Infrastructure", "rationale": "r",
         "candidates": [{"name": "Kubernetes", "recommended": True, "reason": "x"}]},
    ]}
    md = tech_stack_markdown(stored, {})
    assert "Node.js + Express" in md                       # kept
    assert "Stripe" not in md and "Kubernetes" not in md   # payments/hosting dropped at render


def test_backend_recommendation_tilts_python_for_agentic_workload():
    chunks = [_chunk("No specific technology is mandated.")]
    reqs = [_req("R1", "The system shall run an agentic LLM pipeline for analytics and recommendation."),
            _req("R2", "The system shall train a machine learning model on order data.")]
    res = analyze_tech_stack(None, reqs, chunks)
    backend = next(a for a in res.aspects if a.key == "backend")
    rec = next(c for c in backend.candidates if c.recommended)
    assert "python" in rec.name.lower()


def test_lone_stray_token_is_not_a_stated_stack():
    chunks = [_chunk("The legacy system was hand-coded in Java years ago and is hard to change.")]
    assert "java" in detect_stated_tokens(chunks[0].text.lower())
    res = analyze_tech_stack(None, [_req("R1", "The system shall be rebuilt.")], chunks)
    assert res.stated_in_inputs is False and len(res.aspects) >= 4   # 4 core (hosting switched off)


def test_llm_output_normalised_to_one_recommended_per_aspect():
    chunks = [_chunk("Technology stack is not specified.")]
    payload = {"stated_in_inputs": False, "basis": "proposed",
               "aspects": [{"key": "backend", "title": "Backend", "rationale": "logic",
                            "candidates": [{"name": "Node", "recommended": True, "reason": "a"},
                                           {"name": "Python", "recommended": True, "reason": "b"}]}]}
    res = analyze_tech_stack(MockProvider(responses=[_json.dumps(payload)]),
                             [_req("R1", "The system shall do things.")], chunks)
    assert _one_rec_per_aspect(res)                       # the 2nd 'recommended' was cleared


# --- SRS §7 rendering: the six QuickBite layers, built from the per-aspect picks -------------------
_LAYER_HEADINGS = ("### 7.1 Client Applications", "### 7.2 Backend Architecture",
                   "### 7.3 Data Storage", "### 7.4 Third-Party Integrations",
                   "### 7.5 Security Technologies", "### 7.6 Device Capabilities")


def test_srs_renders_six_quickbite_layers_when_not_stated():
    chunks = [_chunk("No technology is mandated.")]
    reqs = [_approved("R1", "The system shall let a customer search the catalogue and pay by card.")]
    ts = analyze_tech_stack(None, reqs, chunks).model_dump()
    md = generate_srs(reqs, project_name="Shop", date="2026-01-01", tech_stack=ts)
    assert "## 7. Technology Stack" in md and "Proposed technology stack" in md   # provisional marker
    for h in _LAYER_HEADINGS:
        assert h in md, h                                                          # all 6 layers present
    assert md.index("## 7. Technology Stack") < md.index("## Appendix A: Glossary")


def test_srs_renders_adopted_stack_when_stated():
    chunks = [_chunk("Built on the MERN stack — MongoDB, Express, React, Node.js.")]
    reqs = [_approved("R1", "The system shall let a user log in.")]
    ts = analyze_tech_stack(None, reqs, chunks).model_dump()
    md = generate_srs(reqs, project_name="Shop", date="2026-01-01", tech_stack=ts)
    assert "## 7. Technology Stack" in md and "taken directly from the project's source inputs" in md
    for h in _LAYER_HEADINGS:
        assert h in md, h


def test_srs_selection_overrides_recommended_in_its_layer():
    chunks = [_chunk("No technology is mandated.")]
    reqs = [_approved("R1", "The system shall let a customer place an order.")]
    res = analyze_tech_stack(None, reqs, chunks)
    backend = next(a for a in res.aspects if a.key == "backend")
    pick = next(c.name for c in backend.candidates if not c.recommended)   # choose a non-default
    md = generate_srs(reqs, project_name="Shop", date="2026-01-01",
                      tech_stack=res.model_dump(), tech_stack_selection={"backend": pick})
    # the chosen backend candidate appears under the Backend Architecture layer
    after = md.split("### 7.2 Backend Architecture", 1)[1]
    assert pick in after.split("### 7.3", 1)[0]


def test_srs_renders_custom_other_selection_not_among_candidates():
    """Ask 1 ('Other'): a reviewer's own value that is NOT one of the proposed candidates still
    renders in §7 under its aspect's layer — never silently replaced by the recommended default."""
    chunks = [_chunk("No technology is mandated.")]
    reqs = [_approved("R1", "The system shall let a customer place an order.")]
    res = analyze_tech_stack(None, reqs, chunks)
    md = generate_srs(reqs, project_name="Shop", date="2026-01-01",
                      tech_stack=res.model_dump(), tech_stack_selection={"backend": "Deno + Hono"})
    after = md.split("### 7.2 Backend Architecture", 1)[1].split("### 7.3", 1)[0]
    assert "Deno + Hono" in after                      # the custom entry reaches §7


def test_fallback_candidates_are_simple_and_short():
    """Ask 3: the offline/fallback candidate names are short, atomic technology names (buildable
    building blocks), not long descriptive phrases or all-in-one platforms."""
    chunks = [_chunk("No technology is mandated; customers browse, search and pay by card.")]
    reqs = [_approved("R1", "The system shall let a customer search the catalogue and pay by card.")]
    res = analyze_tech_stack(None, reqs, chunks)
    names = [c.name for a in res.aspects for c in a.candidates]
    assert "React" in names and "PostgreSQL" in names and "Python + FastAPI" in names
    assert all(len(n) <= 32 for n in names), [n for n in names if len(n) > 32]     # short, atomic
    banned = ("shopify", "medusa", "vendure")                                      # no all-in-one platforms
    assert not any(b in n.lower() for n in names for b in banned)


def test_handoff_carries_tech_stack_into_srs_and_manifest():
    from rga.generate.handoff import generate_handoff
    chunks = [_chunk("No technology is mandated by the client.")]
    reqs = [_approved("R1", "The system shall let a customer place an order.")]
    ts = analyze_tech_stack(None, reqs, chunks).model_dump()
    pack = generate_handoff(reqs, project_name="Shop", date="2026-01-01", tech_stack=ts)
    assert "## 7. Technology Stack" in pack["srs_markdown"]
    assert pack["manifest"]["tech_stack"]["stated_in_inputs"] is False
    assert pack["manifest"]["tech_stack"]["aspects"] >= 4   # core aspects (payments/hosting switched off)
