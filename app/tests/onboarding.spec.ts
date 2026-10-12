import { test, expect } from "@playwright/test";

// Runs against the real library, so it only reads and navigates: it never
// imports, and never captures or replaces the stick-export template.

test("onboarding status reports library, template and sticks", async ({ request }) => {
  const s = await (await request.get("/api/onboarding/status")).json();
  expect(s.library.tracks).toBeGreaterThan(0);
  expect(s.show).toBe(false);                 // populated library: never auto-opens
  expect(s.template.ready).toBe(true);
  expect(Array.isArray(s.sticks)).toBe(true);
});

test("capture rejects drives that aren't mounted", async ({ request }) => {
  for (const volume of ["", "no-such-volume", "../tmp"]) {
    const r = await request.post("/api/onboarding/capture-template", { data: { volume } });
    expect(r.status()).toBe(404);
  }
});

test("onboarding stays closed on a populated library, reopens from the rail", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".trow").first()).toBeVisible();
  await expect(page.locator(".ob")).toHaveCount(0);
  await page.locator('.navi[title="Getting started"]').click();
  await expect(page.locator(".ob .obh")).toHaveText("Welcome to Crate Constellation");

  await page.getByRole("button", { name: "Get started" }).click();
  await expect(page.locator(".obrow", { hasText: "Library imported" })).toContainText("tracks");
  await expect(page.getByRole("button", { name: "Import from rekordbox" })).toHaveCount(0);

  await page.getByRole("button", { name: "Next →" }).click();
  await expect(page.locator(".obrow", { hasText: "Stick exports are ready" })).toBeVisible();

  await page.getByRole("button", { name: "Next →" }).click();
  await expect(page.locator(".ob .obh")).toHaveText("You're set");
  await page.getByRole("button", { name: "Open my library" }).click();
  await expect(page.locator(".ob")).toHaveCount(0);
});
