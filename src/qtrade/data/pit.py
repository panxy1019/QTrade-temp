from __future__ import annotations

from datetime import date, datetime

from qtrade.data.models import CorporateAction, IndexMembership


class PointInTimeStore:
    """In-memory reference semantics for leakage-safe data adapters."""

    def __init__(self) -> None:
        self._memberships: list[IndexMembership] = []
        self._actions: list[CorporateAction] = []

    def add_membership(self, membership: IndexMembership) -> None:
        if membership.known_at.tzinfo is None:
            raise ValueError("known_at must be timezone-aware")
        if membership.valid_to is not None and membership.valid_to < membership.valid_from:
            raise ValueError("valid_to cannot precede valid_from")
        self._memberships.append(membership)

    def universe(self, index_symbol: str, *, as_of: date, known_at: datetime) -> frozenset[str]:
        if known_at.tzinfo is None:
            raise ValueError("known_at must be timezone-aware")
        return frozenset(
            item.member_symbol
            for item in self._memberships
            if item.index_symbol == index_symbol
            and item.known_at <= known_at
            and item.valid_from <= as_of
            and (item.valid_to is None or as_of <= item.valid_to)
        )

    def add_corporate_action(self, action: CorporateAction) -> None:
        if action.announced_at.tzinfo is None:
            raise ValueError("announced_at must be timezone-aware")
        self._actions.append(action)

    def corporate_actions(
        self, symbol: str, *, through: date, known_at: datetime
    ) -> tuple[CorporateAction, ...]:
        if known_at.tzinfo is None:
            raise ValueError("known_at must be timezone-aware")
        return tuple(
            action
            for action in self._actions
            if action.symbol == symbol
            and action.effective_date <= through
            and action.announced_at <= known_at
        )
