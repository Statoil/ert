import json
import pprint

import pytest
from ert_shared.storage.blob_api import BlobApi
from ert_shared.storage.rdb_api import RdbApi
from ert_shared.storage.storage_api import StorageApi

from tests.storage import populated_db


def test_response(populated_db):
    with StorageApi(rdb_url=populated_db, blob_url=populated_db) as api:
        schema = api.response(1, "response_one", None)
        assert len(schema["realizations"]) == 2
        assert len(schema["observations"]) == 1
        assert len(schema["observations"][0]["data"]) == 4
        print("############### RESPONSE ###############")
        pprint.pprint(schema)


def test_ensembles(populated_db):
    with StorageApi(rdb_url=populated_db, blob_url=populated_db) as api:
        schema = api.ensembles()
        print("############### ENSEBMLES ###############")
        pprint.pprint(schema)


def test_ensemble(populated_db):
    with StorageApi(rdb_url=populated_db, blob_url=populated_db) as api:
        schema = api.ensemble_schema(1)
        print("############### ENSEMBLE ###############")
        pprint.pprint(schema)


def test_realization(populated_db):
    with StorageApi(rdb_url=populated_db, blob_url=populated_db) as api:
        schema = api.realization(ensemble_id=1, realization_idx=0, filter=None)
        print("############### REALIZATION ###############")
        pprint.pprint(schema)


def test_priors(populated_db):
    with StorageApi(rdb_url=populated_db, blob_url=populated_db) as api:
        schema = api.ensemble_schema(1)
        assert {
            "group": "group",
            "key": "key1",
            "prior": {
                "function": "function",
                "parameter_names": ["paramA", "paramB"],
                "parameter_values": [0.1, 0.2],
            },
            "parameter_ref": 3,
        } in schema["parameters"]


def test_parameter(populated_db):
    with StorageApi(rdb_url=populated_db, blob_url=populated_db) as api:
        schema = api.parameter(ensemble_id=1, parameter_def_id="3")
        assert schema["key"] == "key1"
        assert schema["group"] == "group"
        assert schema["prior"]["function"] == "function"


def test_observation(populated_db):
    name = "observation_one"
    with StorageApi(rdb_url=populated_db, blob_url=populated_db) as api:
        obs = api.observation("observation_one")
        assert obs == {
            "attributes": {"region": "1"},
            "data": {
                "data_indexes": {"data_ref": 2},
                "key_indexes": {"data_ref": 1},
                "std": {"data_ref": 4},
                "values": {"data_ref": 3},
            },
        }


def test_observation_attributes(populated_db):
    attr = "region"
    value = "1"
    name = "observation_one"
    expected = {"attributes": {attr: value}}

    with StorageApi(rdb_url=populated_db, blob_url=populated_db) as api:
        api.set_observation_attribute(name, attr, value)

    with StorageApi(rdb_url=populated_db, blob_url=populated_db) as api:
        assert api.get_observation_attribute(name, attr) == expected
