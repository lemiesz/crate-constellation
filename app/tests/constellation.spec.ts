import { test, expect } from "@playwright/test";

test("constellation api serves the full map", async ({ request }) => {
  const d = await (await request.get("/api/constellation")).json();
  expect(d.rows.length).toBeGreaterThan(15000);
  const [id, x, y, genre] = d.rows[0];
  expect(typeof id).toBe("string");
  expect(typeof x).toBe("number");
  expect(typeof genre).toBe("string");
});

test("rail switches to constellation view with canvas + legend", async ({ page }) => {
  await page.goto("/");
  await page.locator(".navi[title=Constellation]").click();
  await expect(page.locator(".constel canvas")).toBeVisible();
  await expect(page.locator(".legend .leg")).toHaveCount(9);
  await expect(page.locator(".tree")).toHaveCount(0);        // sidebar hidden here
  await page.locator(".navi[title=Library]").click();
  await expect(page.locator(".trow").first()).toBeVisible();
});

test("open in constellation focuses and selects the track", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Search title/).fill("leger");
  const title = await page.locator(".trow .tt").first().innerText();
  await page.locator(".trow").first().click({ button: "right" });
  await page.locator(".ctxit", { hasText: "Open in constellation" }).click();
  await expect(page.locator(".constel canvas")).toBeVisible();
  await expect(page.locator(".detail .card h3")).toContainText(title.slice(0, 10));
  await expect(page.locator(".nb").first()).toBeVisible();   // neighbors panel alive
});

test("constellation serves validated subgenre colors", async ({ request }) => {
  const d = await (await request.get("/api/constellation")).json();
  const colors = new Set(d.rows.map((r: any) => r[8]));
  expect(colors.size).toBeGreaterThan(25);              // family hues + shades
  expect(d.legend.length).toBe(9);
});

test("neighbor click stays inside the constellation", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Search title/).fill("leger");
  await page.locator(".trow").first().click({ button: "right" });
  await page.locator(".ctxit", { hasText: "Open in constellation" }).click();
  await expect(page.locator(".constel canvas")).toBeVisible();
  await page.locator(".nb").first().click();            // hop to a neighbor
  await expect(page.locator(".constel canvas")).toBeVisible();   // still here
  await expect(page.locator(".trow")).toHaveCount(0);   // not the library
  await expect(page.locator(".nb").first()).toBeVisible();       // new neighbors
});
