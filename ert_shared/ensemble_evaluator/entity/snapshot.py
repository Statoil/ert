import copy
import datetime
import typing
from collections import defaultdict
from typing import Dict, Optional, Any

import pyrsistent
from dateutil.parser import parse
from pydantic import BaseModel

from ert_shared.ensemble_evaluator.entity import identifiers as ids
from ert_shared.ensemble_evaluator.entity.tool import (
    get_job_id,
    get_real_id,
    get_step_id,
    recursive_update,
)
from ert_shared.status.entity import state


class UnsupportedOperationException(ValueError):
    pass


_FM_TYPE_EVENT_TO_STATUS = {
    ids.EVTYPE_FM_STEP_WAITING: state.STEP_STATE_WAITING,
    ids.EVTYPE_FM_STEP_PENDING: state.STEP_STATE_PENDING,
    ids.EVTYPE_FM_STEP_RUNNING: state.STEP_STATE_PENDING,
    ids.EVTYPE_FM_STEP_FAILURE: state.STEP_STATE_FAILURE,
    ids.EVTYPE_FM_STEP_SUCCESS: state.STEP_STATE_SUCCESS,
    ids.EVTYPE_FM_STEP_UNKNOWN: state.STEP_STATE_UNKNOWN,
    ids.EVTYPE_FM_STEP_TIMEOUT: state.STEP_STATE_FAILURE,
    ids.EVTYPE_FM_JOB_START: state.JOB_STATE_START,
    ids.EVTYPE_FM_JOB_RUNNING: state.JOB_STATE_RUNNING,
    ids.EVTYPE_FM_JOB_SUCCESS: state.JOB_STATE_FINISHED,
    ids.EVTYPE_FM_JOB_FAILURE: state.JOB_STATE_FAILURE,
}

_ENSEMBLE_TYPE_EVENT_TO_STATUS = {
    ids.EVTYPE_ENSEMBLE_STARTED: state.ENSEMBLE_STATE_STARTED,
    ids.EVTYPE_ENSEMBLE_STOPPED: state.ENSEMBLE_STATE_STOPPED,
    ids.EVTYPE_ENSEMBLE_CANCELLED: state.ENSEMBLE_STATE_CANCELLED,
    ids.EVTYPE_ENSEMBLE_FAILED: state.ENSEMBLE_STATE_FAILED,
}

_STEP_STATE_TO_REALIZATION_STATE = {
    state.STEP_STATE_WAITING: state.REALIZATION_STATE_WAITING,
    state.STEP_STATE_PENDING: state.REALIZATION_STATE_PENDING,
    state.STEP_STATE_RUNNING: state.REALIZATION_STATE_RUNNING,
    state.STEP_STATE_UNKNOWN: state.REALIZATION_STATE_UNKNOWN,
    state.STEP_STATE_FAILURE: state.REALIZATION_STATE_FAILED,
}


def convert_iso8601_to_datetime(timestamp):
    if isinstance(timestamp, datetime.datetime):
        return timestamp

    return parse(timestamp)


