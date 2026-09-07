import { test, expect } from "@playwright/test";

// editing tests mutate library.db but restore state before finishing

test("energy edit round-trip: field, E-tag and hashtag stay consistent", async ({ request }) => {
  const t = (await (await request.get("/api/tracks?query=leger&limit=1")).json()).rows[0];
  const orig = t.energy;
  await request.post(`/api/tracks/${t.id}/energy`, { data: { value: 3 } });
  let now = await (await request.get(`/api/track/${t.id}`)).json();
  expect(now.energy).toBe(3);
  expect(now.comment).toContain("#E3");
  expect(now.comment).not.toContain(`#E${orig}`);
  expect(now.tags.some((g: any) => g.name === "E3")).toBe(true);
  await request.post(`/api/tracks/${t.id}/energy`, { data: { value: orig } });
  now = await (await request.get(`/api/track/${t.id}`)).json();
  expect(now.energy).toBe(orig);
  const j = (await (await request.get("/api/journal?limit=2")).json()).rows;
  expect(j[0].field).toBe("energy");
});

test("tag add/remove mirrors the comment hashtag", async ({ request }) => {
  const t = (await (await request.get("/api/tracks?query=leger&limit=1")).json()).rows[0];
  const tags = (await (await request.get("/api/tags")).json()).rows;
  const candidate = tags.find((g: any) =>
    g.bank === "Vibe" && !t.tags.some((x: any) => x.name === g.name));
  await request.post(`/api/tracks/${t.id}/tags`, { data: { tag_id: candidate.id } });
  let now = await (await request.get(`/api/track/${t.id}`)).json();
  expect(now.tags.some((g: any) => g.name === candidate.name)).toBe(true);
  expect(now.comment).toContain(`#${candidate.name}`);
  await request.delete(`/api/tracks/${t.id}/tags/${candidate.id}`);
  now = await (await request.get(`/api/track/${t.id}`)).json();
  expect(now.tags.some((g: any) => g.name === candidate.name)).toBe(false);
  expect(now.comment).not.toContain(`#${candidate.name}`);
});

test("energy picker edits from the detail pane", async ({ page, request }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Search title/).fill("leger");
  await page.locator(".trow").first().click();
  const before = await (await request.get(
    `/api/track/${await page.locator(".trow").first().getAttribute("id").then(s => s!.slice(4))}`)).json();
  await page.locator(".ep", { hasText: /^5$/ }).click();
  await expect(page.locator(".ep.on")).toHaveText("5");
  // restore
  await page.locator(".ep", { hasText: new RegExp(`^${before.energy}$`) }).click();
  await expect(page.locator(".ep.on")).toHaveText(String(before.energy));
});
