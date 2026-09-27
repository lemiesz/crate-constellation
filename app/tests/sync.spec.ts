import { test, expect, type APIRequestContext } from "@playwright/test";

// Not the "leger" track editing.spec.ts uses: spec files run in parallel, and
// two tests flipping the same track's energy race each other's restore.
async function ownTrack(request: APIRequestContext) {
  const rows = (await (await request.get("/api/tracks?query=gibba&limit=5")).json()).rows;
  return rows.find((r: any) => r.energy != null);
}

test("sync status reports targets and pending edits", async ({ request }) => {
  const s = await (await request.get("/api/sync/status")).json();
  expect(s.tracks).toBeGreaterThan(19000);
  expect(s.rekordbox.size).toBeGreaterThan(100_000_000);
  expect(s.traktor.size).toBeGreaterThan(10_000_000);
  expect(typeof s.pending_edits).toBe("number");
});

test("sync screen shows three target cards + journal", async ({ page }) => {
  await page.goto("/");
  await page.locator(".navi[title=Sync]").click();
  await expect(page.locator(".synchd")).toHaveCount(3);
  await expect(page.locator(".synchd").nth(0)).toContainText("rekordbox");
  await expect(page.locator(".synchd").nth(1)).toContainText("Traktor");
  await expect(page.locator(".synchd").nth(2)).toContainText("USB");
  await expect(page.locator(".syncbtn", { hasText: "Re-import" })).toBeVisible();
  await expect(page.locator(".lbl", { hasText: "Edit journal" })).toBeVisible();
});

test("pending edits collapse to net changes per track", async ({ request }) => {
  const t = await ownTrack(request);
  const orig = t.energy;
  const other = orig === 9 ? 8 : 9;
  // a round trip is two journal rows and no net change ...
  await request.post(`/api/tracks/${t.id}/energy`, { data: { value: other } });
  let p = await (await request.get("/api/sync/pending-edits")).json();
  let mine = p.tracks.find((x: any) => x.id === t.id);
  expect(mine.changes).toContainEqual({ kind: "energy", from: String(orig), to: String(other) });
  await request.post(`/api/tracks/${t.id}/energy`, { data: { value: orig } });
  p = await (await request.get("/api/sync/pending-edits")).json();
  mine = p.tracks.find((x: any) => x.id === t.id);
  expect(mine.changes).toEqual([]);
  expect(p.raw_edits).toBeGreaterThanOrEqual(2);
});

test("re-import opens a review modal that lists the edits and can be cancelled", async ({ page, request }) => {
  const t = await ownTrack(request);
  const orig = t.energy;
  const other = orig === 9 ? 8 : 9;
  await request.post(`/api/tracks/${t.id}/energy`, { data: { value: other } });
  try {
    const before = (await (await request.get("/api/sync/status")).json()).imported_at;
    await page.goto("/");
    await page.locator(".navi[title=Sync]").click();
    await page.getByTestId("view-edits").click();
    const modal = page.getByTestId("import-edits-modal");
    await expect(modal).toBeVisible();
    await expect(modal.getByTestId("pe-summary")).toContainText(/\d+ changes? on \d+ tracks?/);
    await expect(modal.locator(".pe-chip", { hasText: `→ ${other}` }).first()).toBeVisible();
    await modal.locator(".mcancel", { hasText: "Keep my edits" }).click();
    await expect(modal).toBeHidden();
    // cancelling must not have imported
    const after = (await (await request.get("/api/sync/status")).json()).imported_at;
    expect(after).toBe(before);
    // the Re-import button routes through the same modal while edits are pending
    await page.locator(".syncbtn", { hasText: "Re-import from rekordbox" }).click();
    await expect(modal).toBeVisible();
    await modal.locator(".mcancel").click();
  } finally {
    await request.post(`/api/tracks/${t.id}/energy`, { data: { value: orig } });
  }
});
