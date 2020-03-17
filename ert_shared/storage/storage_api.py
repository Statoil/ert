from ert_shared.storage.rdb_api import RdbApi
import pandas as pd
from ert_shared.storage.rdb_api import RdbApi


def get_response_data(name, ensemble_name, rdb_api=None):
    if rdb_api is None:
        rdb_api = RdbApi()

    with rdb_api:
        for response in rdb_api.get_response_data(name, ensemble_name):
            yield response


def get_all_ensembles(rdb_api=None):
    if rdb_api is None:
        rdb_api = RdbApi()

    with rdb_api:
        return [ensemble.name for ensemble in rdb_api.get_all_ensembles()]


class PlotStorageApi(object):

    def __init__(self, session=None):
        self._session = session

    def _repo(self):
        if self._session is not None:
            return RdbApi(self._session)
        else:
            return RdbApi()

    def all_data_type_keys(self):
        """ Returns a list of all the keys except observation keys. For each key a dict is returned with info about
            the key"""
        # Will likely have to change this to somehow include the ensemble name
        ens = self._repo().get_all_ensembles()[0]

        result = []

        result.extend([{
            "key": param.name,
            "index_type": None,
            "observations": [],
            "has_refcase": False,
            "dimensionality": 1,
            "metadata": {"data_origin": "Parameters"}
        } for param in ens.parameter_definitions])

        def _obs_names(obs):
            if obs is None:
                return []
            return [obs.name]

        result.extend([{
            "key": resp.name,
            "index_type": None,
            "observations": _obs_names(resp.observation),
            "has_refcase": False,
            "dimensionality": 2,
            "metadata": {"data_origin": "Response"}
        } for resp in ens.response_definitions])

        return result


    def get_all_cases_not_running(self):
        """ Returns a list of all cases that are not running. For each case a dict with info about the case is
            returned """
        return [{'has_data': True, 'hidden': False, 'name': 'default'}]


    def data_for_key(self, case, key):
        """ Returns a pandas DataFrame with the datapoints for a given key for a given case. The row index is
            the realization number, and the column index is a multi-index with (key, index/date)"""

        ens = self._repo().get_ensemble(case)
        data = [resp.values for real in ens.realizations for resp in real.responses if resp.response_definition.name==key]
        if len(data) > 0:
            df = pd.DataFrame(data)
            with_key = pd.concat({key:df}, axis=1).astype(float)
        else:
            data = [param.value for real in ens.realizations for param in real.parameters if param.parameter_definition.name==key]
            with_key = pd.DataFrame(data, columns=[key]).astype(float)

        return with_key

    def observations_for_obs_keys(self, case, obs_keys):
        """ Returns a pandas DataFrame with the datapoints for a given observation key for a given case. The row index
            is the realization number, and the column index is a multi-index with (obs_key, index/date, obs_index),
            where index/date is used to relate the observation to the data point it relates to, and obs_index is
            the index for the observation itself"""
        if len(obs_keys) > 0:
            obs = self._repo().get_observation(obs_keys[0])
            values = obs.values
            idx = pd.MultiIndex.from_arrays([range(len(obs.key_indexes)), obs.key_indexes],
                                             names=['data_index', 'key_index'])
            values = pd.DataFrame(obs.values)
            stds = pd.DataFrame(obs.stds)
            all = pd.DataFrame({"OBS":obs.values, "STD":obs.stds}, index=idx)
            return all.T
        else:
            return pd.DataFrame()

    def make_df(self, msgs):
        case_to_data = {}
        done = False
        df = None
        idx = None
        while not done:
            msg = next(msgs)
            if msg.type == "ensemble" and msg.status == "start":
                df = pd.DataFrame()
            elif msg.type == "realisation" and msg.status == "start":
                real_df = pd.DataFrame()
                real_done = False

                while not real_done:
                    msg = next(msgs)
                    if msg.type == "realisation" and msg.status == "done":
                        df = df.append(real_df.T)
                        real_done = True
                    elif msg.type == "parameter" and msg.status == "start":
                        param_series = pd.Series()
                        param_done = False
                        while not param_done:
                            msg = next(msgs)
                            if msg.type == "parameter" and msg.status == "chunk":
                                param_series = param_series.append(pd.Series(msg.value), ignore_index=True)
                            elif msg.type == "parameter" and msg.status == "done":
                                real_df = real_df.append(param_series, ignore_index=True)
                                param_done = True
            elif msg.type == "ensemble" and msg.status == "stop":
                case_to_data[msg.name] = df
                pass
            elif msg.type == "EOL":
                done = True
        return case_to_data

    def get_param_data(self, ensembles, keys=[], realizations=[]):

        for ens_name in ensembles:
            ens = self._repo().get_ensemble(ens_name)

            yield DataStreamMessage("ensemble", ens_name, "start")

            for param_def in ens.parameter_definitions:
                if param_def.name in keys or len(keys) == 0:
                    if len(param_def.parameters) > 0:
                        size = 1 # len(param_def.parameters[0].value)
                    else:
                        size = 0
                    yield DataStreamMessage("param_def", param_def.name, "size", size)

            for real in ens.realizations:
                if real.id in realizations or len(realizations) == 0:
                    yield DataStreamMessage("realisation", real.id, "start")
                    for param_def in ens.parameter_definitions:
                        if param_def.name in keys or len(keys) == 0:
                            for param in real.parameters:
                                if param.parameter_definition == param_def:
                                    yield DataStreamMessage("parameter", param_def.name, "start")
                                    yield DataStreamMessage("parameter", param_def.name, "chunk", param.value)
                            yield DataStreamMessage("parameter", param_def.name, "done")
                    yield DataStreamMessage("realisation", real.id, "done")

            yield DataStreamMessage("ensemble", ens_name, "stop")

        yield DataStreamMessage("EOL", "EOL", "EOL")

    def _add_index_range(self, data):
        """
        Adds a second column index with which corresponds to the data
        index. This is because in libres simulated data and observations
        are connected through an observation key and data index, so having
        that information available when the data is joined is helpful.
        """
        pass


    def refcase_data(self, key):
        """ Returns a pandas DataFrame with the data points for the refcase for a given data key, if any.
            The row index is the index/date and the column index is the key."""
        return pd.DataFrame()

class DataStreamMessage(object):

    def __init__(self, type, name, status, value=None):
        self.value = value
        self.status = status
        self.name = name
        self.type = type

    def __repr__(self):
        return "DataStreamMessage: " + str(self.__dict__)