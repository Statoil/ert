import os
import stat

import json
import yaml
import pytest
import sys

import numpy as np
import pathlib
import ert3

import subprocess
import time
import requests

POLY_SCRIPT = """
#!/usr/bin/env python
import json
import sys
with open(sys.argv[2], "r") as f:
    coefficients = json.load(f)
a, b, c = coefficients["a"], coefficients["b"], coefficients["c"]
result = tuple(a * x ** 2 + b * x + c for x in range(10))
with open(sys.argv[4], "w") as f:
    json.dump(result, f)
"""

POLY_FUNCTION = """
def polynomial(coefficients):
    return tuple(
        coefficients["a"] * x ** 2 + coefficients["b"] * x + coefficients["c"]
        for x in range(10)
    )
"""


@pytest.fixture()
def workspace(tmpdir, ert_storage):
    workspace = tmpdir / "polynomial"
    workspace.mkdir()
    workspace.chdir()
    ert3.workspace.initialize(workspace)
    yield workspace


@pytest.fixture()
def base_ensemble_dict():
    yield {
        "size": 10,
        "input": [{"source": "stochastic.coefficients", "record": "coefficients"}],
        "forward_model": {"driver": "local", "stages": ["evaluate_polynomial"]},
    }


@pytest.fixture()
def ensemble(base_ensemble_dict):
    yield ert3.config.load_ensemble_config(base_ensemble_dict)


@pytest.fixture()
def stages_config():
    config_list = [
        {
            "name": "evaluate_polynomial",
            "type": "unix",
            "input": [{"record": "coefficients", "location": "coefficients.json"}],
            "output": [{"record": "polynomial_output", "location": "output.json"}],
            "script": ["poly --coefficients coefficients.json --output output.json"],
            "transportable_commands": [
                {
                    "name": "poly",
                    "location": "poly.py",
                }
            ],
        }
    ]
    script_file = pathlib.Path("poly.py")
    script_file.write_text(POLY_SCRIPT)
    st = os.stat(script_file)
    os.chmod(script_file, st.st_mode | stat.S_IEXEC)

    yield ert3.config.load_stages_config(config_list)


@pytest.fixture()
def function_stages_config():
    config_list = [
        {
            "name": "evaluate_polynomial",
            "type": "function",
            "input": [{"record": "coefficients", "location": "coeffs"}],
            "output": [{"record": "polynomial_output", "location": "output"}],
            "function": "function_steps.functions:polynomial",
        }
    ]
    func_dir = pathlib.Path("function_steps")
    func_dir.mkdir()
    (func_dir / "__init__.py").write_text("")
    (func_dir / "functions.py").write_text(POLY_FUNCTION)
    sys.path.append(os.getcwd())

    yield ert3.config.load_stages_config(config_list)


def load_experiment_config(workspace, ensemble_config, stages_config):
    config = {}
    config["ensemble"] = ensemble_config
    config["stages"] = stages_config
    with open(workspace / "parameters.yml") as f:
        config["parameters"] = yaml.safe_load(f)
    return config


def assert_ensemble_size(config, export_data):
    assert len(export_data) == config["ensemble"].size


def assert_input_records(config, export_data):
    input_records = {}
    for input_data in config["ensemble"].input:
        record = input_data.record
        source = input_data.source

        for p in config["parameters"]:
            if p["type"] + "." + p["name"] == source:
                parameter = p
                break

        input_records[record] = parameter["variables"]

    for realisation in export_data:
        assert sorted(input_records.keys()) == sorted(realisation["input"].keys())
        for record_name in input_records.keys():
            input_variables = sorted(input_records[record_name])
            realisation_variables = sorted(realisation["input"][record_name].keys())
            assert input_variables == realisation_variables


def assert_output_records(config, export_data):
    output_records = []
    for forward_stage in config["ensemble"].forward_model.stages:
        for stage in config["stages"]:
            if stage.name == forward_stage:
                output_records += [output_data.record for output_data in stage.output]
    for realisation in export_data:
        assert sorted(output_records) == sorted(realisation["output"].keys())


