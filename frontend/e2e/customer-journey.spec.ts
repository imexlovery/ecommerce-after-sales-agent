import { expect, test } from "@playwright/test";

test("customer can complete one bounded logistics investigation and refresh safely", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1094, height: 506 });
  const expectedMode = (process.env.EXPECTED_LLM_MODE ?? "mock").toUpperCase();
  const expectPolicyUnavailable = process.env.SURFACE_EXPECT_POLICY_UNAVAILABLE === "1";
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "物流客服" })).toBeVisible();
  await expect(page.getByText(expectedMode, { exact: true })).toBeVisible();
  await expect(page.getByText("DATASET business-demo-v1", { exact: true })).toBeVisible();
  await expect(page.getByText("当前虚拟客户", { exact: true })).toBeVisible();
  await expect(page.getByText("可访问订单 / 包裹", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "重置合成 Demo" }).click();
  await page.getByRole("button", { name: "确认重置合成数据" }).click();
  await expect(page.getByText("目前可以处理“显示签收但没收到”和“物流长时间没更新”"))
    .toBeVisible();

  await page.getByRole("button", { name: /显示签收但没收到/ }).click();
  await page.getByRole("button", { name: "发送物流问题" }).click();
  if (expectPolicyUnavailable) {
    await expect(page.getByText("关键物流信息暂时不可用")).toBeVisible({ timeout: 150_000 });
    await expect(page.getByText(/尚未创建任何工单/)).toBeVisible();
    await expect(page.getByRole("button", { name: "发起物流核查" })).toHaveCount(0);
    await page.reload();
    await expect(page.getByText("关键物流信息暂时不可用")).toBeVisible();
    await expect(page.getByRole("button", { name: "发起物流核查" })).toHaveCount(0);
    return;
  }
  await expect(page.getByRole("heading", { name: "需要我发起物流核查吗？" }))
    .toBeVisible({ timeout: 150_000 });
  await expect(page.getByText("条款摘录")).toBeVisible();
  await expect(page.locator('[data-customer-disposition="INVESTIGATE"]')).toBeVisible();
  await expect(page.getByLabel("customer_disposition=INVESTIGATE")).toBeVisible();
  await expect(
    page.getByText("说明文本，非 Evidence Gate 或 Proposal 的决策依据"),
  ).toBeVisible();
  await expect(page.getByText("未生成可用摘录")).toHaveCount(0);

  const traceGeometry = await page.locator(".progress-steps").evaluate((container) => {
    const overlaps = (first: DOMRect, second: DOMRect) =>
      first.left < second.right - 0.5 &&
      first.right > second.left + 0.5 &&
      first.top < second.bottom - 0.5 &&
      first.bottom > second.top + 0.5;

    const hasTextOverlap = Array.from(container.querySelectorAll(".progress-step")).some((step) => {
      const heading = step.querySelector(".progress-step__heading");
      const title = step.querySelector("h3");
      const status = step.querySelector(".progress-step__heading span");
      const detail = step.querySelector(".progress-step__body > p");
      if (!heading || !title || !status || !detail) return true;

      const headingRect = heading.getBoundingClientRect();
      const detailRect = detail.getBoundingClientRect();
      return (
        overlaps(title.getBoundingClientRect(), status.getBoundingClientRect()) ||
        detailRect.top < headingRect.bottom - 0.5
      );
    });

    return {
      hasTextOverlap,
      hasHorizontalOverflow: container.scrollWidth > container.clientWidth,
    };
  });
  expect(traceGeometry.hasTextOverlap).toBe(false);
  expect(traceGeometry.hasHorizontalOverflow).toBe(false);

  await page.getByRole("button", { name: "发起物流核查" }).click();
  await expect(page.getByText("已为你发起物流核查")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("处理请求已经提交并确认，请保留处理编号，无需重复提交。"))
    .toBeVisible();
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

test("business scenario lab exposes all five customer dispositions", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "业务场景演示" })).toBeVisible();

  await page.getByRole("button", { name: "重置合成 Demo" }).click();
  await page.getByRole("button", { name: "确认重置合成数据" }).click();
  await page.locator(".demo-scenario-panel__supporting summary").click();

  const scenarios = [
    ["customer_c", "signed-pod-conflict", "ESCALATE"],
    ["customer_a", "stalled-carrier-recovery", "WAIT"],
    ["customer_b", "stalled-within-sla", "WAIT"],
    ["customer_a", "signed-foreign-order", "ANSWER"],
    ["customer_c", "signed-pod-location-explanation", "CLARIFY"],
  ] as const;

  for (const [customerKey, scenarioId, disposition] of scenarios) {
    await page.locator(".customer-switcher select").selectOption(customerKey);
    const fillButton = page.getByTestId(`demo-scenario-${scenarioId}-fill`);
    await expect(fillButton).toBeEnabled();
    await fillButton.click();
    await expect(page.locator("#customer-message")).not.toHaveValue("");
    await page.getByRole("button", { name: "发送物流问题" }).click();
    await expect(page.locator(`[data-customer-disposition="${disposition}"]`)).toBeVisible({
      timeout: 150_000,
    });
  }

  await expect(page.getByText("Failure Lab · provider-free 故障路径")).toBeVisible();
});
