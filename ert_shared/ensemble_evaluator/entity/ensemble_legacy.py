import asyncio
import logging
import threading
import uuid
from functools import partial

import ert_shared.ensemble_evaluator.entity.identifiers as identifiers
from cloudevents.http.event import CloudEvent
from ert_shared.ensemble_evaluator.entity.ensemble_base import _Ensemble
from ert_shared.ensemble_evaluator.ws_util import wait_for_ws
from res.enkf import RunArg

CONCURRENT_INTERNALIZATION = 10

logger = logging.getLogger(__name__)


class _LegacyEnsemble(_Ensemble):
    def __init__(
        self,
        reals,
        metadata,
        *args,
    ):
        super().__init__(reals, metadata)
        (
            queue_config,
            analysis_config,
        ) = args
        if not queue_config:
            raise ValueError(f"{self} needs queue_config")
        if not analysis_config:
            raise ValueError(f"{self} needs analysis_config")
        self._queue_config = queue_config
        self._analysis_config = analysis_config
        self._job_queue = None
        self._allow_cancel = threading.Event()
        self._aggregate_future = None
        self._config = None
        self._ee_id = None

    def evaluate(self, config, ee_id):
        self._config = config
        self._ee_id = ee_id
        wait_for_ws(
            url=self._config.url,
            token=self._config.token,
            cert=self._config.cert,
        )
        self._evaluate_thread = threading.Thread(target=self._evaluate)
        self._evaluate_thread.start()

    def _evaluate(self):
        asyncio.set_event_loop(asyncio.new_event_loop())

        dispatch_url = self._config.dispatch_uri
        cert = self._config.cert
        token = self._config.token
        try:
            out_cloudevent = CloudEvent(
                {
                    "type": identifiers.EVTYPE_ENSEMBLE_STARTED,
                    "source": f"/ert/ee/{self._ee_id}/ensemble",
                    "id": str(uuid.uuid1()),
                }
            )
            asyncio.get_event_loop().run_until_complete(
                self.send_cloudevent(
                    dispatch_url, out_cloudevent, token=token, cert=cert
                )
            )

            self._job_queue = self._queue_config.create_job_queue()

            loop = asyncio.get_event_loop()
            timeout_queue = asyncio.Queue(loop=loop)

            async def send_timeout_message():
                while True:
                    timeout_cloudevent = await timeout_queue.get()
                    if timeout_cloudevent is None:
                        break
                    await self.send_cloudevent(dispatch_url, timeout_cloudevent, token=token, cert=cert)

            def on_timeout(callback_args):
                #print("In on_timeout function")
                run_args: RunArg = callback_args[0]
                timeout_cloudevent = CloudEvent(
                    {
                        "type": identifiers.EVTYPE_FM_STEP_TIMEOUT,
                        "source": f"/ert/ee/{self._ee_id}/real/{run_args.iens}/step/0",
                        "id": str(uuid.uuid1()),
                    }
                )
                timeout_queue.put_nowait(timeout_cloudevent)
                #print(timeout_cloudevent)

            for real in self.get_active_reals():
                self._job_queue.add_ee_stage(real.get_steps()[0], after_exit_callback_function=on_timeout)

            self._job_queue.submit_complete()

            # TODO: this is sort of a callback being preemptively called.
            # It should be lifted out of the queue/evaluate, into the evaluator. If
            # something is long running, the evaluator will know and should send
            # commands to the task in order to have it killed/retried.
            # See https://github.com/equinor/ert/issues/1229
            queue_evaluators = None
            if (
                self._analysis_config.get_stop_long_running()
                and self._analysis_config.minimum_required_realizations > 0
            ):
                queue_evaluators = [
                    partial(
                        self._job_queue.stop_long_running_jobs,
                        self._analysis_config.minimum_required_realizations,
                    )
                ]

            self._job_queue.add_ensemble_evaluator_information_to_jobs_file(
                self._ee_id, dispatch_url, cert, token
            )

            futures = [
                self._job_queue.execute_queue_async(
                    dispatch_url,
                    threading.BoundedSemaphore(value=CONCURRENT_INTERNALIZATION),
                    queue_evaluators,
                    cert=cert,
                    token=token,
                )
            ]

            self._aggregate_future = asyncio.gather(*futures, return_exceptions=True)
            send_timeout_future = loop.create_task(send_timeout_message())
            self._allow_cancel.set()
            try:
                asyncio.get_event_loop().run_until_complete(self._aggregate_future)
                timeout_queue.put_nowait(None)
                asyncio.get_event_loop().run_until_complete(send_timeout_future)
            except asyncio.CancelledError:
                logger.debug("cancelled aggregate future")
            else:
                out_cloudevent = CloudEvent(
                    {
                        "type": identifiers.EVTYPE_ENSEMBLE_STOPPED,
                        "source": f"/ert/ee/{self._ee_id}/ensemble",
                        "id": str(uuid.uuid1()),
                    }
                )
                asyncio.get_event_loop().run_until_complete(
                    self.send_cloudevent(
                        dispatch_url, out_cloudevent, token=token, cert=cert
                    )
                )
        except Exception as e:
            logger.exception(
                "An exception occurred while starting the ensemble evaluation",
                exc_info=True,
            )
            out_cloudevent = CloudEvent(
                {
                    "type": identifiers.EVTYPE_ENSEMBLE_FAILED,
                    "source": f"/ert/ee/{self._ee_id}/ensemble",
                    "id": str(uuid.uuid1()),
                }
            )
            asyncio.get_event_loop().run_until_complete(
                self.send_cloudevent(
                    dispatch_url, out_cloudevent, token=token, cert=cert
                )
            )

    def is_cancellable(self):
        return True

    def cancel(self):
        threading.Thread(target=self._cancel).start()

    def _cancel(self):
        logger.debug("cancelling, waiting for wakeup...")
        self._allow_cancel.wait()
        logger.debug("got wakeup, killing all jobs...")
        self._job_queue.kill_all_jobs()
        logger.debug("cancelling futures...")
        if self._aggregate_future.cancelled():
            logger.debug("future was already cancelled")
            return

        self._aggregate_future.cancel()
        logger.debug("cancelled")

        out_cloudevent = CloudEvent(
            {
                "type": identifiers.EVTYPE_ENSEMBLE_CANCELLED,
                "source": f"/ert/ee/{self._ee_id}/ensemble",
                "id": str(uuid.uuid1()),
            }
        )
        loop = asyncio.new_event_loop()
        loop.run_until_complete(
            self.send_cloudevent(
                self._config.dispatch_uri,
                out_cloudevent,
                token=self._config.token,
                cert=self._config.cert,
            )
        )
        loop.close()
