"""SYNTHETIC skilled-nursing-facility data. Entirely fabricated; not derived from any real record.

Read this before using the dataset for anything:

* **No row describes a real person.** Residents are generated from a seeded pseudo-random number
  generator. There are no names, no addresses, no dates of birth, no social-security or insurance
  numbers, no medical-record numbers and no provider identifiers anywhere in this schema.
* **Identifiers are deliberately non-conforming.** A resident is ``SYNTH-R-000123`` and a facility is
  ``SYNTH-F-01``; a claim is ``SYNTH-CLM-000123``. None of these match the format of a real MRN, NPI,
  CMS certification number, ICD-10 code or claim number, so a value from this fixture cannot be
  mistaken for - or accidentally used as - a real one. Only a birth *year* is stored, never a date.
* **Diagnoses are coarse groups, not codes.** ``diagnoses.diagnosis_group`` holds text such as
  "respiratory" or "cardiovascular". Emitting anything ICD-shaped would be inviting a reader to
  treat this fixture as clinically meaningful. It is not.
* **Nothing here is clinically valid.** Scores, lengths of stay, therapy minutes and payment amounts
  are plausible-looking noise. No conclusion about care, cost or outcomes may be drawn from a query
  against this database, and any report that quotes a number from it must say so.

Why the domain is in the corpus at all: post-acute care is the shape of schema that breaks
text-to-SQL. A resident has many admissions; a claim belongs to an admission, not to a resident, so
cost per resident needs two joins; census is a per-facility daily fact with no resident key at all;
and "length of stay" is undefined for an admission that has not been discharged. Those are real
modelling problems, and they can be posed without any real data.

Sizes: 8 facilities, 300 residents, ~420 admissions, 2,920 census days (2025), ~500 claims.
"""

from __future__ import annotations

from random import Random

from app.evaluation.datasets.spec import (
    Column,
    DatasetSpec,
    Population,
    Relationship,
    Table,
    code,
    date_series,
    money,
    weighted_choice,
)

SEED = 20260826
START_DATE = "2025-01-01"
DAYS = 365

STATE_CODES = ("AA", "BB", "CC", "DD")  # invented two-letter codes, not US state abbreviations
OWNERSHIP = ("nonprofit", "for-profit", "government")
SEXES = ("F", "M", "unspecified")
ADMISSION_SOURCES = ("hospital", "home", "another facility", "emergency")
DISPOSITIONS = ("home", "hospital", "long-term care", "deceased", "still admitted")
DIAGNOSIS_GROUPS = (
    "respiratory",
    "cardiovascular",
    "musculoskeletal",
    "neurological",
    "endocrine",
    "infection",
    "wound care",
)
ASSESSMENT_TYPES = ("admission", "quarterly", "significant change", "discharge")
THERAPY_TYPES = ("physical", "occupational", "speech")
INCIDENT_TYPES = ("fall", "medication variance", "skin integrity", "elopement risk")
SEVERITIES: tuple[tuple[str, float], ...] = (
    ("minor", 0.62),
    ("moderate", 0.28),
    ("major", 0.10),
)
PAYERS: tuple[tuple[int, str, str], ...] = (
    (1, "Synthetic Public Plan A", "public"),
    (2, "Synthetic Public Plan B", "public"),
    (3, "Synthetic Managed Plan C", "managed"),
    (4, "Synthetic Managed Plan D", "managed"),
    (5, "Synthetic Private Plan E", "private"),
    (6, "Self Pay (synthetic)", "self"),
)
CLAIM_STATUS: tuple[tuple[str, float], ...] = (
    ("paid", 0.7),
    ("partially paid", 0.15),
    ("denied", 0.09),
    ("submitted", 0.06),
)


