import asyncio

from pipeline.events import EventBus


def test_publish_then_subscribe_receives_events_and_stops_at_done() -> None:
    async def scenario() -> list[dict]:
        loop = asyncio.get_running_loop()
        bus = EventBus(loop)
        bus.publish("run-1", "triage", {"ok": True})
        bus.publish("run-1", "__done__", {})
        received = []
        async for event in bus.subscribe("run-1"):
            received.append(event)
        return received

    received = asyncio.run(scenario())
    assert received[0] == {"stage": "triage", "payload": {"ok": True}}
    assert received[-1]["stage"] == "__done__"
