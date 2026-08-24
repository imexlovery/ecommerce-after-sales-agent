"""One object-authorization rule shared by every order-scoped read tool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

ORDER_NOT_FOUND_OR_FORBIDDEN = "ORDER_NOT_FOUND_OR_FORBIDDEN"


class OrderOwnershipRecord(Protocol):
    @property
    def order_id(self) -> str: ...

    @property
    def customer_id(self) -> str: ...

    @property
    def source_revision(self) -> str: ...


class OrderOwnershipSource(Protocol):
    def get_order_for_authorization(self, order_id: str) -> OrderOwnershipRecord | None: ...


@dataclass(frozen=True, slots=True)
class AuthorizationGrant:
    customer_id: str
    order_id: str
    source_revision: str


class AuthorizationError(Exception):
    """Safe collapsed denial; it intentionally carries no existence detail."""

    code = ORDER_NOT_FOUND_OR_FORBIDDEN

    def __init__(self) -> None:
        super().__init__(self.code)


def authorize_order(
    customer_id: str,
    order_id: str,
    source: OrderOwnershipSource,
) -> AuthorizationGrant:
    """Authorize one synthetic order without disclosing absent vs foreign."""

    record = source.get_order_for_authorization(order_id)
    if record is None or record.customer_id != customer_id:
        raise AuthorizationError
    return AuthorizationGrant(
        customer_id=customer_id,
        order_id=record.order_id,
        source_revision=record.source_revision,
    )