def _tables() -> tuple[Table, ...]:
    return (
        Table(
            name="facilities",
            description=(
                "Synthetic skilled-nursing facilities. facility_code is an invented identifier and "
                "does not correspond to any real certification or provider number."
            ),
            columns=(
                Column("facility_id", "INTEGER"),
                Column("facility_code", "TEXT", description="synthetic code, e.g. SYNTH-F-01"),
                Column("facility_name", "TEXT", description="invented name"),
                Column(
                    "state_code", "TEXT", description="invented two-letter code, not a US state"
                ),
                Column("bed_count", "INTEGER"),
                Column("ownership_type", "TEXT"),
            ),
            primary_key=("facility_id",),
            keywords=("facility", "site", "home", "building", "beds"),
        ),
        Table(
            name="residents",
            description=(
                "Synthetic residents. No names, no dates of birth and no medical-record numbers: "
                "identity is the opaque resident_code and only a birth year is stored."
            ),
            columns=(
                Column("resident_id", "INTEGER"),
                Column("facility_id", "INTEGER"),
                Column("resident_code", "TEXT", description="synthetic code, e.g. SYNTH-R-000123"),
                Column("birth_year", "INTEGER", description="year only, never a full date"),
                Column("sex", "TEXT"),
                Column("first_admitted_on", "TEXT"),
            ),
            primary_key=("resident_id",),
            keywords=("resident", "patient", "person", "census", "cohort"),
        ),
        Table(
            name="admissions",
            description=(
                "One stay. A resident can have several. discharged_on is null while the resident is "
                "still admitted, so length of stay is undefined for those rows."
            ),
            columns=(
                Column("admission_id", "INTEGER"),
                Column("resident_id", "INTEGER"),
                Column("admitted_on", "TEXT"),
                Column(
                    "discharged_on", "TEXT", nullable=True, description="null if still admitted"
                ),
                Column("admission_source", "TEXT"),
                Column("discharge_disposition", "TEXT"),
                Column("length_of_stay_days", "INTEGER", nullable=True),
            ),
            primary_key=("admission_id",),
            keywords=("admission", "stay", "discharge", "length of stay", "readmission"),
        ),
        Table(
            name="census_days",
            description=(
                "Daily occupancy per facility. A facility-level fact with no resident key: it can "
                "be joined to facilities but never to a resident."
            ),
            columns=(
                Column("census_id", "INTEGER"),
                Column("facility_id", "INTEGER"),
                Column("census_date", "TEXT"),
                Column("occupied_beds", "INTEGER"),
                Column("available_beds", "INTEGER"),
            ),
            primary_key=("census_id",),
            keywords=("census", "occupancy", "beds", "daily", "utilisation"),
        ),
        Table(
            name="diagnoses",
            description=(
                "Coarse diagnosis groups recorded against an admission. Deliberately not coded: "
                "these are broad text categories, never ICD or any other clinical code set."
            ),
            columns=(
                Column("diagnosis_id", "INTEGER"),
                Column("admission_id", "INTEGER"),
                Column("diagnosis_group", "TEXT", description="broad category, not a code"),
                Column("recorded_on", "TEXT"),
                Column("is_primary", "INTEGER", description="1 for the principal diagnosis"),
            ),
            primary_key=("diagnosis_id",),
            keywords=("diagnosis", "condition", "acuity", "primary", "comorbidity"),
        ),
        Table(
            name="assessments",
            description=(
                "Periodic functional assessments of a resident. adl_score is 0-28 (higher means "
                "more assistance needed); cognition_score is 0-15. Both are invented scales."
            ),
            columns=(
                Column("assessment_id", "INTEGER"),
                Column("resident_id", "INTEGER"),
                Column("assessment_date", "TEXT"),
                Column("assessment_type", "TEXT"),
                Column("adl_score", "INTEGER", description="0-28, invented scale"),
                Column("cognition_score", "INTEGER", description="0-15, invented scale"),
            ),
            primary_key=("assessment_id",),
            keywords=("assessment", "adl", "cognition", "functional", "score"),
        ),
        Table(
            name="payers",
            description="Synthetic payer directory. Every payer name is invented.",
            columns=(
                Column("payer_id", "INTEGER"),
                Column("payer_name", "TEXT"),
                Column("payer_type", "TEXT", description="public, managed, private or self"),
            ),
            primary_key=("payer_id",),
            keywords=("payer", "insurer", "plan", "coverage", "reimbursement"),
        ),
        Table(
            name="claims",
            description=(
                "Billing for an admission. A claim belongs to an admission, not to a resident, so "
                "cost per resident requires joining through admissions."
            ),
            columns=(
                Column("claim_id", "INTEGER"),
                Column("claim_code", "TEXT", description="synthetic code, e.g. SYNTH-CLM-000123"),
                Column("admission_id", "INTEGER"),
                Column("payer_id", "INTEGER"),
                Column("claim_date", "TEXT"),
                Column("billed_amount", "REAL"),
                Column("paid_amount", "REAL"),
                Column("claim_status", "TEXT"),
            ),
            primary_key=("claim_id",),
            keywords=("claim", "billing", "payment", "denied", "revenue", "reimbursement"),
        ),
        Table(
            name="therapy_sessions",
            description=(
                "Therapy delivered to a resident, in minutes. Recorded against the resident rather "
                "than the admission, so therapy and claims meet only through residents."
            ),
            columns=(
                Column("session_id", "INTEGER"),
                Column("resident_id", "INTEGER"),
                Column("session_date", "TEXT"),
                Column("therapy_type", "TEXT", description="physical, occupational or speech"),
                Column("minutes", "INTEGER"),
            ),
            primary_key=("session_id",),
            keywords=("therapy", "rehab", "minutes", "physical", "occupational", "speech"),
        ),
        Table(
            name="incidents",
            description=(
                "Reported safety incidents. Some are facility-level with no resident attached, so "
                "resident_id is nullable."
            ),
            columns=(
                Column("incident_id", "INTEGER"),
                Column("facility_id", "INTEGER"),
                Column("resident_id", "INTEGER", nullable=True),
                Column("incident_date", "TEXT"),
                Column("incident_type", "TEXT"),
                Column("severity", "TEXT", description="minor, moderate or major"),
            ),
            primary_key=("incident_id",),
            keywords=("incident", "fall", "safety", "severity", "event", "report"),
        ),
    )


