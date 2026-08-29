import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ConversationPanel } from "./components/ConversationPanel";
import { DemoScenarioPanel } from "./components/DemoScenarioPanel";
import { EvalDashboard } from "./components/EvalDashboard";
import { RouteStrip } from "./components/RouteStrip";
import { TracePanel } from "./components/TracePanel";
import { useConversationEvents } from "./hooks/useConversationEvents";
import {
  confirmProposal,
  createConversation,
  declineProposal,
  getConversation,
  getDemoCatalog,
  resetSyntheticDemo,
  retryCase,
  sendCustomerMessage,
} from "./lib/api";
import {
  compactId,
} from "./lib/presentation";
import {
  buildConversationTimeline,
  latestCustomerDisposition,
  latestCurrentProposal,
  latestCurrentResult,
  scopeEventsToCase,
} from "./lib/conversationTimeline";
import type {
  ActionProposalView,
  CustomerDisposition,
  DemoCatalogView,
  DemoScenarioView,
  EventEnvelope,
  LlmMode,
  OrderSummaryView,
  SyntheticCustomerView,
} from "./types";
import { ApiClientError } from "./types";

const CUSTOMER_OPTIONS = [
  { key: "customer_a", label: "虚拟客户 A", scope: "合成订单范围" },
  { key: "customer_b", label: "虚拟客户 B", scope: "合成订单范围" },
  { key: "customer_c", label: "虚拟客户 C", scope: "合成订单范围" },
  { key: "customer_d", label: "虚拟客户 D", scope: "合成订单范围" },
  { key: "customer_r", label: "虚拟客户 R", scope: "合成订单范围" },
] as const;

const SYNTHETIC_SESSION_KEY = "after-sales-agent.synthetic-session.v1";

function orderStatusLabel(status: string): string {
  if (status === "delivered") return "已送达";
  if (status === "shipped") return "运输中";
  if (status === "processing") return "处理中";
  if (status === "cancelled") return "已取消";
  return status;
}

function shipmentStatusLabel(status: string): string {
  if (status === "delivered") return "已送达";
  if (status === "in_transit") return "运输中";
  if (status === "stalled") return "已停滞";
  if (status === "processing") return "处理中";
  return status;
}