class PartialSnapshot:
    def __init__(self, snapshot):
        """Create a PartialSnapshot. If no snapshot is provided, the object is
        a immutable POD, and any attempt at mutating it will raise an
        UnsupportedOperationException."""
        self._data = pyrsistent.m()
        self._snapshot = copy.copy(snapshot) if snapshot else None

    def update_status(self, status):
        self._apply_update(SnapshotDict(status=status))

    def update_real(
        self,
        real_id,
        real,
    ):
        self._apply_update(SnapshotDict(reals={real_id: real}))

    def _apply_update(self, update):
        if self._snapshot is None:
            raise UnsupportedOperationException(
                f"trying to mutate {self.__class__} without providing a snapshot is not supported"
            )
        dictionary = update.dict(
            exclude_unset=True, exclude_none=True, exclude_defaults=True
        )
        self._data = recursive_update(self._data, dictionary, check_key=False)
        self._snapshot.merge(dictionary)

    def update_step(self, real_id, step_id, step):
        self._apply_update(
            SnapshotDict(reals={real_id: Realization(steps={step_id: step})})
        )
        if self._snapshot.get_real(real_id).status != state.REALIZATION_STATE_FAILED:
            if step.status in _STEP_STATE_TO_REALIZATION_STATE:
                self.update_real(
                    real_id,
                    Realization(status=_STEP_STATE_TO_REALIZATION_STATE[step.status]),
                )
            elif (
                step.status == state.REALIZATION_STATE_FINISHED
                and self._snapshot.all_steps_finished(real_id)
            ):
                self.update_real(
                    real_id, Realization(status=state.REALIZATION_STATE_FINISHED)
                )
            elif (
                step.status == state.STEP_STATE_SUCCESS
                and not self._snapshot.all_steps_finished(real_id)
            ):
                pass
            else:
                raise ValueError(
                    f"unknown step status {step.status} for real: {real_id} step: {step_id}"
                )
        return self

    def update_job(
        self,
        real_id,
        step_id,
        job_id,
        job,
    ):
        self._apply_update(
            SnapshotDict(
                reals={real_id: Realization(steps={step_id: Step(jobs={job_id: job})})}
            )
        )

    def to_dict(self):
        return pyrsistent.thaw(self._data)

    def data(self):
        return self._data

    def from_cloudevent(self, event):
        e_type = event["type"]
        e_source = event["source"]
        status = _FM_TYPE_EVENT_TO_STATUS.get(e_type)
        timestamp = event["time"]

        if e_type in ids.EVGROUP_FM_STEP:
            start_time = None
            end_time = None

            if e_type == ids.EVTYPE_FM_STEP_RUNNING:
                start_time = convert_iso8601_to_datetime(timestamp)
            elif e_type in {ids.EVTYPE_FM_STEP_SUCCESS, ids.EVTYPE_FM_STEP_FAILURE, ids.EVTYPE_FM_STEP_TIMEOUT}:
                end_time = convert_iso8601_to_datetime(timestamp)

            self.update_step(
                get_real_id(e_source),
                get_step_id(e_source),
                step=Step(
                    status=status,
                    start_time=start_time,
                    end_time=end_time,
                ),
            )

            if e_type == ids.EVTYPE_FM_STEP_TIMEOUT:
                step = self._snapshot.get_step(get_real_id(e_source), get_step_id(e_source))
                for job_id, job in step.jobs.items():
                    if job.status != state.JOB_STATE_FINISHED:
                        job_error = "The run is cancelled due to reaching MAX_RUNTIME"
                        self.update_job(
                            get_real_id(e_source),
                            get_step_id(e_source),
                            job_id,
                            job=Job(
                                status=state.JOB_STATE_FAILURE,
                                error=job_error
                            ),
                        )

        elif e_type in ids.EVGROUP_FM_JOB:
            start_time = None
            end_time = None
            if e_type == ids.EVTYPE_FM_JOB_START:
                start_time = convert_iso8601_to_datetime(timestamp)
            elif e_type in {ids.EVTYPE_FM_JOB_SUCCESS, ids.EVTYPE_FM_JOB_FAILURE}:
                end_time = convert_iso8601_to_datetime(timestamp)

            self.update_job(
                get_real_id(e_source),
                get_step_id(e_source),
                get_job_id(e_source),
                job=Job(
                    status=status,
                    start_time=start_time,
                    end_time=end_time,
                    data=event.data if e_type == ids.EVTYPE_FM_JOB_RUNNING else None,
                    stdout=event.data.get(ids.STDOUT)
                    if e_type == ids.EVTYPE_FM_JOB_START
                    else None,
                    stderr=event.data.get(ids.STDERR)
                    if e_type == ids.EVTYPE_FM_JOB_START
                    else None,
                    error=event.data.get(ids.ERROR_MSG)
                    if e_type == ids.EVTYPE_FM_JOB_FAILURE
                    else None,
                ),
            )
        elif e_type in ids.EVGROUP_ENSEMBLE:
            self.update_status(_ENSEMBLE_TYPE_EVENT_TO_STATUS[e_type])
        elif e_type == ids.EVTYPE_EE_SNAPSHOT_UPDATE:
            self._data = recursive_update(self._data, event.data, check_key=False)
        else:
            raise ValueError("Unknown type: {}".format(e_type))
        return self


