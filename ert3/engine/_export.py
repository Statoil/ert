import json
from pathlib import Path

import ert3.storage as ert3_storage
import ert3.workspace as ert3_workspace
from ._status import experiment_has_run as ert3_experiment_has_run


def _prepare_export(workspace_root, experiment_name, parameter_names, response_names):
    data_mapping = [(pname, "input") for pname in parameter_names]
    data_mapping += [(rname, "output") for rname in response_names]
    data = None
    for record_name, data_type in data_mapping:
        ensemble_record = ert3_storage.get_ensemble_record(
            workspace=workspace_root,
            experiment_name=experiment_name,
            record_name=record_name,
        )

        if data is None:
            data = [{"input": {}, "output": {}} for _ in ensemble_record.records]

        assert len(data) == ensemble_record.ensemble_size
        for realization, record in zip(data, ensemble_record.records):
            assert record_name not in realization[data_type]
            realization[data_type][record_name] = record.data

    return data


def export(workspace_root: Path, experiment_name: str) -> None:
    experiment_root = (
        Path(workspace_root) / ert3_workspace.EXPERIMENTS_BASE / experiment_name
    )
    ert3_workspace.assert_experiment_exists(workspace_root, experiment_name)

    if not ert3_experiment_has_run(workspace_root, experiment_name):
        raise ValueError("Cannot export experiment that has not been carried out")

    parameter_names = set(
        ert3_storage.get_experiment_parameters(
            workspace=workspace_root, experiment_name=experiment_name
        )
    )
    response_names = (
        set(
            ert3_storage.get_ensemble_record_names(
                workspace=workspace_root, experiment_name=experiment_name
            )
        )
        - parameter_names
    )

    data = _prepare_export(
        workspace_root, experiment_name, parameter_names, response_names
    )
    with open(experiment_root / "data.json", "w") as f:
        json.dump(data, f)
