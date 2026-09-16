import { test, expect } from "@playwright/test";
test("saved evidence opens its exact source chunk without model calls", async ({
  page,
}) => {
  await page.goto("/retrieval");
  await page.getByLabel("历史检索问题").selectOption({ index: 1 });
  await expect(page.getByText("5 条", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "[3]", exact: true }).click();
  await expect(page.getByText("证据 [3]", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "打开分块", exact: true }).click();
  await expect(page).toHaveURL(/documents\/doc_.*chunk=doc_/);
  await expect(page.locator(".chunk-detail")).toBeVisible();
});
test("real imported paper, PDF, chunk selection and URL persistence", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByRole("button", {
      name: "打开 Inescapable Anisotropy of Nonreciprocal XY Models",
      exact: true,
    })
    .click();
  await expect(
    page.getByText("15 块 · 上限 2500", { exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("PDF 第 1 页")).toHaveAttribute(
    "width",
    /[4-9]\d\d/,
  );
  await page.getByRole("button", { name: /15 2316 t 参考文献/ }).click();
  await expect(page).toHaveURL(/chunk=/);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "References", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "原始 Markdown", exact: true }).click();
  await expect(page.locator(".reading-scroll .raw-text")).toContainText(
    "Inescapable Anisotropy",
  );
  await page.screenshot({
    path: "test-results/workbench-desktop.png",
    fullPage: true,
  });
});
test("responsive navigation and persisted dark theme", async ({ page }) => {
  await page.setViewportSize({ width: 600, height: 900 });
  await page.goto("/");
  await page.getByRole("button", { name: "切换深色" }).click();
  await page.reload();
  await expect(page.locator("html")).toHaveClass("dark");
  await page.getByRole("button", { name: "系统状态", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "系统状态", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("text-embedding-v3", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "切换浅色" }).click();
});
test("upload rejects malformed PDF without starting a run", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "添加论文" }).click();
  await page
    .getByLabel("选择 PDF 文件")
    .setInputFiles({
      name: "broken.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-broken"),
    });
  await page.getByRole("button", { name: "保存文件", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("PDF 无法读取");
  await expect(
    page.getByRole("button", { name: "开始处理", exact: true }),
  ).toHaveCount(0);
});
