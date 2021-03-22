import os
from pathlib import Path
from typing import Any, Iterable, Optional, Union
import io
import yaml
import requests

from ert3.workspace import DATA_ROOT as ERT3_DATA_ROOT
from ert3.data import EnsembleRecord


_STORAGE_FILE = "storage.yaml"
_STORAGE_URL = "http://localhost:8000"


_DATA = "__data__"
_PARAMETERS = "__parameters__"

_ENSEMBLE_RECORDS = "__ensemble_records__"

_SPECIAL_KEYS = (_ENSEMBLE_RECORDS,)


def _generate_storage_location(workspace: Union[str, Path]) -> Path:
    workspace = Path(workspace)
    return workspace / ERT3_DATA_ROOT / _STORAGE_FILE


def _assert_storage_initialized(storage_location: Path) -> None:
    if not os.path.isfile(storage_location):
        raise ValueError("Storage is not initialized")


def init(*, workspace: Union[str, Path]) -> None:
    storage_location = _generate_storage_location(workspace)

    if os.path.exists(storage_location):
        raise ValueError(f"Storage already initialized for workspace {workspace}")

    if not os.path.exists(storage_location.parent):
        os.makedirs(storage_location.parent)

    with open(storage_location, "w") as f:
        yaml.dump({}, f)

    for special_key in _SPECIAL_KEYS:
        init_experiment(workspace=workspace, experiment_name=special_key, parameters=[])


def init_experiment(
    *, workspace: Union[str, Path], experiment_name: str, parameters: Iterable[str]
) -> None:
    storage_location = _generate_storage_location(workspace)
    _assert_storage_initialized(storage_location)

    with open(storage_location) as f:
        storage = yaml.safe_load(f)

    if experiment_name in storage:
        raise KeyError(f"Cannot initialize existing experiment: {experiment_name}")

    response = requests.post(
        url=f"{_STORAGE_URL}/ensembles", json={"parameters": list(parameters)}
    )

    storage[experiment_name] = {
        _PARAMETERS: list(parameters),
        _DATA: {},
        "id": response.json()["id"],
    }

    with open(storage_location, "w") as f:
        yaml.dump(storage, f)


def get_experiment_names(*, workspace: Union[str, Path]) -> Iterable[str]:
    storage_location = _generate_storage_location(workspace)
    _assert_storage_initialized(storage_location)

    with open(storage_location) as f:
        storage = yaml.safe_load(f)

    experiment_names = set(storage.keys())
    for special_key in _SPECIAL_KEYS:
        experiment_names.remove(special_key)
    return experiment_names


def _add_data(
    workspace: Union[str, Path], experiment_name: str, record_name: str, data: Any
) -> None:
    storage_location = _generate_storage_location(workspace)
    _assert_storage_initialized(storage_location)

    with open(storage_location) as f:
        storage = yaml.safe_load(f)

    if experiment_name not in storage:
        raise KeyError(
            f"Cannot add {record_name} data to "
            f"non-existing experiment: {experiment_name}"
        )

    ensemble_id = storage[experiment_name]["id"]

    response = requests.post(
        url=f"{_STORAGE_URL}/ensembles/{ensemble_id}/records/{record_name}/file",
        files={"file": (record_name, io.StringIO(data), "something")},
    )
    if response.status_code == 409:
        raise KeyError("Record already exists")

    assert response.status_code == 200


def _get_data(
    workspace: Union[str, Path], experiment_name: str, record_name: str
) -> Any:
    storage_location = _generate_storage_location(workspace)
    _assert_storage_initialized(storage_location)

    with open(storage_location) as f:
        storage = yaml.safe_load(f)

    if experiment_name not in storage:
        raise KeyError(
            f"Cannot get {record_name} data, no experiment named: {experiment_name}"
        )

    ensemble_id = storage[experiment_name]["id"]

    response = requests.get(
        url=f"{_STORAGE_URL}/ensembles/{ensemble_id}/records/{record_name}"
    )

    if response.status_code == 404:
        raise KeyError(f"No {record_name} data for experiment: {experiment_name}")

    return response.content


def add_ensemble_record(
    *,
    workspace: Union[str, Path],
    record_name: str,
    ensemble_record: EnsembleRecord,
    experiment_name: Optional[str] = None,
) -> None:
    if experiment_name is None:
        experiment_name = _ENSEMBLE_RECORDS
    _add_data(workspace, experiment_name, record_name, ensemble_record.json())


def get_ensemble_record(
    *,
    workspace: Union[str, Path],
    record_name: str,
    experiment_name: Optional[str] = None,
) -> EnsembleRecord:
    if experiment_name is None:
        experiment_name = _ENSEMBLE_RECORDS
    return EnsembleRecord.parse_raw(
        _get_data(workspace, experiment_name, record_name)
    )


def get_ensemble_record_names(
    *, workspace: Union[str, Path], experiment_name: Optional[str] = None
) -> Iterable[str]:
    if experiment_name is None:
        experiment_name = _ENSEMBLE_RECORDS
    storage_location = _generate_storage_location(workspace)
    _assert_storage_initialized(storage_location)

    with open(storage_location) as f:
        storage = yaml.safe_load(f)

    if experiment_name not in storage:
        raise KeyError(
            f"Cannot get record names of non-existing experiment: {experiment_name}"
        )
    ensemble_id = storage[experiment_name]["id"]
    response = requests.get(url=f"{_STORAGE_URL}/ensembles/{ensemble_id}/records")
    return list(response.json().keys())


def get_experiment_parameters(
    *, workspace: Union[str, Path], experiment_name: str
) -> Iterable[str]:
    storage_location = _generate_storage_location(workspace)
    _assert_storage_initialized(storage_location)

    with open(storage_location) as f:
        storage = yaml.safe_load(f)

    if experiment_name not in storage:
        raise KeyError(
            f"Cannot get parameters from non-existing experiment: {experiment_name}"
        )
    ensemble_id = storage[experiment_name]["id"]
    response = requests.get(url=f"{_STORAGE_URL}/ensembles/{ensemble_id}/parameters")

    return list(response.json())


def delete_experiment(*, workspace: Union[str, Path], experiment_name: str) -> None:
    storage_location = _generate_storage_location(workspace)
    _assert_storage_initialized(storage_location)

    with open(storage_location) as f:
        storage = yaml.safe_load(f)

    if experiment_name in storage:
        del storage[experiment_name]
        with open(storage_location, "w") as f:
            yaml.dump(storage, f)
    else:
        raise ert3.exceptions.NonExistantExperiment(
            f"Experiment does not exist: {experiment_name}"
        )
