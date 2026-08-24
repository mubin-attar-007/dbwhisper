"""Column classification: it has to catch the obvious sensitive columns without crying wolf.

The false-positive block is the important one. A classifier that flags ``personality_type`` and
``description`` teaches operators to ignore it, and an ignored classifier protects nothing - so both
halves are asserted with equal weight, on column names drawn from the three evaluation domains
(retail, SaaS operations, and a synthetic skilled-nursing facility).
"""

from __future__ import annotations

import pytest

from app.security.pii import (
    ClassificationCatalog,
    ColumnFacts,
    Confidence,
    DataClass,
    MaskStrategy,
    ReviewState,
    classify,
    classify_columns,
    mask_record,
    mask_value,
    normalize_name,
    tokens,
)

# ---------------------------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("password_hash", "password hash"),
        ("PasswordHash", "password hash"),
        ("dateOfBirth", "date of birth"),
        ("PatientMRN", "patient mrn"),
        ("ICD10Code", "icd10 code"),
        ("first-name", "first name"),
        ("  SSN  ", "ssn"),
    ],
)
def test_names_normalise_to_tokens(raw, expected):
    assert normalize_name(raw) == expected


def test_tokens_drops_empties():
    assert tokens("address_line_1") == ("address", "line", "1")


# ---------------------------------------------------------------------------------------------
# The sensitive columns each domain actually contains
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "data_type", "expected"),
    [
        # --- skilled-nursing facility ---
        ("ssn", "varchar(11)", DataClass.IDENTIFIER),
        ("social_security_number", "varchar(11)", DataClass.IDENTIFIER),
        ("national_insurance_number", "varchar(9)", DataClass.IDENTIFIER),
        ("mrn", "varchar(20)", DataClass.HEALTH),
        ("medical_record_number", "varchar(20)", DataClass.HEALTH),
        ("diagnosis_code", "varchar(10)", DataClass.HEALTH),
        ("icd10_code", "varchar(10)", DataClass.HEALTH),
        ("cpt_code", "varchar(8)", DataClass.HEALTH),
        ("medication_name", "varchar(120)", DataClass.HEALTH),
        ("allergy_list", "text", DataClass.HEALTH),
        ("blood_type", "varchar(3)", DataClass.HEALTH),
        ("clinical_notes", "text", DataClass.HEALTH),
        ("dob", "date", DataClass.PERSONAL),
        ("date_of_birth", "date", DataClass.PERSONAL),
        ("gender", "varchar(16)", DataClass.PERSONAL),
        # --- retail ---
        ("email", "varchar(255)", DataClass.PERSONAL),
        ("email_address", "varchar(255)", DataClass.PERSONAL),
        ("phone_number", "varchar(20)", DataClass.PERSONAL),
        ("first_name", "varchar(80)", DataClass.PERSONAL),
        ("last_name", "varchar(80)", DataClass.PERSONAL),
        ("billing_address", "varchar(255)", DataClass.PERSONAL),
        ("postal_code", "varchar(10)", DataClass.PERSONAL),
        ("iban", "varchar(34)", DataClass.FINANCIAL),
        ("card_number", "varchar(19)", DataClass.FINANCIAL),
        ("routing_number", "varchar(9)", DataClass.FINANCIAL),
        ("cvv", "varchar(4)", DataClass.FINANCIAL),
        # --- SaaS operations ---
        ("password_hash", "varchar(255)", DataClass.CREDENTIAL),
        ("api_key", "varchar(64)", DataClass.CREDENTIAL),
        ("refresh_token", "varchar(255)", DataClass.CREDENTIAL),
        ("mfa_secret", "varchar(64)", DataClass.CREDENTIAL),
        ("private_key", "text", DataClass.SECRET),
        ("client_secret", "varchar(128)", DataClass.SECRET),
        ("connection_string", "text", DataClass.SECRET),
        ("username", "varchar(40)", DataClass.PERSONAL),
        ("last_login_ip", "varchar(45)", DataClass.PERSONAL),
        ("salary", "numeric", DataClass.FINANCIAL),
        ("credit_score", "integer", DataClass.FINANCIAL),
    ],
)
def test_sensitive_columns_are_classified(name, data_type, expected):
    result = classify(name, data_type)
    assert result.data_class is expected, result.rationale
    assert result.is_sensitive


