from typing import List, Mapping, Union
from pydantic import BaseModel, validator
from datetime import datetime


class ObservationBase(BaseModel):
    name: str
    key_indices: Union[List[int], List[datetime]]
    data_indices: List[int]
    values: List[float]
    errors: List[float]


class ObservationCreate(ObservationBase):
    pass


class ObservationUpdate(ObservationBase):
    pass


class Observation(ObservationBase):
    id: int
    attributes: Mapping[str, str]

    @validator("attributes", pre=True)
    def _validate_attributes(cls, val):
        return {k: v.value for k, v in val.items()}

    class Config:
        orm_mode = True
