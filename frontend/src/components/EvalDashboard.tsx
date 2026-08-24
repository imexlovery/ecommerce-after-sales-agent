import { useEffect, useMemo, useState } from "react";

import { getLatestEval } from "../lib/api";
import { isRecord } from "../lib/presentation";
import type { EvalReport } from "../types";

const CONCLUSION_TEXT: Record<EvalReport["architecture_conclusion"], string> = {
  ADOPT_AGENT: "采用 Agent",
  KEEP_EXPERIMENTAL: "保留实验路径",
  PREFER_WORKFLOW: "优先强 Workflow",
};

function record(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function numberValue(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function optionalNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringValue(value: unknown, fallback = "—"): string {
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

function booleanValue(value: unknown): boolean {
  return value === true;
}

function formatNumber(value: unknown, suffix = ""): string {
  const observed = optionalNumber(value);
  return observed === null ? "未提供" : `${observed.toLocaleString("zh-CN")}${suffix}`;
}

function conclusionReason(
  report: EvalReport,
  comparison: Record<string, unknown>,
): string {
  if (report.dataset_partition === "development") {
    return "开发 Pilot 只记录行为与资源形态；只有 locked 三次运行报告才选择最终架构。";
  }
  if (report.architecture_conclusion === "ADOPT_AGENT") {
    return "Agent 已通过安全与质量门槛，并证明了冻结判据要求的动态路径优势。";
  }
  if (report.architecture_conclusion === "PREFER_WORKFLOW") {
    return "强 Workflow 在稳定性或资源轨迹上更有优势，当前证据不足以保留 Agent。";
  }
  return stringValue(
    comparison.reason,
    "安全门槛已单独判定，但当前证据尚未证明哪条路径应成为默认架构。",
  );
}

function Metric({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="eval-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {note && <small>{note}</small>}
    </div>
  );
}

function QualityCell({
  label,
  details,
  acceptanceApplicable,
}: {
  label: string;
  details: Record<string, unknown>;
  acceptanceApplicable: boolean;
}) {
  const passed = booleanValue(details.threshold_pass);
  return (
    <div className={`eval-quality-cell ${!acceptanceApplicable ? "is-neutral" : passed ? "is-pass" : "is-fail"}`}>
      <div>
        <span>{label}</span>
        <strong>{numberValue(details.stable_pass)}/8 {acceptanceApplicable ? "stable" : "完成"}</strong>
      </div>
      <em>
        {!acceptanceApplicable
          ? "开发 Pilot"
          : passed
            ? "达到 7/8 门槛"
            : "未达到 7/8 门槛"}
      </em>
      <small>
        flaky {numberValue(details.flaky)} · fail {numberValue(details.fail)}
      </small>
    </div>
  );
}

function ArchitectureRail({
  name,
  tone,
  investigation,
  e2e,
  trajectory,
}: {
  name: string;
  tone: "agent" | "workflow";
  investigation: Record<string, unknown>;
  e2e: Record<string, unknown>;
  trajectory: Record<string, unknown>;
}) {
  return (
    <div className={`eval-rail eval-rail--${tone}`}>
      <div className="eval-rail__name">
        <span>{tone === "agent" ? "动态调查" : "条件流程"}</span>
        <strong>{name}</strong>
      </div>
      <div className="eval-rail__station">
        <span>Investigation</span>
        <strong>{numberValue(investigation.stable_pass)}/8</strong>
      </div>
      <div className="eval-rail__line" aria-hidden="true"><i /><i /></div>
      <div className="eval-rail__station">
        <span>Full E2E</span>
        <strong>{numberValue(e2e.stable_pass)}/8</strong>
      </div>
      <div className="eval-rail__station eval-rail__station--quiet">
        <span>实际读取</span>
        <strong>{formatNumber(trajectory.actual_executions_total)}</strong>
      </div>
    </div>
  );
}

function LatencyRow({
  label,
  values,
}: {
  label: string;
  values: Record<string, unknown>;
}) {
  return (
    <div className="eval-latency-row">
      <strong>{label}</strong>
      <span>min {formatNumber(values.min, " ms")}</span>
      <span>median {formatNumber(values.median, " ms")}</span>
      <span>max {formatNumber(values.max, " ms")}</span>
    </div>
  );
}

export function EvalDashboard({ onBack }: { onBack: () => void }) {
  const [report, setReport] = useState<EvalReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void getLatestEval()
      .then((value) => {
        if (active) setReport(value);
      })
      .catch(() => {
        if (active) {
          setError("先运行版本化评测；这里不会用占位数字制造一份看起来完整的报告。");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const view = useMemo(() => {
    if (!report) return null;
    const safety = record(report.sections.safety);
    const taskQuality = record(report.sections.task_quality);
    const triage = record(taskQuality.triage);
    const investigation = record(taskQuality.investigation);
    const fullE2e = record(taskQuality.full_e2e);
    const trajectory = record(report.sections.tool_trajectory);
    const stability = record(report.sections.stability);
    const latency = record(report.sections.latency);
    const token = record(report.sections.token);
    const cost = record(report.sections.cost);
    const comparison = record(report.sections.agent_vs_workflow);
    const scenarioRows = Array.isArray(stability.scenarios) ? stability.scenarios : [];
    const unstable = scenarioRows
      .filter((item) => record(item).classification !== "stable_pass")
      .map(record);
    return {
      safety,
      triage,
      investigation,
      fullE2e,
      trajectory,
      latency,
      token,
      cost,
      comparison,
      unstable,
    };
  }, [report]);

  return (
    <main className="eval-page">
      <header className="eval-page__header">
        <button className="button button--quiet" type="button" onClick={onBack}>
          ← 返回客户对话
        </button>
        <div>
          <p className="eyebrow">LOCKED ACCEPTANCE RECORD</p>
          <h1>架构验收台</h1>
          <p>同一批场景、同一套安全边界；分开看质量、轨迹、稳定性与成本。</p>
        </div>
      </header>

      {loading && <div className="eval-empty" role="status">正在读取最新评测报告…</div>}
      {error && !loading && (
        <div className="eval-empty">
          <strong>尚无评测报告</strong>
          <p>{error}</p>
        </div>
      )}

      {report && view && (
        <>
          <section className="eval-command" aria-label="验收结论">
            <div className="eval-command__statement">
              <span>本次预注册结论</span>
              <h2>{CONCLUSION_TEXT[report.architecture_conclusion]}</h2>
              <p>{conclusionReason(report, view.comparison)}</p>
              <code>{report.evaluation_revision}</code>
            </div>
            <div className="eval-command__gates">
              <div className={report.safety_gate_pass ? "is-pass" : "is-fail"}>
                <span>Safety Gate</span>
                <strong>{report.safety_gate_pass ? "PASS" : "FAIL"}</strong>
                <small>{formatNumber(view.safety.violation_count)} 项违规</small>
              </div>
              <div className={report.dataset_partition !== "locked" ? "is-neutral" : report.acceptance_gate_pass ? "is-pass" : "is-fail"}>
                <span>Acceptance</span>
                <strong>{report.dataset_partition !== "locked" ? "NOT RUN" : report.acceptance_gate_pass ? "PASS" : "FAIL"}</strong>
                <small>{report.dataset_partition === "locked" ? "安全 + 质量 + 冻结预算" : "development pilot"}</small>
              </div>
              <div>
                <span>Raw Runs</span>
                <strong>{report.raw_run_count}</strong>
                <small>失败与超时同样计入</small>
              </div>
            </div>
          </section>

          <section className="eval-comparison" aria-labelledby="paired-title">
            <div className="eval-section-heading">
              <div>
                <span>PAIRED TRACK</span>
                <h2 id="paired-title">两种调查路径，同一终点</h2>
              </div>
              <p>{conclusionReason(report, view.comparison)}</p>
            </div>
            <ArchitectureRail
              name="Agent"
              tone="agent"
              investigation={record(view.investigation.agent)}
              e2e={record(view.fullE2e.agent)}
              trajectory={record(view.trajectory.agent)}
            />
            <ArchitectureRail
              name="Strong Workflow"
              tone="workflow"
              investigation={record(view.investigation.workflow)}
              e2e={record(view.fullE2e.workflow)}
              trajectory={record(view.trajectory.workflow)}
            />
            <div className="eval-comparison__ratios">
              <Metric
                label="稳定场景差"
                value={formatNumber(view.comparison.stable_pass_delta_agent_minus_workflow)}
                note="Agent − Workflow（Layer 2）"
              />
              <Metric
                label="读取调用比"
                value={formatNumber(view.comparison.agent_to_workflow_read_ratio, "×")}
                note="越低代表 Agent 查询更精简"
              />
              <Metric
                label="中位延迟比"
                value={formatNumber(view.comparison.agent_to_workflow_median_latency_ratio, "×")}
                note="同场景 Investigation"
              />
              <Metric
                label="动态路径优势"
                value={booleanValue(view.comparison.registered_dynamic_path_advantage) ? "已证明" : "未证明"}
                note="按冻结前判据得出"
              />
            </div>
          </section>

          <div className="eval-grid">
            <section className="eval-card eval-card--safety">
              <div className="eval-section-heading">
                <div><span>HARD GATE</span><h2>Safety</h2></div>
                <strong className={report.safety_gate_pass ? "is-pass" : "is-fail"}>
                  {report.safety_gate_pass ? "零违规" : `${formatNumber(view.safety.violation_count)} 项违规`}
                </strong>
              </div>
              <p>安全失败不会被更高准确率、更低延迟或更多成功场景抵消。</p>
              {Array.isArray(view.safety.violations) && view.safety.violations.length > 0 ? (
                <div className="eval-failures">
                  {view.safety.violations.map((item, index) => {
                    const violation = record(item);
                    return <code key={`${stringValue(violation.eval_run_id)}-${index}`}>{stringValue(violation.scenario_id)} · {stringValue(violation.architecture)}</code>;
                  })}
                </div>
              ) : (
                <div className="eval-card__confirmation">所有 {report.raw_run_count} 次运行均通过适用的硬安全断言。</div>
              )}
            </section>

            <section className="eval-card">
              <div className="eval-section-heading">
                <div><span>LAYER 1</span><h2>Triage</h2></div>
                <strong className={report.dataset_partition === "locked" ? booleanValue(view.triage.threshold_pass) ? "is-pass" : "is-fail" : "is-neutral"}>
                  {report.dataset_partition === "locked" ? booleanValue(view.triage.threshold_pass) ? "PASS" : "FAIL" : "PILOT"}
                </strong>
              </div>
              <div className="eval-metric-grid eval-metric-grid--triage">
                <Metric label="Schema" value={`${numberValue(view.triage.schema_valid_stable)}/${report.dataset_partition === "locked" ? numberValue(view.triage.schema_valid_required) : numberValue(view.triage.scenario_count)}`} />
                <Metric label="粗粒度路由" value={`${numberValue(view.triage.coarse_route_stable)}/${numberValue(view.triage.scenario_count)}`} note={report.dataset_partition === "locked" ? `门槛 ≥ ${numberValue(view.triage.coarse_route_required)}` : "development coverage"} />
                <Metric label="细粒度意图" value={`${numberValue(view.triage.fine_intent_stable)}/${numberValue(view.triage.scenario_count)}`} note={report.dataset_partition === "locked" ? `门槛 ≥ ${numberValue(view.triage.fine_intent_required)}` : "development coverage"} />
                <Metric label="订单号提取" value={`${numberValue(view.triage.order_id_stable)}/${numberValue(view.triage.order_id_applicable)}`} />
              </div>
            </section>

            <section className="eval-card eval-card--wide">
              <div className="eval-section-heading">
                <div><span>LAYERS 2 + 3</span><h2>Task Quality</h2></div>
                <p>{report.dataset_partition === "locked" ? "每格至少 7/8 个场景三次完整通过。" : "开发 Pilot 每个场景运行一次；locked 阶段才判定三次稳定性。"}</p>
              </div>
              <div className="eval-quality-grid">
                <QualityCell label="Agent · Investigation" details={record(view.investigation.agent)} acceptanceApplicable={report.dataset_partition === "locked"} />
                <QualityCell label="Workflow · Investigation" details={record(view.investigation.workflow)} acceptanceApplicable={report.dataset_partition === "locked"} />
                <QualityCell label="Agent · Full E2E" details={record(view.fullE2e.agent)} acceptanceApplicable={report.dataset_partition === "locked"} />
                <QualityCell label="Workflow · Full E2E" details={record(view.fullE2e.workflow)} acceptanceApplicable={report.dataset_partition === "locked"} />
              </div>
            </section>

            <section className="eval-card">
              <div className="eval-section-heading">
                <div><span>TRAJECTORY</span><h2>Tool path</h2></div>
              </div>
              {["agent", "workflow"].map((architecture) => {
                const trajectory = record(view.trajectory[architecture]);
                return (
                  <div className="eval-tool-row" key={architecture}>
                    <strong>{architecture === "agent" ? "Agent" : "Workflow"}</strong>
                    <span>{formatNumber(trajectory.actual_executions_total)} reads</span>
                    <span>{formatNumber(trajectory.cache_hits_total)} cache</span>
                    <span>{formatNumber(trajectory.blocked_calls_total)} blocked</span>
                  </div>
                );
              })}
            </section>

            <section className="eval-card">
              <div className="eval-section-heading">
                <div><span>WALL CLOCK</span><h2>Latency</h2></div>
                <strong className={report.dataset_partition === "locked" ? booleanValue(record(view.latency.budget).budget_pass) ? "is-pass" : "is-fail" : "is-neutral"}>
                  {report.dataset_partition === "locked" ? booleanValue(record(view.latency.budget).budget_pass) ? "预算内" : "超出预算" : "PILOT"}
                </strong>
              </div>
              <LatencyRow label="Agent · Investigation" values={record(record(view.latency.investigation).agent)} />
              <LatencyRow label="Workflow · Investigation" values={record(record(view.latency.investigation).workflow)} />
              <LatencyRow label="Agent · Full E2E" values={record(record(view.latency.full_e2e).agent)} />
              <LatencyRow label="Workflow · Full E2E" values={record(record(view.latency.full_e2e).workflow)} />
              {report.dataset_partition === "locked" && (
                <div className="eval-card__confirmation">
                  单次冻结上限 {formatNumber(record(record(view.latency.budget).limits).latency_ms, " ms")} · {formatNumber(record(view.latency.budget).violation_count)} 次超限
                </div>
              )}
            </section>

            <section className="eval-card eval-card--wide">
              <div className="eval-section-heading">
                <div><span>REPEATED RUNS</span><h2>Stability & raw failures</h2></div>
                <strong>{view.unstable.length === 0 ? "全部稳定" : `${view.unstable.length} 个不稳定单元`}</strong>
              </div>
              {view.unstable.length === 0 ? (
                <div className="eval-card__confirmation">每个场景 × 架构 × 层级均完成{report.dataset_partition === "locked" ? "三次注册运行" : "开发 Pilot 运行"}并通过完整要求。</div>
              ) : (
                <div className="eval-instability-list">
                  {view.unstable.map((item) => (
                    <details key={`${stringValue(item.scenario_id)}-${stringValue(item.layer)}-${stringValue(item.architecture)}`}>
                      <summary>
                        <code>{stringValue(item.scenario_id)}</code>
                        <span>{stringValue(item.layer)} · {stringValue(item.architecture)}</span>
                        <strong>{stringValue(item.classification)}</strong>
                      </summary>
                      <pre>{JSON.stringify(item.failed_runs ?? [], null, 2)}</pre>
                    </details>
                  ))}
                </div>
              )}
            </section>

            <section className="eval-card">
              <div className="eval-section-heading">
                <div><span>MEASUREMENT COVERAGE</span><h2>Token & cost</h2></div>
              </div>
              <div className="eval-usage-row">
                <span>Token</span>
                <strong>{numberValue(view.token.coverage_runs)}/{numberValue(view.token.total_runs)} runs</strong>
                <small>
                  {booleanValue(view.token.provider_usage_available) ? "provider metadata" : "provider 未返回可用数据"}
                  {report.dataset_partition === "locked" && ` · total 上限 ${formatNumber(record(record(view.token.budget).limits).total_tokens)}`}
                </small>
              </div>
              <div className="eval-usage-row">
                <span>Cost</span>
                <strong>{numberValue(view.cost.coverage_runs)}/{numberValue(view.cost.total_runs)} runs</strong>
                <small>
                  {booleanValue(view.cost.price_basis_available) ? "frozen price basis" : "没有冻结价格基准，不估算"}
                  {report.dataset_partition === "locked" && ` · 上限 ${formatNumber(record(record(view.cost.budget).limits).cost_usd, " USD")}`}
                </small>
              </div>
            </section>

            <section className="eval-card">
              <div className="eval-section-heading">
                <div><span>REPRODUCIBILITY</span><h2>Version lock</h2></div>
              </div>
              <dl className="eval-version-list">
                {Object.entries(report.versions).map(([key, value]) => (
                  <div key={key}><dt>{key}</dt><dd><code>{value}</code></dd></div>
                ))}
              </dl>
            </section>
          </div>
        </>
      )}
    </main>
  );
}
