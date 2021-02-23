import pandas as pd
import pytest

from ert_data import loader
from ert_data.measured import MeasuredData

from unittest.mock import Mock


@pytest.fixture()
def valid_obs_data():
    df = pd.DataFrame(data=[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], index=["OBS", "STD"])
    df.columns = _set_multiindex(df)
    return df


@pytest.fixture()
def valid_data():
    df = pd.DataFrame(data=[[7.0, 8.0, 9.0]], index=[1])
    return df


@pytest.fixture()
def measured_data_setup():
    def _setup(input_dataframe, valid_obs_data, monkeypatch):
        input_dataframe.columns = _set_multiindex(input_dataframe)
        mock_data = pd.concat(
            {"obs_key": pd.concat([valid_obs_data, input_dataframe])}, axis=1
        )
        mocked_data_loader = Mock(return_value=mock_data)
        factory = Mock(return_value=(mocked_data_loader))
        monkeypatch.setattr(loader, "data_loader_factory", factory)
        return factory

    return _setup


def _set_multiindex(df):
    tuples = list(zip(*[df.columns.to_list(), df.columns.to_list()]))
    return pd.MultiIndex.from_tuples(tuples, names=["key_index", "data_index"])


@pytest.mark.parametrize("obs_type", [("GEN_OBS"), ("SUMMARY_OBS"), ("BLOCK_OBS")])
def test_get_data(
    obs_type, monkeypatch, facade, valid_data, measured_data_setup, valid_obs_data
):

    facade.get_impl_type_name_for_obs_key.return_value = obs_type
    facade.get_data_key_for_obs_key.return_value = "data_key"

    factory = measured_data_setup(valid_data, valid_obs_data, monkeypatch)
    md = MeasuredData(facade, ["obs_key"], index_lists=[[1, 2]])

    factory.assert_called_once_with(obs_type)
    mocked_data_loader = factory()
    mocked_data_loader.assert_called_once_with(facade, ["obs_key"], "test_case", include_data=True)
    df = pd.DataFrame(
        data=[[2.0, 3.0], [5.0, 6.0], [8.0, 9.0]], index=["OBS", "STD", 1], columns=[1, 2]
    )
    df.columns = _set_multiindex(df)
    expected_result = pd.concat({"obs_key": df}, axis=1)

    assert md.data.equals(expected_result)


@pytest.mark.parametrize(
    "input_dataframe,expected_result",
    [
        (
            pd.DataFrame(data=[[7, 8, 9]], index=[1]),
            pd.DataFrame(
                data=[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]],
                index=["OBS", "STD", 1],
            ),
        ),
        (
            pd.DataFrame(data=[[None, None, None]], index=[1]),
            pd.DataFrame(data=[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], index=["OBS", "STD"]),
        ),
        (
            pd.DataFrame(data=[[7, 8, None]], index=[1]),
            pd.DataFrame(
                data=[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, None]],
                index=["OBS", "STD", 1],
            ),
        ),
    ],
)
def test_remove_failed_realizations(
    input_dataframe,
    expected_result,
    monkeypatch,
    facade,
    measured_data_setup,
    valid_obs_data,
):
    measured_data_setup(input_dataframe, valid_obs_data, monkeypatch)
    md = MeasuredData(facade, ["obs_key"])

    md = md.remove_failed_realizations()

    expected_result.columns = _set_multiindex(expected_result)
    expected_result = pd.concat({"obs_key": expected_result}, axis=1)
    assert md.data.equals(expected_result)


@pytest.mark.usefixtures("facade", "measured_data_setup")
@pytest.mark.parametrize(
    "input_dataframe,expected_result",
    [
        (
            pd.DataFrame(data=[[1, 2, 3], [4, 5, 6]], index=["OBS", "STD"]),
            pd.DataFrame(
                data=[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]],
                index=["OBS", "STD", 1],
            ),
        ),
        (
            pd.DataFrame(data=[[1, None, 3], [4, None, 6]], index=["OBS", "STD"]),
            pd.DataFrame(
                data=[[1.0, 3.0], [4.0, 6.0], [7.0, 9.0]],
                index=["OBS", "STD", 1],
                columns=[0, 2],
            ),
        ),
    ],
)
def test_remove_inactive_observations(
    input_dataframe,
    expected_result,
    monkeypatch,
    facade,
    measured_data_setup,
    valid_data,
):
    input_dataframe.columns = _set_multiindex(input_dataframe)
    measured_data_setup(valid_data, input_dataframe, monkeypatch)
    md = MeasuredData(facade, ["obs_key"])

    expected_result.columns = _set_multiindex(expected_result)
    expected_result = pd.concat({"obs_key": expected_result}, axis=1)

    md = md.remove_inactive_observations()
    assert md.data.equals(expected_result)


