"""Local file browser + handoff ZIP.

Some orgs block browser uploads, so the reviewer browses the LOCAL disk (the backend runs on their
machine) and selects files BY PATH; the backend reads them off disk and bundles them with the SRS
pack into `<pid>_handoff.zip` (asset1/ = design elements, asset2/ = static images). Missing paths
are skipped; the ZIP is refused until the SRS is generated.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from rga.api.app import create_app
from rga.models import Project
from rga.store.db import Database
from rga.store.repository import Repository

PID = "P-ZIP"


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                                  # handoff/ resolves under tmp_path
    files = tmp_path / "myfiles"
    (files / "sub").mkdir(parents=True)
    (files / "logo.png").write_bytes(b"\x89PNG-fake")
    (files / "hero.jpg").write_bytes(b"jpeg-fake")
    (files / "sub" / "icon.svg").write_text("<svg/>")
    hd = tmp_path / "handoff" / PID
    hd.mkdir(parents=True)
    (hd / "SRS.docx").write_bytes(b"docx-bytes")
    (hd / "SRS.md").write_text("# SRS")
    (hd / "RTM.md").write_text("# RTM")
    (hd / "manifest.json").write_text("{}")
    db = Database(str(tmp_path / "z.db"))
    await db.init()
    r = Repository(db)
    await r.save_project(Project(id=PID, name="Z"))
    try:
        yield {"app": create_app(r), "files": files, "tmp": tmp_path}
    finally:
        await db.dispose()


@pytest_asyncio.fixture
async def client(env):
    async with AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_fs_roots_lists_starting_points(client):
    r = await client.get("/api/fs/roots")
    assert r.status_code == 200
    j = r.json()
    assert j["roots"] and all("path" in x and "name" in x for x in j["roots"])
    assert "cwd" in j


@pytest.mark.asyncio
async def test_fs_list_shows_folders_first_and_flags_images(client, env):
    r = await client.get("/api/fs/list", params={"path": str(env["files"])})
    assert r.status_code == 200
    j = r.json()
    names = [e["name"] for e in j["entries"]]
    assert "sub" in names and "logo.png" in names and "hero.jpg" in names
    assert j["entries"][0]["is_dir"] is True                              # folders sort first
    assert next(e for e in j["entries"] if e["name"] == "logo.png")["is_image"] is True
    assert Path(j["parent"]).resolve() == env["tmp"].resolve()


@pytest.mark.asyncio
async def test_fs_list_rejects_a_file_path(client, env):
    r = await client.get("/api/fs/list", params={"path": str(env["files"] / "logo.png")})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_zip_bundles_selected_local_files(client, env):
    f = env["files"]
    body = {"asset1": [str(f / "logo.png"), str(f / "sub" / "icon.svg")], "asset2": [str(f / "hero.jpg")]}
    r = await client.post(f"/api/projects/{PID}/handoff-zip", json=body)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert {"SRS.docx", "SRS.md", "RTM.md", "manifest.json"} <= names       # the handoff pack
    assert {"asset1/logo.png", "asset1/icon.svg", "asset2/hero.jpg"} <= names  # selected local files


@pytest.mark.asyncio
async def test_zip_skips_missing_paths(client, env):
    body = {"asset1": [str(env["files"] / "does-not-exist.png")], "asset2": []}
    r = await client.post(f"/api/projects/{PID}/handoff-zip", json=body)
    assert r.status_code == 200
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert "SRS.docx" in names and not any(n.startswith("asset1/") for n in names)


@pytest.mark.asyncio
async def test_zip_refused_before_generation(client):
    r = await client.post("/api/projects/P-UNGENERATED/handoff-zip", json={"asset1": [], "asset2": []})
    assert r.status_code == 400
