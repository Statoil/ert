import json
import os
import pathlib
import shutil

from ert_shared.ensemble_evaluator.config import EvaluatorServerConfig
from ert_shared.ensemble_evaluator.evaluator import EnsembleEvaluator
from ert_shared.ensemble_evaluator.prefect_ensemble.prefect_ensemble import (
    PrefectEnsemble,
    storage_driver_factory,
)

from ert3.data import Record, EnsembleRecord, MultiEnsembleRecord
from ert3.workspace import DATA_ROOT as ERT3_DATA_ROOT

_EVTYPE_SNAPSHOT_STOPPED = "Stopped"
_EVTYPE_SNAPSHOT_FAILED = "Failed"


def _create_evaluator_tmp_dir(workspace_root, evaluation_name):
    return (
        pathlib.Path(workspace_root)
        / ERT3_DATA_ROOT
        / "tmp"
        / evaluation_name
    )


def _assert_single_stage_forward_model(stages_config, ensemble):
    # The current implementation only support one stage as the forward model
    # and hence we fail if multiple are provided
    assert len(ensemble.forward_model.stages) == 1


def _prepare_input(ee_config, stages_config, inputs, evaluation_tmp_dir, ensemble):
    stage_name = ensemble.forward_model.stages[0]
    stage_config = next(stage for stage in stages_config if stage.name == stage_name)
    tmp_input_folder = evaluation_tmp_dir / "prep_input_files"
    os.makedirs(tmp_input_folder)
    ee_storage = storage_driver_factory(ee_config["storage"], tmp_input_folder)
    record2location = {input.record: input.location for input in stage_config.input}
    input_files = {iens: () for iens in range(ee_config["realizations"])}
    for record_name in inputs.record_names:
        for iens, record in enumerate(inputs.ensemble_records[record_name].records):
            filename = record2location[record_name]
            input_files[iens] += (ee_storage.store_data(record.data, filename, iens),)
    return input_files


def _build_ee_config(evaluation_tmp_dir, ensemble, stages_config, input_records):
    _assert_single_stage_forward_model(stages_config, ensemble)

    if ensemble.size != None:
        ensemble_size = ensemble.size
    else:
        ensemble_size = input_records.ensemble_size

    stage_name = ensemble.forward_model.stages[0]
    stage = stages_config.step_from_key(stage_name)
    commands = stage.transportable_commands
    command_scripts = [cmd.location for cmd in commands] if commands else []
    output_locations = [out.location for out in stage.output]
    jobs = []

    def command_location(name):
        return next(
            cmd.location for cmd in stage.transportable_commands if cmd.name == name
        )

    if stage.function:
        jobs.append(
            {
                "name": stage.function.__name__,
                "executable": stage.function,
                "output": output_locations[0],
            }
        )

    for script in stage.script:
        name, *args = script.split()
        jobs.append(
            {
                "name": name,
                "executable": command_location(name),
                "args": tuple(args),
            }
        )

    stages = [
        {
            "name": stage.name,
            "steps": [
                {
                    "name": stage.name + "-only_step",
                    "resources": command_scripts,
                    "inputs": [],
                    "outputs": output_locations,
                    "jobs": jobs,
                    "type": "function" if stage.function else "unix",
                }
            ],
        }
    ]

    ee_config = {
        "stages": stages,
        "realizations": ensemble_size,
        "max_running": 10000,
        "max_retries": 0,
        "run_path": evaluation_tmp_dir / "my_output",
        "executor": ensemble.forward_model.driver,
        "storage": {
            "type": "shared_disk",
            "storage_path": evaluation_tmp_dir / ".my_storage",
        },
    }

    ee_config["input_files"] = _prepare_input(
        ee_config, stages_config, input_records, evaluation_tmp_dir, ensemble
    )
    ee_config["ens_records"] = input_records.ensemble_records

    return ee_config


def _fetch_results(ee_config, ensemble, stages_config):
    _assert_single_stage_forward_model(stages_config, ensemble)

    results = []
    ee_storage = storage_driver_factory(ee_config["storage"], ee_config["run_path"])
    for iens in range(ee_config["realizations"]):
        spath = pathlib.Path(ee_storage.get_storage_path(iens))
        realization_results = {}

        stage_name = ensemble.forward_model.stages[0]
        stage = stages_config.step_from_key(stage_name)
        for output_elem in stage.output:
            with open(spath / output_elem.location) as f:
                realization_results[output_elem.record] = json.load(f)
        results.append(realization_results)
    return results


def _run(ensemble_evaluator):
    with ensemble_evaluator.run() as monitor:
        for event in monitor.track():
            if event.data is not None and event.data.get("status") in [
                _EVTYPE_SNAPSHOT_STOPPED,
                _EVTYPE_SNAPSHOT_FAILED,
            ]:
                monitor.signal_done()


def _prepare_responses(raw_responses):
    responses = {response_name: [] for response_name in raw_responses[0]}
    for realization in raw_responses:
        assert responses.keys() == realization.keys()
        for key in realization:
            responses[key].append(Record(data=realization[key]))

    for key in responses:
        responses[key] = EnsembleRecord(records=responses[key])

    return MultiEnsembleRecord(ensemble_records=responses)


def evaluate(
    workspace_root, evaluation_name, input_records, ensemble_config, stages_config
):
    evaluation_tmp_dir = _create_evaluator_tmp_dir(workspace_root, evaluation_name)

    ee_config = _build_ee_config(
        evaluation_tmp_dir, ensemble_config, stages_config, input_records
    )
    ensemble = PrefectEnsemble(ee_config)

    config = EvaluatorServerConfig()
    ee = EnsembleEvaluator(ensemble=ensemble, config=config, iter_=0)
    _run(ee)

    results = _fetch_results(ee_config, ensemble_config, stages_config)
    responses = _prepare_responses(results)

    return responses


def cleanup(workspace_root, evaluation_name):
    tmp_dir = _create_evaluator_tmp_dir(workspace_root, evaluation_name)
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
