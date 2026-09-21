"""Brownfield support: scan + understand an existing codebase, and generate a change-only SRS + RTM.

Deterministic end-to-end (no LLM): the scanner/understanding/impact and the change pack are all
pure functions of the source tree + approved requirements.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from rga.api.app import create_app
from rga.codebase.scan import scan_codebase
from rga.codebase.understand import impact_for, system_capabilities, understand_codebase
from rga.generate.change_pack import NoChangeRequirements, generate_change_pack
from rga.models import Priority, Project, Requirement, RType, SourceRef, Status
from rga.store.db import Database
from rga.store.repository import Repository


def _r(rid, s, t="functional", *, feature="Orders", status=Status.approved, pid="P-CB"):
    return Requirement(id=rid, project_id=pid, statement=s, rtype=RType(t), feature=feature,
                       priority=Priority.must, status=status,
                       source_refs=[SourceRef(doc_id="human", source_type="analysis",
                                              location="1", raw_quote=s, start=0, end=len(s))])


@pytest.fixture
def codebase(tmp_path):
    """A tiny synthetic source tree (with a vendor dir that must be skipped)."""
    root = tmp_path / "shop"
    (root / "api").mkdir(parents=True)
    (root / "web").mkdir()
    (root / "node_modules" / "junk").mkdir(parents=True)
    (root / "api" / "orders.py").write_text(
        "class OrderService:\n    def create_order(self):\n        pass\n"
        "    def cancel_order(self):\n        pass\n", encoding="utf-8")
    (root / "web" / "checkout.js").write_text(
        "export function checkout() {}\nexport const promoCode = () => {}\n", encoding="utf-8")
    (root / "node_modules" / "junk" / "huge.js").write_text("export function nope(){}\n", encoding="utf-8")
    (root / "README.md").write_text("# Shop\nAn e-commerce platform.\n", encoding="utf-8")
    return root


# ---------------------------------------------------------------- scan
def test_scan_finds_source_and_skips_vendor(codebase):
    idx = scan_codebase(codebase)
    paths = {f["path"] for f in idx["files"]}
    assert paths == {"api/orders.py", "web/checkout.js"}          # node_modules skipped
    assert idx["languages"] == {"Python": 1, "JavaScript": 1}
    orders = next(f for f in idx["files"] if f["path"] == "api/orders.py")
    assert "OrderService" in orders["symbols"] and "cancel_order" in orders["symbols"]
    assert "Shop" in idx["readme"]


def test_scan_rejects_non_directory(tmp_path):
    with pytest.raises(NotADirectoryError):
        scan_codebase(tmp_path / "does-not-exist")


# ---------------------------------------------------------------- understand + impact
def test_capabilities_and_impact(codebase):
    idx = scan_codebase(codebase)
    caps = system_capabilities(idx)
    mods = {c["module"] for c in caps}
    assert mods == {"api", "web"}
    # a cancel-order change should point at the orders module, not checkout
    hits = impact_for("The system shall let a customer cancel an order.", idx)
    assert hits and hits[0]["path"] == "api/orders.py"


def test_understand_is_deterministic_without_provider(codebase):
    u = understand_codebase(scan_codebase(codebase), provider=None)
    assert u["n_files"] == 2 and len(u["capabilities"]) == 2 and u["summary"]


# ---------------------------------------------------------------- change pack (pure)
def test_generate_change_pack_content(codebase):
    idx = scan_codebase(codebase)
    u = understand_codebase(idx)
    reqs = [_r("a", "The system shall let a customer cancel an order."),
            _r("b", "The site shall respond within 2 seconds.", "non_functional", feature=None)]
    pack = generate_change_pack(reqs, project_name="Shop", date="2026-01-01", understanding=u, index=idx)
    srs, rtm = pack["change_srs_markdown"], pack["change_rtm_csv"]
    assert "Change Requirements Specification" in srs
    assert "## 2. Existing System Overview" in srs and "`api`" in srs
    assert "## 4. Impact on Existing System" in srs and "api/orders.py" in srs
    assert "Change ID,Requirement,Type,Priority" in rtm and "api/orders.py" in rtm
    assert pack["manifest"]["change_requirements"] == 2 and pack["manifest"]["codebase_attached"] is True


def test_change_pack_refused_without_approved():
    with pytest.raises(NoChangeRequirements):
        generate_change_pack([_r("a", "X", status=Status.candidate)], project_name="Shop")


# ---------------------------------------------------------------- endpoints
@pytest_asyncio.fixture
async def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                              # handoff/ writes under tmp_path
    db = Database(str(tmp_path / "cb.db"))
    await db.init()
    r = Repository(db)
    await r.save_project(Project(id="P-CB", name="CB"))
    try:
        yield r
    finally:
        await db.dispose()


@pytest_asyncio.fixture
async def client(repo):
    async with AsyncClient(transport=ASGITransport(app=create_app(repo)), base_url="http://t") as ac:
        yield ac


@pytest.mark.asyncio
async def test_attach_codebase_and_generate_change_pack(client, repo, codebase):
    # greenfield by default
    assert (await client.get("/api/projects/P-CB/codebase")).json()["attached"] is False

    # attach the codebase
    r = await client.post("/api/projects/P-CB/codebase", json={"path": str(codebase)})
    assert r.status_code == 200 and r.json()["attached"] is True
    got = (await client.get("/api/projects/P-CB/codebase")).json()
    assert got["attached"] is True and got["n_files"] == 2

    # an approved change requirement, then generate the change pack
    await repo.save_requirement(_r("a", "The system shall let a customer cancel an order."))
    gen = await client.post("/api/projects/P-CB/change-pack")
    assert gen.status_code == 200
    j = gen.json()
    assert "CHANGE_SRS.md" in j["files"] and "CHANGE_RTM.csv" in j["files"]
    srs = (await client.get("/api/projects/P-CB/artifacts/CHANGE_SRS.md")).text
    assert "Change Requirements Specification" in srs and "api/orders.py" in srs


@pytest.mark.asyncio
async def test_attach_codebase_rejects_bad_path(client, tmp_path):
    r = await client.post("/api/projects/P-CB/codebase", json={"path": str(tmp_path / "nope")})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_attach_codebase_from_zip(client, repo, codebase, tmp_path):
    import io
    import zipfile

    # zip the synthetic codebase (as a single top-level folder, the common repo.zip shape)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in codebase.rglob("*"):
            if p.is_file() and "node_modules" not in p.parts:
                zf.write(p, arcname=f"shop/{p.relative_to(codebase).as_posix()}")
    buf.seek(0)

    r = await client.post("/api/projects/P-CB/codebase-zip",
                          files={"file": ("shop.zip", buf.read(), "application/zip")})
    assert r.status_code == 200
    j = r.json()
    assert j["attached"] is True and j["n_files"] == 2      # api/orders.py + web/checkout.js
    # and the change pack works off the uploaded codebase
    await repo.save_requirement(_r("a", "The system shall let a customer cancel an order."))
    gen = await client.post("/api/projects/P-CB/change-pack")
    assert gen.status_code == 200 and "CHANGE_SRS.md" in gen.json()["files"]


def test_synthesize_requirements_are_distinct_enhancements(codebase):
    from rga.codebase.document import synthesize_requirements

    idx = scan_codebase(codebase)
    u = understand_codebase(idx)
    feats = synthesize_requirements(idx, u, project_name="Shop")   # deterministic (no provider)
    assert feats and all(f["feature"] and f["requirements"] for f in feats)
    text = " ".join(r for f in feats for r in f["requirements"])
    assert "shall" in text                                         # 'The system shall …' statements


def test_existing_identifiers_flags_only_code_symbols():
    # requirements that NAME an existing camelCase/PascalCase symbol are restating the code, not
    # proposing a change — these identifiers are what the synthesizer filters such requirements on.
    from rga.codebase.document import _existing_identifiers

    idx = {"files": [{"path": "a.js", "symbols": ["OrderService", "promoCode", "checkout", "login"]}]}
    idents = _existing_identifiers(idx)
    assert "OrderService" in idents and "promoCode" in idents      # code identifiers
    assert "checkout" not in idents and "login" not in idents      # plain words, kept out


@pytest.mark.asyncio
async def test_attach_registers_synthetic_pdf_corpus(client, codebase):
    from pathlib import Path

    from rga.ingest.loaders import load_raw

    r = await client.post("/api/projects/P-CB/codebase", json={"path": str(codebase)})
    assert r.status_code == 200
    j = r.json()
    assert j.get("corpus") and j.get("doc") == "Enhancement_Requirements.pdf"
    # a real PDF (distinct from the source) is written as the run corpus, and the pipeline's loader
    # can read the requirements back out of it
    p = Path("data/uploads/P-CB/docs") / j["doc"]
    assert p.is_file() and p.read_bytes()[:4] == b"%PDF"
    assert "shall" in load_raw(p)


@pytest.mark.asyncio
async def test_change_pack_scopes_to_baseline_delta(client, repo, codebase):
    """With a baseline, the change pack documents ONLY the added/modified requirements since it —
    not the whole approved spec (the fix for 'it just dumped the full app spec')."""
    from rga.review.changes import snapshot_approved

    await client.post("/api/projects/P-CB/codebase", json={"path": str(codebase)})
    base = _r("old", "The system shall let a customer browse the catalogue.")
    await repo.save_requirement(base)
    await repo.save_baseline("P-CB", 1, "Initial", snapshot_approved([base]))  # 'old' is in the baseline
    # add a NEW change requirement after the baseline
    await repo.save_requirement(_r("new", "The system shall let a customer cancel an order."))

    gen = await client.post("/api/projects/P-CB/change-pack")
    assert gen.status_code == 200
    assert gen.json()["manifest"]["change_requirements"] == 1        # only the new one, not both
    srs = (await client.get("/api/projects/P-CB/artifacts/CHANGE_SRS.md")).text
    assert "cancel an order" in srs and "browse the catalogue" not in srs  # baselined req excluded


@pytest.mark.asyncio
async def test_change_pack_no_changes_since_baseline_is_clear(client, repo, codebase):
    from rga.review.changes import snapshot_approved

    await client.post("/api/projects/P-CB/codebase", json={"path": str(codebase)})
    base = _r("only", "The system shall let a customer browse the catalogue.")
    await repo.save_requirement(base)
    await repo.save_baseline("P-CB", 1, "Initial", snapshot_approved([base]))  # nothing changed since
    gen = await client.post("/api/projects/P-CB/change-pack")
    assert gen.status_code == 400 and "since the last SRS baseline" in gen.json()["detail"]


@pytest.mark.asyncio
async def test_zip_endpoint_rejects_non_zip(client):
    r = await client.post("/api/projects/P-CB/codebase-zip",
                          files={"file": ("bad.zip", b"not a real zip", "application/zip")})
    assert r.status_code == 400
