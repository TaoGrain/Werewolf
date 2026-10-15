import { expect, test } from "@playwright/test";

const game = {
  id: "game-001",
  name: "月蚀测试局",
  phase: "DAY_DISCUSSION",
  day: 2,
  status: "active",
  is_paused: false,
  is_processing: false,
  god_mode_enabled: true,
  rules_version: "mvp-v1",
  ruleset_version: "mvp-v1",
  player_count: 6,
  alive_player_count: 5,
  discussion: {
    completed: 1,
    total: 5,
    completed_player_ids: ["player-1"],
    next_player_id: "player-2",
  },
  players: Array.from({ length: 6 }, (_, index) => ({
    id: `player-${index + 1}`,
    seat: index + 1,
    name: ["沈砚", "林雾", "周烬", "许昼", "白露", "陆川"][index],
    is_alive: index !== 3,
    role: index === 0 ? "SEER" : index === 1 ? "WITCH" : "VILLAGER",
    role_history: index === 0
      ? [{ day: 1, actor_id: "player-1", target_id: "player-3", alignment: "GOOD" }]
      : index === 1
        ? [{ day: 1, actor_id: "player-2", rescue_target_id: "player-4", poison_target_id: null }]
        : [],
  })),
};

const classicPlayers = Array.from({ length: 9 }, (_, index) => ({
  id: `classic-player-${index + 1}`,
  seat: index + 1,
  name: ["沈砚", "林雾", "周烬", "许昼", "白露", "陆川", "秦野", "苏禾", "顾舟"][index],
  is_alive: true,
  role: index < 3
    ? "WEREWOLF"
    : ["SEER", "WITCH", "HUNTER", "VILLAGER", "VILLAGER", "VILLAGER"][index - 3],
  role_history: index === 5 ? [
    { day: 2, event_type: "HUNTER_ACTION_REQUESTED", legal_target_ids: ["classic-player-2", "classic-player-4"] },
    { day: 2, event_type: "HUNTER_ACTION_SUBMITTED", action: "shoot", target_id: "classic-player-2" },
    { day: 2, event_type: "HUNTER_SHOT", target_id: "classic-player-2" },
    { day: 3, event_type: "HUNTER_PASSED" },
  ] : [],
}));

