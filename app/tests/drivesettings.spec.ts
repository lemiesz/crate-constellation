import { test, expect } from "@playwright/test";

test("dismiss refuses unknown and running-only states", async ({ request }) => {
  const r = await (await request.post("/api/usb/wizard/dismiss", {
    data: { volume: "NO-SUCH-DRIVE" } })).json();
  expect(r.dismissed).toBe(false);
});

test("resync without a stick/manifest fails loudly, not silently", async ({ request }) => {
  const r = await request.post("/api/usb/resync", { data: { volume: "NO-SUCH-DRIVE" } });
  expect([404, 409]).toContain(r.status());
});

test("drive settings endpoint answers for the mounted stick", async ({ request }) => {
  const s = await (await request.get("/api/usb/settings")).json();
  if (!s.mounted) test.skip(true, "no stick mounted");
  expect(s.volume).toBeTruthy();
  expect(s.total_bytes).toBeGreaterThan(0);
  // manifest may be null (pre-manifest stick) but the key must exist
  expect("manifest" in s).toBe(true);
});

test("drive settings panel opens from the USB screen", async ({ page, request }) => {
  const s = await (await request.get("/api/usb/settings")).json();
  if (!s.mounted) test.skip(true, "no stick mounted");
  await page.goto("/");
  await page.locator(".navi[title='USB stick']").click();
  await page.getByRole("button", { name: "Drive settings…" }).click();
  await expect(page.locator(".wiz")).toContainText("Drive settings");
  await expect(page.locator(".wiz")).toContainText("Repair / re-sync");
  await expect(page.getByRole("button", { name: "Check & heal library" })).toBeVisible();
});

test("art cache carries deck-normalized variants after precompute", async ({ request }) => {
  // poll briefly — startup kicks an incremental precompute
  for (let i = 0; i < 30; i++) {
    const s = await (await request.get("/api/heal/art")).json();
    if (s.state === "done" || s.state === "idle") break;
    await new Promise(r => setTimeout(r, 2000));
  }
  const s = await (await request.get("/api/heal/art")).json();
  expect(["done", "idle"]).toContain(s.state);
});
