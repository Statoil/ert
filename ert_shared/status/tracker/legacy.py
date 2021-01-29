"""Track evaluations that does not use the Ensemble Evaluator.
Tracking happens cross-iteration, which means there's complexity pertaining to
job queues and run_context being created, and then abandoned by the experiment.

A FullSnapshotEvent will be emitted at the beginning of each iteration. Within
the life-span of an iteration, zero or more SnapshotUpdateEvent will be
emitted. A final EndEvent is emitted when the experiment is over.
"""

import logging
import time
import typing

from ert_shared.ensemble_evaluator.entity.snapshot import (
    PartialSnapshot,
    Snapshot,
    _SnapshotDict,
    _ForwardModel,
    _Job,
    _Realization,
    _Stage,
    _Step,
)
from ert_shared.models.base_run_model import BaseRunModel
from ert_shared.status.entity.event import (
    EndEvent,
    FullSnapshotEvent,
    SnapshotUpdateEvent,
)
from ert_shared.status.entity.state import (
    REALIZATION_STATE_UNKNOWN,
    queue_status_to_real_state,
)
from ert_shared.status.queue_diff import InconsistentIndicesError, QueueDiff
from res.enkf.ert_run_context import ErtRunContext
from res.job_queue.job_status_type_enum import JobStatusType

logger = logging.getLogger(__name__)

_THE_EMPTY_DETAILED_PROGRESS = ({}, -1)


