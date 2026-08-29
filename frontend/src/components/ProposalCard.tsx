import { useEffect, useId, useMemo, useState } from "react";

import { humanIssueType } from "../lib/presentation";
import type { ActionProposalView } from "../types";

interface ProposalCardProps {
  proposal: ActionProposalView;
  busy: "confirm" | "decline" | null;
  onConfirm: () => void;
  onDecline: () => void;
}

function isExpired(expiresAt: string | null, now: number): boolean {
  if (!expiresAt) return false;
  const expiry = new Date(expiresAt).valueOf();
  return !Number.isNaN(expiry) && expiry <= now;
}

const STATE_TEXT: Record<ActionProposalView["state"], string> = {
  pending_confirmation: "等待你的选择",
  confirmed: "已确认",
  declined: "这次暂不处理",
  superseded: "已有新的处理建议",
  expired: "本次结果已过期",
  invalidated: "情况有变化，请重新查询",
};

function nextStep(issueType: string): string {
  return issueType === "stalled_tracking"
    ? "联系物流方确认当前运输状态"
    : "联系物流方继续确认包裹去向";
}

export function ProposalCard({ proposal, busy, onConfirm, onDecline }: ProposalCardProps) {
  const headingId = useId();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!proposal.expiresAt || proposal.state !== "pending_confirmation") return;
    const interval = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(interval);
  }, [proposal.expiresAt, proposal.state]);

  const expired = useMemo(() => isExpired(proposal.expiresAt, now), [proposal.expiresAt, now]);
  const canAct = proposal.state === "pending_confirmation" && !expired && busy === null;

  return (
    <section className="proposal-card" aria-labelledby={headingId}>
      <div className="proposal-card__heading">
        <div>
          <p className="eyebrow">下一步</p>
          <h3 id={headingId}>需要我发起物流核查吗？</h3>
        </div>
        <span className={`proposal-state proposal-state--${proposal.state}`}>
          {STATE_TEXT[proposal.state]}
        </span>
      </div>

      <dl className="proposal-card__facts">
        <div>
          <dt>订单</dt>
          <dd className="mono">{proposal.orderId}</dd>
        </div>
        <div>
          <dt>当前问题</dt>
          <dd>{humanIssueType(proposal.issueType)}</dd>
        </div>
        <div className="proposal-card__fact--wide">
          <dt>接下来</dt>
          <dd>{nextStep(proposal.issueType)}</dd>
        </div>
        {proposal.targetShipmentId && (
          <div>
            <dt>目标包裹</dt>
            <dd className="mono">{proposal.targetShipmentId}</dd>
          </div>
        )}
      </dl>

      <div className="proposal-card__boundary">
        <strong>只有你确认后才会提交。</strong>
        <span>不会退款、赔付或修改订单。</span>
      </div>

      {proposal.state === "pending_confirmation" && (
        <div className="proposal-card__actions">
          <button
            className="button button--primary"
            type="button"
            disabled={!canAct}
            onClick={onConfirm}
          >
            {busy === "confirm" ? "正在提交…" : "发起物流核查"}
          </button>
          <button
            className="button button--quiet"
            type="button"
            disabled={!canAct}
            onClick={onDecline}
          >
            {busy === "decline" ? "正在记录…" : "暂时不用"}
          </button>
        </div>
      )}
      {expired && proposal.state === "pending_confirmation" && (
        <p className="inline-notice inline-notice--warning" role="status">
          这次核对结果已经过期，请重新查询后再操作。
        </p>
      )}
    </section>
  );
}
