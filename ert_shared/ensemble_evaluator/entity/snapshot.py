from pydantic import BaseModel
from typing import Dict, List, Optional, Any
import logging
from ert_shared.ensemble_evaluator.entity import identifiers as ids
from ert_shared.ensemble_evaluator.entity.tool import (
    recursive_update,
    get_real_id,
    get_stage_id,
    get_step_id,
    get_job_id,
)
from pyrsistent import freeze, thaw

# Taken from ert_shared/tracker/base.py
_FM_TYPE_EVENT_TO_STATUS = {
    ids.EVTYPE_FM_STAGE_WAITING: "Waiting",
    ids.EVTYPE_FM_STAGE_PENDING: "Pending",
    ids.EVTYPE_FM_STAGE_RUNNING: "Running",
    ids.EVTYPE_FM_STAGE_FAILURE: "Failed",
    ids.EVTYPE_FM_STAGE_SUCCESS: "Finished",
    ids.EVTYPE_FM_STAGE_UNKNOWN: "Unknown",
    ids.EVTYPE_FM_STEP_START: "Pending",
    ids.EVTYPE_FM_STEP_FAILURE: "Failed",
    ids.EVTYPE_FM_STEP_SUCCESS: "Finished",
    ids.EVTYPE_FM_JOB_START: "Pending",
    ids.EVTYPE_FM_JOB_RUNNING: "Running",
    ids.EVTYPE_FM_JOB_SUCCESS: "Finished",
    ids.EVTYPE_FM_JOB_FAILURE: "Failed",
}

_ENSEMBLE_TYPE_EVENT_TO_STATUS = {
    ids.EVTYPE_ENSEMBLE_STARTED: "Starting",
    ids.EVTYPE_ENSEMBLE_STOPPED: "Stopped",
    ids.EVTYPE_ENSEMBLE_CANCELLED: "Cancelled",
}

logger = logging.getLogger(__name__)