async function mockApi(page: import("@playwright/test").Page, override: Record<string, unknown> = {}) {
 
  const responseGame = { ...game, ...override };
  await page.route("http://127.0.0.1:8000/api/games", async (route) => {
    if (route.request().method() === "POST") return route.fulfill({ json: { ...responseGame, phase: "LOBBY", day: 0 } });
    return route.fulfill({ json: [responseGame] });
  });
  await page.route(/http:\/\/127\.0\.0\.1:8000\/api\/games\/game-001(?:\?.*)?$/, (route) => route.fulfill({ json: responseGame }));
  await page.route(/http:\/\/127\.0\.0\.1:8000\/api\/games\/game-001\/events.*/, (route) => {
    const hostView = new URL(route.request().url()).searchParams.get("god_mode") === "true";
    const events: Array<Record<string, unknown>> = [
      { id: "1", sequence: 1, type: "SPEECH", day: 2, phase: "DAY_DISCUSSION", player_id: "player-1", content: "**重点**：我想先听完后置位的观点。", visibility: "PUBLIC", payload: {} },
      { id: "2", sequence: 2, type: "NIGHT_ANNOUNCED", day: 2, phase: "DAY_ANNOUNCEMENT", visibility: "PUBLIC", payload: { death_ids: ["player-4"] } },
      { id: "3", sequence: 3, type: "VOTES_REVEALED", day: 2, phase: "DAY_VOTING", visibility: "PUBLIC", payload: { round: 1, votes: [{ actor_id: "player-1", target_id: "player-3", abstain: false }, { actor_id: "player-2", target_id: "player-3", abstain: false }, { actor_id: "player-5", target_id: null, abstain: true }], counts: { "player-3": 2 }, abstain_count: 1 } },
    ];
    if (hostView) events.push(
      { id: "4", sequence: 4, type: "WOLF_DISCUSSION_MESSAGE", day: 2, phase: "NIGHT_WOLF_DISCUSSION", visibility: "WOLF_TEAM_PRIVATE", player_id: "player-1", payload: { content: "我建议优先处理五号。", proposed_target_id: "player-5" } },
      { id: "5", sequence: 5, type: "WOLF_TARGET_SELECTED", day: 2, phase: "NIGHT_WOLF", visibility: "WOLF_TEAM_PRIVATE", payload: { target_id: "player-5" } },
    );
    return route.fulfill({ json: { events } });
  });
  await page.route(/http:\/\/127\.0\.0\.1:8000\/api\/games\/game-001\/(advance|pause|retry|start)/, (route) => route.fulfill({ json: { command_id: "mock-command", status: "RUNNING" } }));
  await page.route(/http:\/\/127\.0\.0\.1:8000\/api\/games\/game-001\/review.*/, (route) => route.fulfill({ json: {
    game: { ...responseGame, phase: "FINISHED", winner: { winner: "WOLVES", reason: "全部神职出局", reason_code: "ALL_GODS_ELIMINATED", alive_wolves: 2, alive_good: 3, alive_gods: 0, alive_villagers: 3 } },
    players: responseGame.players,
    events: [
      ...Array.from({ length: 8 }, (_, index) => ({
        id: `vote-${index + 1}`, sequence: index + 1, type: "VOTE_SUBMITTED", day: 1,
        phase: "DAY_VOTING", visibility: "HOST_PRIVATE", payload: {},
      })),
      ...Array.from({ length: 2 }, (_, index) => ({
        id: `reveal-${index + 1}`, sequence: index + 9, type: "VOTES_REVEALED", day: index + 1,
        phase: "DAY_VOTING", visibility: "PUBLIC", payload: {},
      })),
      { id: "hunter-request", sequence: 11, type: "HUNTER_ACTION_REQUESTED", day: 2, phase: "HUNTER_REACTION", visibility: "HOST_PRIVATE", player_id: "classic-player-6", payload: { hunter_id: "classic-player-6", legal_target_ids: ["classic-player-2", "classic-player-4"] } },
      { id: "hunter-submit", sequence: 12, type: "HUNTER_ACTION_SUBMITTED", day: 2, phase: "HUNTER_REACTION", visibility: "HOST_PRIVATE", player_id: "classic-player-6", payload: { action: "shoot", target_id: "classic-player-2", public_statement: "我带走二号。" } },
      { id: "hunter-shot", sequence: 13, type: "HUNTER_SHOT", day: 2, phase: "HUNTER_REACTION", visibility: "PUBLIC", player_id: "classic-player-6", payload: { hunter_id: "classic-player-6", target_id: "classic-player-2" } },
      { id: "hunter-pass", sequence: 14, type: "HUNTER_PASSED", day: 3, phase: "HUNTER_REACTION", visibility: "PUBLIC", player_id: "classic-player-6", payload: { hunter_id: "classic-player-6" } },
      { id: "hunter-death", sequence: 15, type: "PLAYER_DIED", day: 2, phase: "HUNTER_REACTION", visibility: "PUBLIC", payload: { player_id: "classic-player-2", death_causes: ["HUNTER_SHOT"] } },
    ],
    calls: [],
    metrics: { vote_count: 8, invalid_action_count: 0, llm_call_count: 0, total_tokens: 480, average_latency_ms: 0 },
    fact_based: true,
  } }));
  await page.route(/http:\/\/127\.0\.0\.1:8000\/api\/games\/game-001\/stream.*/, (route) => route.fulfill({ status: 200, headers: { "Content-Type": "text/event-stream" }, body: ": connected\n\n" }));
  await page.route(/http:\/\/127\.0\.0\.1:8000\/api\/games\/game-001\/players\/[^/]+\/calls.*/, (route) => route.fulfill({ json: [{
    id: "call-1", player_id: "player-1", day: 2, phase: "DAY_DISCUSSION", call_type: "speech",
    provider: "mock", model: "测试模型", status: "succeeded", latency_ms: 120,
    reasoning_text: "我先汇总公开发言，再判断矛盾点。", response_text: '{"speech":"建议关注三号玩家。"}',
    parsed_result: { speech: "建议关注三号玩家。", public_reason: "其发言前后不一致。" },
    input_tokens: 10, output_tokens: 20, reasoning_tokens: 5,
  }] }));
  await page.route(/http:\/\/127\.0\.0\.1:8000\/api\/games\/game-001\/players\/[^/]+\/memory.*/, (route) => route.fulfill({ json: { current: null, snapshots: [] } }));
}

