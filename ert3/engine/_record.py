import json
from typing import Optional, TextIO
from pathlib import Path

from ert3.data import Record, EnsembleRecord
import ert3.storage as ert3_storage

from . import _utils


def load_record(workspace: Path, record_name: str, record_stream: TextIO) -> None:
    raw_ensrecord = json.load(record_stream)

    ensrecord = EnsembleRecord(
        records=[Record(data=raw_record) for raw_record in raw_ensrecord]
    )
    ert3_storage.add_ensemble_record(
        workspace=workspace,
        record_name=record_name,
        ensemble_record=ensrecord,
    )


def sample_record(
    workspace: Path,
    parameter_group_name: str,
    record_name: str,
    ensemble_size: int,
    experiment_name: Optional[str] = None,
) -> None:
    parameters = _utils.load_parameters(workspace)

    if parameter_group_name not in parameters:
        raise ValueError(f"No parameter group found named: {parameter_group_name}")
    distribution = parameters[parameter_group_name]

    ensrecord = EnsembleRecord(
        records=[distribution.sample() for _ in range(ensemble_size)]
    )
    ert3_storage.add_ensemble_record(
        workspace=workspace,
        record_name=record_name,
        ensemble_record=ensrecord,
        experiment_name=experiment_name,
    )
