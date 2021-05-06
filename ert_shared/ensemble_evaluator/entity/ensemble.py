import copy
import logging
import pickle
from typing import Dict, List, Tuple, Optional, Iterator, Type
from collections import defaultdict
from graphlib import TopologicalSorter
from pathlib import Path

from ert_shared.ensemble_evaluator.entity.ensemble_base import _Ensemble
from ert_shared.ensemble_evaluator.entity.ensemble_legacy import _LegacyEnsemble
from ert_shared.ensemble_evaluator.entity.function_step import FunctionTask
from ert_shared.ensemble_evaluator.entity.unix_step import UnixTask
from res.enkf import EnKFState, RunArg

logger = logging.getLogger(__name__)


def _sort_steps(steps: List["_Step"]) -> Tuple[str, ...]:
    """Return a tuple comprised by step names in the order they should be
    executed."""
    graph = defaultdict(set)
    if len(steps) == 1:
        return (steps[0].get_name(),)
    edged_nodes = set()
    for step in steps:
        for other in steps:
            if step == other:
                continue
            step_outputs = set([io.get_name() for io in step.get_outputs()])
            other_inputs = set([io.get_name() for io in other.get_inputs()])
            if len(step_outputs) > 0 and not step_outputs.isdisjoint(other_inputs):
                graph[other.get_name()].add(step.get_name())
                edged_nodes.add(step.get_name())
                edged_nodes.add(other.get_name())

    isolated_nodes = set([step.get_name() for step in steps]) - edged_nodes
    for node in isolated_nodes:
        graph[node] = set()

    ts = TopologicalSorter(graph)
    return tuple(ts.static_order())


class _IO:
    def __init__(self, name):
        if not name:
            raise ValueError(f"{self} needs name")
        self._name = name

    def get_name(self):
        return self._name


class _IOBuilder:
    _concrete_cls: Optional[Type[_IO]] = None

    def __init__(self):
        self._name = None

    def set_name(self, name):
        self._name = name
        return self

    def build(self):
        if self._concrete_cls is None:
            raise TypeError("cannot build _IO")
        return self._concrete_cls(self._name)


class _DummyIO(_IO):
    pass


class _DummyIOBuilder(_IOBuilder):
    _concrete_cls = _DummyIO

    def build(self):
        super().set_name("dummy i/o")
        return super().build()


class _FileIO(_IO):
    def __init__(self, name: str, path: Path, mime: str) -> None:
        super().__init__(name)
        self._path = path
        self._mime = mime

    def get_path(self):
        return self._path

    def get_mime(self):
        return self._mime

    def is_executable(self):
        return isinstance(self, _ExecIO)


class _ExecIO(_FileIO):
    pass


class _FileIOBuilder(_IOBuilder):
    def __init__(self) -> None:
        super().__init__()
        self._path: Optional[Path] = None
        self._mime: Optional[str] = None
        self._cls = _FileIO

    def set_path(self, path: Path) -> "_FileIOBuilder":
        self._path = path
        return self

    def set_mime(self, mime: str) -> "_FileIOBuilder":
        self._mime = mime
        return self

    def set_executable(self) -> "_FileIOBuilder":
        self._cls = _ExecIO
        return self

    def build(self):
        if not self._mime:
            raise ValueError(f"FileIO {self._name} needs mime")
        return self._cls(self._name, self._path, self._mime)


def create_file_io_builder() -> _FileIOBuilder:
    return _FileIOBuilder()


class _Input(_IO):
    pass


class _InputBuilder(_IOBuilder):
    _concrete_cls = _Input


def create_input_builder():
    return _InputBuilder()


class _Output(_IO):
    pass


class _OutputBuilder(_IOBuilder):
    _concrete_cls = _Output


def create_output_builder():
    return _OutputBuilder()


