import asyncio
import websockets
from ert_shared.ensemble_evaluator.ws_util import wait_for_ws
import logging
import threading
from cloudevents.http import from_json
from cloudevents.http.event import CloudEvent
from cloudevents.http import to_json
from cloudevents.exceptions import DataUnmarshallerError
import ert_shared.ensemble_evaluator.entity.identifiers as identifiers
from ert_shared.ensemble_evaluator.entity import serialization
import uuid
import pickle


logger = logging.getLogger(__name__)


class _Monitor:
    def __init__(self, host, port):
        self._base_uri = f"ws://{host}:{port}"
        self._client_uri = f"{self._base_uri}/client"
        self._result_uri = f"{self._base_uri}/result"

        self._loop = None
        self._incoming = None
        self._receive_future = None
        self._ws = None
        self._id = str(uuid.uuid1()).split("-")[0]

    def __enter__(self):
        self._loop = asyncio.new_event_loop()
        self._incoming = asyncio.Queue(loop=self._loop)
        return self

    def __exit__(self, *args):
        self._loop.close()

    def get_base_uri(self):
        return self._base_uri

    # def get_result(self):
    #     async def _send():
    #         async with websockets.connect(self._result_uri) as websocket:
    #             result = await websocket.recv()
    #             message = from_json(result, lambda x: pickle.loads(x))
    #             return message.data

    #     if self._loop.is_running():
    #         return asyncio.run_coroutine_threadsafe(_send(), self._loop).result()
    #     return self._loop.run_until_complete(_send())

    def _send_event(self, cloud_event):
        message = to_json(
            cloud_event, data_marshaller=serialization.evaluator_marshaller
        )
        asyncio.run_coroutine_threadsafe(self._ws.send(message), self._loop).result()

    def signal_cancel(self):
        logger.debug(f"monitor-{self._id} asking server to cancel...")

        out_cloudevent = CloudEvent(
            {
                "type": identifiers.EVTYPE_EE_USER_CANCEL,
                "source": f"/ert/monitor/{self._id}",
                "id": str(uuid.uuid1()),
            }
        )
        self._send_event(out_cloudevent)
        logger.debug(f"monitor-{self._id} asked server to cancel")

    def signal_done(self):
        logger.debug(f"monitor-{self._id} informing server monitor is done...")

        out_cloudevent = CloudEvent(
            {
                "type": identifiers.EVTYPE_EE_USER_DONE,
                "source": f"/ert/monitor/{self._id}",
                "id": str(uuid.uuid1()),
            }
        )
        self._send_event(out_cloudevent)
        logger.debug(f"monitor-{self._id} informed server monitor is done")

    async def _receive(self):
        logger.debug(f"monitor-{self._id} starting receive")
        self._ws = await websockets.connect(
            self._client_uri, max_size=2 ** 26, max_queue=500
        )

        async for message in self._ws:
            try:
                event = from_json(
                    message, data_unmarshaller=serialization.evaluator_unmarshaller
                )
            except DataUnmarshallerError:
                event = from_json(message, data_unmarshaller=pickle.loads)
            self._incoming.put_nowait(event)
            if event["type"] == identifiers.EVTYPE_EE_TERMINATED:
                logger.debug(f"monitor-{self._id} client received terminated")
                break
        await self._ws.close()
        logger.debug(f"monitor-{self._id} disconnected")

    def _run(self, done_future):
        asyncio.set_event_loop(self._loop)
        self._receive_future = self._loop.create_task(self._receive())
        try:
            self._loop.run_until_complete(self._receive_future)
            self._incoming.put_nowait("end")
        except asyncio.CancelledError:
            logger.debug(f"monitor-{self._id} receive cancelled")
        self._loop.run_until_complete(done_future)

    def track(self):
        wait_for_ws(self._base_uri)

        done_future = asyncio.Future(loop=self._loop)

        thread = threading.Thread(
            name=f"ert_monitor-{self._id}_loop", target=self._run, args=(done_future,)
        )
        thread.start()

        event = None
        try:
            while event is None or event["type"] != identifiers.EVTYPE_EE_TERMINATED:
                event = asyncio.run_coroutine_threadsafe(
                    self._incoming.get(), self._loop
                ).result()
                if not isinstance(event, CloudEvent):
                    break
                yield event
            self._loop.call_soon_threadsafe(done_future.set_result, None)
        except GeneratorExit:
            logger.debug(f"monitor-{self._id} generator exit")
            self._loop.call_soon_threadsafe(self._receive_future.cancel)
            if not done_future.done():
                self._loop.call_soon_threadsafe(done_future.set_result, None)
        thread.join()


def create(host, port):
    return _Monitor(host, port)
