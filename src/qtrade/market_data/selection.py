from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ProviderChoice(StrEnum):
    AKSHARE = "AKSHARE"
    RQDATA = "RQDATA"
    XTDATA = "XTDATA"


@dataclass(frozen=True, slots=True)
class ProviderSelectionStatus:
    provider: ProviderChoice
    ready_for_authorized_bridge: bool
    missing_configuration: tuple[str, ...]
    required_user_actions: tuple[str, ...]
    operational_probe_required: bool = False


def inspect_provider_selection(
    provider: ProviderChoice, *, environment: dict[str, str] | None = None
) -> ProviderSelectionStatus:
    values = dict(os.environ if environment is None else environment)
    required: tuple[str, ...]
    actions: tuple[str, ...]
    missing: tuple[str, ...]
    operational_probe_required = False
    if provider is ProviderChoice.AKSHARE:
        required = ("QTRADE_AKSHARE_NONCOMMERCIAL_ACK",)
        actions = (
            "Review AKShare's official data-use statement for noncommercial research.",
            "Set QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true only after accepting that boundary.",
            "Install pinned AKShare 1.18.84 in an isolated provider environment.",
        )
        missing = (
            ()
            if values.get("QTRADE_AKSHARE_NONCOMMERCIAL_ACK", "").lower() == "true"
            else ("QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true",)
        )
        operational_probe_required = True
    elif provider is ProviderChoice.RQDATA:
        required = ("QTRADE_RQDATA_USERNAME", "QTRADE_RQDATA_TOKEN")
        actions = (
            "Obtain a licensed RQData account with realtime A-share/ETF/index entitlement.",
            "Set QTRADE_RQDATA_USERNAME and QTRADE_RQDATA_TOKEN in the bridge process only.",
            "Install pinned rqdatac/vnpy_rqdata in the isolated provider environment.",
        )
        missing = tuple(name for name in required if not values.get(name, "").strip())
    else:
        required = ("QTRADE_XTDATA_PATH", "QTRADE_XTDATA_ENTITLEMENT_CONFIRMED")
        actions = (
            "Install an authorized QMT/XT client and confirm market-data entitlement.",
            "Set QTRADE_XTDATA_PATH to the bridge-visible QMT data directory.",
            "Set QTRADE_XTDATA_ENTITLEMENT_CONFIRMED=true only after entitlement verification.",
        )
        missing_list = [name for name in required if not values.get(name, "").strip()]
        path = values.get("QTRADE_XTDATA_PATH", "")
        if path and not Path(path).is_dir():
            missing_list.append("QTRADE_XTDATA_PATH(valid directory)")
        if values.get("QTRADE_XTDATA_ENTITLEMENT_CONFIRMED", "").lower() != "true":
            missing_list.append("QTRADE_XTDATA_ENTITLEMENT_CONFIRMED=true")
        missing = tuple(dict.fromkeys(missing_list))
    return ProviderSelectionStatus(
        provider,
        not missing and not operational_probe_required,
        missing,
        actions,
        operational_probe_required,
    )
