from ert_shared.feature_toggling import FeatureToggling
from ert_shared.ensemble_evaluator.entity.snapshot import PartialSnapshot, Snapshot
from ert_shared.status.entity.event import (
    EndEvent,
    FullSnapshotEvent,
    SnapshotUpdateEvent,
)
from ert_shared.status.entity.state import (
    JOB_STATE_FINISHED,
    REALIZATION_STATE_FINISHED,
)
from ert_shared.status.tracker.factory import create_tracker
import threading
from ert_shared.cli.model_factory import create_model
from ert_shared.cli.notifier import ErtCliNotifier
import shutil
import os
from argparse import ArgumentParser
from ert_shared.main import ert_parser
from ert_shared.cli import (
    ENSEMBLE_SMOOTHER_MODE,
    ENSEMBLE_EXPERIMENT_MODE,
    ITERATIVE_ENSEMBLE_SMOOTHER_MODE,
    ES_MDA_MODE,
)
from res.enkf.enkf_main import EnKFMain
from res.enkf.res_config import ResConfig
from ert_shared import ERT
import pytest


@pytest.mark.parametrize(
    "cmd_line_arguments,num_successful,num_iters",
    [
        (
            [
                ENSEMBLE_EXPERIMENT_MODE,
                "--realizations",
                "0,1,2,3,4",
                "poly_example/poly.ert",
            ],
            5,
            1,
        ),
        (
            [
                ENSEMBLE_SMOOTHER_MODE,
                "--target-case",
                "poly_runpath_file",
                "--realizations",
                "1,2,4,8,16,32,64",
                "poly_example/poly.ert",
            ],
            7,
            2,
        ),
        (
            [
                ENSEMBLE_SMOOTHER_MODE,
                "--enable-ensemble-evaluator",
                "--target-case",
                "poly_runpath_file",
                "--realizations",
                "1,2,4,8,16,32,64",
                "poly_example/poly.ert",
            ],
            7,
            2,
        ),
    ],
)
def test_tracking(cmd_line_arguments, num_successful, num_iters, tmpdir, source_root):
    shutil.copytree(
        os.path.join(source_root, "test-data", "local", "poly_example"),
        os.path.join(str(tmpdir), "poly_example"),
    )

    config_lines = ["INSTALL_JOB poly_eval2 POLY_EVAL\n" "SIMULATION_JOB poly_eval2\n"]

    with tmpdir.as_cwd():
        with open("poly_example/poly.ert", "a") as fh:
            fh.writelines(config_lines)

        parser = ArgumentParser(prog="test_main")
        parsed = ert_parser(
            parser,
            cmd_line_arguments,
        )
        FeatureToggling.update_from_args(parsed)

        res_config = ResConfig(parsed.config)
        os.chdir(res_config.config_path)
        ert = EnKFMain(res_config, strict=True, verbose=parsed.verbose)
        notifier = ErtCliNotifier(ert, parsed.config)
        ERT.adapt(notifier)

        model, argument = create_model(parsed)

        thread = threading.Thread(
            name="ert_cli_simulation_thread",
            target=model.startSimulations,
            args=(argument,),
        )
        thread.start()

        tracker = create_tracker(model, general_interval=1, detailed_interval=2)

        snapshots = {}

        for event in tracker.track():
            if isinstance(event, FullSnapshotEvent):
                snapshots[event.iteration] = Snapshot(event.snapshot.dict())
            if isinstance(event, SnapshotUpdateEvent):
                snapshots[event.iteration].merge(event.partial_snapshot.data())
            if isinstance(event, EndEvent):
                assert not event.failed
                break

        assert len(snapshots) == num_iters
        for snapshot in snapshots.values():
            assert len(snapshot.get_reals()) == num_successful
            for real in snapshot.get_reals().values():
                assert real["status"] == REALIZATION_STATE_FINISHED

                poly = real["stages"]["0"]["steps"]["0"]["jobs"]["0"]
                poly2 = real["stages"]["0"]["steps"]["0"]["jobs"]["1"]
                assert poly["name"] == "poly_eval"
                assert poly["status"] == JOB_STATE_FINISHED
                assert poly2["name"] == "poly_eval2"
                assert poly2["status"] == JOB_STATE_FINISHED