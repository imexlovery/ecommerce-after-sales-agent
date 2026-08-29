import { Fragment, useEffect, useMemo, useRef } from "react";
import type { ReactElement } from "react";

import type { ConversationTimelineItem } from "../lib/conversationTimeline";
import { formatClock } from "../lib/presentation";
import type {
  ActionProposalView,
  CustomerDisposition,
  DemoCatalogView,
  DemoScenarioView,
  SyntheticCustomerView,
} from "../types";
import { ProposalCard } from "./ProposalCard";

const DISPOSITION_LABELS: Record<CustomerDisposition, string> = {
  ANSWER: "已说明",
  WAIT: "等待更新",
  CLARIFY: "需要补充",
  INVESTIGATE: "已进入物流核查",
  ESCALATE: "转人工支持",
};

function quickExampleLabel(scenario: DemoScenarioView): string {
  return scenario.note.split(/[，；。]/, 1)[0] || scenario.order_id;
}

interface ConversationPanelProps {
  timeline: ConversationTimelineItem[];
  composer: string;
  setComposer: (value: string) => void;
  canSend: boolean;
  error: string | null;
  proposalBusyId: string | null;
  proposalBusyOperation: "confirm" | "decline" | null;
  retryBusyCaseId: string | null;
  syntheticCustomer: SyntheticCustomerView | null;
  demoCatalog: DemoCatalogView | null;
  customerDisposition: CustomerDisposition | null;
  onSend: () => void;
  onConfirm: (proposal: ActionProposalView) => void;
  onDecline: (proposal: ActionProposalView) => void;
  onRetry: (caseId: string) => void;
}

function caseNumbers(timeline: ConversationTimelineItem[]): Map<string, number> {
  const numbers = new Map<string, number>();
  for (const item of timeline) {
    if (item.caseId && !numbers.has(item.caseId)) numbers.set(item.caseId, numbers.size + 1);
  }
  return numbers;
}

function caseHeading(number: number): ReactElement {
  return (
    <div className="conversation-case-marker" aria-label={"第 " + number + " 次物流核查"}>
      <span>CASE {String(number).padStart(2, "0")}</span>
      <i aria-hidden="true" />
      <small>本次物流核查</small>
    </div>
  );
}

