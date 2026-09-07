import { test, expect } from "@playwright/test";

test("wizard targets exclude system/source volumes", async ({ request }) => {
  const d = await (await request.get("/api/usb/wizard/targets")).json();
  const names = d.rows.map((r: any) => r.volume);
  expect(names).not.toContain("Macintosh HD");
  expect(names).not.toContain("T7");
});

test("wizard plan dedupes and sizes the selection", async ({ request }) => {
  const pls = (await (await request.get("/api/playlists")).json()).rows
    .filter((p: any) => p.kind === "playlist" && p.n > 20).slice(0, 2);
  const ids = pls.map((p: any) => p.id);
  const one = await (await request.post("/api/usb/wizard/plan",
    { data: { playlist_ids: [ids[0]] } })).json();
  const both = await (await request.post("/api/usb/wizard/plan",
    { data: { playlist_ids: ids } })).json();
  expect(one.n_tracks).toBeGreaterThan(0);
  expect(both.n_tracks).toBeGreaterThanOrEqual(one.n_tracks);
  expect(both.total_bytes).toBeGreaterThan(0);
  expect(both.playlists.length).toBe(2);
});

test("wizard UI walks select → review", async ({ page, request }) => {
  const t = await (await request.get("/api/usb/wizard/targets")).json();
  test.skip(t.rows.length === 0, "no target volume mounted");
  await page.goto("/");
  await page.locator(".navi[title='USB stick']").click();
  await page.locator(".syncbtn", { hasText: "Sync wizard" }).click();
  await expect(page.locator(".wiz .vtitle")).toContainText("USB sync wizard");
  await expect(page.locator(".wizsel option")).not.toHaveCount(0);
  // pick the first two playlists
  await page.locator(".wizpl input").nth(0).check();
  await page.locator(".wizpl input").nth(1).check();
  await expect(page.locator(".wizfoot span")).toContainText("tracks", { timeout: 8000 });
  const goEnabled = await page.locator(".wizgo").isEnabled();
  if (goEnabled) {
    await page.locator(".wizgo").click();
    await expect(page.locator(".wizwrites")).toContainText("corpus-validated writer");
    await page.locator(".bkbtn", { hasText: "Back" }).click();   // never start in tests
  }
  await page.locator(".bkbtn", { hasText: "Close" }).click();
  await expect(page.locator(".wiz")).toHaveCount(0);
});
