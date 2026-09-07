import { test, expect } from "@playwright/test";

test("heal runs clean and is idempotent", async ({ request }) => {
  const a = await (await request.post("/api/heal")).json();
  expect(a.steps.find((s: any) => s.step === "sqlite integrity").found).toBe("ok");
  const b = await (await request.post("/api/heal")).json();  // second run: nothing to fix
  for (const st of b.steps) if (typeof st.fixed === "number") expect(st.fixed).toBe(0);
});

test("art cache is complete and status answers", async ({ request }) => {
  const s = await (await request.get("/api/heal/art")).json();
  expect(s.coverage.scanned).toBe(s.coverage.total);
  expect(s.coverage.with_art).toBeGreaterThan(10000);
});

test("sync screen has the heal button", async ({ page }) => {
  await page.goto("/");
  await page.locator(".navi[title=Sync]").click();
  await expect(page.locator(".syncbtn", { hasText: "Heal library" })).toBeVisible();
});
