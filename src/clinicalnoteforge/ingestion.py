"""Synthea CSV boundary. Optional tables may be absent; malformed tables may not."""

import csv
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol

from .models import (
    ConditionFact,
    Encounter,
    MedicationFact,
    ObservationFact,
    Patient,
    ProcedureFact,
    Provenance,
    SelectedFacts,
)

REQUIRED = {
    "patients": {"Id", "BIRTHDATE", "GENDER"},
    "encounters": {"Id", "START", "STOP", "PATIENT", "ENCOUNTERCLASS", "DESCRIPTION"},
    "conditions": {"START", "STOP", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION"},
    "medications": {"START", "STOP", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION"},
    "observations": {"DATE", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION", "VALUE", "UNITS"},
    "procedures": {"START", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION"},
}
PREFIX = {"conditions": "COND", "medications": "MED", "observations": "OBS", "procedures": "PROC"}


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp requires UTC offset: {value!r}")
    return parsed.astimezone(UTC)


def provenance(table: str, number: int, row: dict) -> Provenance:
    return Provenance(table=table, row=number, row_sha256=digest(row))


@dataclass
class SourceData:
    patients: dict[str, Patient]
    encounters: dict[str, Encounter]
    facts_by_patient: dict[str, SelectedFacts]
    files: dict[str, dict]
    warnings: list[str]


class EHRAdapter(Protocol):
    def load(self, directory: Path) -> SourceData: ...


class SyntheaCSVAdapter:
    def load(self, directory: Path) -> SourceData:
        rows = {}
        files = {}
        warnings = []
        for table, columns in REQUIRED.items():
            path = directory / f"{table}.csv"
            if not path.is_file():
                if table in ("patients", "encounters"):
                    raise ValueError(f"missing required file: {path.name}")
                warnings.append(f"optional table absent: {path.name}")
                rows[table] = []
                continue
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                header = reader.fieldnames or []
                missing = columns - set(header)
                if missing:
                    raise ValueError(f"{path.name}: missing required columns: {sorted(missing)}")
                if len(set(header)) != len(header):
                    raise ValueError(f"{path.name}: duplicate column names")
                entries = []
                for number, row in enumerate(reader, 2):
                    if None in row or any(v is None for v in row.values()):
                        raise ValueError(f"{path.name} row {number}: inconsistent column count")
                    entries.append((number, row))
                rows[table] = entries
            files[path.name] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "rows": len(rows[table]),
                "columns": header,
            }
        patients = {}
        for number, row in rows["patients"]:
            try:
                patient = Patient(
                    patient_id=row["Id"],
                    birth_date=date.fromisoformat(row["BIRTHDATE"]),
                    sex=row["GENDER"],
                    synthetic_name=" ".join(
                        row.get(k, "") for k in ("FIRST", "MIDDLE", "LAST") if row.get(k)
                    )
                    or None,
                    race=row.get("RACE") or None,
                    ethnicity=row.get("ETHNICITY") or None,
                    source=provenance("patients", number, row),
                )
                if patient.patient_id in patients:
                    raise ValueError(f"duplicate patient ID: {patient.patient_id}")
                patients[patient.patient_id] = patient
            except ValueError as exc:
                raise ValueError(f"patients.csv row {number}: {exc}") from exc
        encounters = {}
        for number, row in rows["encounters"]:
            try:
                patient = patients.get(row["PATIENT"])
                if patient is None:
                    raise ValueError(f"unknown patient: {row['PATIENT']}")
                start = timestamp(row["START"])
                day, birth = start.date(), patient.birth_date
                age = day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))
                if day < birth:
                    raise ValueError("encounter before patient birth")
                encounter = Encounter(
                    encounter_id=row["Id"],
                    patient_id=patient.patient_id,
                    start=start,
                    stop=timestamp(row["STOP"]) if row["STOP"] else None,
                    encounter_class=row["ENCOUNTERCLASS"].lower(),
                    description=row["DESCRIPTION"],
                    reason=row.get("REASONDESCRIPTION") or None,
                    age=age,
                    source=provenance("encounters", number, row),
                )
                if encounter.encounter_id in encounters:
                    raise ValueError(f"duplicate encounter ID: {encounter.encounter_id}")
                encounters[encounter.encounter_id] = encounter
            except ValueError as exc:
                raise ValueError(f"encounters.csv row {number}: {exc}") from exc
        facts_by_patient = defaultdict(SelectedFacts)
        for table in PREFIX:
            for number, row in rows[table]:
                try:
                    patient_id, encounter_id = row["PATIENT"], row["ENCOUNTER"] or None
                    if patient_id not in patients:
                        raise ValueError(f"unknown patient: {patient_id}")
                    linked = None
                    if encounter_id:
                        linked = encounters.get(encounter_id)
                        if linked is None and table == "conditions":
                            warnings.append(
                                f"conditions.csv row {number}: origin encounter unavailable; "
                                "using inclusive calendar-date fallback"
                            )
                        elif linked is None or linked.patient_id != patient_id:
                            raise ValueError(f"invalid encounter/patient reference: {encounter_id}")
                    source = provenance(table, number, row)
                    common = dict(
                        fact_id=f"{PREFIX[table]}_{digest([table, number, source.row_sha256])}",
                        patient_id=patient_id,
                        encounter_id=encounter_id,
                        code=row["CODE"],
                        description=row["DESCRIPTION"],
                        system=row.get("SYSTEM") or None,
                        source=source,
                    )
                    if table == "conditions":
                        fact = ConditionFact(
                            **common,
                            start=date.fromisoformat(row["START"]),
                            stop=date.fromisoformat(row["STOP"]) if row["STOP"] else None,
                            originating_encounter_start=linked.start if linked else None,
                        )
                    elif table == "medications":
                        fact = MedicationFact(
                            **common,
                            start=timestamp(row["START"]),
                            stop=timestamp(row["STOP"]) if row["STOP"] else None,
                            reason=row.get("REASONDESCRIPTION") or None,
                        )
                    elif table == "observations":
                        fact = ObservationFact(
                            **common,
                            timestamp=timestamp(row["DATE"]),
                            value=row["VALUE"],
                            unit=row["UNITS"],
                            category=row.get("CATEGORY") or None,
                            value_type=row.get("TYPE") or None,
                        )
                    else:
                        fact = ProcedureFact(
                            **common,
                            timestamp=timestamp(row["START"]),
                            stop=timestamp(row["STOP"]) if row.get("STOP") else None,
                        )
                    getattr(facts_by_patient[patient_id], table).append(fact)
                except ValueError as exc:
                    raise ValueError(f"{table}.csv row {number}: {exc}") from exc
        return SourceData(patients, encounters, dict(facts_by_patient), files, warnings)