class _BaseJobBuilder:
    def __init__(self):
        self._id = None
        self._name = None

    def reset(self):
        self._id = None
        self._name = None
        return self

    def set_id(self, id_):
        self._id = id_
        return self

    def set_name(self, name):
        self._name = name
        return self

    def build(_):
        raise NotImplementedError("cannot build basejob")


class _JobBuilder(_BaseJobBuilder):
    def __init__(self):
        super().__init__()
        self._executable = None
        self._args = None
        self._step_source = None

    def set_step_source(self, source):
        self._step_source = source
        return self

    def set_executable(self, executable):
        self._executable = executable
        return self

    def set_args(self, args):
        self._args = args
        return self

    def build(self):
        try:
            cmd_is_callable = callable(pickle.loads(self._executable))
        except TypeError:
            cmd_is_callable = False
        if cmd_is_callable:
            if self._args is not None:
                raise ValueError(
                    "callable executable does not take args, use inputs instead"
                )
            return _FunctionJob(
                self._id, self._name, self._step_source, self._executable
            )
        return _UnixJob(
            self._id, self._name, self._step_source, self._executable, self._args
        )


def create_job_builder():
    return _JobBuilder()


class _LegacyJobBuilder(_BaseJobBuilder):
    def __init__(self):
        super().__init__()
        self._ext_job = None
        self._script = None
        self._run_path = None
        self._num_cpu = None
        self._status_file = None
        self._ok_file = None
        self._exit_file = None
        self._done_callback_function = None
        self._exit_callback_function = None
        self._callback_arguments = None
        self._max_runtime = None
        self.reset()

    def reset(self):
        super().reset()
        self._ext_job = None
        self._script = None
        self._run_path = None
        self._num_cpu = None
        self._status_file = None
        self._ok_file = None
        self._exit_file = None
        self._done_callback_function = None
        self._exit_callback_function = None
        self._callback_arguments = None
        self._max_runtime = None
        return self

    def set_ext_job(self, ext_job):
        self._ext_job = ext_job
        return self

    def build(self):
        return _LegacyJob(self._id, self._name, self._ext_job)


def create_legacy_job_builder():
    return _LegacyJobBuilder()


class _BaseJob:
    def __init__(self, id_, name, step_source):
        if id_ is None:
            raise ValueError(f"{self} need id")
        if name is None:
            raise ValueError(f"{self} need name")
        self._id = id_
        self._name = name
        self._step_source = step_source

    def get_id(self):
        return self._id

    def get_name(self):
        return self._name

    def get_source(self, ee_id):
        return f"{self._step_source.format(ee_id=ee_id)}/job/{self._id}"


class _UnixJob(_BaseJob):
    def __init__(
        self,
        id_,
        name,
        step_source,
        executable,
        args,
    ):
        super().__init__(id_, name, step_source)
        self._executable = executable
        self._args = args

    def get_executable(self):
        return self._executable

    def get_args(self):
        return self._args


class _FunctionJob(_BaseJob):
    def __init__(
        self,
        id_,
        name,
        step_source,
        command,
    ):
        super().__init__(id_, name, step_source)
        self._command = command

    def get_command(self):
        return self._command


class _LegacyJob(_BaseJob):
    def __init__(
        self,
        id_,
        name,
        ext_job,
    ):
        super().__init__(id_, name, "")  # no step_source needed for legacy (pt.)
        if ext_job is None:
            raise TypeError(f"{self} needs ext_job")
        self._ext_job = ext_job

    def get_ext_job(self):
        return self._ext_job


class _Stage:
    def __init__(
        self, id_: str, name: str, inputs: List[_IO], outputs: List[_IO]
    ) -> None:
        self._id = id_
        self._inputs = inputs
        self._outputs = outputs
        self._name = name

    def get_id(self):
        return self._id

    def get_inputs(self):
        return self._inputs

    def get_outputs(self):
        return self._outputs

    def get_name(self):
        return self._name


