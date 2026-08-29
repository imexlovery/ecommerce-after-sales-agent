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
  onFill: (message: string) => void;
  main: boolean;
}

function ScenarioCard({ scenario, canFill, onFill, main }: ScenarioCardProps) {
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
          aria-label={`填入 ${scenario.scenario_id}`}
          onClick={() => onFill(scenario.customer_message ?? "")}
          disabled={!canFill}
        >
          <span>填入消息</span>
          <small>先切换为 {customerLabel(scenario.customer_key)}</small>
        </button>
      )}
    </article>
  );
}

interface DemoScenarioPanelProps {
  scenarios: DemoScenarioView[];
  faultProfiles: DemoFaultProfileView[];
  policyClauseCount: number;
  canFill: boolean;
  onFill: (message: string) => void;
}

export function DemoScenarioPanel({
  scenarios,
  faultProfiles,
  policyClauseCount,
  canFill,
  onFill,
}: DemoScenarioPanelProps) {
  const mainScenarios = MAIN_SCENARIO_IDS.map((scenarioId) =>
    scenarios.find((scenario) => scenario.scenario_id === scenarioId),
  ).filter((scenario): scenario is DemoScenarioView => scenario !== undefined);
  const supportingScenarios = scenarios.filter(
    (scenario) => !MAIN_SCENARIO_IDS.includes(scenario.scenario_id as (typeof MAIN_SCENARIO_IDS)[number]),
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
        场景按钮只会把合成客户消息填入输入框，不会代替自由文本 triage，也不会自动选择路线。需要演示某个身份时，请先切换顶部虚拟客户。
      </p>

      <div className="demo-scenario-panel__main">
        {mainScenarios.map((scenario) => (
          <ScenarioCard
            key={scenario.scenario_id}
            scenario={scenario}
            canFill={canFill}
            onFill={onFill}
            main
          />
        ))}
      </div>

      {supportingScenarios.length > 0 && (
        <details className="demo-scenario-panel__supporting">
          <summary>
            <span>展开组合矩阵：五类客户结果</span>
            <small>ANSWER · WAIT · CLARIFY · INVESTIGATE · ESCALATE</small>
          </summary>
          <div className="demo-scenario-panel__supporting-grid">
            {supportingScenarios.map((scenario) => (
              <ScenarioCard
                key={scenario.scenario_id}
                scenario={scenario}
                canFill={canFill}
                onFill={onFill}
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
