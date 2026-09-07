import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 20_000,
  use: { baseURL: "http://localhost:8765" },
  webServer: {
    command: "cd .. && ./.venv/bin/uvicorn engine.server:app --port 8765",
    url: "http://localhost:8765/api/health",
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
