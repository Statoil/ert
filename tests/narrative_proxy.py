import asyncio
import queue
import threading
from contextlib import contextmanager

import websockets

from tests.narrative import _Narrative, InteractionDirection


class NarrativeProxy(object):
    def __init__(self, narrative: _Narrative):
        self.narrative = narrative
        self.currentInteraction = narrative.interactions.pop()
        self.error = None

    async def _async_proxy(self, url, q):
        self.done = asyncio.get_event_loop().create_future()

        async def handle_messages(msg_q: asyncio.Queue, done: asyncio.Future):
            try:
                for interaction in self.narrative.interactions:
                    await interaction.verify(msg_q.get)
            except Exception as e:
                done.set_result(e)

        async def handle_server(server, client, msg_q):
            async for msg in server:
                await msg_q.put((InteractionDirection.RESPONSE, msg))
                await client.send(msg)

        async def handle_client(client, _path):
            msg_q = asyncio.Queue()
            if _path == "/client":
                async with websockets.connect(url + _path) as server:
                    msg_task = asyncio.create_task(handle_messages(msg_q, self.done))
                    server_task = asyncio.create_task(
                        handle_server(server, client, msg_q)
                    )

                    async for msg in client:
                        await msg_q.put((InteractionDirection.REQUEST, msg))
                        await server.send(msg)

                    server_task.cancel()
                    await server_task
                    await msg_task

        async with websockets.serve(handle_client, host="localhost", port=0) as s:
            port = s.sockets[0].getsockname()[1]
            asyncio.get_event_loop().run_in_executor(None, lambda: q.put(port))
            asyncio.get_event_loop().run_in_executor(None, lambda: q.put(self.done))
            error = await self.done
            q.put(error)

    def _proxy(self, url, q):
        asyncio.set_event_loop(asyncio.new_event_loop())
        q.put(asyncio.get_event_loop())
        asyncio.get_event_loop().run_until_complete(self._async_proxy(url, q))

    @contextmanager
    def proxy(self, url):
        q = queue.Queue()
        t = threading.Thread(target=self._proxy, args=(url, q))
        t.start()
        loop = q.get()
        port = q.get()
        done = q.get()
        yield port
        if not done.done():
            loop.call_soon_threadsafe(done.set_result, None)
        t.join()
        error = q.get()
        if error:
            raise error