class LegacyTracker:
    def __init__(
        self,
        model: BaseRunModel,
        general_interval: int,
        detailed_interval: int,
    ) -> None:
        self._model = model

        self._iter_snapshot = {}
        self._iter_differ = {}

        self._general_interval = general_interval
        self._detailed_interval = detailed_interval

    def track(self) -> None:
        tick = 0
        current_iter = -1
        while not self.is_finished():
            time.sleep(1)
            run_context = self._model.get_run_context()

            if run_context is None:
                logger.debug(f"no run_context at tick {tick}, sleeping...")
                continue

            iter_ = _get_run_context_iter(run_context)

            # If a new iteration is seen, an attempt at creating a full
            # snapshot event is made. If it can't be created, it is retried
            # until it can.
            # NOTE: there's not timeout for this operation.
            if current_iter != iter_:
                full_snapshot_event = self._full_snapshot_event(iter_)
                if full_snapshot_event is None:
                    logger.debug(
                        f"no full_snapshot_event on new iter {iter_} (current {current_iter}), sleeping at tick {tick}"
                    )
                    continue
                yield full_snapshot_event
                current_iter = iter_

            self._set_iter_differ(iter_)
            if self._general_interval > 0 and (tick % self._general_interval == 0):
                yield self._partial_snapshot_event(iter_)
            if self._detailed_interval > 0 and (tick % self._detailed_interval == 0):
                yield self._partial_snapshot_event(iter_, read_from_disk=True)
            tick += 1

        yield from self._retroactive_update_event()
        yield self._end_event()

    def _create_snapshot_dict(
        self,
        run_context: ErtRunContext,
        detailed_progress: typing.Tuple[typing.Dict, int],
        iter_: int,
    ) -> typing.Optional[_SnapshotDict]:
        """create a snapshot of a run_context and detailed_progress.
        detailed_progress is expected to be a tuple of a realization_progress
        dict and iteration number. iter_ represents the current assimilation
        cycle."""
        self._set_iter_differ(iter_)

        snapshot = _SnapshotDict(
            status=REALIZATION_STATE_UNKNOWN,
            reals={},
            metadata={"iter": iter_},
            # TODO: populate step defs
            forward_model=_ForwardModel(step_definitions={}),
        )

        forward_model = self._model.get_forward_model()

        real_prog, dp_iter_ = detailed_progress
        if dp_iter_ != iter_:
            logger.debug(
                f"run_context iter ({iter_}) and detailed_progress ({dp_iter_} iter differed"
            )
            real_prog = {}

        enumerated = 0
        for iens, run_arg in _enumerate_from_volatile(run_context):
            real_id = str(iens)
            enumerated += 1
            if not _is_iens_active(iens, run_context):
                continue

            status = JobStatusType.JOB_QUEUE_UNKNOWN
            try:
                # will throw if not yet submitted (is in a limbo state)
                queue_index = run_arg.getQueueIndex()
                if self._model._job_queue:
                    status = self._model._job_queue.getJobStatus(queue_index)
            except ValueError:
                logger.debug(f"iens {iens} was in a limbo state")

            snapshot.reals[real_id] = _Realization(
                status=queue_status_to_real_state(status), active=True, stages={}
            )

            step = _Step(status="", jobs={})
            snapshot.reals[real_id].stages["0"] = _Stage(status="", steps={"0": step})

            for index in range(0, len(forward_model)):
                ext_job = forward_model.iget_job(index)
                step.jobs[str(index)] = _Job(
                    name=ext_job.name(), status="Unknown", data={}
                )

            progress = real_prog[iter_].get(iens, None)
            if not progress:
                continue

            jobs = progress[0]
            for idx, fm in enumerate(jobs):
                job = step.jobs[str(idx)]

                # FIXME: parse these as iso date
                job.start_time = str(fm.start_time)
                job.end_time = str(fm.end_time)
                job.name = fm.name
                job.status = fm.status
                job.error = fm.error
                job.stdout = fm.std_out_file
                job.stderr = fm.std_err_file
                job.data = {
                    "current_memory_usage": fm.current_memory_usage,
                    "max_memory_usage": fm.max_memory_usage,
                }

        if enumerated == 0:
            logger.debug("enumerated 0 items from run_context, it is gone")
            return None

        return snapshot

    def _retroactive_update_event(self):
        """Return generator producing update events for all queues that has run
        thus far."""
        for iter_ in self._iter_differ:
            partial = self._create_partial_snapshot(None, ({}, -1), iter_)

            if partial is not None:
                self._set_iter_snapshot(iter_, partial._snapshot)

            yield SnapshotUpdateEvent(
                phase_name=self._model.getPhaseName(),
                current_phase=self._model.currentPhase(),
                total_phases=self._model.phaseCount(),
                indeterminate=self._model.isIndeterminate(),
                progress=self._progress(),
                iteration=iter_,
                partial_snapshot=partial,
            )

    def _progress(self) -> float:
        return self._model.currentPhase() / self._model.phaseCount()

    def _set_iter_differ(self, iter_: int) -> None:
        """Make an attempt at creating a differ for iter_ should it be zero or
        higher."""
        if iter_ < 0:
            return
        if iter_ not in self._iter_differ:
            self._iter_differ[iter_] = None

        if self._iter_differ[iter_] is None and self._model._job_queue is not None:
            self._iter_differ[iter_] = QueueDiff(self._model._job_queue)

    def _set_iter_snapshot(self, iter_, snapshot: typing.Optional[Snapshot]) -> None:
        if iter_ < 0:
            return
        assert snapshot is not None
        self._iter_snapshot[iter_] = snapshot

    def _create_partial_snapshot(
        self,
        run_context: ErtRunContext,
        detailed_progress: typing.Tuple[typing.Dict, int],
        iter_: int,
    ) -> typing.Optional[PartialSnapshot]:
        """Create a PartialSnapshot, or None if the sources of data were
        destroyed or had not been created yet. Both run_context and
        detailed_progress needs to be aligned with the stars if job status etc
        is to be produced."""
        differ = self._iter_differ.get(iter_, None)
        if differ is None:
            return None
        try:
            changes = differ.changes_after_transition()
        except InconsistentIndicesError:
            return None

        snapshot = self._iter_snapshot.get(iter_, None)
        if snapshot is None:
            return None

        partial = PartialSnapshot(snapshot)
        for iens, change in changes.items():
            change_enum = JobStatusType.from_string(change)
            partial.update_real(
                str(iens), status=queue_status_to_real_state(change_enum)
            )

        detailed_progress, dp_iter_ = detailed_progress
        if not detailed_progress:
            logger.debug(f"partial: no detailed progress for iter:{iter_}")
            return partial
        if iter_ != dp_iter_:
            logger.debug(
                f"partial: detailed_progress iter ({dp_iter_}) differed from run_context ({iter_})"
            )

        for iens, _ in _enumerate_from_volatile(run_context):
            real_id = str(iens)
            if not _is_iens_active(iens, run_context):
                continue

            progress = detailed_progress[iter_].get(iens, None)
            if not progress:
                continue

            jobs = progress[0]
            for idx, fm in enumerate(jobs):
                partial.update_job(
                    real_id,
                    "0",
                    "0",
                    str(idx),
                    status=fm.status,
                    start_time=str(fm.start_time),
                    end_time=str(fm.end_time),
                    data={
                        "current_memory_usage": fm.current_memory_usage,
                        "max_memory_usage": fm.max_memory_usage,
                    },
                )

        return partial

    def _partial_snapshot_event(
        self, iter_, read_from_disk=False
    ) -> SnapshotUpdateEvent:
        """Return a SnapshotUpdateEvent. If read_from_disk is set, this method
        will ultimately read status.json files from disk in order to create an
        event."""
        run_context = self._model.get_run_context()
        detailed_progress = (
            self._model.getDetailedProgress()
            if read_from_disk
            else _THE_EMPTY_DETAILED_PROGRESS
        )
        partial = self._create_partial_snapshot(run_context, detailed_progress, iter_)
        if partial is not None:
            self._set_iter_snapshot(iter_, partial._snapshot)

        return SnapshotUpdateEvent(
            phase_name=self._model.getPhaseName(),
            current_phase=self._model.currentPhase(),
            total_phases=self._model.phaseCount(),
            indeterminate=self._model.isIndeterminate(),
            progress=self._progress(),
            iteration=iter_,
            partial_snapshot=partial,
        )

    def _full_snapshot_event(self, iter_) -> typing.Optional[FullSnapshotEvent]:
        """Return a FullSnapshotEvent if it was possible to create a snapshot.
        Return None if not, indicating that there should be no event."""
        run_context = self._model.get_run_context()
        detailed_progress = self._model.getDetailedProgress()
        if detailed_progress == _THE_EMPTY_DETAILED_PROGRESS:
            return None
        snapshot_dict = self._create_snapshot_dict(
            run_context, detailed_progress, iter_
        )
        if not snapshot_dict:
            return None

        snapshot = Snapshot(snapshot_dict.dict())

        self._set_iter_snapshot(iter_, snapshot)

        return FullSnapshotEvent(
            phase_name=self._model.getPhaseName(),
            current_phase=self._model.currentPhase(),
            total_phases=self._model.phaseCount(),
            indeterminate=self._model.isIndeterminate(),
            progress=self._progress(),
            iteration=iter_,
            # TODO: should be snapshot instead of dict
            snapshot=snapshot_dict,
        )

    def _end_event(self) -> EndEvent:
        return EndEvent(
            failed=self._model.hasRunFailed(), failed_msg=self._model.getFailMessage()
        )

    def is_finished(self) -> bool:
        return self._model.isFinished()

    def request_termination(self) -> None:
        return self._model.killAllSimulations()

    def reset(self):
        self._iter_differ = {}
        self._iter_snapshot = {}


def _get_run_context_iter(run_context: ErtRunContext) -> int:
    """Return the iter from run_context."""
    try:
        return run_context.get_iter()
    except AttributeError:
        return -1


def _is_iens_active(iens: int, run_context: ErtRunContext) -> bool:
    """Return whether or not the iens is active."""
    try:
        return run_context.is_active(iens)
    except AttributeError:
        return False


def _enumerate_from_volatile(run_context: ErtRunContext) -> typing.Iterable:
    """Return an iterable that's either (iens, run_arg) or empty."""
    try:
        yield from enumerate(run_context)
    except TypeError:
        yield from ()