test("大厅展示真实对局入口", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "对局大厅" })).toBeVisible();
  await expect(page.getByText("月蚀测试局")).toBeVisible();
  await expect(page.getByText("规则版本 · mvp-v1")).toBeVisible();
  await expect(page.getByText("5 / 6 存活")).toBeVisible();
  await expect(page.locator(".seat-dots i")).toHaveCount(6);
});

test("对局默认安全观战，只有显式操作才开启上帝视角", async ({ page }) => {
  await mockApi(page);
  await page.goto("/games/game-001");
  await expect(page.getByText("安全观战中")).toBeVisible();
  await expect(page.getByText("身份隐藏").first()).toBeVisible();
  await page.getByRole("button", { name: "显式开启上帝视角" }).click();
  await expect(page).toHaveURL(/god_mode=true/);
  await expect(page.getByText("上帝视角已开启")).toBeVisible();
});

test("创建表单默认选择经典 9 人局和安全观战", async ({ page }) => {
  await mockApi(page);
  await page.goto("/games/new");
  await expect(page.getByRole("heading", { name: "创建一场新对局" })).toBeVisible();
  await expect(page.getByRole("button", { name: /经典 9 人局/ })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("经典 9 人阵容")).toBeVisible();
  await expect(page.locator(".agent-list > div")).toHaveCount(9);
  await expect(page.getByText(/屠边与人数对等规则：狼人消灭全部神职/)).toBeVisible();
  await expect(page.getByText("安全观战", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /MiniMax M3/ })).toHaveAttribute("aria-pressed", "true");
  const thinkingToggle = page.getByRole("checkbox", { name: /开启模型思考/ });
  await expect(thinkingToggle).not.toBeChecked();
  const deepseekModel = page.getByRole("button", { name: /DeepSeek V4 Pro/ });
  await expect(async () => {
    await deepseekModel.click();
    await expect(deepseekModel).toHaveAttribute("aria-pressed", "true");
  }).toPass();
  const advancedSettings = page.locator("summary", { hasText: "高级配置" });
  await expect(advancedSettings).toBeVisible();
  await expect(page.getByRole("spinbutton", { name: "行动温度" })).not.toBeVisible();
  await advancedSettings.click();
  await expect(page.getByRole("spinbutton", { name: "发言温度" })).toHaveValue("0.55");
  await expect(page.getByRole("spinbutton", { name: "投票温度" })).toHaveValue("0.35");
  await expect(page.getByRole("spinbutton", { name: "行动温度" })).toHaveValue("0.5");
  await page.getByRole("spinbutton", { name: "发言温度" }).fill("0.7");
  await page.getByRole("spinbutton", { name: "投票温度" }).fill("0.4");
  await page.getByRole("spinbutton", { name: "行动温度" }).fill("0.8");
  await thinkingToggle.check();
  await expect(thinkingToggle).toBeChecked();
  await page.getByRole("button", { name: /上帝视角/ }).click();
  expect(await page.locator("form").evaluate((form) => (form as HTMLFormElement).checkValidity())).toBe(true);
  const createRequestPromise = page.waitForRequest((request) =>
    request.url() === "http://127.0.0.1:8000/api/games" && request.method() === "POST"
  );
  await page.getByRole("button", { name: "创建并进入对局" }).click();
  const createRequest = await createRequestPromise;
  expect(createRequest.postDataJSON()).toMatchObject({
    model: "deepseekv4pro",
    thinking_enabled: true,
    speech_temperature: 0.7,
    vote_temperature: 0.4,
    action_temperature: 0.8,
    ruleset_version: "classic-9-hunter-v1",
    player_count: 9,
    player_names: ["沈砚", "林雾", "周烬", "许昼", "白露", "陆川", "秦野", "苏禾", "顾舟"],
  });
  await expect(page).toHaveURL(/games\/game-001\?god_mode=true/, { timeout: 15_000 });
});