function shipmentUpdateLabel(timestamp: string | null): string {
  if (!timestamp) return "无最新时间";
  const date = new Date(timestamp);
  if (Number.isNaN(date.valueOf())) return "时间不可用";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function loadPersistedConversationId(): string | null {
  try {
    const raw = window.localStorage.getItem(SYNTHETIC_SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { conversationId?: unknown };
    return typeof parsed.conversationId === "string" ? parsed.conversationId : null;
  } catch {
    return null;
  }
}

function persistConversationId(conversationId: string): void {
  try {
    window.localStorage.setItem(SYNTHETIC_SESSION_KEY, JSON.stringify({ conversationId }));
  } catch {
    // The synthetic demo remains usable when browser storage is unavailable.
  }
}

function forgetPersistedConversation(): void {
  try {
    window.localStorage.removeItem(SYNTHETIC_SESSION_KEY);
  } catch {
    // Nothing else to clean up when browser storage is unavailable.
  }
}

function safeError(error: unknown): string {
  if (error instanceof ApiClientError) {
    return error.message;
  }
  return "暂时无法连接本地服务。请确认 API 已启动后重试。";
}

export default function App() {
  const [customerKey, setCustomerKey] = useState("customer_a");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [llmMode, setLlmMode] = useState<LlmMode | null>(null);
  const [fixtureVersion, setFixtureVersion] = useState<string | null>(null);
  const [syntheticCustomer, setSyntheticCustomer] = useState<SyntheticCustomerView | null>(null);
  const [accessibleOrders, setAccessibleOrders] = useState<OrderSummaryView[]>([]);
  const [demoCatalog, setDemoCatalog] = useState<DemoCatalogView | null>(null);
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [composer, setComposer] = useState("");
  const [activeCaseId, setActiveCaseId] = useState<string | null>(null);
  const [activeRun, setActiveRun] = useState(false);
  const [proposalBusyId, setProposalBusyId] = useState<string | null>(null);
  const [proposalBusyOperation, setProposalBusyOperation] = useState<"confirm" | "decline" | null>(
    null,
  );
  const [retryBusyCaseId, setRetryBusyCaseId] = useState<string | null>(null);
  const [booting, setBooting] = useState(true);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [traceDrawerOpen, setTraceDrawerOpen] = useState(false);
  const [scenarioLabOpen, setScenarioLabOpen] = useState(false);
  const [view, setView] = useState<"conversation" | "eval">(
    window.location.hash === "#eval" ? "eval" : "conversation",
  );
  const resetDialogRef = useRef<HTMLDialogElement>(null);
  const traceCloseButtonRef = useRef<HTMLButtonElement>(null);
  const traceTriggerRef = useRef<HTMLButtonElement>(null);
  const scenarioCloseButtonRef = useRef<HTMLButtonElement>(null);
  const requestGenerationRef = useRef(0);
  const bootstrappedRef = useRef(false);

  const clearConversationState = useCallback(() => {
    setEvents([]);
    setComposer("");
    setFixtureVersion(null);
    setSyntheticCustomer(null);
    setAccessibleOrders([]);
    setActiveCaseId(null);
    setActiveRun(false);
    setProposalBusyId(null);
    setProposalBusyOperation(null);
    setRetryBusyCaseId(null);
    setError(null);
  }, []);

  const startConversation = useCallback(
    async (fixtureCustomerKey: string) => {
      const generation = ++requestGenerationRef.current;
      setBooting(true);
      setConversationId(null);
      setLlmMode(null);
      clearConversationState();
      try {
        const created = await createConversation(fixtureCustomerKey);
        if (generation !== requestGenerationRef.current) return;
        setConversationId(created.conversation_id);
        setLlmMode(created.llm_mode);
        setFixtureVersion(created.fixture_version);
        setSyntheticCustomer(created.synthetic_customer);
        setAccessibleOrders(created.accessible_orders);
        persistConversationId(created.conversation_id);
      } catch (caught) {
        if (generation === requestGenerationRef.current) setError(safeError(caught));
      } finally {
        if (generation === requestGenerationRef.current) setBooting(false);
      }
    },
    [clearConversationState],
  );

  const restoreOrStartConversation = useCallback(async () => {
    const persistedConversationId = loadPersistedConversationId();
    if (!persistedConversationId) {
      await startConversation("customer_a");
      return;
    }

    const generation = ++requestGenerationRef.current;
    setBooting(true);
    setConversationId(null);
    setLlmMode(null);
    clearConversationState();
    try {
      const restored = await getConversation(persistedConversationId);
      if (generation !== requestGenerationRef.current) return;

      setCustomerKey(restored.fixture_customer_key);
      setLlmMode(restored.llm_mode);
      setFixtureVersion(restored.fixture_version);
      setSyntheticCustomer(restored.synthetic_customer);
      setAccessibleOrders(restored.accessible_orders);
      setActiveCaseId(
        restored.active_case_id ?? restored.cases.at(-1)?.case_id ?? null,
      );
      setConversationId(restored.conversation_id);
    } catch {
      if (generation !== requestGenerationRef.current) return;
      forgetPersistedConversation();
      await startConversation("customer_a");
      return;
    } finally {
      if (generation === requestGenerationRef.current) setBooting(false);
    }
  }, [clearConversationState, startConversation]);

  useEffect(() => {
    if (bootstrappedRef.current) return;
    bootstrappedRef.current = true;
    void restoreOrStartConversation();
  }, [restoreOrStartConversation]);

  useEffect(() => {
    let mounted = true;
    void getDemoCatalog()
      .then((catalog) => {
        if (mounted) setDemoCatalog(catalog);
      })
      .catch(() => {
        // The conversation remains usable if the optional scenario catalog is unavailable.
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    const onHashChange = () => setView(window.location.hash === "#eval" ? "eval" : "conversation");
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    if (!traceDrawerOpen) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    traceCloseButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setTraceDrawerOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    document.body.classList.add("drawer-open");
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.classList.remove("drawer-open");
      previouslyFocused?.focus();
    };
  }, [traceDrawerOpen]);

  useEffect(() => {
    if (!scenarioLabOpen) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    scenarioCloseButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setScenarioLabOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    document.body.classList.add("scenario-lab-open");
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.classList.remove("scenario-lab-open");
      previouslyFocused?.focus();
    };
  }, [scenarioLabOpen]);

  const handleEvent = useCallback((event: EventEnvelope) => {
    setEvents((current) => {
      if (current.some((item) => item.event_id === event.event_id)) return current;
      return [...current, event].sort((left, right) => left.sequence - right.sequence);
    });

    if (event.event_type === "case_created" && event.case_id) {
      setActiveCaseId(event.case_id);
    }
    if (event.event_type === "case_closed" && event.case_id) {
      setActiveCaseId((current) => (current === event.case_id ? null : current));
      // `case_closed` is a durable terminal fact.  It is also a safe fallback
      // for an SSE reconnect that observes the terminal Case event after its
      // preceding Run terminal event, so a completed Case never leaves the
      // next free-text request artificially disabled.
      setActiveRun(false);
      setProposalBusyId(null);
      setProposalBusyOperation(null);
    }
    if (event.event_type === "run_started") setActiveRun(true);
    if (event.event_type === "run_succeeded") setActiveRun(false);
    if (event.event_type === "run_failed") {
      setActiveRun(false);
      setError(event.summary || "本次调查没有完成，可以在安全状态下显式重试。 ");
    }
    if (
      new Set([
        "proposal_confirmed",
        "proposal_declined",
        "proposal_superseded",
        "proposal_expired",
        "proposal_invalidated",
      ]).has(event.event_type)
    ) {
      setProposalBusyId(null);
      setProposalBusyOperation(null);
    }
  }, []);

  const { connectionState, highestContiguousSequence } = useConversationEvents(
    conversationId,
    handleEvent,
  );

  const handleSend = async () => {
    const content = composer.trim();
    if (!conversationId || !content || activeRun) return;
    setComposer("");
    setError(null);
    setActiveRun(true);
    try {
      await sendCustomerMessage(conversationId, content);
    } catch (caught) {
      setActiveRun(false);
      setError(safeError(caught));
    }
  };

  const handleConfirm = async (proposal: ActionProposalView) => {
    if (proposalBusyId) return;
    setProposalBusyId(proposal.proposalId);
    setProposalBusyOperation("confirm");
    setError(null);
    setActiveRun(true);
    try {
      const accepted = await confirmProposal(proposal.proposalId, proposal.proposalVersion);
      setActiveCaseId(accepted.case_id);
    } catch (caught) {
      setActiveRun(false);
      setProposalBusyId(null);
      setProposalBusyOperation(null);
      setError(safeError(caught));
    }
  };

  const handleDecline = async (proposal: ActionProposalView) => {
    if (proposalBusyId) return;
    setProposalBusyId(proposal.proposalId);
    setProposalBusyOperation("decline");
    setError(null);
    setActiveRun(true);
    try {
      const accepted = await declineProposal(proposal.proposalId, proposal.proposalVersion);
      setActiveCaseId(accepted.case_id);
    } catch (caught) {
      setActiveRun(false);
      setProposalBusyId(null);
      setProposalBusyOperation(null);
      setError(safeError(caught));
    }
  };

  const handleRetry = async (caseId: string) => {
    if (retryBusyCaseId) return;
    setRetryBusyCaseId(caseId);
    setError(null);
    setActiveRun(true);
    try {
      await retryCase(caseId);
    } catch (caught) {
      setActiveRun(false);
      setError(safeError(caught));
    } finally {
      setRetryBusyCaseId(null);
    }
  };

  const handleReset = async () => {
    setResetting(true);
    setError(null);
    try {
      await resetSyntheticDemo();
      resetDialogRef.current?.close();
      await startConversation(customerKey);
    } catch (caught) {
      setError(safeError(caught));
    } finally {
      setResetting(false);
    }
  };

  const handleScenarioSelect = async (scenario: DemoScenarioView) => {
    if (!scenario.customer_message) return;
    if (scenario.customer_key !== customerKey) {
      setCustomerKey(scenario.customer_key);
      await startConversation(scenario.customer_key);
    }
    setComposer(scenario.customer_message);
    setScenarioLabOpen(false);
  };

  const timeline = useMemo(
    () => buildConversationTimeline(events, activeCaseId),
    [activeCaseId, events],
  );
  const currentProposal = useMemo(
    () => latestCurrentProposal(timeline, activeCaseId),
    [activeCaseId, timeline],
  );
  const currentResult = useMemo(
    () => latestCurrentResult(timeline, activeCaseId),
    [activeCaseId, timeline],
  );
  const traceCaseId = useMemo(
    () =>
      activeCaseId ??
      [...events].reverse().find((event) => event.case_id)?.case_id ??
      null,
    [activeCaseId, events],
  );
  const currentCaseEvents = useMemo(
    () => scopeEventsToCase(events, traceCaseId),
    [events, traceCaseId],
  );
  const customerDisposition: CustomerDisposition | null = useMemo(
    () => latestCustomerDisposition(events, traceCaseId),
    [events, traceCaseId],
  );
  const canSend =
    Boolean(conversationId) && !booting && !activeRun && proposalBusyId === null;
  const statusAnnouncement = activeRun
    ? "正在查看订单和物流记录"
    : error
      ? error
      : currentProposal?.state === "pending_confirmation"
        ? "查询完成，等待你决定是否发起物流核查"
        : currentResult?.title ?? "可以描述一个物流问题";

  if (view === "eval") {
    return (
      <EvalDashboard
        onBack={() => {
          window.location.hash = "";
          setView("conversation");
        }}
      />
    );
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="product-lockup">
          <span className="product-lockup__mark" aria-hidden="true"><i /><i /><i /></span>
          <div>
            <span className="product-lockup__utility">PARCEL CARE / AGENT</span>
            <strong>物流客服 Agent</strong>
          </div>
          <span className="demo-badge">合成数据 DEMO</span>
          {fixtureVersion && <code className="dataset-badge">DATASET {fixtureVersion}</code>}
        </div>

        <div className="topbar__controls">
          <label className="customer-switcher">
            <span>当前虚拟身份</span>
            <select
              value={customerKey}
              disabled={booting || activeRun || resetting}
              onChange={(event) => {
                const nextCustomer = event.target.value;
                setCustomerKey(nextCustomer);
                void startConversation(nextCustomer);
              }}
            >
              {CUSTOMER_OPTIONS.map((customer) => (
                <option value={customer.key} key={customer.key}>
                  {customer.label} · {customer.scope}
                </option>
              ))}
            </select>
          </label>

          <details className="orders-menu">
            <summary aria-label={`查看 ${syntheticCustomer?.display_name ?? "当前客户"}的可访问订单`}>
              订单 <span>{accessibleOrders.length}</span>
            </summary>
            <div className="orders-menu__panel">
              <div className="orders-menu__heading">
                <div>
                  <span className="eyebrow">ACCESSIBLE ORDERS</span>
                  <strong>{syntheticCustomer?.display_name ?? "当前虚拟客户"}</strong>
                </div>
                <span>{accessibleOrders.length} 单</span>
              </div>
              <div className="orders-menu__list">
                {accessibleOrders.map((order) => (
                  <article className="orders-menu__order" key={order.order_id}>
                    <div className="orders-menu__order-heading">
                      <strong className="mono">{order.order_id}</strong>
                      <span>{orderStatusLabel(order.order_status)}</span>
                      <small>{order.package_count} 个包裹</small>
                    </div>
                    {order.shipments.map((shipment) => (
                      <div className="orders-menu__shipment" key={shipment.shipment_id}>
                        <span className="mono">P{shipment.package_sequence}</span>
                        <strong>{shipment.shipment_id}</strong>
                        <span>{shipmentStatusLabel(shipment.shipment_status)}</span>
                        <small className="mono">{shipment.tracking_number}</small>
                        <time dateTime={shipment.last_update_at ?? undefined}>
                          更新 {shipmentUpdateLabel(shipment.last_update_at)}
                        </time>
                      </div>
                    ))}
                  </article>
                ))}
              </div>
            </div>
          </details>

          <button
            className="topbar-button topbar-button--scenarios"
            type="button"
            onClick={() => setScenarioLabOpen(true)}
            disabled={!demoCatalog}
          >
            全部场景
          </button>

          <span className={`mode-badge ${llmMode ? `mode-badge--${llmMode}` : ""}`}>
            <i aria-hidden="true" />
            {llmMode ? llmMode.toUpperCase() : booting ? "连接中" : "未知模式"}
          </span>
          <button
            className="topbar-button topbar-button--reset"
            type="button"
            aria-label="重置合成 Demo"
            onClick={() => resetDialogRef.current?.showModal()}
            disabled={resetting || booting}
          >
            <span className="topbar-button__full">重置合成 Demo</span>
            <span className="topbar-button__compact" aria-hidden="true">重置 Demo</span>
          </button>
          <button
            className="topbar-button topbar-button--eval"
            type="button"
            aria-label="打开 Eval Dashboard"
            onClick={() => {
              window.location.hash = "eval";
              setView("eval");
            }}
          >
            <span className="topbar-button__full">Eval Dashboard ↗</span>
            <span className="topbar-button__compact" aria-hidden="true">Eval ↗</span>
          </button>
        </div>
      </header>

      <RouteStrip
        events={currentCaseEvents}
        proposal={currentProposal}
        result={currentResult}
        activeRun={activeRun}
      />

      {conversationId && (
        <div className="session-tape" aria-label="当前合成会话信息">
          <span>SESSION</span>
          <code title={conversationId}>{compactId(conversationId)}</code>
          <span>CASE</span>
          <code title={traceCaseId ?? undefined}>{compactId(traceCaseId)}</code>
          <button
            className="trace-drawer-trigger"
            type="button"
            ref={traceTriggerRef}
            onClick={() => setTraceDrawerOpen(true)}
          >
            查看 Developer Trace
          </button>
        </div>
      )}

      {!conversationId && error && (
        <div className="service-error" role="alert">
          <div><strong>本地服务尚未就绪</strong><p>{error}</p></div>
          <button className="button button--primary" type="button" onClick={() => void startConversation(customerKey)}>
            重新连接
          </button>
        </div>
      )}

      <main className="workspace">
        <ConversationPanel
          timeline={timeline}
          composer={composer}
          setComposer={setComposer}
          canSend={canSend}
          error={conversationId ? error : null}
          proposalBusyId={proposalBusyId}
          proposalBusyOperation={proposalBusyOperation}
          retryBusyCaseId={retryBusyCaseId}
          syntheticCustomer={syntheticCustomer}
          demoCatalog={demoCatalog}
          customerDisposition={customerDisposition}
          onSend={() => void handleSend()}
          onConfirm={(proposal) => void handleConfirm(proposal)}
          onDecline={(proposal) => void handleDecline(proposal)}
          onRetry={(caseId) => void handleRetry(caseId)}
        />
        <TracePanel
          events={currentCaseEvents}
          connectionState={connectionState}
          highestSequence={highestContiguousSequence}
          activeRun={activeRun}
          drawerOpen={traceDrawerOpen}
          closeButtonRef={traceCloseButtonRef}
          onClose={() => setTraceDrawerOpen(false)}
        />
      </main>

      {traceDrawerOpen && (
        <button
          className="drawer-scrim"
          type="button"
          aria-label="关闭 Developer Trace"
          onClick={() => setTraceDrawerOpen(false)}
        />
      )}

      {scenarioLabOpen && demoCatalog && (
        <div className="scenario-lab-overlay" role="presentation" onMouseDown={() => setScenarioLabOpen(false)}>
          <section
            className="scenario-lab-drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="scenario-lab-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header className="scenario-lab-drawer__header">
              <div>
                <p className="eyebrow">SCENARIO CATALOG / SYNTHETIC ONLY</p>
                <h2 id="scenario-lab-title">全部业务场景</h2>
              </div>
              <button
                className="scenario-lab-drawer__close"
                type="button"
                ref={scenarioCloseButtonRef}
                onClick={() => setScenarioLabOpen(false)}
                aria-label="关闭全部业务场景"
              >
                关闭 ×
              </button>
            </header>
            <DemoScenarioPanel
              scenarios={demoCatalog.scenarios}
              faultProfiles={demoCatalog.fault_profiles}
              policyClauseCount={demoCatalog.policy_clause_count}
              currentCustomerKey={syntheticCustomer?.customer_key ?? "customer_a"}
              canFill={canSend}
              onSelect={(scenario) => void handleScenarioSelect(scenario)}
            />
          </section>
        </div>
      )}

      <div className="visually-hidden" aria-live="polite" aria-atomic="true">
        {statusAnnouncement}
      </div>

      <dialog className="reset-dialog" ref={resetDialogRef} aria-labelledby="reset-title">
        <div className="reset-dialog__seal" aria-hidden="true">RESET / SYNTHETIC ONLY</div>
        <h2 id="reset-title">重置合成 Demo？</h2>
        <p>这会清除虚拟 Conversation、Case、Run、Proposal、Action、Ticket 与 Event，并恢复 Fixture。</p>
        <p className="reset-dialog__boundary">
          不会修改 <code>.env</code>、模型配置或历史评测报告。
        </p>
        <div className="reset-dialog__actions">
          <button className="button button--quiet" type="button" onClick={() => resetDialogRef.current?.close()} disabled={resetting}>
            取消
          </button>
          <button className="button button--danger" type="button" onClick={() => void handleReset()} disabled={resetting}>
            {resetting ? "正在重置…" : "确认重置合成数据"}
          </button>
        </div>
      </dialog>
    </div>
  );
}