class Snapshot:
    def __init__(self, input_dict):
        self._data = pyrsistent.freeze(input_dict)

    def merge_event(self, event):
        self._data = recursive_update(self._data, event.data())

    def merge(self, update):
        self._data = recursive_update(self._data, update)

    def to_dict(self):
        return pyrsistent.thaw(self._data)

    def get_status(self):
        return self._data[ids.STATUS]

    def get_reals(self):
        return SnapshotDict(**self._data).reals

    def get_real(self, real_id):
        if real_id not in self._data[ids.REALS]:
            raise ValueError(f"No realization with id {real_id}")
        return Realization(**self._data[ids.REALS][real_id])

    def get_step(self, real_id, step_id):
        real = self.get_real(real_id)
        steps = real.steps
        if step_id not in steps:
            raise ValueError(f"No step with id {step_id} in {real_id}")
        return steps[step_id]

    def get_job(self, real_id, step_id, job_id):
        step = self.get_step(real_id, step_id)
        jobs = step.jobs
        if job_id not in jobs:
            raise ValueError(f"No job with id {job_id} in {step_id}")
        return jobs[job_id]

    def all_steps_finished(self, real_id):
        real = self.get_real(real_id)
        return all(
            step.status == state.STEP_STATE_SUCCESS for step in real.steps.values()
        )

    def get_successful_realizations(self):
        return len(
            [
                real
                for real in self._data[ids.REALS].values()
                if real[ids.STATUS] == state.REALIZATION_STATE_FINISHED
            ]
        )

    def aggregate_real_states(self) -> typing.Dict[str, int]:
        states: Dict[str, int] = defaultdict(int)
        for real in self._data[ids.REALS].values():
            states[real[ids.STATUS]] += 1
        return states


class Job(BaseModel):
    status: Optional[str]
    start_time: Optional[datetime.datetime]
    end_time: Optional[datetime.datetime]
    data: Optional[Dict]
    name: Optional[str]
    error: Optional[str]
    stdout: Optional[str]
    stderr: Optional[str]


class Step(BaseModel):
    status: Optional[str]
    start_time: Optional[datetime.datetime]
    end_time: Optional[datetime.datetime]
    jobs: Optional[Dict[str, Job]] = {}


class Realization(BaseModel):
    status: Optional[str]
    active: Optional[bool]
    start_time: Optional[datetime.datetime]
    end_time: Optional[datetime.datetime]
    steps: Optional[Dict[str, Step]] = {}


class SnapshotDict(BaseModel):
    status: Optional[str]
    reals: Optional[Dict[str, Realization]] = {}
    metadata: Optional[Dict[str, Any]]


class SnapshotBuilder(BaseModel):
    steps: Dict[str, Step] = {}
    metadata: Dict[str, Any] = {}

    def build(self, real_ids, status, start_time=None, end_time=None):
        top = SnapshotDict(status=status, metadata=self.metadata)
        for r_id in real_ids:
            top.reals[r_id] = Realization(
                active=True,
                steps=self.steps,
                start_time=start_time,
                end_time=end_time,
                status=status,
            )
        return Snapshot(top.dict())

    def add_step(self, step_id, status, start_time=None, end_time=None):
        self.steps[step_id] = Step(
            status=status, start_time=start_time, end_time=end_time
        )
        return self

    def add_job(
        self,
        step_id,
        job_id,
        name,
        status,
        data,
        start_time=None,
        end_time=None,
        stdout=None,
        stderr=None,
    ):
        step = self.steps[step_id]
        step.jobs[job_id] = Job(
            status=status,
            data=data,
            start_time=start_time,
            end_time=end_time,
            name=name,
            stdout=stdout,
            stderr=stderr,
        )
        return self

    def add_metadata(self, key, value):
        self.metadata[key] = value
        return self