test("创建表单可切换到 6 人快速局并提交对应规则", async ({ page }) => {
  await mockApi(page);
  await page.goto("/games/new");

  await page.getByRole("button", { name: /6 人快速局/ }).click();

  await expect(page.getByRole("button", { name: /6 人快速局/ })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: /经典 9 人局/ })).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByLabel("6 人快速局阵容")).toBeVisible();
  await expect(page.locator(".agent-list > div")).toHaveCount(6);
  await expect(page.getByText(/人数对等规则：好人消灭全部狼人/)).toBeVisible();

  const createRequestPromise = page.waitForRequest((request) =>
    request.url() === "http://127.0.0.1:8000/api/games" && request.method() === "POST"
  );
  await page.getByRole("button", { name: "创建并进入对局" }).click();
  const createRequest = await createRequestPromise;
  expect(createRequest.postDataJSON()).toMatchObject({
    ruleset_version: "mvp-v1",
    player_count: 6,
    player_names: ["沈砚", "林雾", "周烬", "许昼", "白露", "陆川"],
  });
});

test("事件面板展示富文本、夜晚死亡和投票票数", async ({ page }) => {
  await mockApi(page);
  await page.goto("/games/game-001");
  await expect(page.locator(".timeline-item strong", { hasText: "重点" })).toBeVisible();
  await expect(page.getByText("昨夜死亡：4 号 许昼")).toBeVisible();
  await expect(page.getByText(/票数统计：3 号 周烬 2 票/)).toBeVisible();
  await expect(page.getByText(/弃票 1 票/)).toBeVisible();
});

test("白天发言逐位触发并在对局信息中展示", async ({ page }) => {
  await mockApi(page);
  await page.goto("/games/game-001");
  await expect(page.getByRole("button", { name: "下一位发言 · 林雾" })).toBeVisible();
  await expect(page.getByText("已完成 1 / 5，点击按钮后才会调用下一位 Agent")).toBeVisible();
  await expect(page.getByRole("heading", { name: "对局信息" })).toBeVisible();
  await expect(page.getByText("我想先听完后置位的观点。", { exact: false })).toBeVisible();
});

test("狼人交流和最终出刀显示为两个独立阶段", async ({ page }) => {
  await mockApi(page, { phase: "NIGHT_WOLF_DISCUSSION", discussion: undefined });
  await page.goto("/games/game-001");
  await expect(page.getByText("狼人交流", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "进行狼人交流" })).toBeVisible();
  await expect(page.getByText("交流完成后会停在狼人出刀阶段，不会自动出刀")).toBeVisible();
});

test("狼人出刀阶段需要再次点击确认", async ({ page }) => {
  await mockApi(page, { phase: "NIGHT_WOLF", discussion: undefined });
  await page.goto("/games/game-001");
  await expect(page.getByText("狼人出刀", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "确认狼人出刀" })).toBeVisible();
  await expect(page.getByText("点击后两名狼人将分别提交最终刀票")).toBeVisible();
});

test("右侧玩家发言可以收起和展开", async ({ page }) => {
  await mockApi(page);
  await page.goto("/games/game-001");
  const speech = page.locator(".speech-details").first();
  await expect(speech.getByText("我想先听完后置位的观点。", { exact: false })).toBeVisible();
  await speech.getByText("收起", { exact: true }).click();
  await expect(speech.getByText("我想先听完后置位的观点。", { exact: false })).toBeHidden();
  await speech.getByText("展开", { exact: true }).click();
  await expect(speech.getByText("我想先听完后置位的观点。", { exact: false })).toBeVisible();
});

