import pathlib
from typing import List, Mapping

import ert3.algorithms as ert3_algorithms
import ert3.config as ert3_config
import ert3.data as ert3_data
import ert3.evaluator as ert3_evaluator
import ert3.stats as ert3_stats
import ert3.storage as ert3_storage

from ._record import sample_record
from ._status import experiment_has_run as ert3_experiment_has_run
from . import _utils


def _prepare_experiment(
    workspace_root: pathlib.Path,
    experiment_name: str,
    ensemble: ert3_config.EnsembleConfig,
):
    if ert3_experiment_has_run(workspace_root, experiment_name):
        raise ValueError(f"Experiment {experiment_name} have been carried out.")

    parameter_names = [elem.record for elem in ensemble.input]
    ert3_storage.init_experiment(
        workspace=workspace_root,
        experiment_name=experiment_name,
        parameters=parameter_names,
    )


def _prepare_experiment_record(
    record_name: str,
    record_source: List[str],
    ensemble_size: int,
    experiment_name: str,
    workspace_root: pathlib.Path,
) -> None:
    if record_source[0] == "storage":
        assert len(record_source) == 2
        ensemble_record = ert3_storage.get_ensemble_record(
            workspace=workspace_root, record_name=record_source[1]
        )
        ert3_storage.add_ensemble_record(
            workspace=workspace_root,
            experiment_name=experiment_name,
            record_name=record_name,
            ensemble_record=ensemble_record,
        )
    elif record_source[0] == "stochastic":
        sample_record(
            workspace_root,
            record_source[1],
            record_name,
            ensemble_size,
            experiment_name=experiment_name,
        )
    else:
        raise ValueError("Unknown record source location {}".format(record_source[0]))


def _prepare_evaluation(
    ensemble: ert3_config.EnsembleConfig,
    workspace_root: pathlib.Path,
    experiment_name: str,
) -> None:
    # This reassures mypy that the ensemble size is defined
    assert ensemble.size is not None

    for input_record in ensemble.input:
        record_name = input_record.record
        record_source = input_record.source.split(".")

        _prepare_experiment_record(
            record_name, record_source, ensemble.size, experiment_name, workspace_root
        )


def _load_ensemble_parameters(
    ensemble: ert3_config.EnsembleConfig,
    workspace: pathlib.Path,
) -> Mapping[str, ert3_stats.Distribution]:
    parameters = _utils.load_parameters(workspace)

    ensemble_parameters = {}
    for input_record in ensemble.input:
        record_name = input_record.record
        record_source = input_record.source.split(".")
        assert len(record_source) == 2
        assert record_source[0] == "stochastic"
        parameter_group_name = record_source[1]
        ensemble_parameters[record_name] = parameters[parameter_group_name]
    return ensemble_parameters


def _prepare_sensitivity(
    ensemble: ert3_config.EnsembleConfig,
    workspace_root: pathlib.Path,
    experiment_name: str,
) -> None:
    parameter_distributions = _load_ensemble_parameters(ensemble, workspace_root)
    input_records = ert3_algorithms.one_at_the_time(parameter_distributions)

    parameters: Mapping[str, List[ert3_data.Record]] = {
        param.record: [] for param in ensemble.input
    }
    for realization in input_records:
        assert parameters.keys() == realization.keys()
        for record_name in realization:
            parameters[record_name].append(realization[record_name])

    for record_name in parameters:
        ensemble_record = ert3_data.EnsembleRecord(records=parameters[record_name])
        ert3_storage.add_ensemble_record(
            workspace=workspace_root,
            experiment_name=experiment_name,
            record_name=record_name,
            ensemble_record=ensemble_record,
        )


def _store_responses(
    workspace_root: pathlib.Path,
    experiment_name: str,
    responses: ert3_data.MultiEnsembleRecord,
) -> None:
    for record_name in responses.record_names:
        ert3_storage.add_ensemble_record(
            workspace=workspace_root,
            experiment_name=experiment_name,
            record_name=record_name,
            ensemble_record=responses.ensemble_records[record_name],
        )


def _load_experiment_parameters(
    workspace_root: pathlib.Path,
    experiment_name: str,
) -> ert3_data.MultiEnsembleRecord:
    parameter_names = ert3_storage.get_experiment_parameters(
        workspace=workspace_root, experiment_name=experiment_name
    )

    parameters = {}
    for parameter_name in parameter_names:
        parameters[parameter_name] = ert3_storage.get_ensemble_record(
            workspace=workspace_root,
            experiment_name=experiment_name,
            record_name=parameter_name,
        )

    return ert3_data.MultiEnsembleRecord(ensemble_records=parameters)


def _evaluate(
    ensemble: ert3_config.EnsembleConfig,
    stages_config: ert3_config.StagesConfig,
    workspace_root: pathlib.Path,
    experiment_name: str,
) -> None:
    parameters = _load_experiment_parameters(workspace_root, experiment_name)
    responses = ert3_evaluator.evaluate(
        workspace_root, experiment_name, parameters, ensemble, stages_config
    )
    _store_responses(workspace_root, experiment_name, responses)


def run(
    ensemble: ert3_config.EnsembleConfig,
    stages_config: ert3_config.StagesConfig,
    experiment_config: ert3_config.ExperimentConfig,
    workspace_root: pathlib.Path,
    experiment_name: str,
) -> None:
    _prepare_experiment(workspace_root, experiment_name, ensemble)

    if experiment_config.type == "evaluation":
        _prepare_evaluation(ensemble, workspace_root, experiment_name)
    elif experiment_config.type == "sensitivity":
        _prepare_sensitivity(ensemble, workspace_root, experiment_name)
    else:
        raise ValueError(f"Unknown experiment type {experiment_config.type}")

    _evaluate(ensemble, stages_config, workspace_root, experiment_name)
