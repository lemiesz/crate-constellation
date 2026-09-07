import { test, expect } from "@playwright/test";

// USB tests only run with a rekordbox-exported stick mounted
test.beforeEach(async ({ request }) => {
  const st = await (await request.get("/api/usb/status")).json();
  test.skip(!st.mounted, "no rekordbox stick mounted");
});

test("usb status diffs stick vs library", async ({ request }) => {
  const st = await (await request.get("/api/usb/status")).json();
  const stats = await (await request.get("/api/stats")).json();
  expect(st.on_stick).toBeGreaterThan(0);
  expect(st.synced + st.missing).toBe(stats.tracks);       // partition invariant
  expect(st.on_stick).toBe(st.synced + st.stick_only);     // matching invariant
  expect(typeof st.missing_bytes).toBe("number");
});

test("usb tracks filter + fuzzy query", async ({ request }) => {
  const m = await (await request.get("/api/usb/tracks?filter=missing")).json();
  expect(m.total).toBeGreaterThan(0);
  expect(m.rows[0].status).toBe("missing");
  const q = await (await request.get("/api/usb/tracks?filter=all&query=leger")).json();
  expect(q.total).toBeGreaterThan(0);                      // library-wide fuzzy
});

test("usb explorer screen renders summary + rows", async ({ page }) => {
  await page.goto("/");
  await page.locator(".navi[title='USB stick']").click();
  await expect(page.locator(".vtitle")).toContainText("USB ·");
  await expect(page.locator(".bignum").first()).not.toHaveText("…");
  await expect(page.locator(".bkrow").first()).toBeVisible();
  await page.locator(".seg2 button", { hasText: "synced" }).click();
  await expect(page.locator(".bkrow").first()).toBeVisible();
  await expect(page.locator(".bkbtn[disabled]")).toContainText("gated");
  // patch button exists but is NOT clicked in tests (writes to the stick)
  await expect(page.locator(".syncbtn", { hasText: "Patch matching" })).toBeVisible();
});

test("stick playlist tree browses in playlist order", async ({ page, request }) => {
  const st = await (await request.get("/api/usb/status")).json();
  const pl = st.playlists.find((p: any) => p.kind === "playlist" && p.n > 0);
  test.skip(!pl, "no playlists on stick");
  await page.goto("/");
  await page.locator(".navi[title='USB stick']").click();
  await expect(page.locator(".thead", { hasText: "Playlists on stick" })).toBeVisible();
  // the header renders before the playlists fetch resolves — wait for a real
  // tree row (beyond "All tracks") or the open-folders loop no-ops on nothing
  await expect(page.locator(".titem").nth(1)).toBeVisible();
  // open collapsed folders until the (possibly deeply nested) playlist shows —
  // bounded by folder count, not a guess (big sticks have dozens of folders)
  for (let i = 0; i < 200; i++) {
    if (await page.locator(".titem", { hasText: pl.name }).count() > 0) break;
    const closed = page.locator(".titem", { hasText: "▸" }).first();
    if (await closed.count() === 0) break;
    await closed.click();
  }
  await page.locator(".titem", { hasText: pl.name }).first().click();
  await expect(page.locator(".chip.acc")).toContainText("in playlist order");
  await expect(page.locator(".bkrow").first()).toBeVisible();
});
