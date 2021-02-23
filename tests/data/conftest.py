import pytest
import sys

from unittest.mock import Mock
from ert_shared.libres_facade import LibresFacade


@pytest.fixture()
def facade():
    obs_mock = Mock()
    obs_mock.getDataKey.return_value = "test_data_key"
    obs_mock.getStepList.return_value = [1]

    facade = Mock(spec=LibresFacade)
    facade.get_ensemble_size.return_value = 3
    facade.get_observations.return_value = {"some_key": obs_mock}
    facade.get_data_key_for_obs_key.return_value = "some_key"

    facade.get_current_case_name.return_value = "test_case"

    return facade
