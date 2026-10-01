import { test, expect } from "@playwright/test";
async function setup(page) {
  await page.goto("/");
  await page.getByLabel("工作台口令").fill("SYNTHETIC-browser-password-only");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("button", { name: "教学支持示例" }).click();
  await expect(page.getByLabel("规则1 业务状态")).toHaveValue("REVIEW");
}
test("late real evaluation response cannot overwrite edited draft", async ({
  page,
}) => {
  await setup(page);
  let release, received;
  const ready = new Promise((resolve) => (received = resolve)),
    pending = new Promise((resolve) => (release = resolve));
  await page.route("**/api/evaluate", async (route) => {
    const response = await route.fetch();
    received();
    await pending;
    await route.fulfill({ response });
  });
  await page.getByRole("button", { name: "试算", exact: true }).click();
  await ready;
  await page.getByLabel("规则1 业务状态").fill("SYNTHETIC_NEW_DRAFT");
  release();
  await expect(
    page.getByRole("button", { name: "试算", exact: true }),
  ).toBeEnabled();
  await expect(page.locator(".results")).toHaveCount(0);
});
test("late freeze response cannot enable template export for a newer draft", async ({
  page,
}) => {
  await setup(page);
  await expect(page.getByRole("status")).toHaveText("已保存到事务存储");
  let release, received;
  const ready = new Promise((resolve) => (received = resolve)),
    pending = new Promise((resolve) => (release = resolve));
  await page.route("**/api/releases/freeze", async (route) => {
    const response = await route.fetch();
    received();
    await pending;
    await route.fulfill({ response });
  });
  await page.getByRole("button", { name: "冻结内容版本", exact: true }).click();
  await ready;
  await page.getByLabel("项目名称").fill("newer draft");
  release();
  await expect(page.getByRole("status")).toHaveText("已保存到事务存储");
  await expect(
    page.getByRole("button", { name: "生成 Dify 双流程模板" }),
  ).toBeDisabled();
});
