"""Customer-facing wording derived only from deterministic structured facts."""

from __future__ import annotations

from after_sales_agent.application.policy_router import BlockedFragment, PolicyDecision, PolicyRoute
from after_sales_agent.domain.state import EvidenceGateDecision, IssueType


def render_investigation_ack(
    *, order_id: str, issue_type: IssueType, target_shipment_id: str | None = None
) -> str:
    """Acknowledge the customer before the internal investigation starts."""

    target = f"包裹 {target_shipment_id} 的" if target_shipment_id else ""
    if issue_type is IssueType.SIGNED_NOT_RECEIVED:
        return (
            f"我来帮你查一下 {target}{order_id}。"
            "我会先核对订单状态、签收记录，以及是否已经有人在处理这个问题。"
        )
    return (
        f"我来帮你查一下 {target}{order_id}。"
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
    target_shipment_id: str | None = None,
    existing_case_detail: str | None = None,
    carrier_alert_detail: str | None = None,
) -> str:
    prefix = blocked_fragment_notices(blocked_fragments)
    target = f"包裹 {target_shipment_id} 的" if target_shipment_id else ""
    if decision is EvidenceGateDecision.PROPOSE_TICKET:
        if issue_type is IssueType.SIGNED_NOT_RECEIVED:
            body = (
                f"我查到了：{target}{order_id} 当前显示已签收，但没有找到能确认具体收件位置的"
                "签收凭证，也没有正在处理的同类核查。下一步，我可以联系物流方继续"
                "查找包裹去向。需要我现在发起核查吗？"
            )
        else:
            body = (
                f"我查到了：{target}{order_id} 的物流已经超过预计更新时间，目前也没有正在处理的"
                "同类核查。下一步，我可以向物流方发起核查，请他们确认当前运输状态。"
                "需要我现在发起吗？"
            )
    elif decision is EvidenceGateDecision.REQUEST_BUSINESS_CLARIFICATION:
        body = (
            "签收记录需要先核对收件人或代收位置。请确认本人、家庭成员、门卫、前台、"
            "邻居、代收点或快递柜是否确实收到。"
        )
    elif decision is EvidenceGateDecision.RETRY_LATER:
        body = "关键物流证据暂时无法完成查询，因此现在不会创建工单。请稍后重试。"
    elif decision is EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT:
        if reason_code == "DELIVERY_EVIDENCE_CONFLICT":
            body = (
                "签收凭证显示的收件位置与客户核实结果冲突，已停止自动判断，"
                "请联系人工支持继续核查。"
            )
        else:
            body = "现有信息存在无法安全自动判断的冲突，请联系人工支持继续核查。"
    elif reason_code == "ACTIVE_LOGISTICS_TICKET_EXISTS":
        body = "这笔订单已有进行中的物流核查，不会重复创建。"
        if existing_case_detail:
            body += f"{existing_case_detail}"
    elif reason_code == "ACTIVE_CARRIER_RECOVERY_WINDOW":
        body = carrier_alert_detail or "承运商当前存在服务异常恢复窗口，先等待下一次物流更新。"
        body += "不会重复创建核查工单。"
    elif reason_code == "WITHIN_TRACKING_SLA":
        body = "物流仍在规则时限内，目前不需要创建核查工单。"
    else:
        body = "核查已经完成，目前不需要创建新的物流核查工单。"
    return "".join(prefix + [body])