_RELATIONSHIPS = (
    Relationship("residents", ("facility_id",), "facilities", ("facility_id",)),
    Relationship("admissions", ("resident_id",), "residents", ("resident_id",)),
    Relationship("census_days", ("facility_id",), "facilities", ("facility_id",)),
    Relationship("diagnoses", ("admission_id",), "admissions", ("admission_id",)),
    Relationship("assessments", ("resident_id",), "residents", ("resident_id",)),
    Relationship("claims", ("admission_id",), "admissions", ("admission_id",)),
    Relationship("claims", ("payer_id",), "payers", ("payer_id",)),
    Relationship("therapy_sessions", ("resident_id",), "residents", ("resident_id",)),
    Relationship("incidents", ("facility_id",), "facilities", ("facility_id",)),
    Relationship("incidents", ("resident_id",), "residents", ("resident_id",)),
)


def populate(rng: Random) -> Population:
    """Every row of the synthetic SNF fixture. Fabricated; see the module docstring."""
    dates = date_series(START_DATE, DAYS)

    facilities: list[tuple] = []
    for facility_id in range(1, 9):
        facilities.append(
            (
                facility_id,
                code("SYNTH-F", facility_id, width=2),
                f"Synthetic Care Site {facility_id}",
                STATE_CODES[(facility_id - 1) % len(STATE_CODES)],
                rng.randrange(40, 180),
                OWNERSHIP[(facility_id - 1) % len(OWNERSHIP)],
            )
        )

    residents: list[tuple] = []
    for resident_id in range(1, 301):
        facility_id = 1 + rng.randrange(len(facilities))
        first_admitted = dates[rng.randrange(0, 300)]
        residents.append(
            (
                resident_id,
                facility_id,
                code("SYNTH-R", resident_id),
                rng.randrange(1932, 1966),
                SEXES[rng.randrange(len(SEXES))],
                first_admitted,
            )
        )

    admissions: list[tuple] = []
    admission_id = 0
    for resident in residents:
        resident_id = resident[0]
        start_index = dates.index(resident[5])
        for stay in range(1 + (1 if rng.random() < 0.35 else 0)):
            admission_id += 1
            admitted_index = min(start_index + stay * rng.randrange(40, 120), DAYS - 1)
            still_in = rng.random() < 0.18
            if still_in:
                discharged = None
                disposition = "still admitted"
                length = None
            else:
                length = rng.randrange(4, 95)
                discharged_index = min(admitted_index + length, DAYS - 1)
                discharged = dates[discharged_index]
                length = discharged_index - admitted_index
                disposition = DISPOSITIONS[rng.randrange(len(DISPOSITIONS) - 1)]
            admissions.append(
                (
                    admission_id,
                    resident_id,
                    dates[admitted_index],
                    discharged,
                    ADMISSION_SOURCES[rng.randrange(len(ADMISSION_SOURCES))],
                    disposition,
                    length,
                )
            )

    census: list[tuple] = []
    census_id = 0
    for facility in facilities:
        beds = facility[4]
        for iso in dates:
            census_id += 1
            occupied = max(0, min(beds, int(beds * (0.62 + rng.random() * 0.33))))
            census.append((census_id, facility[0], iso, occupied, beds - occupied))

    diagnoses: list[tuple] = []
    diagnosis_id = 0
    for admission in admissions:
        for position in range(rng.randrange(1, 4)):
            diagnosis_id += 1
            diagnoses.append(
                (
                    diagnosis_id,
                    admission[0],
                    DIAGNOSIS_GROUPS[rng.randrange(len(DIAGNOSIS_GROUPS))],
                    admission[2],
                    1 if position == 0 else 0,
                )
            )

    assessments: list[tuple] = []
    assessment_id = 0
    for resident in residents:
        base_index = dates.index(resident[5])
        for step in range(rng.randrange(2, 6)):
            assessment_id += 1
            assessments.append(
                (
                    assessment_id,
                    resident[0],
                    dates[min(base_index + step * 30, DAYS - 1)],
                    ASSESSMENT_TYPES[min(step, len(ASSESSMENT_TYPES) - 1)],
                    rng.randrange(0, 29),
                    rng.randrange(0, 16),
                )
            )

    payers = [tuple(row) for row in PAYERS]

    claims: list[tuple] = []
    claim_id = 0
    for admission in admissions:
        if rng.random() < 0.08:
            continue  # some stays were never billed in this fixture
        for _ in range(1 + (1 if rng.random() < 0.25 else 0)):
            claim_id += 1
            billed = money(900 + rng.random() * 22000)
            status = weighted_choice(rng, CLAIM_STATUS)
            paid = {
                "paid": billed,
                "partially paid": money(billed * (0.35 + rng.random() * 0.5)),
                "denied": 0.0,
                "submitted": 0.0,
            }[status]
            claim_date = admission[3] or admission[2]
            claims.append(
                (
                    claim_id,
                    code("SYNTH-CLM", claim_id),
                    admission[0],
                    1 + rng.randrange(len(PAYERS)),
                    claim_date,
                    billed,
                    paid,
                    status,
                )
            )

    therapy: list[tuple] = []
    session_id = 0
    for resident in residents:
        if rng.random() < 0.12:
            continue  # not every resident receives therapy
        base_index = dates.index(resident[5])
        for _ in range(rng.randrange(2, 12)):
            session_id += 1
            therapy.append(
                (
                    session_id,
                    resident[0],
                    dates[min(base_index + rng.randrange(0, 90), DAYS - 1)],
                    THERAPY_TYPES[rng.randrange(len(THERAPY_TYPES))],
                    rng.randrange(15, 91),
                )
            )

    incidents: list[tuple] = []
    for incident_id in range(1, 181):
        facility_id = 1 + rng.randrange(len(facilities))
        resident_id = None
        if rng.random() < 0.8:
            local = [r[0] for r in residents if r[1] == facility_id]
            if local:
                resident_id = local[rng.randrange(len(local))]
        incidents.append(
            (
                incident_id,
                facility_id,
                resident_id,
                dates[rng.randrange(0, DAYS)],
                INCIDENT_TYPES[rng.randrange(len(INCIDENT_TYPES))],
                weighted_choice(rng, SEVERITIES),
            )
        )

    return {
        "facilities": facilities,
        "residents": residents,
        "admissions": admissions,
        "census_days": census,
        "diagnoses": diagnoses,
        "assessments": assessments,
        "payers": payers,
        "claims": claims,
        "therapy_sessions": therapy,
        "incidents": incidents,
    }


