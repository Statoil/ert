import asyncio
import logging
import threading
from contextlib import contextmanager
from uuid import uuid4
import base64
import pickle
import cloudevents.exceptions

import cloudpickle

import ert_shared.ensemble_evaluator.entity.identifiers as identifiers
import ert_shared.ensemble_evaluator.monitor as ee_monitor
import websockets
from async_generator import asynccontextmanager
from cloudevents.http import from_json, to_json
from cloudevents.http.event import CloudEvent
from ert_shared.ensemble_evaluator.dispatch import Dispatcher
from ert_shared.ensemble_evaluator.entity import serialization
from ert_shared.ensemble_evaluator.entity.snapshot import (
    PartialSnapshot,
    Snapshot,
    Job,
    Realization,
    SnapshotDict,
    Step,
)
from ert_shared.status.entity.state import (
    ENSEMBLE_STATE_CANCELLED,
    ENSEMBLE_STATE_FAILED,
    ENSEMBLE_STATE_STARTED,
    ENSEMBLE_STATE_STOPPED,
    JOB_STATE_START,
    REALIZATION_STATE_WAITING,
    STEP_STATE_UNKNOWN,
)

logger = logging.getLogger(__name__)


class EnsembleEvaluator:
    _dispatch = Dispatcher()

    def __init__(self, ensemble, config, iter_, ee_id=0):
        # Without information on the iteration, the events emitted from the
        # evaluator are ambiguous. In the future, an experiment authority* will
        # "own" the evaluators and can add iteration information to events they
        # emit. In the mean time, it is added here.
        # * https://github.com/equinor/ert/issues/1250
        self._iter = iter_
        self._ee_id = ee_id

        self._config = config
        self._ensemble = ensemble

        self._loop = asyncio.new_event_loop()
        self._ws_thread = threading.Thread(
            name="ert_ee_run_server", target=self._run_server, args=(self._loop,)
        )
        self._done = self._loop.create_future()

        self._clients = set()
        self._dispatchers_connected = asyncio.Queue(loop=self._loop)

        self._snapshot = self.create_snapshot(ensemble)
        self._event_index = 1
        self._result = None

    @staticmethod
    def create_snapshot(ensemble):
        reals = {}
        for real in ensemble.get_active_reals():
            reals[str(real.get_iens())] = Realization(
                active=True,
                status=REALIZATION_STATE_WAITING,
            )
            for step in real.get_steps():
                reals[str(real.get_iens())].steps[str(step.get_id())] = Step(
                    status=STEP_STATE_UNKNOWN
                )
                for job in step.get_jobs():
                    reals[str(real.get_iens())].steps[str(step.get_id())].jobs[
                        str(job.get_id())
                    ] = Job(
                        status=JOB_STATE_START,
                        data={},
                        name=job.get_name(),
                    )
        top = SnapshotDict(
            reals=reals,
            status=ENSEMBLE_STATE_STARTED,
            metadata=ensemble.get_metadata(),
        )

        return Snapshot(top.dict())

    @_dispatch.register_event_handler(identifiers.EVGROUP_FM_ALL)
    async def _fm_handler(self, event):
        snapshot_mutate_event = PartialSnapshot(self._snapshot).from_cloudevent(event)
        await self._send_snapshot_update(snapshot_mutate_event)

    @_dispatch.register_event_handler(identifiers.EVTYPE_ENSEMBLE_STOPPED)
    async def _ensemble_stopped_handler(self, event):
        self._result = event.data
        if self._snapshot.get_status() != ENSEMBLE_STATE_FAILED:
            snapshot_mutate_event = PartialSnapshot(self._snapshot).from_cloudevent(
                event
            )
            await self._send_snapshot_update(snapshot_mutate_event)

    @_dispatch.register_event_handler(identifiers.EVTYPE_ENSEMBLE_STARTED)
    async def _ensemble_started_handler(self, event):
        if self._snapshot.get_status() != ENSEMBLE_STATE_FAILED:
            snapshot_mutate_event = PartialSnapshot(self._snapshot).from_cloudevent(
                event
            )
            await self._send_snapshot_update(snapshot_mutate_event)

    @_dispatch.register_event_handler(identifiers.EVTYPE_ENSEMBLE_CANCELLED)
    async def _ensemble_cancelled_handler(self, event):
        if self._snapshot.get_status() != ENSEMBLE_STATE_FAILED:
            snapshot_mutate_event = PartialSnapshot(self._snapshot).from_cloudevent(
                event
            )
            await self._send_snapshot_update(snapshot_mutate_event)
            self._stop()

    @_dispatch.register_event_handler(identifiers.EVTYPE_ENSEMBLE_FAILED)
    async def _ensemble_failed_handler(self, event):
        if self._snapshot.get_status() not in [
            ENSEMBLE_STATE_STOPPED,
            ENSEMBLE_STATE_CANCELLED,
        ]:
            snapshot_mutate_event = PartialSnapshot(self._snapshot).from_cloudevent(
                event
            )
            await self._send_snapshot_update(snapshot_mutate_event)

    async def _send_snapshot_update(self, snapshot_mutate_event):
        self._snapshot.merge_event(snapshot_mutate_event)
        out_cloudevent = CloudEvent(
            {
                "type": identifiers.EVTYPE_EE_SNAPSHOT_UPDATE,
                "source": f"/ert/ee/{self._ee_id}",
                "id": self.event_index(),
            },
            snapshot_mutate_event.to_dict(),
        )
        out_cloudevent.data["iter"] = self._iter
        out_msg = to_json(
            out_cloudevent, data_marshaller=serialization.evaluator_marshaller
        ).decode()
        if out_msg and self._clients:
            await asyncio.wait([client.send(out_msg) for client in self._clients])

    @staticmethod
    def create_snapshot_msg(ee_id, iter_, snapshot, event_index):
        data = snapshot.to_dict()
        data["iter"] = iter_
        out_cloudevent = CloudEvent(
            {
                "type": identifiers.EVTYPE_EE_SNAPSHOT,
                "source": f"/ert/ee/{ee_id}",
                "id": event_index,
            },
            data,
        )
        return to_json(
            out_cloudevent, data_marshaller=serialization.evaluator_marshaller
        ).decode()

    @contextmanager
    def store_client(self, websocket):
        self._clients.add(websocket)
        yield
        self._clients.remove(websocket)

    async def handle_client(self, websocket, path):
        with self.store_client(websocket):
            message = self.create_snapshot_msg(
                self._ee_id, self._iter, self._snapshot, self.event_index()
            )
            await websocket.send(message)

            async for message in websocket:
                client_event = from_json(
                    message, data_unmarshaller=serialization.evaluator_unmarshaller
                )
                logger.debug(f"got message from client: {client_event}")
                if client_event["type"] == identifiers.EVTYPE_EE_USER_CANCEL:
                    logger.debug(f"Client {websocket.remote_address} asked to cancel.")
                    if self._ensemble.is_cancellable():
                        # The evaluator will stop after the ensemble has
                        # indicated it has been cancelled.
                        self._ensemble.cancel()
                    else:
                        self._stop()

                if client_event["type"] == identifiers.EVTYPE_EE_USER_DONE:
                    logger.debug(f"Client {websocket.remote_address} signalled done.")
                    self._stop()

    @asynccontextmanager
    async def count_dispatcher(self):
        await self._dispatchers_connected.put(None)
        yield
        await self._dispatchers_connected.get()
        self._dispatchers_connected.task_done()

    async def handle_dispatch(self, websocket, path):
        async with self.count_dispatcher():
            async for msg in websocket:
                try:
                    event = from_json(
                        msg, data_unmarshaller=serialization.evaluator_unmarshaller
                    )
                except cloudevents.exceptions.DataUnmarshallerError:
                    event = from_json(msg, data_unmarshaller=lambda x: pickle.loads(x))
                await self._dispatch.handle_event(self, event)
                if event["type"] in [
                    identifiers.EVTYPE_ENSEMBLE_STOPPED,
                    identifiers.EVTYPE_ENSEMBLE_FAILED,
                ]:
                    return

    async def handle_result(self, websocket, path):
        if self._result is None:
            event = CloudEvent(
                {
                    "type": identifiers.EVTYPE_EE_RESULT_NOT_READY,
                    "source": f"/ert/ee/{self._ee_id}",
                },
            )
        else:
            event = CloudEvent(
                {
                    "type": identifiers.EVTYPE_EE_RESULT,
                    "source": f"/ert/ee/{self._ee_id}",
                    "datacontenttype": "application/octet-stream",
                },
                cloudpickle.dumps(self._result),
            )
        await websocket.send(to_json(event))

    async def connection_handler(self, websocket, path):
        elements = path.split("/")
        if elements[1] == "client":
            await self.handle_client(websocket, path)
        elif elements[1] == "dispatch":
            await self.handle_dispatch(websocket, path)
        elif elements[1] == "result":
            await self.handle_result(websocket, path)
        else:
            logger.info(f"Connection attempt to unknown path: {path}.")

    async def evaluator_server(self, done):
        async with websockets.serve(
            self.connection_handler,
            sock=self._config.get_socket(),
            max_queue=500,
            max_size=2 ** 26,
        ):
            await done
            logger.debug("Got done signal.")
            # give NFS adaptors and Queue adaptors some time to read/send last events
            try:
                await asyncio.wait_for(self._dispatchers_connected.join(), timeout=10)
            except asyncio.TimeoutError:
                pass
            message = self.terminate_message()
            if self._clients:
                await asyncio.wait([client.send(message) for client in self._clients])
            logger.debug("Sent terminated to clients.")

        logger.debug("Async server exiting.")

    def _run_server(self, loop):
        loop.run_until_complete(self.evaluator_server(self._done))
        logger.debug("Server thread exiting.")

    def terminate_message(self):
        attrs = {
            "type": identifiers.EVTYPE_EE_TERMINATED,
            "source": f"/ert/ee/{self._ee_id}",
            "id": str(self.event_index()),
        }
        data = None
        if self._result:
            attrs["datacontenttype"] = "application/octet-stream"
            data = cloudpickle.dumps(self._result)
        out_cloudevent = CloudEvent(attrs, data)
        message = to_json(out_cloudevent, data_marshaller=cloudpickle.dumps).decode()
        return message

    def event_index(self):
        index = self._event_index
        self._event_index += 1
        return index

    def run(self):
        self._ws_thread.start()
        self._ensemble.evaluate(self._config, self._ee_id)
        return ee_monitor.create(self._config.host, self._config.port)

    def _stop(self):
        if not self._done.done():
            self._done.set_result(None)

    def stop(self):
        self._loop.call_soon_threadsafe(self._stop)
        self._ws_thread.join()

    def get_successful_realizations(self):
        return self._snapshot.get_successful_realizations()

    def run_and_get_successful_realizations(self):
        with self.run() as mon:
            for _ in mon.track():
                pass
        return self.get_successful_realizations()