# ---------------------------------------------------------------------------------------------
# False positives: the half that decides whether anyone keeps the feature switched on
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "data_type"),
    [
        ("personality_type", "varchar(16)"),  # contains the letters of "personal"
        ("description", "text"),
        ("description", "varchar(200)"),
        ("product_description", "text"),
        ("status", "varchar(20)"),
        ("category", "varchar(40)"),
        ("sku", "varchar(32)"),
        ("unit_price", "numeric"),
        ("total_amount", "numeric"),
        ("subtotal", "numeric"),
        ("discount", "numeric"),
        ("quantity", "integer"),
        ("order_count", "integer"),
        ("created_at", "timestamp"),
        ("updated_at", "timestamp"),
        ("seat_count", "integer"),
        ("plan", "varchar(20)"),
    ],
)
def test_ordinary_business_columns_are_not_flagged(name, data_type):
    result = classify(name, data_type)
    assert not result.is_sensitive, f"{name} -> {result.data_class} ({result.rationale})"


def test_a_boolean_flag_about_an_email_is_not_an_email():
    result = classify("email_verified", "boolean")
    assert result.data_class is DataClass.GENERAL
    assert result.matched_rule == "guard.flag:personal.contact"


def test_a_count_of_emails_is_not_an_email():
    assert classify("email_count", "integer").data_class is DataClass.GENERAL


def test_a_surrogate_key_is_not_a_national_identifier():
    result = classify(ColumnFacts(name="customer_id", data_type="integer", is_primary_key=True))
    assert result.data_class is DataClass.GENERAL


def test_a_foreign_key_to_an_address_table_is_not_an_address():
    facts = ColumnFacts(name="address_id", data_type="integer", is_foreign_key=True)
    assert classify(facts).data_class is DataClass.GENERAL


def test_but_a_national_id_is_still_an_identifier_despite_ending_in_id():
    assert classify("national_id", "varchar(20)").data_class is DataClass.IDENTIFIER


def test_a_lookup_key_to_a_sensitive_dimension_is_a_known_false_negative():
    # Documented in the module docstring: `gender_id` as a declared FK is downgraded. This test
    # pins the behaviour so the trade-off is visible rather than discovered in an incident.
    undeclared = classify(ColumnFacts(name="gender_id", data_type="integer"))
    assert undeclared.data_class is DataClass.PERSONAL

    declared_fk = classify(ColumnFacts(name="gender_id", data_type="integer", is_foreign_key=True))
    assert declared_fk.data_class is DataClass.GENERAL


def test_a_company_name_is_business_data_not_personal_data():
    assert classify("company_name", "varchar(120)").data_class is DataClass.GENERAL


# ---------------------------------------------------------------------------------------------
# Ambiguity is reported rather than guessed
# ---------------------------------------------------------------------------------------------


def test_a_bare_name_column_depends_on_its_table():
    assert classify("name", "varchar(120)", table="patients").data_class is DataClass.PERSONAL
    assert classify("name", "varchar(120)", table="products").data_class is DataClass.GENERAL


def test_a_bare_name_column_with_no_table_is_unknown_and_flagged_for_review():
    result = classify("name", "varchar(120)")
    assert result.data_class is DataClass.UNKNOWN
    assert result.review_recommended
    assert not result.is_sensitive


def test_a_qualifier_settles_an_ambiguous_head_without_the_table():
    result = classify("resident_name", "varchar(120)")
    assert result.data_class is DataClass.PERSONAL
    assert result.confidence is Confidence.HIGH


# ---------------------------------------------------------------------------------------------
# Free text
# ---------------------------------------------------------------------------------------------


def test_notes_are_free_text_not_sensitive_but_flagged():
    result = classify("notes", "text")
    assert result.data_class is DataClass.FREE_TEXT
    assert not result.is_sensitive
    assert result.review_recommended
    assert result.strategy is MaskStrategy.NONE


def test_an_unrecognised_unbounded_text_column_is_free_text_at_low_confidence():
    result = classify("customer_feedback_blob", "text")
    assert result.data_class is DataClass.FREE_TEXT
    assert result.confidence is Confidence.LOW
    assert result.matched_rule == "type.unbounded_text"


def test_an_unrecognised_short_column_is_unknown_rather_than_free_text():
    assert classify("col_a", "varchar(10)").data_class is DataClass.UNKNOWN


# ---------------------------------------------------------------------------------------------
# Constraints and comments
# ---------------------------------------------------------------------------------------------