export function ConversationPanel({
  timeline,
  composer,
  setComposer,
  canSend,
  error,
  proposalBusyId,
  proposalBusyOperation,
  retryBusyCaseId,
  syntheticCustomer,
  demoCatalog,
  customerDisposition,
  onSend,
  onConfirm,
  onDecline,
  onRetry,
}: ConversationPanelProps) {
  const timelineRef = useRef<HTMLDivElement>(null);
  const numbers = useMemo(() => caseNumbers(timeline), [timeline]);
  const quickExamples = useMemo(
    () => {
      const customerScenarios =
        demoCatalog?.scenarios.filter(
          (scenario) =>
            scenario.customer_key === syntheticCustomer?.customer_key &&
            scenario.customer_message !== null,
        ) ?? [];
      const preferred = [
        customerScenarios.find((scenario) => scenario.issue_type === "signed_not_received"),
        customerScenarios.find((scenario) => scenario.issue_type === "stalled_tracking"),
        ...customerScenarios,
      ].filter((scenario): scenario is DemoScenarioView => scenario !== undefined);
      return preferred
        .filter(
          (scenario, index) =>
            preferred.findIndex((candidate) => candidate.order_id === scenario.order_id) === index,
        )
        .slice(0, 2);
    },
    [demoCatalog, syntheticCustomer?.customer_key],
  );

  useEffect(() => {
    timelineRef.current?.scrollTo({ top: timelineRef.current.scrollHeight, behavior: "smooth" });
  }, [timeline]);

  let previousCaseId: string | null = null;

  return (
    <section className="conversation-panel" aria-labelledby="conversation-heading">
      <div className="conversation-panel__heading">
        <div>
          <p className="eyebrow">CUSTOMER SERVICE</p>
          <h1 id="conversation-heading">物流客服</h1>
        </div>
        <span className="synthetic-stamp">仅使用合成订单</span>
      </div>

      <div className="conversation-body">
        {customerDisposition && (
          <div
            className={"business-disposition business-disposition--" + customerDisposition.toLowerCase()}
            data-customer-disposition={customerDisposition}
            aria-label={"customer_disposition=" + customerDisposition}
          >
            <span className="eyebrow">本轮客户结果</span>
            <strong>{DISPOSITION_LABELS[customerDisposition]}</strong>
            <code>{customerDisposition}</code>
          </div>
        )}

        <div className="conversation-timeline" ref={timelineRef}>
        {timeline.length === 0 && (
          <div className="conversation-empty">
            <div className="parcel-mark" aria-hidden="true">
              <span className="parcel-mark__box">□</span>
              <span className="parcel-mark__route" />
            </div>
            <p className="eyebrow">HOW CAN I HELP?</p>
            <h2>告诉我订单号和遇到的情况，我来帮你查。</h2>
            <p>
              目前可以处理“显示签收但没收到”和“物流长时间没更新”。系统只使用虚拟订单，任何后续处理都会先征得你的确认。
            </p>
          </div>
        )}

        {timeline.map((item) => {
          const showCaseHeading = item.caseId !== null && item.caseId !== previousCaseId;
          previousCaseId = item.caseId;
          const number = item.caseId ? numbers.get(item.caseId) : undefined;
          return (
            <Fragment key={item.id}>
              {showCaseHeading && number && caseHeading(number)}
              {item.kind === "message" && (
                <article
                  className={[
                    "message",
                    "message--" + item.role,
                    item.failed ? "message--failed" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                >
                  <div className="message__meta">
                    <span>{item.role === "customer" ? "你 · 虚拟客户" : "物流客服"}</span>
                    <time dateTime={item.timestamp}>{formatClock(item.timestamp)}</time>
                  </div>
                  <p>{item.content}</p>
                </article>
              )}

              {item.kind === "progress" && (
                <div
                  className={[
                    "investigation-progress",
                    item.active ? "investigation-progress--active" : "investigation-progress--complete",
                  ].join(" ")}
                  role="status"
                >
                  <span className="investigation-progress__scanner" aria-hidden="true" />
                  <div>
                    <strong>{item.active ? "正在查看订单和物流记录" : "已完成本轮订单与物流核对"}</strong>
                    <span>
                      {item.active
                        ? "请稍等，我会把查到的情况和下一步说清楚。"
                        : "本次核对过程已保留在这条处理记录中。"}
                    </span>
                  </div>
                </div>
              )}

              {item.kind === "proposal" && (
                <ProposalCard
                  proposal={item.proposal}
                  busy={
                    proposalBusyId === item.proposal.proposalId ? proposalBusyOperation : null
                  }
                  onConfirm={() => onConfirm(item.proposal)}
                  onDecline={() => onDecline(item.proposal)}
                />
              )}

              {item.kind === "retry" && (
                <section className="case-retry" role="status">
                  <span aria-hidden="true">↻</span>
                  <div>
                    <p className="eyebrow">核查恢复</p>
                    <h3>
                      {item.reason === "action"
                        ? "处理请求暂时未能提交"
                        : "关键物流信息暂时不可用"}
                    </h3>
                    <p>
                      {item.reason === "action"
                        ? "系统会保留原处理编号和幂等键；重试不会创建新的处理请求。"
                        : "系统没有把“暂时无法取得”当作“没有记录”，因此尚未创建任何工单。"}
                    </p>
                    {item.enabled && (
                      <button
                        className="text-button"
                        type="button"
                        onClick={() => onRetry(item.caseId)}
                        disabled={retryBusyCaseId === item.caseId}
                      >
                        {retryBusyCaseId === item.caseId
                          ? "正在安全重试…"
                          : item.reason === "action"
                            ? "安全重试原处理请求"
                            : "重新查询"}
                      </button>
                    )}
                  </div>
                </section>
              )}

              {item.kind === "result" && (
                <section className={"action-result action-result--" + item.result.kind} role="status">
                  <span className="action-result__icon" aria-hidden="true">
                    {item.result.kind === "verified" ? "✓" : item.result.kind === "uncertain" ? "?" : "!"}
                  </span>
                  <div>
                    <p className="eyebrow">处理进度</p>
                    <h3>{item.result.title}</h3>
                    <p>{item.result.detail}</p>
                    {item.result.ticketId && (
                      <p className="action-result__ticket mono">处理编号 {item.result.ticketId}</p>
                    )}
                    {item.result.targetShipmentId && (
                      <p className="action-result__ticket mono">
                        目标包裹 {item.result.targetShipmentId}
                        {item.result.readBackVerified ? " · write/read-back verified" : ""}
                      </p>
                    )}
                  </div>
                </section>
              )}
            </Fragment>
          );
        })}
        </div>
      </div>

      <div className="conversation-controls">
        {error && (
          <div className="error-banner" role="alert">
            <span aria-hidden="true">!</span>
            <p>{error}</p>
          </div>
        )}

        <div className="example-fillers" aria-label="当前身份快捷示例">
          <span>当前身份快捷示例</span>
          {quickExamples.map((example) => (
            <button
              type="button"
              key={example.scenario_id}
              onClick={() => setComposer(example.customer_message ?? "")}
              disabled={!canSend}
            >
              <strong>{quickExampleLabel(example)}</strong>
              <small>合成订单 {example.order_id}</small>
            </button>
          ))}
        </div>

        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault();
            onSend();
          }}
        >
          <label className="visually-hidden" htmlFor="customer-message">
            描述物流问题
          </label>
          <textarea
            id="customer-message"
            value={composer}
            maxLength={1200}
            disabled={!canSend}
            placeholder={
              quickExamples[0]
                ? `例如：描述合成订单 ${quickExamples[0].order_id} 的物流问题…`
                : "描述一个合成订单的物流问题…"
            }
            onChange={(event) => setComposer(event.target.value)}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !(event.nativeEvent as KeyboardEvent).isComposing
              ) {
                event.preventDefault();
                onSend();
              }
            }}
          />
          <div className="composer__footer">
            <p>请勿输入真实姓名、电话、地址、支付信息或真实订单。Shift+Enter 换行。</p>
            <span className="composer__count mono">{composer.length}/1200</span>
            <button
              className="send-button"
              type="submit"
              disabled={!canSend || composer.trim().length === 0}
              aria-label="发送物流问题"
            >
              <span>发送</span>
              <span aria-hidden="true">↗</span>
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}
