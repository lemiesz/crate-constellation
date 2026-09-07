import { test, expect } from "@playwright/test";

test("sync lock + kill + log endpoints behave", async ({ request }) => {
  // kill on idle is a no-op, not an error
  const k = await (await request.post("/api/usb/wizard/kill",
    { data: { volume: "ANY" } })).json();
  expect(k.killed).toBe(false);
  // progress reports a jobs list (multi-drive)
  const pr = await (await request.get("/api/usb/wizard/progress")).json();
  expect(Array.isArray(pr.jobs)).toBe(true);
  // starting against a nonexistent volume is a clean 409, not a crash
  const r = await request.post("/api/usb/wizard/start",
    { data: { playlist_ids: [], volume: "DOES-NOT-EXIST" } });
  expect(r.status()).toBe(409);
  // log endpoint always answers
  const l = await (await request.get("/api/usb/wizard/log")).json();
  expect(Array.isArray(l.rows)).toBe(true);
});

test("sync screen shows the USB sync log card", async ({ page }) => {
  await page.goto("/");
  await page.locator(".navi[title=Sync]").click();
  await expect(page.locator(".lbl", { hasText: "USB sync log" })).toBeVisible();
});
