from collections import defaultdict
from typing import Optional, List
import deprecation
import pandas as pd
import numpy as np

from ert_data import loader
from ert_shared.libres_facade import LibresFacade

# importlib.metadata added in python 3.8
try:
    from importlib import metadata

    __version__ = metadata.version("ert")
except ImportError:
    from pkg_resources import get_distribution

    __version__ = get_distribution("ert").version


class MeasuredData(pd.DataFrame):
    """
    MeasuredData is an object designed to extract data from an ert run and present
    it as a pandas DataFrame. It has the properties of a DataFrame but a set structure
    to conform to having observations and responses. It also has a few functions to filter
    based on the response and/or observations. If not observation keys are provided, all
    observations are loaded.
    """
    def __init__(
        self,
        facade: LibresFacade,
        keys: Optional[List[str]] = None,
        index_lists: Optional[List[List[int]]] = None,
        load_data: bool = True,
        case_name: Optional[str] = None,
        **kwargs,
    ):
        """
        This is a bit ugly, but is close to the way pandas does this internally.
        The problem is that when an operation is performed on a DataFrame, a view of
        the dataframe is returned. We want that returned dataframe to be a MeasuredData
        object, but we dont want to read from libres each time, so we need to have a
        variable constructor.
        """
        if isinstance(facade, LibresFacade):
            if not keys:
                keys = [facade.get_observation_key(nr) for nr, _ in enumerate(facade.get_observations())]
            if case_name is None:
                case_name = facade.get_current_case_name()
            df = MeasuredData._get_data(facade, keys, load_data, case_name)
            if index_lists:
                df = MeasuredData.filter_on_column_index(df, keys, index_lists)
            super().__init__(data=df.values, columns=df.columns, index=df.index)
        else:
            super().__init__(facade, **kwargs)

    @property
    def _constructor(self):
        """
        Part of the pandas API, we want views to be a MeasuredData object
        """
        return MeasuredData

    @property
    def response(self):
        return self._get_simulated_data()

    @property
    def observations(self):
        return self.loc[["OBS", "STD"]]

    @property
    def data(self):
        return self

    def remove_failed_realizations(self):
        return self._remove_failed_realizations()

    @deprecation.deprecated(
        deprecated_in="2.19",
        removed_in="3.0",
        current_version=__version__,
        details="The the response property instead",
    )
    def get_simulated_data(self):
        return self._get_simulated_data()

    def _get_simulated_data(self):
        return self[~self.index.isin(["OBS", "STD"])]

    def _remove_failed_realizations(self):
        """Removes rows with no simulated data, leaving observations and
        standard deviations as-is."""
        pre_index = self.index
        post_index = list(self.dropna(axis=0, how="all").index)
        drop_index = set(pre_index) - set(post_index + ["STD", "OBS"])
        return self.drop(index=drop_index)

    def remove_inactive_observations(self):
        return self._remove_inactive_observations()

    def _remove_inactive_observations(self):
        """Removes columns with one or more NaN values."""
        filtered_dataset = self.dropna(axis=1)
        if filtered_dataset.empty:
            raise ValueError(
                "This operation results in an empty dataset (could be due to one or more failed realizations)"
            )
        return filtered_dataset

    @deprecation.deprecated(
        deprecated_in="2.19",
        removed_in="3.0",
        current_version=__version__,
        details="The the empty property instead",
    )
    def is_empty(self):
        return self.empty

    @staticmethod
    def _get_data(facade, observation_keys, load_data, case_name):
        """
        Adds simulated and observed data and returns a dataframe where ensamble
        members will have a data key, observed data will be named OBS and
        observed standard deviation will be named STD.
        """

        # Because several observations can be linked to the same response we create
        # a grouping to avoid reading the same response for each of the corresponding
        # observations, as that is quite slow.
        key_map = defaultdict(list)
        for key in observation_keys:
            try:
                data_key = facade.get_data_key_for_obs_key(key)
            except KeyError:
                raise loader.ObservationError(f"No data key for obs key: {key}")
            key_map[data_key].append(key)

        measured_data = []

        for obs_keys in key_map.values():
            obs_types = [facade.get_impl_type_name_for_obs_key(key) for key in obs_keys]
            assert (
                    len(set(obs_types)) == 1
            ), f"\nMore than one observation type found for obs keys: {obs_keys}"
            observation_type = obs_types[0]
            data_loader = loader.data_loader_factory(observation_type)
            data = data_loader(facade, obs_keys, case_name, include_data=load_data)
            if data.empty:
                raise loader.ObservationError(f"No observations loaded for {obs_keys}")
            measured_data.append(data)

        return pd.concat(measured_data, axis=1)

    def filter_ensemble_std(self, std_cutoff):
        return self._filter_ensemble_std(std_cutoff)

    def filter_ensemble_mean_obs(self, alpha):
        return self._filter_ensemble_mean_obs(alpha)

    def _filter_ensemble_std(self, std_cutoff):
        """
        Filters on ensamble variation versus a user defined standard
        deviation cutoff. If there is not enough variation in the measurements
        the data point is removed.
        """
        ens_std = self.response.std()
        std_filter = ens_std <= std_cutoff
        return self.drop(columns=std_filter[std_filter].index)

    def _filter_ensemble_mean_obs(self, alpha):
        """
        Filters on distance between the observed data and the ensamble mean
        based on variation and a user defined alpha.
        """
        ens_mean = self.response.mean()
        ens_std = self.response.std()
        obs_values = self.loc["OBS"]
        obs_std = self.loc["STD"]

        mean_filter = abs(obs_values - ens_mean) > alpha * (ens_std + obs_std)

        return self.drop(columns=mean_filter[mean_filter].index)

    def filter_on_column_index(self, obs_keys, index_lists):
        if index_lists is None or all(index_list is None for index_list in index_lists):
            return self
        names = self.columns.get_level_values(0)
        data_index = self.columns.get_level_values("data_index")
        cond = MeasuredData._create_condition(names, data_index, obs_keys, index_lists)
        return self.iloc[:, cond]

    @staticmethod
    def _create_condition(names, data_index, obs_keys, index_lists):
        conditions = []
        for obs_key, index_list in zip(obs_keys, index_lists):
            if index_list is not None:
                index_cond = [data_index == index for index in index_list]
                index_cond = np.logical_or.reduce(index_cond)
                conditions.append(np.logical_and(index_cond, (names == obs_key)))
        return np.logical_or.reduce(conditions)
