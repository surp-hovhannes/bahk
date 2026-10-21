const { defineConfig } = require("@playwright/test");
const fs = require("node:fs");

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: "*.spec.cjs",
  fullyParallel: true,
  workers: 2,
  reporter: "list",
  outputDir: "/tmp/bahk-widget-test-results",
  use: {
    baseURL: "http://127.0.0.1:8765",
    browserName: "chromium",
    launchOptions: {
      executablePath: process.env.CHROMIUM_PATH || (fs.existsSync("/usr/bin/chromium") ? "/usr/bin/chromium" : undefined),
    },
  },
  webServer: {
    command: "node tests/widgets/server.cjs",
    cwd: require("node:path").resolve(__dirname, "../.."),
    url: "http://127.0.0.1:8765",
    reuseExistingServer: false,
  },
});
