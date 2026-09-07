import { test, expect } from "@playwright/test";

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
