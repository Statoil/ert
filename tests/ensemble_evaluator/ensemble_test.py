import threading

from cloudevents.http import CloudEvent, to_json

from ert_shared.ensemble_evaluator.client import Client
from ert_shared.ensemble_evaluator.entity import identifiers as identifiers
from ert_shared.ensemble_evaluator.entity.ensemble import _Realization, _Step, _BaseJob
from ert_shared.ensemble_evaluator.entity.ensemble_base import _Ensemble


def send_dispatch_event(client, event_type, source, event_id, data, **extra_attrs):
    event1 = CloudEvent(
        {"type": event_type, "source": source, "id": event_id, **extra_attrs}, data
    )
    client.send(to_json(event1))


class TestEnsemble(_Ensemble):
    def __init__(self, iter, reals, steps, jobs):
        self.iter = iter
        self.reals = reals
        self.steps = steps
        self.jobs = jobs
        self.fail_jobs = []
        self.result = None
        self.result_datacontenttype = None

        the_reals = [
            _Realization(
                real_no,
                steps=[
                    _Step(
                        id_=step_no,
                        inputs=[],
                        outputs=[],
                        jobs=[
                            _BaseJob(id_=job_no, name=f"job-{job_no}", step_source="")
                            for job_no in range(0, jobs)
                        ],
                        name=f"step-{step_no}",
                        ee_url="",
                        source="",
                    )
                    for step_no in range(0, steps)
                ],
                active=True,
            )
            for real_no in range(0, reals)
        ]
        super().__init__(the_reals, {})

    def _evaluate(self, url, ee_id):
        event_id = 0
        with Client(url + "/dispatch") as dispatch:
            send_dispatch_event(
                dispatch,
                identifiers.EVTYPE_ENSEMBLE_STARTED,
                f"/ert/ee/{ee_id}",
                f"event-{event_id}",
                None,
            )
            event_id = event_id + 1
            for real in range(0, self.reals):
                for step in range(0, self.steps):
                    job_failed = False
                    send_dispatch_event(
                        dispatch,
                        identifiers.EVTYPE_FM_STEP_UNKNOWN,
                        f"/ert/ee/{ee_id}/real/{real}/step/{step}",
                        f"event-{event_id}",
                        None,
                    )
                    event_id = event_id + 1
                    for job in range(0, self.jobs):
                        send_dispatch_event(
                            dispatch,
                            identifiers.EVTYPE_FM_JOB_RUNNING,
                            f"/ert/ee/{ee_id}/real/{real}/step/{step}/job/{job}",
                            f"event-{event_id}",
                            {"current_memory_usage": 1000},
                        )
                        event_id = event_id + 1
                        if self._shouldFailJob(real, step, job):
                            send_dispatch_event(
                                dispatch,
                                identifiers.EVTYPE_FM_JOB_FAILURE,
                                f"/ert/ee/{ee_id}/real/{real}/step/{step}/job/{job}",
                                f"event-{event_id}",
                                {},
                            )
                            event_id = event_id + 1
                            job_failed = True
                            break
                        else:
                            send_dispatch_event(
                                dispatch,
                                identifiers.EVTYPE_FM_JOB_SUCCESS,
                                f"/ert/ee/{ee_id}/real/{real}/step/{step}/job/{job}",
                                f"event-{event_id}",
                                {"current_memory_usage": 1000},
                            )
                            event_id = event_id + 1
                    if job_failed:
                        send_dispatch_event(
                            dispatch,
                            identifiers.EVTYPE_FM_STEP_FAILURE,
                            f"/ert/ee/{ee_id}/real/{real}/step/{step}/job/{job}",
                            f"event-{event_id}",
                            {},
                        )
                        event_id = event_id + 1
                    else:
                        send_dispatch_event(
                            dispatch,
                            identifiers.EVTYPE_FM_STEP_SUCCESS,
                            f"/ert/ee/{ee_id}/real/{real}/step/{step}/job/{job}",
                            f"event-{event_id}",
                            {},
                        )
                        event_id = event_id + 1

            data = self.result if self.result else None
            extra_attrs = {}
            if self.result_datacontenttype:
                extra_attrs["datacontenttype"] = self.result_datacontenttype
            send_dispatch_event(
                dispatch,
                identifiers.EVTYPE_ENSEMBLE_STOPPED,
                f"/ert/ee/{ee_id}",
                f"event-{event_id}",
                data,
                **extra_attrs,
            )

    def join(self):
        self._eval_thread.join()

    def evaluate(self, config, ee_id):
        self._eval_thread = threading.Thread(
            target=self._evaluate,
            args=(config.dispatch_uri, ee_id),
            name="TestEnseble",
        )

    def start(self):
        self._eval_thread.start()

    def _shouldFailJob(self, real, step, job):
        return (real, 0, step, job) in self.fail_jobs

    def addFailJob(self, real, step, job):
        self.fail_jobs.append((real, 0, step, job))

    def with_result(self, result, datacontenttype):
        self.result = result
        self.result_datacontenttype = datacontenttype
        return self
