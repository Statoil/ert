# -*- coding: utf-8 -*-
import sys
from datetime import datetime

from colors import color as ansi_color
from console_progressbar import ProgressBar
from ert_shared.ensemble_evaluator.entity.snapshot import Snapshot
from ert_shared.status.entity.event import (
    EndEvent,
    FullSnapshotEvent,
    SnapshotUpdateEvent,
)
from ert_shared.status.entity.state import (
    ALL_REALIZATION_STATES,
    COLOR_FAILED,
    COLOR_FINISHED,
    REAL_STATE_TO_COLOR,
)
from ert_shared.status.utils import format_running_time
from ert_shared.status.entity import state

def _ansi_color(*args, **kwargs):
    # This wraps ansi_color such that when _ansi_color is bound to Monitor,
    # all arguments are passed to ansi_color except the instance (self).
    return ansi_color(*args[1:], **kwargs)


class Monitor:
    """Class for tracking and outputting the progress of a simulation @model,
    where progress is defined as a combination of fields on @tracker.

    Progress is printed to @out. @color_always decides whether or not coloring
    always should take place, i.e. even if @out does not support it.
    """

    dot = "■ "
    empty_bar_char = " "
    filled_bar_char = "█"
    bar_length = 30

    _colorize = _ansi_color

    def __init__(self, out=sys.stdout, color_always=False):
        self._out = out
        self._snapshots = {}
        self._start_time = None
        # If out is not (like) a tty, disable colors.
        if not out.isatty() and not color_always:
            self._colorize = self._no_color

            # The dot adds no value without color, so remove it.
            self.dot = ""

    def _no_color(self, *args, **kwargs):
        """Alternate color method when no coloring is wanted. Conforms to the
        signature of ansi_color.color, wherein the first positional argument
        is the string to be (un-)colored."""
        return args[0]

    def monitor(self, tracker):
        self._start_time = datetime.now()
        for event in tracker.track():
            if isinstance(event, FullSnapshotEvent):
                if event.snapshot is not None:
                    self._snapshots[event.iteration] = event.snapshot
                self._progress = event.progress
            elif isinstance(event, SnapshotUpdateEvent):
                if event.partial_snapshot is not None:
                    self._snapshots[event.iteration].merge_event(event.partial_snapshot)
                self._print_progress(event)
            if isinstance(event, EndEvent):
                self._print_result(event.failed, event.failed_msg)
                self._print_job_errors()
                return

    def _print_job_errors(self):
        for snapshot_id, snapshot in self._snapshots.items():
            for real_id, real in snapshot.get_reals().items():
                for step_id, step in real.steps.items():
                    for job_id, job in step.jobs.items():
                        if job.status == state.JOB_STATE_FAILURE:
                            print(f"Job failure in iteration: {snapshot_id} real: {real_id} "
                                  f"step: {step_id} job: {job_id} name: {job.name}\nError: {job.error}")


    def _get_legends(self) -> str:
        statuses = ""
        latest_snapshot = self._snapshots[max(self._snapshots.keys())]
        total_count = len(latest_snapshot.get_reals())
        aggregate = latest_snapshot.aggregate_real_states()
        for state in ALL_REALIZATION_STATES:
            count = 0
            if state in aggregate:
                count = aggregate[state]

            out = "{}{:10} {:>10}".format(
                self._colorize(self.dot, fg=REAL_STATE_TO_COLOR[state]),
                state,
                "{}/{}".format(count, total_count),
            )
            statuses += "    {}\n".format(out)
        return statuses

    def _print_result(self, failed, failed_message):
        if failed:
            msg = "Simulations failed with the following error: {}".format(
                failed_message
            )
            print(self._colorize(msg, fg=COLOR_FAILED), file=self._out)
        else:
            print(
                self._colorize("Simulations completed.", fg=COLOR_FINISHED),
                file=self._out,
            )

    def _print_progress(self, event):
        """Print a progress based on the information on a GeneralEvent."""
        if event.indeterminate:
            # indeterminate, no progress to be shown
            return

        prefix = """
    --> {phase_name}

    {current_phase}/{target}""".format(
            phase_name=event.phase_name,
            current_phase=min(event.total_phases, event.current_phase + 1),
            target=event.total_phases,
        )

        elapsed = datetime.now() - self._start_time

        statuses = self._get_legends()
        suffix = """{runtime}

{statuses}""".format(
            statuses=statuses, runtime=format_running_time(elapsed.seconds)
        )

        pb = ProgressBar(
            total=100,
            prefix=prefix,
            suffix=suffix,
            decimals=0,
            length=self.bar_length,
            fill=self.filled_bar_char,
            zfill=self.empty_bar_char,
            file=self._out,
        )
        pb.print_progress_bar(event.progress * 100)