class PartialSnapshot:
    def __init__(self, snapshot):
        self._data = {"reals": {}}
        self._snapshot = snapshot

    def update_status(self, status):
        self._data["status"] = status

    def update_real(
        self, real_id, active=None, start_time=None, end_time=None, queue_state=None
    ):
        if real_id not in self._data["reals"]:
            self._data["reals"][real_id] = {"stages": {}}
        real = self._data["reals"][real_id]

        if active is not None:
            real["active"] = active
        if start_time is not None:
            real["start_time"] = start_time
        if end_time is not None:
            real["end_time"] = end_time
        if queue_state is not None:
            real["queue_state"] = queue_state
        return real

    def update_stage(
        self,
        real_id,
        stage_id,
        status=None,
        start_time=None,
        end_time=None,
    ):
        real = self.update_real(real_id)
        if stage_id not in real["stages"]:
            real["stages"][stage_id] = {"steps": {}}
        stage = real["stages"][stage_id]
        if status == "Finished":
            real_finished = True
            for snapshot_stage_id, snapshot_stage in self._snapshot.all_stages(
                real_id
            ).items():
                if snapshot_stage_id != stage_id:
                    if snapshot_stage["status"] != "Finished":
                        real_finished = False
                        break
            if real_finished:
                self.update_real(real_id, queue_state="JOB_QUEUE_SUCCESS")
        if status == "Failed":
            self.update_real(real_id, queue_state="JOB_QUEUE_FAILED")

        if status is not None:
            stage["status"] = status
        if start_time is not None:
            stage["start_time"] = start_time
        if end_time is not None:
            stage["end_time"] = end_time
        return stage

    def update_step(
        self, real_id, stage_id, step_id, status=None, start_time=None, end_time=None
    ):
        stage = self.update_stage(real_id, stage_id)
        if status == "Finished":
            stage_finished = True
            for snapshot_step_id, snapshot_step in self._snapshot.all_steps(
                real_id, stage_id
            ).items():
                if snapshot_step_id != step_id:
                    if snapshot_step["status"] != "Finished":
                        stage_finished = False
                        break
            if stage_finished:
                self.update_stage(real_id, stage_id, "Finished")
        elif status == "Failed":
            self.update_stage(real_id, stage_id, "Failed")

        if step_id not in stage["steps"]:
            stage["steps"][step_id] = {"jobs": {}}
        step = stage["steps"][step_id]

        if status is not None:
            step["status"] = status
        if start_time is not None:
            step["start_time"] = start_time
        if end_time is not None:
            step["end_time"] = end_time
        return step

    def update_job(
        self,
        real_id,
        stage_id,
        step_id,
        job_id,
        status=None,
        data=None,
        start_time=None,
        end_time=None,
        error=None,
    ):
        step = self.update_step(real_id, stage_id, step_id)
        if job_id not in step["jobs"]:
            step["jobs"][job_id] = {}
        job = step["jobs"][job_id]

        if status is not None:
            job["status"] = status
        if start_time is not None:
            job["start_time"] = start_time
        if end_time is not None:
            job["end_time"] = end_time

        if data is not None:
            if "data" not in job:
                job["data"] = {}
            job["data"].update(data)
        if error is not None:
            job["error"] = error

        return job

    def to_dict(self):
        return self._data

    def from_cloudevent(self, event):
        e_type = event["type"]
        e_source = event["source"]
        status = _FM_TYPE_EVENT_TO_STATUS.get(e_type)
        timestamp = event["time"]

        if e_type in ids.EVGROUP_FM_STAGE:
            self.update_stage(
                get_real_id(e_source),
                get_stage_id(e_source),
                status=status,
                start_time=timestamp if e_type == ids.EVTYPE_FM_STAGE_RUNNING else None,
                end_time=timestamp
                if e_type in {ids.EVTYPE_FM_STAGE_FAILURE, ids.EVTYPE_FM_STAGE_SUCCESS}
                else None,
            )
        elif e_type in ids.EVGROUP_FM_STEP:
            self.update_step(
                get_real_id(e_source),
                get_stage_id(e_source),
                get_step_id(e_source),
                status=status,
                start_time=timestamp if e_type == ids.EVTYPE_FM_STEP_START else None,
                end_time=timestamp
                if e_type
                in {
                    ids.EVTYPE_FM_STEP_SUCCESS,
                    ids.EVTYPE_FM_STEP_FAILURE,
                }
                else None,
            )

        elif e_type in ids.EVGROUP_FM_JOB:
            self.update_job(
                get_real_id(e_source),
                get_stage_id(e_source),
                get_step_id(e_source),
                get_job_id(e_source),
                status=status,
                start_time=timestamp if e_type == ids.EVTYPE_FM_JOB_START else None,
                end_time=timestamp
                if e_type
                in {
                    ids.EVTYPE_FM_JOB_SUCCESS,
                    ids.EVTYPE_FM_JOB_FAILURE,
                }
                else None,
                data=event.data if e_type == ids.EVTYPE_FM_JOB_RUNNING else None,
                error=event.data.get("stderr")
                if e_type == ids.EVTYPE_FM_JOB_FAILURE
                else None,
            )
            # TODO See what to do about logging
            # if status == "Failed":
            #     logger.error(event.data["stderr"])
            #     logger.info(event.data["stdout"])
            # if status == "Finished":
            #     logger.info(event.data["stdout"])
        elif e_type in ids.EVGROUP_ENSEMBLE:
            self.update_status(_ENSEMBLE_TYPE_EVENT_TO_STATUS[e_type])
        else:
            raise ValueError("Unknown type: {}".format(e_type))
        return self