class _Step(_Stage):
    def __init__(self, id_, inputs, outputs, jobs, name, source):
        super().__init__(id_, name, inputs, outputs)
        if jobs is None:
            raise ValueError(f"{self} needs jobs")
        self._jobs = jobs
        self._source = source

    def get_jobs(self):
        return self._jobs

    def get_source(self, ee_id):
        return self._source.format(ee_id=ee_id)


class _UnixStep(_Step):
    def __init__(
        self,
        id_,
        inputs,
        outputs,
        jobs,
        name,
        source,
    ):
        super().__init__(id_, inputs, outputs, jobs, name, source)

    def get_task(self, output_transmitters, ee_id, *args, **kwargs):
        return UnixTask(self, output_transmitters, ee_id, *args, **kwargs)


class _FunctionStep(_Step):
    def __init__(
        self,
        id_,
        inputs,
        outputs,
        jobs,
        name,
        source,
    ):
        super().__init__(id_, inputs, outputs, jobs, name, source)

    def get_task(self, output_transmitters, ee_id, *args, **kwargs):
        return FunctionTask(self, output_transmitters, ee_id, *args, **kwargs)


class _LegacyStep(_Step):
    def __init__(
        self,
        id_,
        inputs,
        outputs,
        jobs,
        name,
        ee_url,
        source,
        max_runtime,
        callback_arguments,
        done_callback,
        exit_callback,
        num_cpu,
        run_path,
        job_script,
        job_name,
        run_arg,
    ):
        super().__init__(id_, inputs, outputs, jobs, name, source)
        if max_runtime is not None and max_runtime <= 0:
            raise ValueError(f"{self} needs positive max_runtime")
        if callback_arguments is None:
            raise ValueError(f"{self} needs callback_arguments")
        if done_callback is None:
            raise ValueError(f"{self} needs done_callback")
        if exit_callback is None:
            raise ValueError(f"{self} needs exit_callback")
        if num_cpu is None:
            raise ValueError(f"{self} needs num_cpu")
        if run_path is None:
            raise ValueError(f"{self} needs run_path")
        if job_script is None:
            raise ValueError(f"{self} needs job_script")
        if job_name is None:
            raise ValueError(f"{self} needs job_name")
        if run_arg is None:
            raise ValueError(f"{self} needs run_arg")
        self._max_runtime = max_runtime
        self._callback_arguments = callback_arguments
        self._done_callback = done_callback
        self._exit_callback = exit_callback
        self._num_cpu = num_cpu
        self._run_path = run_path
        self._job_script = job_script
        self._job_name = job_name
        self._run_arg = run_arg

    def get_max_runtime(self):
        return self._max_runtime

    def get_callback_arguments(self):
        return self._callback_arguments

    def get_done_callback(self):
        return self._done_callback

    def get_exit_callback(self):
        return self._exit_callback

    def get_num_cpu(self):
        return self._num_cpu

    def get_run_path(self):
        return self._run_path

    def get_job_script(self):
        return self._job_script

    def get_job_name(self):
        return self._job_name

    def get_run_arg(self):
        return self._run_arg


class _StageBuilder:
    def __init__(self):
        self._id: str = ""
        self._name: str = ""
        self._inputs: List[_IOBuilder] = []
        self._outputs: List[_IOBuilder] = []
        self._stages: List[_StageBuilder] = []
        self._io_map: Dict[_IOBuilder, _IOBuilder] = {}

    def set_id(self, id_: str):
        self._id = id_
        return self

    def set_name(self, name: str):
        self._name = name
        return self

    def add_output(self, output: _IOBuilder):
        self._outputs.append(output)
        return self

    def add_input(self, input: _IOBuilder):
        self._inputs.append(input)
        return self

    def build(self):
        if not self._id:
            raise ValueError(f"invalid id for stage {self._id}")
        if not self._name:
            raise ValueError(f"invalid name for stage {self._name}")
        inputs = [builder.build() for builder in self._inputs]
        outputs = [builder.build() for builder in self._outputs]
        return _Stage(self._id, self._name, inputs, outputs)

    def set_dummy_io(self):
        self.add_input(_DummyIOBuilder())
        self.add_output(_DummyIOBuilder())
        return self