test("主持人信息显示狼人最终出刀目标", async ({ page }) => {
  await mockApi(page);
  await page.goto("/games/game-001");
  await expect(page.getByText("狼人最终出刀目标", { exact: false })).toHaveCount(0);
  await page.getByRole("tab", { name: "主持人信息" }).click();
  await expect(page.getByRole("heading", { name: "主持人信息" })).toBeVisible();
  await expect(page.getByText(/我建议优先处理五号/)).toBeVisible();
  await expect(page.getByText("狼人最终出刀目标：5 号 白露。")).toBeVisible();
});

test("上帝视角可查看预言家查验历史", async ({ page }) => {
  await mockApi(page);
  await page.goto("/games/game-001/agents/player-1?god_mode=true");
  await expect(page.getByRole("heading", { name: "预言家查验历史" })).toBeVisible();
  await expect(page.getByText(/查验 3 号 周烬，结果为 好人阵营/)).toBeVisible();
  await expect(page.locator(".thinking-details").getByText("思考", { exact: true })).toBeVisible();
  await expect(page.getByText("我先汇总公开发言，再判断矛盾点。")).toBeVisible();
  const thinking = page.locator(".thinking-details").first();
  await thinking.getByText("收起", { exact: true }).click();
  await expect(thinking.getByText("我先汇总公开发言，再判断矛盾点。")).toBeHidden();
  await thinking.getByText("展开", { exact: true }).click();
  await expect(thinking.getByText("我先汇总公开发言，再判断矛盾点。")).toBeVisible();
});

test("复盘页使用服务端胜方、投票和 Token 指标", async ({ page }) => {
  await mockApi(page, { players: classicPlayers, player_count: 9, alive_player_count: 9, rules_version: "classic-9-hunter-v1", ruleset_version: "classic-9-hunter-v1" });
  await page.goto("/games/game-001/review");
  await expect(page.locator(".winner-card strong")).toHaveText("狼人阵营");
  await expect(page.locator(".winner-card small")).toContainText("屠神");
  await expect(page.getByLabel("终局分类存活数")).toContainText("2 狼人");
  await expect(page.getByLabel("终局分类存活数")).toContainText("0 神职");
  await expect(page.getByLabel("终局分类存活数")).toContainText("3 平民");
  await expect(page.locator(".identity-board").getByText("猎人", { exact: true })).toBeVisible();
  await expect(page.getByText("等待猎人行动", { exact: true })).toBeVisible();
  await expect(page.getByText(/合法目标：2 号 林雾、4 号 许昼/)).toBeVisible();
  await expect(page.getByText("猎人已作出选择", { exact: true })).toBeVisible();
  await expect(page.getByText(/公开声明：我带走二号/)).toBeVisible();
  await expect(page.getByText("猎人开枪", { exact: true })).toBeVisible();
  await expect(page.getByText(/6 号 陆川开枪带走了 2 号 林雾/)).toBeVisible();
  await expect(page.getByText("猎人放弃开枪", { exact: true })).toBeVisible();
  await expect(page.getByText(/原因：猎人枪杀/)).toBeVisible();
  await expect(page.locator(".review-metric", { hasText: "投票记录" }).locator("strong")).toHaveText("8");
  await expect(page.locator(".review-metric", { hasText: "累计 Token" }).locator("strong")).toHaveText("480");
});

