import { expect, test } from "@playwright/test";

test("customer can complete one bounded logistics investigation and refresh safely", async ({
  page,
}) => {
  const expectedMode = (process.env.EXPECTED_LLM_MODE ?? "mock").toUpperCase();
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "物流客服" })).toBeVisible();
  await expect(page.getByText(expectedMode, { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "重置合成 Demo" }).click();
  await page.getByRole("button", { name: "确认重置合成数据" }).click();
  await expect(page.getByText("目前可以处理“显示签收但没收到”和“物流长时间没更新”"))
    .toBeVisible();

  await page.getByRole("button", { name: /显示签收但没收到/ }).click();
  await page.getByRole("button", { name: "发送物流问题" }).click();
  await expect(page.getByRole("heading", { name: "需要我发起物流核查吗？" }))
    .toBeVisible({ timeout: 150_000 });
  await page.getByRole("button", { name: "发起物流核查" }).click();
  const processingNumber = page.getByText(/处理编号\s+TKT-SYN-/).last();
  await expect(processingNumber).toBeVisible({ timeout: 60_000 });
  const persistedText = await processingNumber.textContent();
  expect(persistedText).toBeTruthy();

  const geometry = await page.evaluate(() => ({
    documentOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    composerVisible: Boolean(document.querySelector("#customer-message")?.getBoundingClientRect().height),
  }));
  expect(geometry.documentOverflow).toBe(false);
  expect(geometry.composerVisible).toBe(true);

  await page.reload();
  await expect(page.getByText(persistedText as string, { exact: true })).toBeVisible();
  await expect(page.getByText(expectedMode, { exact: true })).toBeVisible();
});

test("evaluation dashboard has its own scroll surface and no fabricated empty report", async ({
  page,
}) => {
  await page.goto("/#eval");
  await expect(page.getByRole("heading", { name: "架构验收台" })).toBeVisible();
  const geometry = await page.locator(".eval-page").evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      overflowY: style.overflowY,
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
    };
  });
  expect(["auto", "scroll"]).toContain(geometry.overflowY);
  expect(geometry.clientHeight).toBeGreaterThan(0);
  await expect(
    page.getByText(/尚无评测报告|本次预注册结论/).first(),
  ).toBeVisible();
});