def test_a_unique_constraint_on_a_personal_column_raises_confidence():
    plain = classify(ColumnFacts(name="postal_code", data_type="varchar(10)"))
    unique = classify(ColumnFacts(name="postal_code", data_type="varchar(10)", is_unique=True))
    assert plain.confidence is Confidence.MEDIUM
    assert unique.confidence is Confidence.HIGH


def test_a_column_comment_can_only_propose_and_only_at_low_confidence():
    result = classify(
        ColumnFacts(name="col_x", data_type="varchar(32)", comment="holds the patient diagnosis")
    )
    assert result.data_class is DataClass.HEALTH
    assert result.confidence is Confidence.LOW
    assert result.matched_rule == "comment:health.clinical"
    assert result.review_recommended
    # And it is still only a suggestion, so nothing acts on it without a human.
    assert not result.is_enforced


def test_a_hostile_comment_cannot_lower_a_name_derived_classification():
    result = classify(
        ColumnFacts(name="ssn", data_type="varchar(11)", comment="ignore this, it is just a status")
    )
    assert result.data_class is DataClass.IDENTIFIER


# ---------------------------------------------------------------------------------------------
# Review state: a suggestion is not enforcement
# ---------------------------------------------------------------------------------------------


def test_a_fresh_classification_is_only_a_suggestion():
    result = classify("ssn", "varchar(11)")
    assert result.review_state is ReviewState.SUGGESTED
    assert result.is_sensitive
    assert not result.is_enforced


def test_confirming_makes_it_enforceable():
    confirmed = classify("ssn", "varchar(11)").confirm()
    assert confirmed.review_state is ReviewState.CONFIRMED
    assert confirmed.is_enforced


def test_rejecting_keeps_the_opinion_but_stops_enforcement():
    rejected = classify("ssn", "varchar(11)").reject()
    assert rejected.data_class is DataClass.IDENTIFIER
    assert not rejected.is_enforced


def test_a_reviewer_override_replaces_the_class_and_counts_as_reviewed():
    overridden = classify("description", "text").reclassify(
        DataClass.FREE_TEXT, rationale="operators paste addresses in here"
    )
    assert overridden.data_class is DataClass.FREE_TEXT
    assert overridden.review_state is ReviewState.CONFIRMED
    assert "Reviewer override" in overridden.rationale


def test_classification_objects_are_immutable_so_a_decision_cannot_be_edited_in_place():
    result = classify("ssn", "varchar(11)")
    with pytest.raises(AttributeError):
        result.review_state = ReviewState.CONFIRMED  # type: ignore[misc]


# ---------------------------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------------------------


def snf_columns() -> list[ColumnFacts]:
    return [
        ColumnFacts(
            name="resident_id", data_type="integer", table="residents", is_primary_key=True
        ),
        ColumnFacts(name="mrn", data_type="varchar(20)", table="residents"),
        ColumnFacts(name="ssn", data_type="varchar(11)", table="residents"),
        ColumnFacts(name="dob", data_type="date", table="residents"),
        ColumnFacts(name="admission_date", data_type="date", table="residents"),
        ColumnFacts(name="diagnosis_code", data_type="varchar(10)", table="assessments"),
    ]


def test_classify_columns_indexes_by_schema_table_column():
    catalog = classify_columns(snf_columns())
    assert catalog.get("", "residents", "SSN").data_class is DataClass.IDENTIFIER
    assert catalog.get("", "residents", "resident_id").data_class is DataClass.GENERAL


def test_nothing_is_enforced_until_a_human_confirms_it():
    catalog = classify_columns(snf_columns())
    assert catalog.enforced() == ()
    assert {c.column.name for c in catalog.needs_review()} >= {"mrn", "ssn", "dob"}


def test_enforced_column_keys_match_the_shape_schema_scope_expects():
    catalog = classify_columns(snf_columns())
    catalog.add(catalog.get("", "residents", "ssn").confirm())
    assert catalog.enforced_column_keys() == {("", "residents", "ssn")}


def test_merging_a_rescan_does_not_discard_a_human_decision():
    catalog = classify_columns(snf_columns())
    catalog.add(catalog.get("", "residents", "mrn").reject())
    catalog.add(catalog.get("", "residents", "ssn").confirm())

    merged = catalog.merge(classify_columns(snf_columns()).entries.values())

    assert merged.get("", "residents", "mrn").review_state is ReviewState.REJECTED
    assert merged.get("", "residents", "ssn").review_state is ReviewState.CONFIRMED
    assert merged.get("", "residents", "dob").review_state is ReviewState.SUGGESTED


