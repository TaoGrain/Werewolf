import { expect, test } from "@playwright/test";

test("真实前后端可创建并完成 FakeProvider 对局", async ({ page, request }) => {
  test.setTimeout(180_000);
  test.skip(process.env.REAL_API_E2E !== "1", "仅在本地集成验收时启用");

  await page.goto("/games/new");
  await page.getByRole("button", { name: "创建并进入对局" }).click();
  await page.waitForURL((url) => /^\/games\/[^/]+$/.test(url.pathname) && url.pathname !== "/games/new");
  const match = page.url().match(/\/games\/([^/?]+)/);
  expect(match).not.toBeNull();
  const gameId = match![1];
  await expect(page.getByText("安全观战中")).toBeVisible();
  await expect(page.getByText("身份隐藏").first()).toBeVisible();

  await page.getByRole("button", { name: "开始游戏" }).click();
  await expect(page.getByText("身份已分配").first()).toBeVisible({ timeout: 10_000 });

  const base = "http://127.0.0.1:8000";
  let finished = false;
  for (let index = 0; index < 250; index += 1) {
    const stateResponse = await request.get(`${base}/api/games/${gameId}`);
    expect(stateResponse.ok()).toBeTruthy();
    const state = await stateResponse.json() as { phase: string };
    if (state.phase === "FINISHED") {
      finished = true;
      break;
    }

    const commandId = `browser-e2e-${index}`;
    const queued = await request.post(`${base}/api/games/${gameId}/advance`, {
      headers: { "Idempotency-Key": commandId },
    });
    expect(queued.ok()).toBeTruthy();
    const queuedCommand = await queued.json() as { command_id: string; status: string; result?: unknown };
    const queuedCommandId = queuedCommand.command_id;
    let commandSucceeded = queuedCommand.status === "SUCCEEDED";
    for (let poll = 0; poll < 300; poll += 1) {
      if (commandSucceeded) break;
      const statusResponse = await request.get(`${base}/api/games/${gameId}/commands/${queuedCommandId}`);
      expect(statusResponse.ok()).toBeTruthy();
      const command = await statusResponse.json() as { status: string; result?: unknown };
      if (command.status === "FAILED") throw new Error(`阶段命令失败：${JSON.stringify(command.result)}`);
      if (command.status === "SUCCEEDED") {
        commandSucceeded = true;
        break;
      }
      await page.waitForTimeout(10);
    }
    expect(commandSucceeded).toBeTruthy();
  }

  await page.reload();
  expect(finished).toBeTruthy();
  await expect(page.getByRole("link", { name: "查看赛后复盘" })).toBeVisible({ timeout: 10_000 });
  await page.getByRole("link", { name: "查看赛后复盘" }).click();
  await expect(page.getByText("FACT-BASED REVIEW")).toBeVisible();
  await expect(page.getByRole("heading", { name: /迷雾散去/ })).toBeVisible();
});
