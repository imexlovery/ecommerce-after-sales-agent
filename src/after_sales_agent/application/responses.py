"""Customer-facing wording derived only from deterministic structured facts."""

from __future__ import annotations

from after_sales_agent.application.policy_router import BlockedFragment, PolicyDecision, PolicyRoute
from after_sales_agent.domain.state import EvidenceGateDecision, IssueType


def render_investigation_ack(*, order_id: str, issue_type: IssueType) -> str:
    """Acknowledge the customer before the internal investigation starts."""

    if issue_type is IssueType.SIGNED_NOT_RECEIVED:
        return (
            f"我来帮你查一下 {order_id}。"
            "我会先核对订单状态、签收记录，以及是否已经有人在处理这个问题。"
        )
    return (
        f"我来帮你查一下 {order_id}。"
        "我会先核对最新物流轨迹、预计时效，以及是否已经有人在处理这个问题。"
    )


def blocked_fragment_notices(fragments: tuple[BlockedFragment, ...]) -> list[str]:
    categories = {fragment.category for fragment in fragments}
    notices: list[str] = []
    if "unauthorized_order_access" in categories:
        notices.append("我无法访问与当前账户无关的订单。")
    if "prohibited_action_request" in categories:
        notices.append("退款、赔付和换货不在这次物流核查的处理范围内。")
    if "unnecessary_personal_data" in categories:
        notices.append("请不要在消息中发送电话号码、邮箱等不必要的个人信息。")
    return notices


def render_policy_reply(decision: PolicyDecision) -> str:
    prefix = blocked_fragment_notices(decision.blocked_fragments)
    if decision.reason_code == "ENTRY_CLARIFICATION_EXHAUSTED":
        body = "这次入口信息仍不足以安全创建物流核查，请联系人工支持继续处理。"
    elif decision.route is PolicyRoute.AMBIGUOUS:
        body = "请说明是物流长时间没有更新，还是显示签收但没有收到，并附上订单号。"
    elif decision.route is PolicyRoute.UNAUTHORIZED:
        body = "请使用当前虚拟客户名下的订单继续核查。"
    elif decision.route is PolicyRoute.PROHIBITED:
        body = "我不能执行退款、赔付、退换货等操作，但可以协助核查支持的物流异常。"
    else:
        body = "目前只能处理物流停滞或显示签收但未收到的问题。"
    return "".join(prefix + [body])


def render_gate_reply(
    *,
    order_id: str,
    issue_type: IssueType,
    decision: EvidenceGateDecision,
    reason_code: str,
    blocked_fragments: tuple[BlockedFragment, ...] = (),
) -> str:
    prefix = blocked_fragment_notices(blocked_fragments)
    if decision is EvidenceGateDecision.PROPOSE_TICKET:
        if issue_type is IssueType.SIGNED_NOT_RECEIVED:
            body = (
                f"我查到了：{order_id} 当前显示已签收，但没有找到能确认具体收件位置的"
                "签收凭证，也没有正在处理的同类核查。下一步，我可以联系物流方继续"
                "查找包裹去向。需要我现在发起核查吗？"
            )
        else:
            body = (
                f"我查到了：{order_id} 的物流已经超过预计更新时间，目前也没有正在处理的"
                "同类核查。下一步，我可以向物流方发起核查，请他们确认当前运输状态。"
                "需要我现在发起吗？"
            )
    elif decision is EvidenceGateDecision.REQUEST_BUSINESS_CLARIFICATION:
        body = "签收记录包含代收信息。请先确认门卫、前台、邻居或家人是否代收。"
    elif decision is EvidenceGateDecision.RETRY_LATER:
        body = "关键物流证据暂时无法完成查询，因此现在不会创建工单。请稍后重试。"
    elif decision is EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT:
        body = "现有信息存在无法安全自动判断的冲突，请联系人工支持继续核查。"
    elif reason_code == "ACTIVE_LOGISTICS_TICKET_EXISTS":
        body = "这笔订单已有进行中的物流核查工单，不会重复创建。"
    elif reason_code == "WITHIN_TRACKING_SLA":
        body = "物流仍在规则时限内，目前不需要创建核查工单。"
    else:
        body = "核查已经完成，目前不需要创建新的物流核查工单。"
    return "".join(prefix + [body])