test("猎人阶段展示唯一动作、明确任务并阻止重复命令", async ({ page }) => {
  let commandCount = 0;
  await mockApi(page, {
    phase: "HUNTER_REACTION",
    discussion: undefined,
    players: classicPlayers,
  });
  await page.route(/http:\/\/127\.0\.0\.1:8000\/api\/games\/game-001\/advance/, async (route) => {
    commandCount += 1;
    await new Promise((resolve) => setTimeout(resolve, 120));
    await route.fulfill({ json: { command_id: "hunter-command", status: "RUNNING" } });
  });

  await page.goto("/games/game-001");
  await expect(page.getByText("猎人反应", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("等待猎人作出最终选择")).toBeVisible();
  await expect(page.getByText(/猎人可向一名存活玩家开枪，或选择放弃/)).toBeVisible();
  const action = page.getByRole("button", { name: "执行猎人开枪或放弃" });
  await expect(page.locator(".controls button")).toHaveCount(1);
  await action.dblclick();
  await expect(action).toBeDisabled();
  await expect.poll(() => commandCount).toBe(1);
});

test("PK 发言阶段展示专用名称、进度和重投按钮", async ({ page }) => {
  await mockApi(page, {
    phase: "DAY_RUNOFF_DISCUSSION",
    players: classicPlayers,
    discussion: {
      completed: 1,
      total: 2,
      completed_player_ids: ["classic-player-2"],
      next_player_id: "classic-player-4",
    },
  });
  await page.goto("/games/game-001");
  await expect(page.getByText("平票 PK 发言", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("等待 4 号 许昼进行 PK 发言")).toBeVisible();
  await expect(page.getByRole("button", { name: "下一位 PK 发言 · 许昼" })).toBeVisible();
});

test("未知后端阶段保持原值且不会伪装为大厅", async ({ page }) => {
  await mockApi(page, { phase: "FUTURE_SERVER_PHASE", discussion: undefined });
  await page.goto("/games/game-001");
  await expect(page.getByText("未知阶段（FUTURE_SERVER_PHASE）", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "未知阶段，等待服务端更新" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "开始游戏" })).toHaveCount(0);
});

test("9 个座位在目标桌面与移动视口内可见且互不遮挡", async ({ page }) => {
  await mockApi(page, { players: classicPlayers, discussion: undefined });

  for (const [viewportIndex, width] of [320, 375, 1024, 1440].entries()) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/games/game-001");
    const table = page.locator(".game-table");
    const seats = table.locator(".table-player");
    await expect(seats).toHaveCount(9);
    const tableBox = await table.boundingBox();
    const boxes = await seats.evaluateAll((elements) => elements.map((element) => {
      const rect = element.getBoundingClientRect();
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
    }));
    expect(tableBox, `${width}px 下桌面应可见`).not.toBeNull();
    for (const [index, box] of boxes.entries()) {
      expect(box.width, `${width}px 下第 ${index + 1} 座应可点击`).toBeGreaterThan(0);
      expect(box.x).toBeGreaterThanOrEqual((tableBox?.x ?? 0) - 1);
      expect(box.x + box.width).toBeLessThanOrEqual((tableBox?.x ?? 0) + (tableBox?.width ?? 0) + 1);
      for (let other = index + 1; other < boxes.length; other += 1) {
        const peer = boxes[other];
        const overlaps = box.x < peer.x + peer.width
          && box.x + box.width > peer.x
          && box.y < peer.y + peer.height
          && box.y + box.height > peer.y;
        expect(overlaps, `${width}px 下第 ${index + 1} 与第 ${other + 1} 座不应重叠`).toBe(false);
      }
    }

    const centerHits = await seats.evaluateAll((elements) => elements.map((element) => {
      const rect = element.getBoundingClientRect();
      const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
      return hit != null && element.contains(hit);
    }));
    expect(centerHits, `${width}px 下 9 个座位中心都应命中自身而非遮挡层`).toEqual(Array(9).fill(true));
    for (let index = 0; index < 9; index += 1) {
      await seats.nth(index).click({ trial: true });
    }

    const clickedSeat = viewportIndex * 2 + 1;
    await seats.nth(clickedSeat - 1).click();
    await expect(page, `${width}px 下第 ${clickedSeat} 座应可通过真实指针点击打开`).toHaveURL(
      new RegExp(`/games/game-001/agents/classic-player-${clickedSeat}$`),
    );
  }
});

