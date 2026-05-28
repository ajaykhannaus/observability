from __future__ import annotations

import os

from observability import EVENT_APP_LOG, EventHubPublisher, build_envelope


def main() -> None:
    publisher = EventHubPublisher()
    envelope = build_envelope(
        EVENT_APP_LOG,
        {
            "level": "info",
            "logger": "example-app",
            "message": "hello from azure observability",
            "attributes": {"feature": "onboarding"},
        },
        tenant_id=os.getenv("OBS_TENANT_ID", "internal"),
    )
    ok = publisher._publish_envelope(envelope)
    publisher.flush()
    print("published:", ok, "app_id:", envelope["app_id"], "mock:", publisher.mock_mode)


if __name__ == "__main__":
    main()
