import asyncio

_subscribers: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(entry: dict) -> None:
    for q in list(_subscribers):
        try:
            q.put_nowait(entry)
        except asyncio.QueueFull:
            pass
