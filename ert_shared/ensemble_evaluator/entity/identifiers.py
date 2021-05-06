ID = "id"
TYPE = "type"
INPUTS = "inputs"
OUTPUTS = "outputs"
ARGS = "args"

ACTION = "action"
ACTIVE = "active"
ARG_TYPES = "arg_types"
ARGLIST = "argList"
CREATED = "created"
CURRENT_MEMORY_USAGE = "current_memory_usage"
DATA = "data"
DONE = "done"
END_TIME = "end_time"
ENVIRONMENT = "environment"
ERROR = "error"
ERROR_MSG = "error_msg"
ERROR_FILE = "error_file"
ERROR_MSG = "error_msg"
EVENT_INDEX = "event_index"
EXEC_ENV = "exec_env"
IS_EXECUTABLE = "is_executable"
EXECUTABLE = "executable"
EXECUTOR = "executor"
FORWARD_MODELS = "forward_models"
FUNCTION = "function"
IENS = "iens"
JOBS = "jobs"
LICENSE_PATH = "license_path"
LOCATION = "location"
MAX_ARG = "max_arg"
MAX_MEMORY_USAGE = "max_memory_usage"
MAX_RUNNING = "max_running"
MAX_RUNNING_MINUTES = "max_running_minutes"
MAX_RETRIES = "max_retries"
MIME = "mime"
MIN_ARG = "min_arg"
NAME = "name"
PAUSE = "pause"
REALIZATIONS = "realizations"
REALS = "reals"
RECORD = "record"
RESOURCES = "resources"
RUN_PATH = "run_path"
STAGES = "stages"
STAGE_ID = "stage_id"
START_FILE = "start_file"
START_TIME = "start_time"
STATUS = "status"
STDERR = "stderr"
STDIN = "stdin"
STDOUT = "stdout"
STEPS = "steps"
STEP_ID = "step_id"
STORAGE = "storage"
TARGET_FILE = "target_file"
TERMINATE = "terminate"
TERMINATED = "terminated"
UNIX = "unix"

EVTYPE_FM_STEP_FAILURE = "com.equinor.ert.forward_model_step.failure"
EVTYPE_FM_STEP_PENDING = "com.equinor.ert.forward_model_step.pending"
EVTYPE_FM_STEP_RUNNING = "com.equinor.ert.forward_model_step.running"
EVTYPE_FM_STEP_SUCCESS = "com.equinor.ert.forward_model_step.success"
EVTYPE_FM_STEP_UNKNOWN = "com.equinor.ert.forward_model_step.unknown"
EVTYPE_FM_STEP_WAITING = "com.equinor.ert.forward_model_step.waiting"
EVTYPE_FM_STEP_TIMEOUT = "com.equinor.ert.forward_model_step.timeout"

EVTYPE_FM_JOB_START = "com.equinor.ert.forward_model_job.start"
EVTYPE_FM_JOB_RUNNING = "com.equinor.ert.forward_model_job.running"
EVTYPE_FM_JOB_SUCCESS = "com.equinor.ert.forward_model_job.success"
EVTYPE_FM_JOB_FAILURE = "com.equinor.ert.forward_model_job.failure"


EVGROUP_FM_STEP = {
    EVTYPE_FM_STEP_FAILURE,
    EVTYPE_FM_STEP_PENDING,
    EVTYPE_FM_STEP_RUNNING,
    EVTYPE_FM_STEP_SUCCESS,
    EVTYPE_FM_STEP_UNKNOWN,
    EVTYPE_FM_STEP_WAITING,
    EVTYPE_FM_STEP_TIMEOUT,
}

EVGROUP_FM_JOB = {
    EVTYPE_FM_JOB_START,
    EVTYPE_FM_JOB_RUNNING,
    EVTYPE_FM_JOB_SUCCESS,
    EVTYPE_FM_JOB_FAILURE,
}

EVGROUP_FM_ALL = EVGROUP_FM_STEP | EVGROUP_FM_JOB

EVTYPE_EE_SNAPSHOT = "com.equinor.ert.ee.snapshot"
EVTYPE_EE_SNAPSHOT_UPDATE = "com.equinor.ert.ee.snapshot_update"
EVTYPE_EE_RESULT = "com.equinor.ert.ee.result"
EVTYPE_EE_RESULT_NOT_READY = "com.equinor.ert.ee.result_not_ready"
EVTYPE_EE_TERMINATED = "com.equinor.ert.ee.terminated"
EVTYPE_EE_USER_CANCEL = "com.equinor.ert.ee.user_cancel"
EVTYPE_EE_USER_DONE = "com.equinor.ert.ee.user_done"

EVTYPE_ENSEMBLE_STARTED = "com.equinor.ert.ensemble.started"
EVTYPE_ENSEMBLE_STOPPED = "com.equinor.ert.ensemble.stopped"
EVTYPE_ENSEMBLE_CANCELLED = "com.equinor.ert.ensemble.cancelled"
EVTYPE_ENSEMBLE_FAILED = "com.equinor.ert.ensemble.failed"

EVGROUP_ENSEMBLE = {
    EVTYPE_ENSEMBLE_STARTED,
    EVTYPE_ENSEMBLE_STOPPED,
    EVTYPE_ENSEMBLE_CANCELLED,
    EVTYPE_ENSEMBLE_FAILED,
}
