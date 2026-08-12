"""Print credential-free provider selection requirements without secret values."""

import argparse

from qtrade.market_data import ProviderChoice, inspect_provider_selection


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=[item.value for item in ProviderChoice])
    args = parser.parse_args()
    status = inspect_provider_selection(ProviderChoice(args.provider))
    print(f"provider={status.provider.value}")
    print(f"ready_for_authorized_bridge={str(status.ready_for_authorized_bridge).lower()}")
    if status.missing_configuration:
        print("missing_configuration=" + ",".join(status.missing_configuration))
    for index, action in enumerate(status.required_user_actions, start=1):
        print(f"action_{index}={action}")
    return 0 if status.ready_for_authorized_bridge else 2


if __name__ == "__main__":
    raise SystemExit(main())
