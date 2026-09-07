import { test, expect } from "@playwright/test";

test("library loads with live data", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".tree h1")).toHaveText("Library");
  await expect(page.locator(".trow").first()).toBeVisible();
  // stats count lands in the All tracks row
  await expect(page.locator(".titem.on .n")).toContainText(",");
});

test("fuzzy search: jayz finds Jay-Z", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Search title/).fill("jayz");
  await expect(page.locator(".trow").first()).toContainText(/jay.?z/i);
});

test("playlist filter narrows the table", async ({ page }) => {
  await page.goto("/");
  const total = await page.locator(".toolbar .lbl").innerText();
  await page.locator(".titem", { hasText: "▸ house" }).click();     // open folder
  await page.locator(".titem", { hasText: "prog-acid" }).click();   // pick a playlist
  await expect(page.locator(".toolbar .chip.acc")).toContainText("prog-acid");
  await expect(page.locator(".toolbar .lbl")).not.toHaveText(total);
});

test("selecting a row shows detail + sounds-like neighbors", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Search title/).fill("leger");
  await page.locator(".trow").first().click();
  await expect(page.locator(".detail .card h3")).toBeVisible();
  await expect(page.locator(".nb").first()).toBeVisible();          // neighbors listed
  await expect(page.locator(".nb .sim").first()).toContainText(".");
});

test("neighbor click jumps the library to that track", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Search title/).fill("leger");
  await page.locator(".trow").first().click();
  const neighborTitle = await page.locator(".nb .t").first().innerText();
  await page.locator(".nb").first().click();
  await expect(page.getByPlaceholder(/Search title/)).toHaveValue(neighborTitle);
  await expect(page.locator(".trow.sel")).toBeVisible();
});

test("double-click starts the player with a waveform canvas", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Search title/).fill("leger");
  await page.locator(".trow").first().dblclick();
  await expect(page.locator(".player")).toBeVisible();
  await expect(page.locator(".wavecv")).toBeVisible();
  await expect(page.locator(".ptime")).toContainText("/");
});

test("right-click opens the context menu with constellation slot", async ({ page }) => {
  await page.goto("/");
  await page.locator(".trow").first().click({ button: "right" });
  await expect(page.locator(".ctx")).toBeVisible();
  await expect(page.locator(".ctxit", { hasText: "Open in constellation" })).toBeEnabled();
  await expect(page.locator(".ctxit", { hasText: "Reveal in Finder" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator(".ctx")).toHaveCount(0);
});
