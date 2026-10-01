"""Versioned, strict contracts; observation values retain source lexical precision."""

from datetime import date
from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "2.0"
PROMPT_VERSION = "2.0"
GROUPS = ("conditions", "medications", "observations", "procedures")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Provenance(Model):
    table: str
    row: int = Field(ge=2)
    row_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Patient(Model):
    patient_id: str = Field(min_length=1)
    birth_date: date
    sex: str = Field(min_length=1)
    synthetic_name: str | None = None
    race: str | None = None
    ethnicity: str | None = None
    source: Provenance


class Encounter(Model):
    encounter_id: str = Field(min_length=1)
    patient_id: str = Field(min_length=1)
    start: AwareDatetime
    stop: AwareDatetime | None = None
    encounter_class: str
    description: str = Field(min_length=1)
    reason: str | None = None
    age: int = Field(ge=0)
    source: Provenance

    @model_validator(mode="after")
    def interval(self) -> Self:
        if self.stop is not None and self.stop < self.start:
            raise ValueError("encounter STOP precedes START")
        return self


class Fact(Model):
    fact_id: str = Field(pattern=r"^(COND|MED|OBS|PROC)_[a-f0-9]{64}$")
    patient_id: str
    encounter_id: str | None
    code: str = Field(min_length=1)
    description: str = Field(min_length=1)
    system: str | None = None
    source: Provenance


class ConditionFact(Fact):
    start: date
    stop: date | None = None
    originating_encounter_start: AwareDatetime | None = None
    active_at_encounter: bool = False

    @model_validator(mode="after")
    def interval(self) -> Self:
        if self.stop is not None and self.stop < self.start:
            raise ValueError("condition STOP precedes START")
        return self


class MedicationFact(Fact):
    start: AwareDatetime
    stop: AwareDatetime | None = None
    reason: str | None = None
    active_at_encounter: bool = False

    @model_validator(mode="after")
    def interval(self) -> Self:
        if self.stop is not None and self.stop < self.start:
            raise ValueError("medication STOP precedes START")
        return self


class ObservationFact(Fact):
    timestamp: AwareDatetime
    value: str = Field(min_length=1)
    unit: str
    category: str | None = None
    value_type: str | None = None


class ProcedureFact(Fact):
    timestamp: AwareDatetime
    stop: AwareDatetime | None = None

    @model_validator(mode="after")
    def interval(self) -> Self:
        if self.stop is not None and self.stop < self.timestamp:
            raise ValueError("procedure STOP precedes START")
        return self


class SelectedFacts(Model):
    conditions: list[ConditionFact] = Field(default_factory=list)
    medications: list[MedicationFact] = Field(default_factory=list)
    observations: list[ObservationFact] = Field(default_factory=list)
    procedures: list[ProcedureFact] = Field(default_factory=list)


class CanonicalEncounter(Model):
    patient: Patient
    encounter: Encounter
    relevant_facts: SelectedFacts


class NoteStyle(StrEnum):
    CONCISE = "concise"
    SOAP = "soap"
    NARRATIVE = "narrative"


class Annotation(Model):
    fact_id: str
    placeholder: str
    entity_type: str
    text: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    source_code: str | None
    source_table: str
    source_row: int = Field(ge=2)


class TargetEntity(Model):
    description: str


class TargetObservation(TargetEntity):
    value: str
    unit: str


class Target(Model):
    conditions: list[TargetEntity] = Field(default_factory=list)
    medications: list[TargetEntity] = Field(default_factory=list)
    observations: list[TargetObservation] = Field(default_factory=list)
    procedures: list[TargetEntity] = Field(default_factory=list)


class Note(Model):
    style: NoteStyle
    text: str = Field(min_length=1)


class Generation(Model):
    renderer: Literal["template", "llm"]
    model: str | None = None
    provider: str | None = None
    prompt_version: str = PROMPT_VERSION
    seed: int
    settings: dict = Field(default_factory=dict)
    template: str
    attempts: int = Field(ge=1)
    failures: list[str] = Field(default_factory=list)
    cached: bool = False


class Source(Model):
    dataset: Literal["synthea-compatible-synthetic"] = "synthea-compatible-synthetic"
    patient: Patient
    encounter: Encounter


class Validation(Model):
    passed: bool
    issues: list[str] = Field(default_factory=list)


class Record(Model):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    record_id: str
    patient_id: str
    encounter_id: str
    split: Literal["train", "validation", "test"]
    note: Note
    target: Target
    selected_facts: SelectedFacts
    annotations: list[Annotation]
    source: Source
    generation: Generation
    validation: Validation
