import time
import pathlib
import os
import shutil
import random
import pathlib

import pytest

import numpy as np
from ert_data import loader
from res.enkf import EnKFMain, ResConfig

from ert_data.measured import MeasuredData
from ert_shared.libres_facade import LibresFacade
from tests.utils import SOURCE_DIR

test_data_root = pathlib.Path(SOURCE_DIR) / "test-data" / "local"


@pytest.fixture()
def copy_snake_oil(tmpdir):
    with tmpdir.as_cwd():
        test_data_dir = os.path.join(test_data_root, "snake_oil")

        shutil.copytree(test_data_dir, "test_data")
        os.chdir("test_data")
        yield


@pytest.fixture()
def facade_snake_oil(copy_snake_oil):
    res_config = ResConfig("snake_oil.ert")
    ert = EnKFMain(res_config)
    yield LibresFacade(ert)


def test_history_obs(monkeypatch, facade_snake_oil):

    fopr = MeasuredData(facade_snake_oil, ["FOPR"])
    fopr.remove_inactive_observations()

    assert all(
        fopr.data.columns.get_level_values("data_index").values == list(range(199))
    )


def test_summary_obs(monkeypatch, facade_snake_oil):
    summary_obs = MeasuredData(facade_snake_oil, ["WOPR_OP1_72"])
    summary_obs.remove_inactive_observations()
    assert all(summary_obs.data.columns.get_level_values("data_index").values == [71])
    # Only one observation, we check the key_index is what we expect:
    assert summary_obs.data.columns.get_level_values("key_index").values[
        0
    ] == np.datetime64("2011-12-21")


def test_summary_obs_runtime(monkeypatch, copy_snake_oil):
    """
    This is mostly a regression test, as reading SUMMARY_OBS was very slow when using
    SUMMARY_OBSERVATION and not HISTORY_OBSERVATION where multiple observations
    were pointing to the same response. To simulate that we load the same observations
    though individual points, and also in one go. To avoid this test being flaky the
    we assert on the difference in runtime. The difference in runtime we assert on is
    set to 10x though it should be around 2x
    """

    obs_file = pathlib.Path.cwd() / "observations" / "observations.txt"
    with obs_file.open(mode="a") as fin:
        fin.write(create_summary_observation())

    res_config = ResConfig("snake_oil.ert")
    ert = EnKFMain(res_config)

    facade = LibresFacade(ert)

    start_time = time.time()
    foprh = MeasuredData(facade, [f"FOPR_{restart}" for restart in range(1, 200)])
    summary_obs_time = time.time() - start_time

    start_time = time.time()
    fopr = MeasuredData(facade, ["FOPR"])
    history_obs_time = time.time() - start_time

    assert all(
        fopr.data.columns.get_level_values("data_index").values
        == foprh.data.columns.get_level_values("data_index").values
    )

    result = foprh.get_simulated_data().values == fopr.get_simulated_data().values
    assert np.logical_and.reduce(result).all()
    assert summary_obs_time < 10 * history_obs_time


def test_gen_obs_runtime(monkeypatch, copy_snake_oil):
    obs_file = pathlib.Path.cwd() / "observations" / "observations.txt"
    with obs_file.open(mode="a") as fin:
        fin.write(create_general_observation())

    res_config = ResConfig("snake_oil.ert")
    ert = EnKFMain(res_config)

    facade = LibresFacade(ert)

    df = MeasuredData(facade, [f"CUSTOM_DIFF_{restart}" for restart in range(1, 500)])

    df = df.remove_inactive_observations()
    assert df.data.shape == (27, 1995)


def test_gen_obs(monkeypatch, facade_snake_oil):
    df = MeasuredData(facade_snake_oil, ["WPR_DIFF_1"])
    df.remove_inactive_observations()

    assert all(
        df.data.columns.get_level_values("data_index").values == [400, 800, 1200, 1800]
    )
    assert all(
        df.data.columns.get_level_values("key_index").values == [400, 800, 1200, 1800]
    )


