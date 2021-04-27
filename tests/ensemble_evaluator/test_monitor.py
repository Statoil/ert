from ert_shared.ensemble_evaluator.monitor import _Monitor
import ert_shared.ensemble_evaluator.entity.identifiers as ids
from ert_shared.status.entity.state import (
    ENSEMBLE_STATE_STOPPED,
)

from tests.narratives import monitor_happy_path_narrative


def test_consume(unused_tcp_port):
    expected_events_types = iter(
        [
            ids.EVTYPE_EE_SNAPSHOT,
            ids.EVTYPE_EE_SNAPSHOT_UPDATE,
            ids.EVTYPE_EE_SNAPSHOT_UPDATE,
            ids.EVTYPE_EE_TERMINATED,
        ]
    )
    with monitor_happy_path_narrative.on_uri(
        f"ws://localhost:{unused_tcp_port}/client"
    ) as narrative_mock:
        with _Monitor(narrative_mock.hostname, narrative_mock.port) as monitor:
            for event in monitor.track():
                assert event["type"] == next(expected_events_types)
                if event["type"] == ids.EVTYPE_EE_TERMINATED:
                    return
                if event.data and event.data.get(ids.STATUS) == ENSEMBLE_STATE_STOPPED:
                    monitor.signal_done()