test("旧 6 人持久化对局可从大厅进入终局并打开复盘", async ({ page }) => {
  const legacyId = "legacy-mvp-persisted";
  const legacyPlayers = game.players.map((player, index) => ({
    ...player,
    id: `legacy-player-${index + 1}`,
    is_alive: index >= 4,
    status: index >= 4 ? "ALIVE" : "DEAD",
    role: ["WEREWOLF", "WEREWOLF", "SEER", "WITCH", "VILLAGER", "VILLAGER"][index],
  }));
  const persistedSummary = {
    id: legacyId,
    name: "旧版六人存档",
    status: "ACTIVE",
    phase: "DAY_RESOLUTION",
    day: 3,
    rules_version: "mvp-v1",
    ruleset_version: "mvp-v1",
    random_seed: 42,
    player_count: 6,
    alive_player_count: 2,
    players: legacyPlayers.map(({ id, seat, name, is_alive }) => ({
      id,
      seat,
      name,
      alive: is_alive,
      is_alive,
    })),
    created_at: "2026-07-20T08:00:00",
    updated_at: "2026-07-20T08:30:00",
  };
  const activeGame = {
    ...persistedSummary,
    god_mode_enabled: true,
    is_paused: false,
    is_processing: false,
    players: legacyPlayers,
  };
  const finishedGame = {
    ...activeGame,
    status: "FINISHED",
    phase: "FINISHED",
    winner: "GOOD",
  };
  let exposePersistedTerminal = false;
  let reviewRequestCount = 0;

  await page.route("http://127.0.0.1:8000/api/games", (route) => route.fulfill({ json: [persistedSummary] }));
  await page.route(
    new RegExp(`http://127\\.0\\.0\\.1:8000/api/games/${legacyId}(?:\\?.*)?$`),
    (route) => route.fulfill({ json: exposePersistedTerminal ? finishedGame : activeGame }),
  );
  await page.route(
    new RegExp(`http://127\\.0\\.0\\.1:8000/api/games/${legacyId}/events.*`),
    (route) => route.fulfill({ json: { events: [] } }),
  );
  await page.route(
    new RegExp(`http://127\\.0\\.0\\.1:8000/api/games/${legacyId}/stream.*`),
    (route) => route.fulfill({
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
      body: ": connected\n\n",
    }),
  );
  await page.route(
    new RegExp(`http://127\\.0\\.0\\.1:8000/api/games/${legacyId}/review.*`),
    (route) => {
      reviewRequestCount += 1;
      return route.fulfill({
        json: {
          game: finishedGame,
          players: legacyPlayers,
          events: [{
            id: "legacy-finished",
            sequence: 1,
            event_type: "GAME_FINISHED",
            type: "GAME_FINISHED",
            day: 3,
            phase: "FINISHED",
            visibility: "PUBLIC",
            payload: { winner: "GOOD" },
          }],
          calls: [],
          metrics: { vote_count: 6, invalid_action_count: 0, total_tokens: 0 },
          fact_based: true,
        },
      });
    },
  );

  await page.goto("/");
  const legacyCard = page.locator(".game-card", { hasText: "旧版六人存档" });
  await expect(legacyCard.getByText("规则版本 · mvp-v1")).toBeVisible();
  await expect(legacyCard.getByText("2 / 6 存活")).toBeVisible();
  await expect(legacyCard.locator(".seat-dots i")).toHaveCount(6);
  await legacyCard.click();

  await expect(page).toHaveURL(new RegExp(`/games/${legacyId}$`));
  await expect(page.locator(".game-table .table-player")).toHaveCount(6);
  await expect(page.getByText("投票结算", { exact: true }).first()).toBeVisible();

  exposePersistedTerminal = true;
  await page.reload();
  await expect(page.getByText("对局结束", { exact: true }).first()).toBeVisible();
  await page.getByRole("link", { name: "查看赛后复盘" }).click();

  await expect(page).toHaveURL(new RegExp(`/games/${legacyId}/review$`));
  await expect(page.locator(".winner-card strong")).toHaveText("好人阵营");
  const callMetric = page.locator(".review-metric").filter({ has: page.getByText("模型调用", { exact: true }) });
  await expect(callMetric).toContainText("6 名独立 Agent");
  await expect(page.locator(".identity-board").getByText("狼人", { exact: true })).toHaveCount(2);
  await expect.poll(() => reviewRequestCount).toBe(1);
});

