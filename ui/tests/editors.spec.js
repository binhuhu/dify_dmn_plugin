import { test, expect } from "@playwright/test";

async function example(page) {
  await page.goto("/");
  await page.getByLabel("工作台口令").fill("SYNTHETIC-browser-password-only");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("button", { name: "教学支持示例" }).click();
}

test("referenced input cannot be deleted or silently renamed", async ({
  page,
}) => {
  await example(page);
  await page
    .getByRole("button", { name: "删除字段 needs_support", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("不能删除 needs_support");
  await expect(page.getByLabel("needs_support 字段类型")).toHaveValue(
    "boolean",
  );
});

test("typed nested example authoring is independent of model facts", async ({
  page,
}) => {
  await example(page);
  await page.getByLabel("新字段稳定名称").fill("synthetic_payload");
  await page.getByLabel("新字段类型", { exact: true }).selectOption("object");
  await page.getByRole("button", { name: "新增输入字段", exact: true }).click();
  const field = page
    .locator("fieldset")
    .filter({ has: page.locator("legend", { hasText: /^synthetic_payload$/ }) })
    .first();
  await field.getByText("独立示例值（不会默认提供事实）").click();
  await page.getByLabel("synthetic_payload 示例值 新属性名").fill("entries");
  await page
    .getByLabel("synthetic_payload 示例值 新项类型")
    .selectOption("array");
  await field.getByRole("button", { name: "添加属性", exact: true }).click();
  await page
    .getByLabel("synthetic_payload 示例值.entries 新项类型")
    .selectOption("null");
  await field.getByRole("button", { name: "添加数组项", exact: true }).click();
  await expect(
    page.getByLabel("synthetic_payload 示例值.entries.0", { exact: true }),
  ).toContainText("空值");
});

test("invalid and unsafe integer edits stay local and show an error", async ({
  page,
}) => {
  await example(page);
  await page.getByLabel("新字段稳定名称").fill("synthetic_amount");
  await page.getByLabel("新字段类型", { exact: true }).selectOption("integer");
  await page.getByRole("button", { name: "新增输入字段", exact: true }).click();
  const field = page
    .locator("fieldset")
    .filter({ has: page.locator("legend", { hasText: /^synthetic_amount$/ }) })
    .first();
  await field.getByText("独立示例值（不会默认提供事实）").click();
  await page
    .getByLabel("synthetic_amount 示例值", { exact: true })
    .fill("9007199254740992");
  await page.getByLabel("新字段稳定名称").click();
  await expect(field.getByRole("alert")).toContainText("无效值未提交");
});
