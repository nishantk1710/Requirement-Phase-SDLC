// Client tests (vitest). These mock `fetch`, so no DOM/network is needed. They lock the fetch
// error-handling that shipped broken (F-F2): a failed artifact fetch must THROW, never be returned
// as the document body; and every unwrap-based endpoint must surface the API error detail.
import { afterEach, describe, expect, it, vi } from "vitest";

import { acceptAll, applyRecommendedDecisions, getArtifact, getTechStack, selectTechStack } from "./api";

function mockFetch(status: number, body: string): void {
  globalThis.fetch = vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    statusText: `HTTP ${status}`,
    text: async () => body,
    json: async () => JSON.parse(body),
  })) as unknown as typeof fetch;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("getArtifact (F-F2)", () => {
  it("returns the document text on success", async () => {
    mockFetch(200, "# Software Requirements Specification");
    await expect(getArtifact("P", "SRS.md")).resolves.toContain("Software Requirements Specification");
  });

  it("THROWS on 404 with the API detail — never returns the error body as the document", async () => {
    mockFetch(404, JSON.stringify({ detail: "no artifact 'SRS.docx'" }));
    await expect(getArtifact("P", "SRS.docx")).rejects.toThrow("no artifact 'SRS.docx'");
  });

  it("throws on a 500 with a non-JSON body", async () => {
    mockFetch(500, "Internal Server Error");
    await expect(getArtifact("P", "SRS.md")).rejects.toThrow();
  });
});

describe("unwrap error path (shared by every endpoint)", () => {
  it("surfaces the API error detail (e.g. a 409)", async () => {
    mockFetch(409, JSON.stringify({ detail: "a run is already in progress" }));
    await expect(acceptAll("P")).rejects.toThrow("a run is already in progress");
  });

  it("parses a successful apply-recommended response", async () => {
    mockFetch(200, JSON.stringify({ applied: { conflicts: 2, excluded: 1, included: 3, to_author: 0 }, resolved_total: 6 }));
    const r = await applyRecommendedDecisions("P");
    expect(r.applied.conflicts).toBe(2);
    expect(r.resolved_total).toBe(6);
  });
});

describe("technology stack (§7) review", () => {
  it("parses the per-aspect tech-stack response and selections", async () => {
    mockFetch(200, JSON.stringify({
      tech_stack: {
        stated_in_inputs: false, basis: "b",
        aspects: [{ key: "backend", title: "Backend", rationale: "r", candidates: [
          { name: "Node.js", recommended: true, reason: "x" },
          { name: "Python", recommended: false, reason: "y" }] }],
      },
      selections: { backend: "Python" },
    }));
    const r = await getTechStack("P");
    expect(r.tech_stack?.aspects[0].key).toBe("backend");
    expect(r.tech_stack?.aspects[0].candidates[0].recommended).toBe(true);
    expect(r.selections.backend).toBe("Python");
  });

  it("posts a per-aspect selection and parses the result", async () => {
    mockFetch(200, JSON.stringify({ aspect: "backend", selected: "Python", recommended: "Node.js" }));
    const r = await selectTechStack("P", "backend", "Python");
    expect(r.selected).toBe("Python");
  });

  it("posts a custom 'Other' selection with custom=true", async () => {
    mockFetch(200, JSON.stringify({
      aspect: "backend", selected: "Deno + Hono", recommended: "Node.js + Express", custom: true,
    }));
    const r = await selectTechStack("P", "backend", "Deno + Hono", true);
    expect(r.selected).toBe("Deno + Hono");
    expect(r.custom).toBe(true);
    const calls = (globalThis.fetch as unknown as { mock: { calls: [string, { body: string }][] } }).mock.calls;
    expect(JSON.parse(calls[0][1].body)).toMatchObject({ aspect: "backend", candidate: "Deno + Hono", custom: true });
  });

  it("surfaces a 409 when the stack is stated in the inputs (not selectable)", async () => {
    mockFetch(409, JSON.stringify({ detail: "the stack is stated in the inputs; nothing to select" }));
    await expect(selectTechStack("P", "backend", "Python")).rejects.toThrow("stated in the inputs");
  });
});
