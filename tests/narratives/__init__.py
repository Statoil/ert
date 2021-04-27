import pickle
from ert_shared.ensemble_evaluator.entity import serialization
import re
from ert_shared.status.entity.state import (
    ENSEMBLE_STATE_STARTED,
    ENSEMBLE_STATE_STOPPED,
)
from tests.narrative import (
    Consumer,
    EventDescription,
    Provider,
    ReMatch,
)
import ert_shared.ensemble_evaluator.entity.identifiers as identifiers


dispatchers_failing_job_narrative = (
    Consumer("Dispatch")
    .forms_narrative_with(Provider("Ensemble Evaluator"))
    .given("small ensemble")
    .receives("a job eventually fails")
    .cloudevents_in_order(
        [
            EventDescription(
                type_=identifiers.EVTYPE_FM_JOB_RUNNING,
                source="/ert/ee/0/real/0/stage/0/step/0/job/0",
                data={identifiers.CURRENT_MEMORY_USAGE: 1000},
            ),
            EventDescription(
                type_=identifiers.EVTYPE_FM_JOB_RUNNING,
                source="/ert/ee/0/real/1/stage/0/step/0/job/0",
                data={identifiers.CURRENT_MEMORY_USAGE: 2000},
            ),
            EventDescription(
                type_=identifiers.EVTYPE_FM_JOB_SUCCESS,
                source="/ert/ee/0/real/0/stage/0/step/0/job/0",
                data={identifiers.CURRENT_MEMORY_USAGE: 2000},
            ),
            EventDescription(
                type_=identifiers.EVTYPE_FM_JOB_FAILURE,
                source="/ert/ee/0/real/1/stage/0/step/0/job/0",
                data={identifiers.ERROR_MSG: "error"},
            ),
        ]
    )
)

monitor_happy_path_narrative = (
    Consumer("Monitor")
    .forms_narrative_with(
        Provider("Ensemble Evaluator"),
    )
    .given("a successful one-member one-step one-job ensemble")
    .responds_with("starting snapshot")
    .cloudevents_in_order(
        [
            EventDescription(
                type_=identifiers.EVTYPE_EE_SNAPSHOT,
                source=ReMatch(re.compile(r"/ert/ee/ee."), "/ert/ee/ee-0"),
            ),
        ]
    )
    .responds_with("a bunch of snapshot updates")
    .repeating_unordered_events(
        [
            EventDescription(
                type_=identifiers.EVTYPE_EE_SNAPSHOT_UPDATE,
                source=ReMatch(re.compile(r"/ert/ee/ee."), "/ert/ee/ee-0"),
            ),
        ],
        terminator=EventDescription(
            type_=identifiers.EVTYPE_EE_SNAPSHOT_UPDATE,
            source=ReMatch(re.compile(r"/ert/ee/ee."), "/ert/ee/ee-0"),
            data={identifiers.STATUS: ENSEMBLE_STATE_STOPPED},
        ),
    )
    .receives("done")
    .cloudevents_in_order(
        [
            EventDescription(
                type_=identifiers.EVTYPE_EE_USER_DONE,
                source=ReMatch(re.compile(r"/ert/monitor/."), "/ert/monitor/007"),
            ),
        ]
    )
    .responds_with("termination")
    .cloudevents_in_order(
        [
            EventDescription(
                type_=identifiers.EVTYPE_EE_TERMINATED,
                source=ReMatch(re.compile(r"/ert/ee/ee."), "/ert/ee/ee-0"),
                datacontenttype="application/octet-stream",
                data=b"\x80\x04\x95\x0f\x00\x00\x00\x00\x00\x00\x00\x8c\x0bhello world\x94.",
            ),
        ]
    )
    .with_marshaller("application/json", serialization.evaluator_marshaller)
    .with_unmarshaller("application/json", serialization.evaluator_unmarshaller)
    .with_unmarshaller("application/octet-stream", pickle.loads)
)