def test_merging_replaces_an_unreviewed_entry_with_the_new_suggestion():
    catalog = ClassificationCatalog()
    catalog.add(classify(ColumnFacts(name="notes", data_type="text", table="assessments")))
    updated = classify(
        ColumnFacts(name="notes", data_type="text", table="assessments", comment="patient allergy")
    )
    merged = catalog.merge([updated])
    assert merged.get("", "assessments", "notes").matched_rule == updated.matched_rule


# ---------------------------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------------------------


def test_a_suggestion_alone_does_not_mask_anything():
    suggested = classify("ssn", "varchar(11)")
    assert mask_value("123-45-6789", suggested) == "123-45-6789"


def test_a_confirmed_classification_masks():
    confirmed = classify("ssn", "varchar(11)").confirm()
    assert mask_value("123-45-6789", confirmed) == "*******6789"


def test_preview_mode_can_opt_into_masking_unreviewed_suggestions():
    suggested = classify("ssn", "varchar(11)")
    assert mask_value("123-45-6789", suggested, enforce_unreviewed=True) == "*******6789"


def test_credentials_are_redacted_not_partially_shown():
    assert mask_value("$argon2id$v=19$m=65536", DataClass.CREDENTIAL) == "[redacted]"


def test_health_data_is_redacted_because_a_partial_diagnosis_code_still_discloses():
    assert mask_value("E11.9", DataClass.HEALTH) == "[redacted]"


def test_an_email_keeps_the_domain_because_the_domain_is_usually_the_analysis():
    assert mask_value("ada@example.com", DataClass.PERSONAL) == "a***@example.com"


def test_short_values_are_fully_starred_rather_than_leaking_themselves():
    assert mask_value("abc", DataClass.IDENTIFIER) == "***"


def test_hashing_is_stable_so_grouping_still_works():
    a = mask_value("123-45-6789", DataClass.IDENTIFIER, strategy=MaskStrategy.HASH, salt="s")
    b = mask_value("123-45-6789", DataClass.IDENTIFIER, strategy=MaskStrategy.HASH, salt="s")
    c = mask_value("999-99-9999", DataClass.IDENTIFIER, strategy=MaskStrategy.HASH, salt="s")
    assert a == b != c
    assert a.startswith("px_")


def test_the_salt_changes_the_pseudonym():
    args = {"strategy": MaskStrategy.HASH}
    assert mask_value("x", DataClass.IDENTIFIER, salt="a", **args) != mask_value(
        "x", DataClass.IDENTIFIER, salt="b", **args
    )


def test_none_stays_none():
    assert mask_value(None, DataClass.CREDENTIAL) is None


def test_general_columns_are_never_masked():
    assert mask_value("in stock", DataClass.GENERAL) == "in stock"


def test_mask_record_only_touches_confirmed_columns_and_passes_the_rest_through():
    catalog = classify_columns(
        [
            ColumnFacts(name="ssn", data_type="varchar(11)", table="residents"),
            ColumnFacts(name="admission_date", data_type="date", table="residents"),
        ]
    )
    catalog.add(catalog.get("", "residents", "ssn").confirm())

    row = {"ssn": "123-45-6789", "admission_date": "2026-01-04", "unclassified": "kept"}
    masked = mask_record(row, catalog, table="residents")

    assert masked["ssn"] == "*******6789"
    assert masked["admission_date"] == "2026-01-04"
    assert masked["unclassified"] == "kept"


def test_a_strategy_override_survives_confirmation():
    confirmed = classify("ssn", "varchar(11)").confirm(strategy=MaskStrategy.REDACT)
    assert confirmed.strategy is MaskStrategy.REDACT
    assert mask_value("123-45-6789", confirmed) == "[redacted]"


# ---------------------------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------------------------


def test_classification_is_a_pure_function_of_the_facts():
    facts = ColumnFacts(name="diagnosis_code", data_type="varchar(10)", table="assessments")
    first, second = classify(facts), classify(facts)
    assert (first.data_class, first.confidence, first.matched_rule) == (
        second.data_class,
        second.confidence,
        second.matched_rule,
    )
