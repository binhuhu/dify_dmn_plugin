import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  workers: 1,
  use: {
    baseURL: "https://127.0.0.1:9443",
    ignoreHTTPSErrors: true,
    launchOptions: {
      executablePath:
        process.env.WORKBENCH_CHROMIUM_PATH || "/usr/bin/chromium",
      chromiumSandbox: true,
    },
  },
  webServer: {
    command: "PYTHONPATH=../plugin ../plugin/.venv/bin/python tests/server.py",
    url: "https://127.0.0.1:9443",
    ignoreHTTPSErrors: true,
    reuseExistingServer: false,
  },
});
