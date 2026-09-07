import { test, expect } from "@playwright/test";

test("stats reports the full library", async ({ request }) => {
  const r = await (await request.get("/api/stats")).json();
  expect(r.tracks).toBeGreaterThan(19000);
  expect(r.banks.length).toBeGreaterThanOrEqual(4);
});

test("fuzzy variants agree", async ({ request }) => {
  const a = await (await request.get("/api/tracks?query=jayz&limit=1")).json();
  const b = await (await request.get("/api/tracks?query=jay-z&limit=1")).json();
  expect(a.total).toBeGreaterThan(0);
  expect(a.total).toBe(b.total);
});

test("similar returns ranked, deduped neighbors", async ({ request }) => {
  const t = await (await request.get("/api/tracks?query=leger&limit=1")).json();
  const s = await (await request.get(`/api/similar/${t.rows[0].id}?limit=8`)).json();
  expect(s.rows.length).toBe(8);
  const sims = s.rows.map((r: any) => r.sim);
  expect([...sims].sort((x, y) => y - x)).toEqual(sims);            // descending
  const names = s.rows.map((r: any) => `${r.artist}|${r.title}`);
  expect(new Set(names).size).toBe(names.length);                   // no dupes
});

test("waveform serves 400 rekordbox preview columns", async ({ request }) => {
  const t = await (await request.get("/api/tracks?query=leger&limit=1")).json();
  const w = await (await request.get(`/api/waveform/${t.rows[0].id}`)).json();
  expect(w.heights.length).toBe(400);
  expect(w.colors.length).toBe(400);
});
