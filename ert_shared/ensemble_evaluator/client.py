import websockets
from websockets import ConnectionClosedOK
import asyncio
import cloudevents
from websockets.exceptions import ConnectionClosed


class Client:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):

        if self.websocket is not None:
            self.loop.run_until_complete(self.websocket.close())
        self.loop.close()

    def __init__(self, url, max_retries=10, timeout_multiplier=5):
        self.url = url
        self._max_retries = max_retries
        self._timeout_multiplier = timeout_multiplier
        self.websocket = None
        self.loop = asyncio.new_event_loop()

    async def get_websocket(self):
        return await websockets.connect(self.url)

    async def _send(self, msg):
        for retry in range(self._max_retries + 1):
            try:
                if self.websocket is None:
                    self.websocket = await self.get_websocket()
                await self.websocket.send(msg)
                return
            except ConnectionClosedOK:
                # Connection was closed no point in trying to send more messages
                raise
            except (ConnectionClosed, ConnectionRefusedError, OSError):
                if retry == self._max_retries:
                    raise
                await asyncio.sleep(0.2 + self._timeout_multiplier * retry)
                self.websocket = None

    def send(self, msg):
        self.loop.run_until_complete(self._send(msg))

    def send_event(self, ev_type, ev_source, ev_data=None):
        if ev_data is None:
            ev_data = {}
        event = cloudevents.http.CloudEvent(
            {
                "type": ev_type,
                "source": ev_source,
                "datacontenttype": "application/json",
            },
            ev_data,
        )
        self.send(cloudevents.http.to_json(event).decode())