class _StepBuilder(_StageBuilder):
    def __init__(self):
        super().__init__()
        self._jobs = []
        self._type = None
        self._source = None

        # legacy parts
        self._max_runtime = None
        self._callback_arguments = None
        self._done_callback = None
        self._exit_callback = None
        self._num_cpu = None
        self._run_path = None
        self._job_script = None
        self._job_name = None
        self._run_arg = None

    def set_type(self, type_):
        self._type = type_
        return self

    def set_source(self, source):
        self._source = source
        return self

    def add_job(self, job):
        self._jobs.append(job)
        return self

    def set_max_runtime(self, max_runtime):
        self._max_runtime = max_runtime
        return self

    def set_callback_arguments(self, callback_arguments):
        self._callback_arguments = callback_arguments
        return self

    def set_done_callback(self, done_callback):
        self._done_callback = done_callback
        return self

    def set_exit_callback(self, exit_callback):
        self._exit_callback = exit_callback
        return self

    def set_num_cpu(self, num_cpu):
        self._num_cpu = num_cpu
        return self

    def set_run_path(self, run_path):
        self._run_path = run_path
        return self

    def set_job_script(self, job_script):
        self._job_script = job_script
        return self

    def set_job_name(self, job_name):
        self._job_name = job_name
        return self

    def set_run_arg(self, run_arg):
        self._run_arg = run_arg
        return self

    def build(self):
        stage = super().build()
        jobs = [builder.build() for builder in self._jobs]
        if self._run_arg:
            return _LegacyStep(
                stage.get_id(),
                stage.get_inputs(),
                stage.get_outputs(),
                jobs,
                self._name,
                "",  # ee_url
                "",  # source
                self._max_runtime,
                self._callback_arguments,
                self._done_callback,
                self._exit_callback,
                self._num_cpu,
                self._run_path,
                self._job_script,
                self._job_name,
                self._run_arg,
            )
        cls = _Step
        if self._type == "function":
            cls = _FunctionStep
        elif self._type == "unix":
            cls = _UnixStep
        return cls(
            stage.get_id(),
            stage.get_inputs(),
            stage.get_outputs(),
            jobs,
            stage.get_name(),
            self._source,
        )


def create_step_builder():
    return _StepBuilder()


class _RealizationBuilder:
    def __init__(self):
        self._steps = []
        self._stages: List[_Stage] = []
        self._active = None
        self._iens = None

    def active(self, active):
        self._active = active
        return self

    def add_step(self, step):
        self._steps.append(step)
        return self

    def add_stage(self, stage: _Stage) -> "_RealizationBuilder":
        self._stages.append(stage)
        return self

    def set_iens(self, iens):
        self._iens = iens
        return self

    def build(self):
        steps = [builder.build() for builder in self._steps]
        ts_sorted_steps = _sort_steps(steps)

        return _Realization(
            self._iens, steps, self._active, ts_sorted_steps=ts_sorted_steps
        )


def create_realization_builder():
    return _RealizationBuilder()


class _Realization:
    def __init__(self, iens, steps, active, ts_sorted_steps=None):
        if iens is None:
            raise ValueError(f"{self} needs iens")
        if steps is None:
            raise ValueError(f"{self} needs steps")
        if active is None:
            raise ValueError(f"{self} needs to be set either active or not")

        self._iens = iens
        self._steps = steps
        self._active = active

        self._ts_sorted_indices = None
        if ts_sorted_steps is not None:
            self._ts_sorted_indices = list(range(0, len(ts_sorted_steps)))
            for idx, name in enumerate(ts_sorted_steps):
                for step_idx, step in enumerate(steps):
                    if step.get_name() == name:
                        self._ts_sorted_indices[idx] = step_idx
            if len(self._ts_sorted_indices) != len(steps):
                raise ValueError(
                    f"disparity between amount of sorted items ({self._ts_sorted_indices}) and steps, possibly duplicate step name?"
                )

    def get_steps(self):
        return self._steps

    def get_iens(self):
        return self._iens

    def is_active(self):
        return self._active

    def set_active(self, active):
        self._active = active

    def get_steps_sorted_topologically(self) -> Iterator[_Step]:
        steps = self._steps
        if not self._ts_sorted_indices:
            raise NotImplementedError("steps were not sorted")
        for idx in self._ts_sorted_indices:
            yield steps[idx]


