import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Initialization is about real drives, so the test uses one: a small HFS+
// disk image, mounted, put through the same endpoints the wizard calls.
// It starts bare, gets initialized, and must come out looking like a drive
// rekordbox itself prepared.
const VOL = "CCINITTEST";
let dir = "";
let image = "";
let mounted = false;

const sh = (cmd: string, args: string[]) =>
  execFileSync(cmd, args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });

test.beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), "ccinit-"));
  image = join(dir, "t.dmg");
  try {
    sh("hdiutil", ["create", "-size", "60m", "-fs", "HFS+", "-volname", VOL, "-quiet", image]);
    sh("hdiutil", ["attach", image, "-quiet"]);
    mounted = existsSync(`/Volumes/${VOL}`);
  } catch {
    mounted = false;
  }
});

test.afterAll(() => {
  try { if (mounted) sh("hdiutil", ["detach", `/Volumes/${VOL}`, "-quiet"]); } catch { /* already gone */ }
  try { rmSync(dir, { recursive: true, force: true }); } catch { /* nothing to clean */ }
});

test("a bare drive reports empty and refuses to sync", async ({ request }) => {
  test.skip(!mounted, "could not create a test volume");
  const r = await request.get(`/api/usb/device-state?volume=${VOL}`);
  expect(r.ok()).toBeTruthy();
  const d = await r.json();
  expect(d.state).toBe("empty");
  expect(d.can_initialize).toBe(true);
  expect(d.can_sync).toBe(false);
  expect(d.filesystem_supported).toBe(true);
  expect(d.expected_library_dir).toBe(".PIONEER");   // HFS+ hides it
  expect(d.missing.length).toBeGreaterThan(5);
});

test("initializing makes it a device the players can read", async ({ request }) => {
  test.skip(!mounted, "could not create a test volume");
  const r = await request.post("/api/usb/initialize", { data: { volume: VOL } });
  expect(r.ok()).toBeTruthy();
  const d = await r.json();
  expect(d.initialized).toBe(true);
  expect(d.state.state).toBe("ready");
  expect(d.state.can_sync).toBe(true);

  for (const f of ["DEVSETTING.DAT", "MYSETTING.DAT", "MYSETTING2.DAT",
                   "DJMMYSETTING.DAT", "djprofile.nxs", "extracted/gcred.dat",
                   "rekordbox/export.pdb", "rekordbox/exportExt.pdb",
                   "rekordbox/exportLibrary.db", "rekordbox/RBFLTR.DAT",
                   "rekordbox/playlists3.sync", "rekordbox/playlists3Plus.sync"]) {
    expect(existsSync(`/Volumes/${VOL}/.PIONEER/${f}`), `missing ${f}`).toBeTruthy();
  }
});

test("initializing again changes nothing", async ({ request }) => {
  test.skip(!mounted, "could not create a test volume");
  const before = sh("shasum", ["-a", "256", `/Volumes/${VOL}/.PIONEER/rekordbox/export.pdb`]);
  const r = await request.post("/api/usb/initialize", { data: { volume: VOL } });
  const d = await r.json();
  expect(d.initialized).toBe(false);              // already ready
  const after = sh("shasum", ["-a", "256", `/Volumes/${VOL}/.PIONEER/rekordbox/export.pdb`]);
  expect(after).toBe(before);                     // library never rewritten
});

test("a missing settings file is repaired without touching the library", async ({ request }) => {
  test.skip(!mounted, "could not create a test volume");
  const pdb = `/Volumes/${VOL}/.PIONEER/rekordbox/export.pdb`;
  const before = sh("shasum", ["-a", "256", pdb]);
  rmSync(`/Volumes/${VOL}/.PIONEER/DEVSETTING.DAT`);

  const s = await (await request.get(`/api/usb/device-state?volume=${VOL}`)).json();
  expect(s.state).toBe("incomplete");
  expect(s.missing).toContain("DEVSETTING.DAT");
  expect(s.can_sync).toBe(true);                  // the library is still fine

  const d = await (await request.post("/api/usb/initialize", { data: { volume: VOL } })).json();
  expect(d.written).toEqual(["DEVSETTING.DAT"]);
  expect(d.state.state).toBe("ready");
  expect(sh("shasum", ["-a", "256", pdb])).toBe(before);
});

test("initialize rejects a volume that is not mounted", async ({ request }) => {
  const r = await request.post("/api/usb/initialize", { data: { volume: "NoSuchVolume" } });
  expect(r.status()).toBe(404);
});

test("wizard targets carry each drive's device state", async ({ request }) => {
  const d = await (await request.get("/api/usb/wizard/targets")).json();
  for (const row of d.rows) {
    expect(row.device, `no device state for ${row.volume}`).toBeTruthy();
    expect(typeof row.device.state).toBe("string");
    expect(typeof row.device.can_sync).toBe("boolean");
  }
});
