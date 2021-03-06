from importlib.abc import Loader
import importlib.util
import mimetypes
from typing import Callable, List, cast, Union, Dict, Any

from pydantic import BaseModel, FilePath, ValidationError, validator
import ert3

_DEFAULT_RECORD_MIME_TYPE: str = "application/json"
_DEFAULT_CMD_MIME_TYPE: str = "application/octet-stream"


def _import_from(path: str) -> Callable[..., Any]:
    if ":" not in path:
        raise ValueError("Function should be defined as module:function")
    module_str, func = path.split(":")
    spec = importlib.util.find_spec(module_str)
    if spec is None:
        raise ModuleNotFoundError(f"No module named '{module_str}'")
    module = importlib.util.module_from_spec(spec)
    # A loader should always have been set, and it is assumed it a PEP 302
    # compliant Loader. A cast is made to make this clear to the typing system.
    cast(Loader, spec.loader).exec_module(module)
    try:
        return cast(Callable[..., Any], getattr(module, func))
    except AttributeError:
        raise ImportError(name=func, path=module_str)


class _StagesConfig(BaseModel):
    class Config:
        validate_all = True
        validate_assignment = True
        extra = "forbid"
        allow_mutation = False
        arbitrary_types_allowed = True


def _ensure_mime(cls: _StagesConfig, field: str, values: Dict[str, Any]) -> str:
    if field:
        return field
    guess = mimetypes.guess_type(str(values.get("location", "")))[0]
    if guess:
        return guess
    return (
        _DEFAULT_CMD_MIME_TYPE
        if cls == TransportableCommand
        else _DEFAULT_RECORD_MIME_TYPE
    )


class _MimeBase(_StagesConfig):
    mime: str = ""

    @validator("mime")
    def _ensure_input_record_mime(cls, field: str, values: Dict[str, Any]) -> str:
        return _ensure_mime(cls, field, values)


class Record(_MimeBase):
    record: str
    location: str


class TransportableCommand(_MimeBase):
    name: str
    location: FilePath


class _Step(_StagesConfig):
    name: str
    input: List[Record]
    output: List[Record]


class Function(_Step):
    function: Callable  # type: ignore

    @validator("function", pre=True)
    def function_is_callable(cls, value) -> Callable:  # type: ignore
        return _import_from(value)


class Unix(_Step):
    script: List[str]
    transportable_commands: List[TransportableCommand]


class StagesConfig(BaseModel):
    __root__: List[Union[Function, Unix]]

    def step_from_key(self, key: str) -> Union[Function, Unix, None]:
        return next((step for step in self if step.name == key), None)

    def __iter__(self):  # type: ignore
        return iter(self.__root__)

    def __getitem__(self, item):  # type: ignore
        return self.__root__[item]

    def __len__(self):  # type: ignore
        return len(self.__root__)


def load_stages_config(config_dict: Dict[str, Any]) -> StagesConfig:
    try:
        return StagesConfig.parse_obj(config_dict)
    except ValidationError as err:
        raise ert3.exceptions.ConfigValidationError(str(err), source="stages")