SPEC = DatasetSpec(
    name="synthetic_snf",
    source_id="eval_synthetic_snf",
    version="1.0.0",
    description=(
        "SYNTHETIC skilled-nursing data (entirely fabricated): facilities, residents, admissions, "
        "daily census, diagnosis groups, assessments, payers, claims, therapy and incidents."
    ),
    domain="synthetic_healthcare",
    tables=_tables(),
    relationships=_RELATIONSHIPS,
    populate=populate,
    seed=SEED,
    verified_queries=(
        (
            "how many residents are in each facility",
            "SELECT f.facility_name, COUNT(*) AS resident_count FROM residents r "
            "JOIN facilities f ON f.facility_id = r.facility_id "
            "GROUP BY f.facility_name ORDER BY resident_count DESC",
        ),
        (
            "average length of stay for discharged admissions",
            "SELECT AVG(length_of_stay_days) AS avg_length_of_stay FROM admissions "
            "WHERE discharged_on IS NOT NULL",
        ),
    ),
    synthetic=True,
    provenance_note=(
        "SYNTHETIC DATA - entirely fabricated by app/evaluation/datasets/synthetic_snf.py with "
        f"seed {SEED}. No row describes a real person, facility, diagnosis, claim or payment; the "
        "identifiers are deliberately non-conforming so they cannot be mistaken for real ones. "
        "No clinical, financial or operational conclusion may be drawn from this database."
    ),
    covered_concepts=(
        "residents",
        "admissions",
        "census",
        "diagnoses",
        "assessments",
        "claims",
        "payers",
        "therapy",
        "incidents",
        "facilities",
    ),
)

__all__ = ["SEED", "SPEC", "populate"]
