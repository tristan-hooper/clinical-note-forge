"""Recreate the small entirely fabricated fixture; no downloaded patients or records."""

import csv
from pathlib import Path

HEADERS = {
    "patients": "Id,BIRTHDATE,DEATHDATE,SSN,DRIVERS,PASSPORT,PREFIX,FIRST,MIDDLE,LAST,SUFFIX,"
    "MAIDEN,MARITAL,RACE,ETHNICITY,GENDER,BIRTHPLACE,ADDRESS,CITY,STATE,COUNTY,FIPS,ZIP,LAT,LON,"
    "HEALTHCARE_EXPENSES,HEALTHCARE_COVERAGE,INCOME",
    "encounters": "Id,START,STOP,PATIENT,ORGANIZATION,PROVIDER,PAYER,"
    "ENCOUNTERCLASS,CODE,DESCRIPTION,"
    "BASE_ENCOUNTER_COST,TOTAL_CLAIM_COST,PAYER_COVERAGE,REASONCODE,REASONDESCRIPTION",
    "conditions": "START,STOP,PATIENT,ENCOUNTER,SYSTEM,CODE,DESCRIPTION",
    "medications": "START,STOP,PATIENT,PAYER,ENCOUNTER,CODE,DESCRIPTION,BASE_COST,PAYER_COVERAGE,"
    "DISPENSES,TOTALCOST,REASONCODE,REASONDESCRIPTION",
    "observations": "DATE,PATIENT,ENCOUNTER,CATEGORY,CODE,DESCRIPTION,VALUE,UNITS,TYPE",
    "procedures": "START,STOP,PATIENT,ENCOUNTER,SYSTEM,CODE,DESCRIPTION,BASE_COST,REASONCODE,"
    "REASONDESCRIPTION",
}


def create(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    rows = {name: [] for name in HEADERS}
    conditions = [
        ("44054006", "Type 2 diabetes mellitus"),
        ("59621000", "Essential hypertension"),
        ("195967001", "Asthma"),
    ]
    medications = [
        ("860975", "Metformin hydrochloride 500 MG Oral Tablet"),
        ("314076", "Lisinopril 10 MG Oral Tablet"),
        ("745679", "Albuterol 0.09 MG/ACTUAT Metered Dose Inhaler"),
    ]
    for i in range(1, 11):
        patient = f"synthetic-p{i:02}"
        condition_code, condition = conditions[(i - 1) % 3]
        medication_code, medication = medications[(i - 1) % 3]
        rows["patients"].append(
            {
                "Id": patient,
                "BIRTHDATE": f"{1960 + i}-03-15",
                "FIRST": f"Fabricated{i}",
                "LAST": "Example",
                "GENDER": "F" if i % 2 else "M",
                "RACE": "other",
                "ETHNICITY": "nonhispanic",
            }
        )
        first = f"synthetic-e{i:02}-a"
        rows["conditions"].append(
            {
                "START": "2023-12-01",
                "PATIENT": patient,
                "ENCOUNTER": first,
                "SYSTEM": "http://snomed.info/sct",
                "CODE": condition_code,
                "DESCRIPTION": condition,
            }
        )
        rows["medications"].append(
            {
                "START": "2024-01-10T10:00:00Z",
                "PATIENT": patient,
                "ENCOUNTER": first,
                "CODE": medication_code,
                "DESCRIPTION": medication,
                "REASONCODE": condition_code,
                "REASONDESCRIPTION": condition,
            }
        )
        for suffix, month in (("a", "01"), ("b", "07")):
            encounter = f"synthetic-e{i:02}-{suffix}"
            start = f"2024-{month}-10T10:00:00Z"
            stop = f"2024-{month}-10T10:30:00Z"
            rows["encounters"].append(
                {
                    "Id": encounter,
                    "START": start,
                    "STOP": stop,
                    "PATIENT": patient,
                    "ENCOUNTERCLASS": "ambulatory",
                    "CODE": "185349003",
                    "DESCRIPTION": "Encounter for check up",
                    "REASONCODE": condition_code,
                    "REASONDESCRIPTION": condition,
                }
            )
            rows["observations"].append(
                {
                    "DATE": f"2024-{month}-10T10:10:00Z",
                    "PATIENT": patient,
                    "ENCOUNTER": encounter,
                    "CATEGORY": "vital-signs",
                    "CODE": "29463-7",
                    "DESCRIPTION": "Body weight",
                    "VALUE": f"{60 + i}.00",
                    "UNITS": "kg",
                    "TYPE": "numeric",
                }
            )
            if suffix == "b":
                rows["procedures"].append(
                    {
                        "START": start,
                        "STOP": stop,
                        "PATIENT": patient,
                        "ENCOUNTER": encounter,
                        "SYSTEM": "http://snomed.info/sct",
                        "CODE": "171207006",
                        "DESCRIPTION": "Depression screening",
                    }
                )
    rows["conditions"].append(
        {
            "START": "2024-01-10",
            "STOP": "2024-01-31",
            "PATIENT": "synthetic-p01",
            "ENCOUNTER": "synthetic-e01-a",
            "SYSTEM": "http://snomed.info/sct",
            "CODE": "10509002",
            "DESCRIPTION": "Acute bronchitis",
        }
    )
    rows["medications"].append(
        {
            "START": "2024-08-01T00:00:00Z",
            "STOP": "2024-08-08T00:00:00Z",
            "PATIENT": "synthetic-p01",
            "ENCOUNTER": "synthetic-e01-b",
            "CODE": "308191",
            "DESCRIPTION": "Amoxicillin 500 MG Oral Capsule",
        }
    )
    rows["observations"] += [
        {
            "DATE": "2024-01-10T10:05:00Z",
            "PATIENT": "synthetic-p01",
            "ENCOUNTER": "synthetic-e01-a",
            "CATEGORY": "laboratory",
            "CODE": "4548-4",
            "DESCRIPTION": "Hemoglobin A1c",
            "VALUE": "8.20",
            "UNITS": "%",
            "TYPE": "numeric",
        },
        {
            "DATE": "2024-07-10T10:05:00Z",
            "PATIENT": "synthetic-p01",
            "ENCOUNTER": "synthetic-e01-b",
            "CATEGORY": "survey",
            "CODE": "fixture-text",
            "DESCRIPTION": "Fabricated survey, résumé example",
            "VALUE": "Example response — café",
            "UNITS": "",
            "TYPE": "text",
        },
        {
            "DATE": "2024-02-01T00:00:00Z",
            "PATIENT": "synthetic-p01",
            "CODE": "fixture-unlinked",
            "DESCRIPTION": "Unlinked historical measure",
            "VALUE": "1.000",
            "UNITS": "a",
            "TYPE": "numeric",
        },
    ]
    rows["encounters"].append(
        {
            "Id": "synthetic-inpatient",
            "START": "2024-08-01T00:00:00Z",
            "STOP": "2024-08-02T00:00:00Z",
            "PATIENT": "synthetic-p01",
            "ENCOUNTERCLASS": "inpatient",
            "CODE": "fixture-inpatient",
            "DESCRIPTION": "Fabricated inpatient encounter",
        }
    )
    for table, header in HEADERS.items():
        with (directory / f"{table}.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=header.split(","))
            writer.writeheader()
            writer.writerows(rows[table])


if __name__ == "__main__":
    create(Path(__file__).parent / "fixtures" / "synthea")
