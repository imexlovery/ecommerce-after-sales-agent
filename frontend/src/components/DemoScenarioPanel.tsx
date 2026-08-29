import type { DemoFaultProfileView, DemoScenarioView } from "../types";

const MAIN_SCENARIO_IDS = [
  "partial-packages-target-c",
  "signed-pod-conflict",
  "stalled-carrier-recovery",
] as const;

const CUSTOMER_LABELS: Record<string, string> = {
  customer_a: "虚拟客户 A",
  customer_b: "虚拟客户 B",
  customer_c: "虚拟客户 C",
  customer_d: "虚拟客户 D",
  customer_r: "虚拟客户 R",
};

const ISSUE_LABELS: Record<string, string> = {
  signed_not_received: "显示签收但未收到",
  stalled_tracking: "物流停滞",
};

const DISPOSITION_LABELS: Record<DemoScenarioView["expected_disposition"], string> = {
  ANSWER: "已说明",
  WAIT: "等待更新",
  CLARIFY: "需要补充",
  INVESTIGATE: "进入物流核查",
  ESCALATE: "转人工支持",
};

function customerLabel(customerKey: string): string {
  return CUSTOMER_LABELS[customerKey] ?? customerKey;
}

function envProfileName(profileId: string): string {
  return profileId.replaceAll("-", "_");
}

function scenarioTarget(scenario: DemoScenarioView): string {
  if (scenario.target_shipment_id) return `目标包裹 ${scenario.target_shipment_id}`;
  return "整单范围";
}

interface ScenarioCardProps {
  scenario: DemoScenarioView;
  canFill: boolean;
  currentCustomerKey: string;
  onSelect: (scenario: DemoScenarioView) => void;
  main: boolean;
}

function ScenarioCard({
  scenario,
  canFill,
  currentCustomerKey,
  onSelect,
  main,
}: ScenarioCardProps) {
  const usesCurrentCustomer = scenario.customer_key === currentCustomerKey;
  const actionLabel = usesCurrentCustomer
    ? "填入消息"
    : `切换为${customerLabel(scenario.customer_key)} 并填入`;

  return (
    <article
      className={`demo-scenario-card ${main ? "demo-scenario-card--main" : ""}`}
      data-testid={`demo-scenario-${scenario.scenario_id}`}
    >
      <div className="demo-scenario-card__meta">
        <code>{scenario.scenario_id}</code>
        <span className={`disposition-chip disposition-chip--${scenario.expected_disposition.toLowerCase()}`}>
          {DISPOSITION_LABELS[scenario.expected_disposition]}
        </span>
      </div>
      <h3>{scenario.note}</h3>
      <p className="demo-scenario-card__facts">
        <span>{customerLabel(scenario.customer_key)}</span>
        <span className="mono">{scenario.order_id}</span>
        <span>{ISSUE_LABELS[scenario.issue_type] ?? scenario.issue_type}</span>
        <span className="mono">{scenarioTarget(scenario)}</span>
      </p>
      {scenario.customer_message && (
        <button
          className="demo-scenario-card__fill"
          type="button"
          data-testid={`demo-scenario-${scenario.scenario_id}-fill`}
          aria-label={actionLabel}
          onClick={() => onSelect(scenario)}
          disabled={!canFill}
        >
          <span>{actionLabel}</span>
          <small>{usesCurrentCustomer ? "当前身份" : "新建该身份的合成会话"}</small>
        </button>
      )}
    </article>
  );
}

interface DemoScenarioPanelProps {
  scenarios: DemoScenarioView[];
  faultProfiles: DemoFaultProfileView[];
  policyClauseCount: number;
  currentCustomerKey: string;
  canFill: boolean;
  onSelect: (scenario: DemoScenarioView) => void;
}

export function DemoScenarioPanel({
  scenarios,
  faultProfiles,
  policyClauseCount,
  currentCustomerKey,
  canFill,
  onSelect,
}: DemoScenarioPanelProps) {
  const currentScenarios = scenarios.filter(
    (scenario) => scenario.customer_key === currentCustomerKey,
  );
  const otherScenarios = scenarios.filter(
    (scenario) => scenario.customer_key !== currentCustomerKey,
  );

  return (
    <section className="demo-scenario-panel" aria-label="业务场景与 Failure Lab">
      <div className="demo-scenario-panel__heading">
        <div>
          <p className="eyebrow">BUSINESS SCENARIO LAB</p>
          <h2>业务场景演示</h2>
        </div>
        <span className="demo-scenario-panel__count mono">
          {scenarios.length} scenarios · {policyClauseCount} policy clauses
        </span>
      </div>
      <p className="demo-scenario-panel__intro">
        默认只显示当前虚拟客户可运行的故事。跨身份按钮会明确新建对应客户的合成会话并填入消息，但仍不会替代自由文本 triage 或预选业务路线。
      </p>

      <div className="demo-scenario-panel__current-label">
        <strong>{customerLabel(currentCustomerKey)} 可演示场景</strong>
        <span>{currentScenarios.length} 条</span>
      </div>
      <div className="demo-scenario-panel__main">
        {currentScenarios.map((scenario) => (
          <ScenarioCard
            key={scenario.scenario_id}
            scenario={scenario}
            canFill={canFill}
            currentCustomerKey={currentCustomerKey}
            onSelect={onSelect}
            main={MAIN_SCENARIO_IDS.includes(
              scenario.scenario_id as (typeof MAIN_SCENARIO_IDS)[number]
            )}
          />
        ))}
      </div>

      {otherScenarios.length > 0 && (
        <details className="demo-scenario-panel__supporting">
          <summary>
            <span>查看全部场景矩阵</span>
            <small>{otherScenarios.length} 条其他身份场景</small>
          </summary>
          <div className="demo-scenario-panel__supporting-grid">
            {otherScenarios.map((scenario) => (
              <ScenarioCard
                key={scenario.scenario_id}
                scenario={scenario}
                canFill={canFill}
                currentCustomerKey={currentCustomerKey}
                onSelect={onSelect}
                main={false}
              />
            ))}
          </div>
        </details>
      )}

      <details className="failure-lab">
        <summary>
          <span>Failure Lab · provider-free 故障路径</span>
          <small>只在启动本地 API 时选择 profile</small>
        </summary>
        <div className="failure-lab__grid">
          {faultProfiles.map((profile) => (
            <article className="failure-lab__card" key={profile.fault_profile_id}>
              <div>
                <code>{profile.fault_profile_id}</code>
                <span>{profile.mode}</span>
              </div>
              <h3>{profile.description}</h3>
              <p>{profile.tool_name}</p>
              <code className="failure-lab__env">SYNTHETIC_FAULT_PROFILE={envProfileName(profile.fault_profile_id)}</code>
            </article>
          ))}
        </div>
        <p className="failure-lab__note">
          Failure Lab 使用本地合成故障，默认主故事不会被污染；所有 profile 均保持 Provider calls / Model calls = 0 / 0。
        </p>
      </details>
    </section>
  );
}
