import { test, expect, type Page } from "@playwright/test";

async function researchApi(
  page: Page,
  initial: "new" | "partial" | "failed" = "new",
) {
  const state = {
    created: initial !== "new",
    completed: initial !== "new",
    posts: [] as { path: string; body: any; key?: string }[],
  };
  const stamp = "2026-09-17T08:00:00Z";
  const result = () => ({
    topic: "RAG 查询改写的方法比较",
    mode: "review",
    collection: "fixture",
    status: state.completed
      ? initial === "failed"
        ? "failed"
        : initial === "partial"
          ? "partial"
          : "draft"
      : "in_progress",
    stop_reason: state.completed ? "round_limit" : null,
    markdown: state.completed
      ? "## 方法比较\n\n多查询可补充覆盖 [E2]。\n\n## 局限\n\n仍需核读原文。"
      : "",
    evidence: ["A", "B"].map((name, i) => ({
      id: `E${i + 1}`,
      chunk_id: `chunk_${name}`,
      document_id: `doc_${name}`,
      run_id: `ingest_${name}`,
      title: `研究论文 ${name}`,
      year: "2024",
      text: `论文 ${name} 的证据片段`,
      section: "Methods",
    })),
    iterations: [
      {
        round: 1,
        queries: ["RAG 查询改写的方法比较"],
        new_evidence: 2,
        sufficient: false,
        relevant_ids: ["E1", "E2"],
        gaps: ["缺少方法局限的比较证据"],
        next_queries: ["查询改写的局限"],
      },
    ],
    gaps: ["缺少方法局限的比较证据"],
    warnings:
      initial === "partial" ? ["unsupported_claim_or_date_removed"] : [],
    model_calls: 2,
    source_count: 2,
    run_options: { max_rounds: 3 },
    model: "scripted",
  });
  const run = (id = "run_research") => ({
    id,
    kind: "research",
    title: result().topic,
    collection: "fixture",
    status: state.completed
      ? initial === "failed"
        ? "failed"
        : "succeeded"
      : "running",
    stage: state.completed ? "completed" : "retrieval",
    source: "live",
    created_at: stamp,
    updated_at: stamp,
    started_at: new Date(Date.now() - 3000).toISOString(),
    finished_at: state.completed ? new Date().toISOString() : null,
    config: {},
    result: { research_status: result().status },
    error:
      initial === "failed"
        ? {
            message: "研究阶段执行失败，证据已保留",
            code: "research_failed",
            retryable: true,
          }
        : null,
  });
  const events = () => [
    {
      event_id: 1,
      run_id: "run_research",
      stage: "retrieval",
      phase: state.completed ? "completed" : "started",
      round: 1,
      status: "running",
      message: "检索文献证据",
      timestamp: stamp,
      data: {},
    },
  ];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    if (request.method() === "POST") {
      state.posts.push({
        path,
        body: request.postDataJSON(),
        key: request.headers()["idempotency-key"],
      });
      state.created = true;
      await route.fulfill({
        status: 202,
        json: run(path.endsWith("/retry") ? "run_retry" : undefined),
      });
    } else if (path === "/api/v1/config/public") {
      await route.fulfill({ json: { collection: "fixture" } });
    } else if (path === "/api/v1/runs") {
      await route.fulfill({
        json: { items: state.created ? [run()] : [], next_cursor: null },
      });
    } else if (path.endsWith("/event-history")) {
      await route.fulfill({ json: events() });
    } else if (path.endsWith("/events")) {
      await route.fulfill({
        contentType: "text/event-stream",
        body: `id: 1\nevent: progress\ndata: ${JSON.stringify(events()[0])}\n\n${state.completed ? "event: done\ndata: {}\n\n" : ""}`,
      });
    } else if (path.endsWith("/result")) {
      await route.fulfill({ json: result() });
    } else if (/\/runs\/[^/]+$/.test(path)) {
      await route.fulfill({ json: run(path.split("/").at(-1)) });
    } else {
      await route.fulfill({
        status: 404,
        json: { error: { message: "Fixture endpoint unavailable" } },
      });
    }
  });
  return state;
}

test("research submission, live progress, report citations and original source navigation", async ({
  page,
}) => {
  const state = await researchApi(page);
  await page.goto("/research");
  await page.getByLabel("你想研究什么？").fill("RAG 查询改写的方法比较");
  await page.getByRole("button", { name: "开始研究", exact: true }).click();
  await expect(page).toHaveURL(/research\?run=run_research/);
  await expect(page.locator(".research-node.active")).toContainText("检索文献");
  await expect(page.getByText("缺少方法局限的比较证据")).toBeVisible();
  expect(state.posts[0].key).toBeTruthy();
  expect(state.posts[0].body).toMatchObject({
    max_rounds: 3,
    queries_per_round: 2,
    top_k: 5,
    mode: "review",
  });
  expect(state.posts[0].body.allow_web).toBeUndefined();
  state.completed = true;
  await expect(page.locator(".research-report")).toContainText(
    "已生成研究草稿",
    { timeout: 10000 },
  );
  await page
    .locator(".research-report")
    .getByRole("button", { name: "E2", exact: true })
    .click();
  await expect(page.locator(".research-excerpt")).toContainText(
    "论文 B 的证据片段",
  );
  await expect(
    page.getByRole("link", { name: "下载 Markdown" }),
  ).toHaveAttribute("href", /research_report$/);
  await page.screenshot({
    path: "test-results/research-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "打开原文与分块" }).click();
  await expect(page).toHaveURL(/documents\/doc_B.*chunk=chunk_B/);
});

test("historical partial result survives reload and fits narrow dark screen", async ({
  page,
}) => {
  const state = await researchApi(page, "partial");
  await page.setViewportSize({ width: 600, height: 900 });
  await page.goto("/research?run=run_research");
  await page.getByRole("button", { name: "切换深色" }).click();
  await page.reload();
  await expect(page.locator("html")).toHaveClass("dark");
  await expect(page.locator(".research-report")).toContainText(
    "部分结果 · 存在局限",
  );
  await expect(page.getByText("查看 1 条运行警告")).toBeVisible();
  await expect(page.getByText("第 1 轮", { exact: true })).toBeVisible();
  expect(state.posts).toHaveLength(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/research-mobile-dark.png",
    fullPage: true,
  });
});

test("failed research retains evidence and full retry opens a new task", async ({
  page,
}) => {
  const state = await researchApi(page, "failed");
  await page.goto("/research?run=run_research");
  await expect(page.getByRole("alert")).toContainText("证据已保留");
  await expect(page.locator(".research-excerpt")).toContainText(
    "论文 A 的证据片段",
  );
  await page.getByRole("button", { name: "从头重新运行" }).click();
  await expect(page).toHaveURL(/run=run_retry/);
  expect(state.posts[0].path).toBe("/api/v1/runs/run_research/retry");
  expect(state.posts[0].body).toEqual({ stage: "auto" });
});