test("大厅空玩家列表仍严格使用 API 人数与规则摘要", async ({ page }) => {
  await mockApi(page, {
    players: [],
    player_count: 9,
    alive_player_count: 8,
    rules_version: "classic-9-hunter-v1",
    ruleset_version: "classic-9-hunter-v1",
  });
  await page.goto("/");
  const card = page.locator(".game-card", { hasText: "月蚀测试局" });
  await expect(card.getByText("规则版本 · classic-9-hunter-v1")).toBeVisible();
  await expect(card.getByText("8 / 9 存活")).toBeVisible();
  await expect(card.locator(".seat-dots i.alive")).toHaveCount(8);
  await expect(card.locator(".seat-dots i.dead")).toHaveCount(1);
});

test("上帝视角展示猎人开枪与放弃历史", async ({ page }) => {
  await mockApi(page, { players: classicPlayers, player_count: 9, alive_player_count: 9 });
  await page.goto("/games/game-001/agents/classic-player-6?god_mode=true");
  await expect(page.getByRole("heading", { name: "猎人行动历史" })).toBeVisible();
  await expect(page.getByText("第 2 天", { exact: true })).toHaveCount(3);
  await expect(page.getByText(/获得猎人反应机会.*合法目标：2 号 林雾、4 号 许昼/)).toBeVisible();
  await expect(page.getByText(/提交开枪决定，目标为 2 号 林雾/)).toBeVisible();
  await expect(page.getByText(/枪杀结果：2 号 林雾 出局/)).toBeVisible();
  await expect(page.getByText("最终选择放弃开枪", { exact: true })).toBeVisible();
});

test("安全观战查看猎人 Agent 时不请求私有调用或记忆", async ({ page }) => {
  let privateRequestCount = 0;
  await mockApi(page, { players: classicPlayers, player_count: 9, alive_player_count: 9 });
  await page.route(/\/players\/classic-player-6\/calls/, async (route) => {
    privateRequestCount += 1;
    await route.fulfill({ json: [] });
  });
  await page.route(/\/players\/classic-player-6\/memory/, async (route) => {
    privateRequestCount += 1;
    await route.fulfill({ json: { current: null, snapshots: [] } });
  });
  await page.goto("/games/game-001/agents/classic-player-6");
  await expect(page.getByText("安全观战过滤已启用")).toBeVisible();
  await page.waitForTimeout(300);
  expect(privateRequestCount).toBe(0);
  await expect(page.getByRole("heading", { name: "猎人行动历史" })).toHaveCount(0);
});

test("9 Agent 进度禁用操作并在总超时后允许重试", async ({ page }) => {
  await mockApi(page, { players: classicPlayers, player_count: 9, alive_player_count: 9 });
  let state: Record<string, unknown> = {
    ...game,
    is_processing: true,
    progress: { completed: 4, total: 9, label: "投票" },
  };
  let retryCount = 0;
  await page.route(/http:\/\/127\.0\.0\.1:8000\/api\/games\/game-001(?:\?.*)?$/, async (route) => {
    await route.fulfill({ json: state });
  });
  await page.route(/http:\/\/127\.0\.0\.1:8000\/api\/games\/game-001\/retry$/, async (route) => {
    retryCount += 1;
    await route.fulfill({ json: { command_id: "retry-timeout", status: "RUNNING" } });
  });

  await page.goto("/games/game-001");
  await expect(page.getByText("投票", { exact: true })).toBeVisible();
  await expect(page.getByText("4 / 9 项任务完成", { exact: true })).toBeVisible();
  const controls = page.locator(".controls button");
  await expect(controls).toHaveCount(2);
  for (let index = 0; index < await controls.count(); index += 1) {
    await expect(controls.nth(index)).toBeDisabled();
  }

  state = {
    ...state,
    is_processing: false,
    progress: null,
    error: "阶段推进超过 120 秒总超时",
    error_type: "timeout_error",
    retryable: true,
  };
  await page.reload();
  await expect(page.getByText("阶段执行失败", { exact: true })).toBeVisible();
  await expect(page.getByText("阶段推进超过 120 秒总超时", { exact: true })).toBeVisible();
  const retry = page.getByRole("button", { name: "重试", exact: true });
  await expect(retry).toBeVisible();
  await retry.click();
  await expect.poll(() => retryCount).toBe(1);
});