@pytest.mark.usefixtures("facade", "measured_data_setup")
@pytest.mark.parametrize(
    "std_cutoff,expected_result",
    [
        (
            -1,
            pd.DataFrame(
                data=[[1.0, 2.0], [0.1, 0.2], [1, 1.5], [1, 2.5]],
                index=["OBS", "STD", 1, 2],
            ),
        ),
        (
            0,
            pd.DataFrame(
                data=[[2], [0.2], [1.5], [2.5]], index=["OBS", "STD", 1, 2], columns=[1]
            ),
        ),
        (1.0, pd.DataFrame(index=["OBS", "STD", 1, 2])),
    ],
)
def test_filter_ensamble_std(
    std_cutoff, expected_result, monkeypatch, facade, measured_data_setup
):
    expected_result.columns = _set_multiindex(expected_result)

    input_dataframe = pd.DataFrame(data=[[1, 1.5], [1, 2.5]], index=[1, 2])
    input_obs = pd.DataFrame(data=[[1, 2], [0.1, 0.2]], index=["OBS", "STD"])
    input_obs.columns = _set_multiindex(input_obs)
    measured_data_setup(input_dataframe, input_obs, monkeypatch)
    md = MeasuredData(facade, ["obs_key"])

    md = md.filter_ensemble_std(std_cutoff)
    assert md.data.equals(pd.concat({"obs_key": expected_result}, axis=1))


@pytest.mark.usefixtures("facade", "measured_data_setup")
@pytest.mark.parametrize(
    "alpha,expected_result",
    [
        (
            10,
            pd.DataFrame(
                data=[[1, 2], [0.1, 0.2], [1.1, 1.6], [1, 2.5]],
                index=["OBS", "STD", 1, 2],
            ),
        ),
        (
            0.2,
            pd.DataFrame(
                data=[[2.0], [0.2], [1.6], [2.5]],
                index=["OBS", "STD", 1, 2],
                columns=[1],
            ),
        ),
        (0, pd.DataFrame(index=["OBS", "STD", 1, 2])),
    ],
)
def test_filter_ens_mean_obs(
    alpha, expected_result, monkeypatch, facade, measured_data_setup
):
    expected_result.columns = _set_multiindex(expected_result)

    input_dataframe = pd.DataFrame(data=[[1.1, 1.6], [1, 2.5]], index=[1, 2])
    input_obs = pd.DataFrame(data=[[1, 2], [0.1, 0.2]], index=["OBS", "STD"])
    input_obs.columns = _set_multiindex(input_obs)

    measured_data_setup(input_dataframe, input_obs, monkeypatch)
    md = MeasuredData(facade, ["obs_key"])

    md = md.filter_ensemble_mean_obs(alpha)
    assert md.equals(pd.concat({"obs_key": expected_result}, axis=1))


@pytest.mark.usefixtures("facade", "measured_data_setup")
@pytest.mark.parametrize(
    "input_dataframe,expected_result",
    [
        (
            pd.DataFrame(data=[[7, 8, 9]], index=[1]),
            pd.DataFrame(data=[[7, 8, 9]], index=[1]),
        ),
        (
            pd.DataFrame(
                data=[[7, 8, 9], [10, 11, 12]],
                index=[1, 2],
            ),
            pd.DataFrame(
                data=[[7, 8, 9], [10, 11, 12]], index=[1, 2], columns=[0, 1, 2]
            ),
        ),
    ],
)
def test_get_simulated_data(
    input_dataframe,
    expected_result,
    monkeypatch,
    facade,
    measured_data_setup,
    valid_obs_data,
):
    measured_data_setup(input_dataframe, valid_obs_data, monkeypatch)
    md = MeasuredData(facade, ["obs_key"])

    expected_result.columns = _set_multiindex(expected_result)

    result = md.get_simulated_data()
    assert result.equals(pd.concat({"obs_key": expected_result.astype(float)}, axis=1))