class _EnsembleBuilder:
    def __init__(self):
        self._reals = None
        self._size = None
        self._metadata = None
        self._legacy_dependencies = None
        self.reset()

    def reset(self):
        self._reals = []
        self._size = 0
        self._metadata = {}
        self._legacy_dependencies = None
        return self

    def add_realization(self, real):
        self._reals.append(real)
        return self

    def set_metadata(self, key, value):
        self._metadata[key] = value
        return self

    def set_ensemble_size(self, size):
        """Duplicate the ensemble members that existed at build time so as to
        get the desired state."""
        self._size = size
        return self

    def set_legacy_dependencies(self, *args):
        self._legacy_dependencies = args
        return self

    @staticmethod
    def from_legacy(
        run_context,
        forward_model,
        queue_config,
        analysis_config,
        res_config,
    ):
        builder = _EnsembleBuilder().set_legacy_dependencies(
            queue_config,
            analysis_config,
        )

        num_cpu = res_config.queue_config.num_cpu
        if num_cpu == 0:
            num_cpu = res_config.ecl_config.num_cpu

        max_runtime = analysis_config.get_max_runtime()
        if max_runtime == 0:
            max_runtime = None

        for iens in range(0, len(run_context)):
            active = run_context.is_active(iens)
            real = create_realization_builder().set_iens(iens).active(active)
            step = (
                create_step_builder().set_id("0").set_dummy_io().set_name("legacy step")
            )
            if active:
                real.active(True).add_step(step)
                for index in range(0, len(forward_model)):
                    ext_job = forward_model.iget_job(index)
                    step.add_job(
                        create_legacy_job_builder()
                        .set_id(index)
                        .set_name(ext_job.name())
                        .set_ext_job(ext_job)
                    )
                run_arg = run_context[iens]
                step.set_max_runtime(
                    max_runtime
                ).set_callback_arguments(
                    [run_arg, res_config]
                ).set_done_callback(
                    EnKFState.forward_model_ok_callback
                ).set_exit_callback(
                    EnKFState.forward_model_exit_callback
                ).set_num_cpu(
                    num_cpu
                ).set_run_path(
                    run_arg.runpath
                ).set_job_script(
                    res_config.queue_config.job_script
                ).set_job_name(
                    run_arg.job_name
                ).set_run_arg(
                    run_arg
                )
            builder.add_realization(real)
        return builder

    def build(self):
        # duplicate the original reals
        orig_len = len(self._reals)
        for i in range(orig_len, self._size):
            logger.debug(f"made deep-copied real {i}")
            real = copy.deepcopy(self._reals[i % orig_len])
            real.set_iens(i)
            self._reals.append(real)

        reals = [builder.build() for builder in self._reals]

        if self._legacy_dependencies:
            return _LegacyEnsemble(reals, self._metadata, *self._legacy_dependencies)
        return _Ensemble(reals, self._metadata)


def create_ensemble_builder():
    return _EnsembleBuilder()


def create_ensemble_builder_from_legacy(
    run_context,
    forward_model,
    queue_config,
    analysis_config,
    res_config,
):
    return _EnsembleBuilder.from_legacy(
        run_context,
        forward_model,
        queue_config,
        analysis_config,
        res_config,
    )