def test_block_obs(monkeypatch, tmpdir):
    """
    This test causes util_abort on some runs, so it will not be run by default
    as it is too flaky. I have chosen to leave it here as it could be useful when
    debugging. To run the test, run an ensemble_experiment on the snake_oil_field
    case to create a storage with BLOCK_OBS.
    """
    with tmpdir.as_cwd():
        test_data_dir = pathlib.Path(test_data_root) / "snake_oil_field"
        if not (test_data_dir / "storage").exists():
            pytest.skip()
        else:
            shutil.copytree(test_data_dir, "test_data")
            os.chdir("test_data")

            block_obs = """
            \nBLOCK_OBSERVATION RFT_2006
            {
               FIELD = PRESSURE;
               DATE  = 10/01/2010;
               SOURCE = SUMMARY;

               OBS P1 { I = 5;  J = 5;  K = 5;   VALUE = 100;  ERROR = 5; };
               OBS P2 { I = 1;  J = 3;  K = 8;   VALUE = 50;  ERROR = 2; };
            };
            """
            obs_file = pathlib.Path.cwd() / "observations" / "observations.txt"
            with obs_file.open(mode="a") as fin:
                fin.write(block_obs)

            res_config = ResConfig("snake_oil.ert")
            ert = EnKFMain(res_config)
            facade = LibresFacade(ert)

            df = MeasuredData(facade, ["RFT_2006"])
            df.remove_inactive_observations()
            assert all(df.data.columns.get_level_values("data_index").values == [0, 1])
            assert all(df.data.columns.get_level_values("key_index").values == [0, 1])


def test_gen_obs_and_summary(monkeypatch, facade_snake_oil):
    df = MeasuredData(facade_snake_oil, ["WPR_DIFF_1", "WOPR_OP1_9"])
    df.remove_inactive_observations()

    assert df.data.columns.get_level_values(0).to_list() == [
        "WPR_DIFF_1",
        "WPR_DIFF_1",
        "WPR_DIFF_1",
        "WPR_DIFF_1",
        "WOPR_OP1_9",
    ]
    assert df.data.columns.get_level_values("data_index").to_list() == [
        400,
        800,
        1200,
        1800,
        8,
    ]


def test_gen_obs_and_summary_index_range(monkeypatch, facade_snake_oil):
    df = MeasuredData(facade_snake_oil, ["WPR_DIFF_1", "FOPR"], [[800], [10]])
    df.remove_inactive_observations()

    assert df.data.columns.get_level_values(0).to_list() == [
        "WPR_DIFF_1",
        "FOPR",
    ]
    assert df.data.columns.get_level_values("data_index").to_list() == [
        800,
        10,
    ]
    assert df.data.loc["OBS"].values == pytest.approx([0.1, 0.23281], abs=0.00001)
    assert df.data.loc["STD"].values == pytest.approx([0.2, 0.1])


@pytest.mark.parametrize(
    "obs_key, expected_error",
    [
        ("FOPR", r"No observations loaded for observations keys: \['FOPR'\]"),
        ("SNAKE_OIL_WPR_DIFF", "No data key for obs key: SNAKE_OIL_WPR_DIFF"),
    ],
)
@pytest.mark.usefixtures("copy_snake_oil")
def test_no_storage(monkeypatch, obs_key, expected_error):
    shutil.rmtree("storage")
    res_config = ResConfig("snake_oil.ert")
    ert = EnKFMain(res_config)

    facade = LibresFacade(ert)
    with pytest.raises(
        loader.ObservationError,
        match=expected_error,
    ):
        MeasuredData(facade, [obs_key])


def create_summary_observation():
    observations = ""
    values = np.random.uniform(0, 1.5, 199)
    errors = values * 0.1
    for restart, (value, error) in enumerate(zip(values, errors)):
        restart += 1
        observations += f"""
    \nSUMMARY_OBSERVATION FOPR_{restart}
{{
    VALUE   = {value};
    ERROR   = {error};
    RESTART = {restart};
    KEY     = FOPR;
}};
    """
    return observations


def create_general_observation():
    observations = ""
    index_list = list(range(1, 2001))
    random.shuffle(index_list)
    index_list = [index_list[i : i + 4] for i in range(0, len(index_list), 4)]
    for nr, (i1, i2, i3, i4) in enumerate(index_list):
        observations += f"""
    \nGENERAL_OBSERVATION CUSTOM_DIFF_{nr}
{{
   DATA       = SNAKE_OIL_WPR_DIFF;
   INDEX_LIST = {i1},{i2},{i3},{i4};
   RESTART    = 199;
   OBS_FILE   = wpr_diff_obs.txt;
}};
    """
    return observations
