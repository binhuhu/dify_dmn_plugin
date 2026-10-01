import { test, expect } from "@playwright/test";

async function login(page) {
  await page.goto("/");
  await page.getByLabel("工作台口令").fill("SYNTHETIC-browser-password-only");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
}
test("actual Endpoint: edit, save, refresh, trial, freeze and separate flows", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await login(page);
  await page.getByRole("button", { name: "教学支持示例" }).click();
  await page.getByLabel("项目名称").fill("SYNTHETIC browser edited");
  await expect(page.getByRole("status")).toHaveText("已保存到事务存储");
  await page.reload();
  await expect(page.getByLabel("项目名称")).toHaveValue(
    "SYNTHETIC browser edited",
  );
  await page.getByLabel("needs_support质量").selectOption("KNOWN");
  await page.getByLabel("needs_support试算值").selectOption("true");
  await page.getByLabel("规则1 业务状态").fill("MANUAL_REVIEW");
  await page.getByRole("button", { name: "试算", exact: true }).click();
  await expect(page.locator(".results")).toContainText("MANUAL_REVIEW");
  await expect(page.getByRole("table")).toContainText(
    "已选择（不代表执行动作）",
  );
  await page
    .getByRole("button", { name: "审阅并将结果设为本流程必需样例" })
    .click();
  await expect(page.getByRole("status")).toHaveText("已保存到事务存储");
  await page.getByRole("button", { name: "解决方案", exact: true }).click();
  await expect(page.getByLabel("规则1 业务状态")).toHaveValue("REVIEW");
  await page.getByRole("button", { name: "流程画布" }).click();
  await expect(page.locator(".react-flow")).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "冻结内容版本" }).click();
  await download;
  await expect(page.getByRole("status")).toContainText("未部署");
  expect(errors).toEqual([]);
});
test("failed/unknown trial clears success; offline save keeps exportable draft", async ({
  page,
  context,
}) => {
  await login(page);
  await page.getByRole("button", { name: "订单问题示例" }).click();
  await page.getByRole("button", { name: "试算", exact: true }).click();
  await expect(page.locator(".results")).toContainText("REVIEW");
  await page.getByLabel("delivery_issue质量").selectOption("UNKNOWN");
  await expect(page.locator(".results")).toHaveCount(0);
  await page.getByRole("button", { name: "试算", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("输入未知");
  await expect(page.locator(".results")).toHaveCount(0);
  await context.setOffline(true);
  await page.getByLabel("项目名称").fill("offline draft");
  await expect(page.getByRole("status")).toContainText("网络不可达");
  await expect(page.getByLabel("项目名称")).toHaveValue("offline draft");
  await expect(page.getByRole("button", { name: "导出草稿" })).toBeEnabled();
  await context.setOffline(false);
});
test("two browser tabs cannot silently overwrite the same revision", async ({
  page,
  context,
}) => {
  await login(page);
  await page.getByRole("button", { name: "教学支持示例" }).click();
  await expect(page.getByRole("status")).toHaveText("已保存到事务存储");
  const other = await context.newPage();
  await other.goto("/");
  await expect(other.getByLabel("项目名称")).toBeVisible();
  await page.getByLabel("项目名称").fill("winner");
  await expect(page.getByRole("status")).toHaveText("已保存到事务存储");
  await other.getByLabel("项目名称").fill("stale tab draft");
  await expect(other.getByRole("status")).toContainText("另一页面已保存");
  await expect(other.getByLabel("项目名称")).toHaveValue("stale tab draft");
  await other.close();
});
test("browser cannot supply tenant or bypass CSRF", async ({ page }) => {
  await login(page);
  const status = await page.evaluate(async () => {
    const session = await fetch("./api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }).then((r) => r.json());
    const scoped = await fetch("./api/projects/list", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": session.csrf,
      },
      body: JSON.stringify({ workspace_id: "other" }),
    });
    const csrf = await fetch("./api/projects/list", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    return [scoped.status, csrf.status];
  });
  expect(status).toEqual([403, 403]);
});
