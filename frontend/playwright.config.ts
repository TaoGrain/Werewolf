import { defineConfig, devices } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

function getRootEnv(name: string) {
  if (process.env[name]) return process.env[name];
  for (const filename of [".env", ".env.example"]) {
    const file = path.resolve(process.cwd(), "..", filename);
    if (!fs.existsSync(file)) continue;
    const line = fs.readFileSync(file, "utf8")
      .split(/\r?\n/)
      .find((candidate) => candidate.trimStart().startsWith(`${name}=`));
    if (!line) continue;
    return line.slice(line.indexOf("=") + 1).trim().replace(/^['"]|['"]$/g, "");
  }
  return undefined;
}

const testHost = getRootEnv("PLAYWRIGHT_HOST");
const testPort = getRootEnv("PLAYWRIGHT_PORT");
if (!testHost || !testPort) {
  throw new Error("PLAYWRIGHT_HOST 或 PLAYWRIGHT_PORT 未配置");
}
const testBaseUrl = `http://${testHost}:${testPort}`;

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  reporter: "list",
  use: {
    baseURL: testBaseUrl,
    trace: "on-first-retry",
  },
  webServer: {
    command: `npm run dev -- -p ${testPort}`,
    url: testBaseUrl,
    reuseExistingServer: !process.env.CI,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