def assert_poly_output(export_data):
    for realisation in export_data:
        coeff = realisation["input"]["coefficients"]
        poly_out = realisation["output"]["polynomial_output"]

        assert 10 == len(poly_out)
        for x, y in zip(range(10), poly_out):
            assert coeff["a"] * x ** 2 + coeff["b"] * x + coeff["c"] == pytest.approx(y)


def assert_export(workspace, experiment_name, ensemble_config, stages_config):
    export_file = (
        workspace / ert3.workspace.EXPERIMENTS_BASE / experiment_name / "data.json"
    )
    with open(export_file) as f:
        export_data = json.load(f)

    config = load_experiment_config(workspace, ensemble_config, stages_config)
    assert_ensemble_size(config, export_data)
    assert_input_records(config, export_data)
    assert_output_records(config, export_data)

    # Note: This test assumes the forward model in the setup indeed
    # evaluates a * x^2 + b * x + c. If not, this will fail miserably!
    assert_poly_output(export_data)


def assert_distribution(
    workspace, ensemble_config, stages_config, distribution, coefficients
):
    indices = ("a", "b", "c")

    for real_coefficient in coefficients.records:
        assert sorted(indices) == sorted(real_coefficient.index)
        for idx in real_coefficient.index:
            assert isinstance(real_coefficient.data[idx], float)

    samples = {idx: [] for idx in indices}
    for sample in coefficients.records:
        for key in indices:
            samples[key].append(sample.data[key])

    config = load_experiment_config(workspace, ensemble_config, stages_config)
    parameter = None
    for p in config["parameters"]:
        if p["distribution"]["type"] == distribution:
            parameter = p
            break

    assert parameter is not None

    input_data = parameter["distribution"]["input"]

    for variable in parameter["variables"]:
        values = samples[variable]

        if distribution == "gaussian":
            assert input_data["mean"] == pytest.approx(
                sum(values) / len(values), abs=0.1
            )
            assert input_data["std"] == pytest.approx(np.std(values), abs=0.1)

        elif distribution == "uniform":
            assert input_data["lower_bound"] == pytest.approx(min(values), abs=0.1)
            assert input_data["upper_bound"] == pytest.approx(max(values), abs=0.1)
            mean = (input_data["lower_bound"] + input_data["upper_bound"]) / 2
            assert mean == pytest.approx(sum(values) / len(values), abs=0.1)

        else:
            raise ValueError(f"Unknown distribution {distribution}")


def assert_sensitivity_oat_export(
    workspace, experiment_name, ensemble_config, stages_config
):
    export_file = (
        workspace / ert3.workspace.EXPERIMENTS_BASE / experiment_name / "data.json"
    )
    with open(export_file) as f:
        export_data = json.load(f)

    num_input_coeffs = 3
    assert 2 * num_input_coeffs == len(export_data)

    config = load_experiment_config(workspace, ensemble_config, stages_config)
    assert_input_records(config, export_data)
    assert_output_records(config, export_data)

    # Note: This test assumes the forward model in the setup indeed
    # evaluates a * x^2 + b * x + c. If not, this will fail miserably!
    assert_poly_output(export_data)


@pytest.fixture
def ert_storage(request, tmpdir):
    env = os.environ.copy()
    env["ERT_STORAGE_DATABASE_URL"] = f"sqlite:///{tmpdir}/ert.db"
    process = subprocess.Popen(["uvicorn", "ert_storage.app:app"], env=env)

    def shut_down():
        process.terminate()
        process.wait()

    request.addfinalizer(shut_down)
    for _ in range(20):
        try:
            r = requests.get("http://127.0.0.1:8000/healthcheck")
            if r.status_code == 200:
                break
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(0.5)
    else:
        raise requests.exceptions.ConnectionError("Ert-storage not starting")
    yield
