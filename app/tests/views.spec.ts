import { test, expect } from "@playwright/test";

test("energy screen shows histogram + banks; bar click browses the band", async ({ page }) => {
  await page.goto("/");
  await page.locator(".navi[title=Energy]").click();
  await expect(page.locator(".hcol")).toHaveCount(10);
  await expect(page.locator(".bankhd")).toHaveCount(4);
  await page.locator(".hcol").nth(6).click();               // E7
  await expect(page.locator(".toolbar .chip.acc")).toContainText("tag: E7");
  await expect(page.locator(".trow").first()).toBeVisible();
});

test("tag chip click from banks filters the library", async ({ page }) => {
  await page.goto("/");
  await page.locator(".navi[title=Energy]").click();
  await page.locator(".bankchip", { hasText: "dirtybird" }).click();
  await expect(page.locator(".toolbar .chip.acc")).toContainText("tag: dirtybird");
  await expect(page.locator(".trow").first()).toBeVisible();
});

test("backups screen lists real snapshots per target", async ({ page, request }) => {
  const api = await (await request.get("/api/backups")).json();
  expect(api.rows.length).toBeGreaterThan(10);
  expect(api.rows.some((r: any) => r.group === "rekordbox" && r.name.includes("backup"))).toBe(true);
  await page.goto("/");
  await page.locator(".navi[title=Backups]").click();
  await expect(page.locator(".bkrow").first()).toBeVisible();
  await expect(page.locator(".bkname", { hasText: "master.db  · live" })).toBeVisible();
  await expect(page.locator(".bkname", { hasText: "collection.nml  · live" })).toBeVisible();
});