class Snapshot:
    def __init__(self, input_dict):
        self._data = freeze(input_dict)

    def merge_event(self, event):
        self._data = recursive_update(self._data, freeze(event.to_dict()))

    def to_dict(self):
        return thaw(self._data)

    def get_status(self):
        return self._data["status"]

    def get_real(self, real_id):
        if real_id not in self._data["reals"]:
            raise ValueError(f"No realization with id {real_id}")
        return self._data["reals"][real_id]

    def get_stage(self, real_id, stage_id):
        real = self.get_real(real_id)
        stages = real["stages"]
        if stage_id not in stages:
            raise ValueError(f"No stage with id {stage_id} in {real_id}")
        return stages[stage_id]

    def get_step(self, real_id, stage_id, step_id):
        stage = self.get_stage(real_id, stage_id)
        steps = stage["steps"]
        if step_id not in steps:
            raise ValueError(f"No step with id {step_id} in {stage_id}")
        return steps[step_id]

    def get_job(self, real_id, stage_id, step_id, job_id):
        step = self.get_step(real_id, stage_id, step_id)
        jobs = step["jobs"]
        if job_id not in jobs:
            raise ValueError(f"No job with id {job_id} in {step_id}")
        return jobs[job_id]

    def all_stages(self, real_id):
        real = self.get_real(real_id)
        return real["stages"]

    def all_steps(self, real_id, stage_id):
        stage = self.get_stage(real_id, stage_id)
        return stage["steps"]

    def all_jobs_finished(self, real_id, stage_id, step_id):
        step = self.get_step(real_id, stage_id, step_id)
        jobs_finished = [job["status"] == "Finished" for job in step["jobs"].values()]
        return all(jobs_finished)

    def get_successful_realizations(self):
        return len(
            [
                real
                for real in self.to_dict()["reals"].values()
                if real["queue_state"] == "JOB_QUEUE_SUCCESS"
            ]
        )


class _JobDetails(BaseModel):
    job_id: str
    name: str


class _ForwardModel(BaseModel):
    step_definitions: Dict[str, Dict[str, List[_JobDetails]]]


class _Job(BaseModel):
    status: str
    start_time: Optional[str]
    end_time: Optional[str]
    data: Dict
    name: str
    error: Optional[str]
    stdout: Optional[str]
    stderr: Optional[str]


class _Step(BaseModel):
    status: str
    start_time: Optional[str]
    end_time: Optional[str]
    jobs: Dict[str, _Job] = {}


class _Stage(BaseModel):
    status: str
    start_time: Optional[str]
    end_time: Optional[str]
    steps: Dict[str, _Step] = {}


class _Realization(BaseModel):
    active: bool
    start_time: Optional[str]
    end_time: Optional[str]
    stages: Dict[str, _Stage] = {}
    queue_state: Optional[Any]


class _SnapshotDict(BaseModel):
    status: str
    reals: Dict[str, _Realization] = {}
    forward_model: _ForwardModel
    metadata: Dict[str, Any] = {}


class SnapshotBuilder(BaseModel):
    stages: Dict[str, _Stage] = {}
    forward_model: _ForwardModel = _ForwardModel(step_definitions={})
    metadata: Dict[str, Any] = {}

    def build(self, real_ids, status, start_time=None, end_time=None):
        top = _SnapshotDict(
            status=status, forward_model=self.forward_model, metadata=self.metadata
        )
        for r_id in real_ids:
            top.reals[r_id] = _Realization(
                active=True,
                stages=self.stages,
                start_time=start_time,
                end_time=end_time,
            )
        return Snapshot(top.dict())

    def add_stage(
        self, stage_id, status, start_time=None, end_time=None, queue_state=None
    ):
        self.stages[stage_id] = _Stage(
            status=status,
            start_time=start_time,
            end_time=end_time,
            queue_state=queue_state,
        )
        return self

    def add_step(self, stage_id, step_id, status, start_time=None, end_time=None):
        stage = self.stages[stage_id]
        stage.steps[step_id] = _Step(
            status=status, start_time=start_time, end_time=end_time
        )
        return self

    def add_job(
        self,
        stage_id,
        step_id,
        job_id,
        name,
        status,
        data,
        start_time=None,
        end_time=None,
    ):
        stage = self.stages[stage_id]
        step = stage.steps[step_id]
        step.jobs[job_id] = _Job(
            status=status,
            data=data,
            start_time=start_time,
            end_time=end_time,
            name=name,
        )
        if stage_id not in self.forward_model.step_definitions:
            self.forward_model.step_definitions[stage_id] = {}
        if step_id not in self.forward_model.step_definitions[stage_id]:
            self.forward_model.step_definitions[stage_id][step_id] = []
        self.forward_model.step_definitions[stage_id][step_id].append(
            _JobDetails(job_id=job_id, name=name)
        )
        return self

    def add_metadata(self, key, value):
        self.metadata[key] = value
        return self
