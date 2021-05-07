from collections import defaultdict

import asyncio
from typing import Optional


class Batcher:
    def __init__(self, timeout, loop):
        self._timeout = timeout
        self._loop = loop

        self._running = True
        self.__LOOKUP_MAP_BATCHING = defaultdict(lambda: defaultdict(list))

        # Schedule task
        self._task = asyncio.ensure_future(self._job(), loop=self._loop)

    async def _work(self):
        for f, instance_map in self.__LOOKUP_MAP_BATCHING.items():
            for instance in instance_map:
                events, instance_map[instance] = instance_map[instance], []
                await f(instance, events)

    def put(self, f, instance, event):
        self.__LOOKUP_MAP_BATCHING[f][instance].append(event)

    async def _job(self):
        while self._running:
            await asyncio.sleep(self._timeout)
            await self._work()

        # Make sure no events are lingering
        await self._work()

    async def join(self):
        self._running = False
        await self._task


class Dispatcher:
    def __init__(self):
        self.__LOOKUP_MAP = defaultdict(list)
        self._batcher: Optional[Batcher] = None

    def set_batcher(self, batcher):
        self._batcher = batcher

    def register_event_handler(self, event_types, batching=False):
        def decorator(function):
            nonlocal event_types, batching
            if not isinstance(event_types, set):
                event_types = set({event_types})
            for event_type in event_types:
                self.__LOOKUP_MAP[event_type].append((function, batching))

            def wrapper(*args, **kwargs):
                return function(*args, **kwargs)

            return wrapper

        return decorator

    async def handle_event(self, instance, event):
        for f, batching in self.__LOOKUP_MAP[event["type"]]:
            if batching:
                if self._batcher is None:
                    raise RuntimeError(
                        f"No batcher available when handeling {event} using {f} on {instance}"
                    )
                self._batcher.put(f, instance, event)
            else:
                await f(instance, event)
