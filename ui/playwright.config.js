import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";
for (const name of ["WORKBENCH_TEST_TLS_CERT", "WORKBENCH_TEST_TLS_KEY"]) {
  if (!process.env[name] || !existsSync(process.env[name]))
    throw new Error(
      "BROWSER_BLOCKED_VALID_TLS_REQUIRED: provide a trusted certificate/key for 127.0.0.1; certificate checks and browser sandbox remain enabled",
    );
}
export default defineConfig({
  testDir: "./tests",
  workers: 1,
  use: {
    baseURL: "https://127.0.0.1:9443",
    launchOptions: {
      executablePath:
        process.env.WORKBENCH_CHROMIUM_PATH || "/usr/bin/chromium",
      chromiumSandbox: true,
    },
  },
  webServer: {
    command: "PYTHONPATH=../plugin ../plugin/.venv/bin/python tests/server.py",
    url: "https://127.0.0.1:9443",
    reuseExistingServer: false,
  },
});
