"""Deterministic column classification: which columns are sensitive, and how confident we are.

The point of this module is to give the rest of the system a *typed opinion* about every column
before anyone runs a query against it - so masking, the policy engine's ``sensitive_columns`` set
(``app/sqlpolicy/types.py:124``) and the egress policy have something to consult that is not a
guess made at request time.

Four decisions define it.

**No model.** Classification is regex-and-token matching over the column name, its declared type and
its constraints. A model would be more nuanced and less reproducible: the same schema would classify
differently across runs, providers and versions, and "is this column protected health information"
is not a question that should have a temperature. Everything here is a pure function; the same
:class:`ColumnFacts` always produces the same :class:`ColumnClassification`.

**A suggestion is not enforcement.** Every heuristic result starts life as
:attr:`ReviewState.SUGGESTED`. Only a human moving it to :attr:`ReviewState.CONFIRMED` makes it
enforceable (:attr:`ColumnClassification.is_enforced`), and :meth:`ClassificationCatalog.enforced`
returns only those. This is the difference between a tool that helps a reviewer and a tool that
silently redacts a column an analyst needed - and it is also what stops a heuristic false positive
from becoming an outage. :attr:`ReviewState.REJECTED` is remembered rather than deleted, so a
re-scan does not resurrect a decision a human already made.

**Tokens, never substrings.** ``personality_type`` contains the letters of ``personal``;
``description`` contains ``script``. Every rule matches whole tokens of the normalised name
(``PersonalityType`` -> ``personality type``), which is why the false-positive tests in
``tests/test_pii.py`` pass. Guards then *downgrade*: a boolean ``email_verified`` is a flag, not an
email address, and an integer ``customer_id`` is a surrogate key, not a national identifier.

**Ambiguity is reported, not resolved.** A bare ``name`` column is personal data in ``patients`` and
is not in ``products``. When the table is known it is used; when it is not, the result is
:attr:`DataClass.UNKNOWN` with ``review_recommended`` set, because inventing an answer would put a
confident wrong label in front of a reviewer who is trusting the tool to be careful.

**Known limitation, stated rather than hidden.** A declared primary or foreign key is treated as a
surrogate key and downgraded to :attr:`DataClass.GENERAL`. That is right for ``customer_id`` and
wrong for ``gender_id`` pointing at a lookup dimension, which is a false negative a reviewer has to
catch. It is preferred over the alternative, because flagging every FK in a normalised schema would
bury the real findings and get the whole classifier switched off.

One consequence worth naming: column *comments* are untrusted database content (see
``docs/v2/THREAT_MODEL.md``, hostile-content threats). They are consulted only when the name yields
nothing, and only at :attr:`Confidence.LOW`, so a malicious comment can at most propose extra
protection for its own column - never remove protection from another.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class DataClass(StrEnum):
    """What kind of thing a column holds, ordered loosely by how badly a leak would go.

    ``FREE_TEXT`` is not a sensitivity level - it is an admission that the contents are unstructured
    and could be anything, which is why its default masking strategy is :attr:`MaskStrategy.NONE`
    and it is flagged for review instead.
    """

    GENERAL = "general"  # ordinary business data: price, quantity, status, created_at
    IDENTIFIER = "identifier"  # government or strong identifiers: ssn, passport, tax id
    PERSONAL = "personal"  # directly identifies or describes a person: name, email, dob
    HEALTH = "health"  # clinical data: diagnosis, medication, mrn
    FINANCIAL = "financial"  # account and payment data: iban, card number, salary
    CREDENTIAL = "credential"  # authenticates a principal: password hash, api key, otp
    SECRET = "secret"  # key material and connection secrets
    FREE_TEXT = "free_text"  # unstructured prose that may contain any of the above
    UNKNOWN = "unknown"  # not enough signal to say

    @property
    def is_sensitive(self) -> bool:
        """True for classes where exposing a raw value is a disclosure in its own right."""
        return self in _SENSITIVE_CLASSES


_SENSITIVE_CLASSES = frozenset(
    {
        DataClass.IDENTIFIER,
        DataClass.PERSONAL,
        DataClass.HEALTH,
        DataClass.FINANCIAL,
        DataClass.CREDENTIAL,
        DataClass.SECRET,
    }
)


class Confidence(StrEnum):
    """How much the evidence supports the class. Drives review order, never enforcement."""

    HIGH = "high"  # an unambiguous token: ssn, password_hash, iban
    MEDIUM = "medium"  # a strong token in a context that could still be something else
    LOW = "low"  # a weak signal: an unbounded text type, a column comment


class ReviewState(StrEnum):
    """Whether a human has looked at the suggestion yet. Only ``CONFIRMED`` enforces."""

    SUGGESTED = "suggested"  # produced by the heuristics; advisory
    CONFIRMED = "confirmed"  # a reviewer accepted it; masking and policy may act on it
    REJECTED = "rejected"  # a reviewer said no; retained so a re-scan does not resurrect it


class MaskStrategy(StrEnum):
    """How to render a value that must not be shown in full."""

    NONE = "none"  # show as-is
    REDACT = "redact"  # replace entirely
    PARTIAL = "partial"  # keep a recognisable tail (or an email domain)
    HASH = "hash"  # stable pseudonym, so rows can still be joined and counted


REDACTED = "[redacted]"
_HASH_PREFIX_LEN = 12

# Defaults chosen per class, not per column. Credentials and health data are redacted outright:
# a partial password hash is still a password hash, and "the last four characters of a diagnosis
# code" is both useless and disclosing.
DEFAULT_STRATEGY: Mapping[DataClass, MaskStrategy] = {
    DataClass.GENERAL: MaskStrategy.NONE,
    DataClass.UNKNOWN: MaskStrategy.NONE,
    DataClass.FREE_TEXT: MaskStrategy.NONE,
    DataClass.IDENTIFIER: MaskStrategy.PARTIAL,
    DataClass.PERSONAL: MaskStrategy.PARTIAL,
    DataClass.FINANCIAL: MaskStrategy.PARTIAL,
    DataClass.HEALTH: MaskStrategy.REDACT,
    DataClass.CREDENTIAL: MaskStrategy.REDACT,
    DataClass.SECRET: MaskStrategy.REDACT,
}


@dataclass(frozen=True, slots=True)
class ColumnFacts:
    """Everything the classifier is allowed to look at. No values, ever.

    Sampling actual data would classify better and would also mean reading customer rows to decide
    whether we are allowed to read customer rows. The metadata is what we have and it is enough for
    the cases that matter.
    """

    name: str
    data_type: str | None = None
    table: str | None = None
    schema: str | None = None
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    is_unique: bool = False
    comment: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        """``(schema, table, column)`` lower-cased - the shape ``SchemaScope`` indexes by."""
        return ((self.schema or "").lower(), (self.table or "").lower(), self.name.lower())


@dataclass(frozen=True, slots=True)
class ColumnClassification:
    """A suggestion about one column, with the reasoning attached.

    ``rationale`` exists so a reviewer can disagree with the *reason* rather than with a label. A
    classifier that cannot explain itself cannot be reviewed, and an unreviewable classifier is one
    that gets globally disabled the first time it is wrong.
    """

    column: ColumnFacts
    data_class: DataClass
    confidence: Confidence
    rationale: str
    matched_rule: str | None = None
    review_state: ReviewState = ReviewState.SUGGESTED
    review_recommended: bool = False
    strategy_override: MaskStrategy | None = None

    @property
    def strategy(self) -> MaskStrategy:
        return self.strategy_override or DEFAULT_STRATEGY[self.data_class]

    @property
    def is_sensitive(self) -> bool:
        """The heuristic opinion. Says nothing about whether anything acts on it."""
        return self.data_class.is_sensitive

    @property
    def is_enforced(self) -> bool:
        """The only property masking and the policy engine may act on."""
        return self.review_state is ReviewState.CONFIRMED and self.data_class.is_sensitive

    def confirm(self, *, strategy: MaskStrategy | None = None) -> ColumnClassification:
        return replace(
            self,
            review_state=ReviewState.CONFIRMED,
            strategy_override=strategy or self.strategy_override,
        )

    def reject(self) -> ColumnClassification:
        return replace(self, review_state=ReviewState.REJECTED)

    def reclassify(self, data_class: DataClass, *, rationale: str) -> ColumnClassification:
        """A reviewer's own label. Confirmed by construction - a human just made the decision."""
        return replace(
            self,
            data_class=data_class,
            confidence=Confidence.HIGH,
            rationale=f"Reviewer override: {rationale}",
            matched_rule=None,
            review_state=ReviewState.CONFIRMED,
            review_recommended=False,
        )


# ---------------------------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------------------------

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str) -> str:
    """``PatientMRN_1`` -> ``patient mrn 1``. Tokens, so rules can match on word boundaries."""
    spaced = _CAMEL_BOUNDARY.sub(" ", name)
    return _NON_WORD.sub(" ", spaced.lower()).strip()


def tokens(name: str) -> tuple[str, ...]:
    return tuple(t for t in normalize_name(name).split(" ") if t)


# ---------------------------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NameRule:
    """One name pattern and what it means.

    ``phrases`` match against the normalised name with word boundaries, so ``personal`` never fires
    on ``personality``. ``requires`` is for rules that are only meaningful in combination - ``pan``
    alone is a card number in one industry and an Indian tax id in another, so it is not a rule at
    all; ``card pan`` is.
    """

    rule_id: str
    phrases: tuple[str, ...]
    data_class: DataClass
    confidence: Confidence
    rationale: str

    def matches(self, normalized: str) -> str | None:
        for phrase in self.phrases:
            if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", normalized):
                return phrase
        return None


# Ordered most-specific first. The first match wins, so `national id` beats the generic `_id`
# surrogate-key guard further down, and `password hash` never falls through to `hash`.
NAME_RULES: tuple[NameRule, ...] = (
    # -- credentials and key material ---------------------------------------------------------
    NameRule(
        "credential.password",
        ("password", "passwd", "pwd", "password hash", "passphrase"),
        DataClass.CREDENTIAL,
        Confidence.HIGH,
        "Names a password or its hash; a hash is still an authenticator to an offline cracker.",
    ),
    NameRule(
        "credential.token",
        (
            "api key",
            "apikey",
            "access token",
            "refresh token",
            "bearer token",
            "auth token",
            "session token",
            "reset token",
            "otp",
            "totp",
            "totp secret",
            "mfa secret",
            "mfa code",
            "shared secret",
            "verification code",
        ),
        DataClass.CREDENTIAL,
        Confidence.HIGH,
        "Names a bearer credential: possession of the value is authentication.",
    ),
    NameRule(
        "secret.key_material",
        (
            "private key",
            "secret key",
            "client secret",
            "encryption key",
            "signing key",
            "connection string",
            "dsn",
            "kms key",
            "webhook secret",
            "secret",
        ),
        DataClass.SECRET,
        Confidence.HIGH,
        "Names key material or a connection secret.",
    ),
    # -- government / strong identifiers -------------------------------------------------------
    NameRule(
        "identifier.government",
        (
            "ssn",
            "social security",
            "social security number",
            "national id",
            "national identifier",
            "national insurance number",
            "nino",
            "passport",
            "passport no",
            "passport number",
            "drivers license",
            "driver license",
            "driving licence",
            "license number",
            "licence number",
            "tax id",
            "taxpayer id",
            "tin",
            "ein",
            "aadhaar",
            "aadhar",
            "voter id",
            "npi",
        ),
        DataClass.IDENTIFIER,
        Confidence.HIGH,
        "Names a government-issued identifier: unique, durable and not reissuable after a breach.",
    ),
    # -- health --------------------------------------------------------------------------------
    NameRule(
        "health.clinical",
        (
            "mrn",
            "medical record number",
            "medical record no",
            "diagnosis",
            "diagnosis code",
            "icd",
            "icd9",
            "icd10",
            "cpt",
            "cpt code",
            "snomed",
            "medication",
            "prescription",
            "allergy",
            "allergies",
            "immunization",
            "immunisation",
            "blood type",
            "blood group",
            "care plan",
            "treatment plan",
            "clinical note",
            "clinical notes",
            "nursing notes",
            "progress notes",
            "vital signs",
            "systolic",
            "diastolic",
            "health plan",
            "patient condition",
        ),
        DataClass.HEALTH,
        Confidence.HIGH,
        "Names clinical data; in most jurisdictions this is a special category with its own rules.",
    ),
    # -- financial -----------------------------------------------------------------------------
    NameRule(
        "financial.account",
        (
            "iban",
            "bic",
            "swift code",
            "routing number",
            "sort code",
            "account number",
            "bank account",
            "card number",
            "credit card",
            "debit card",
            "card pan",
            "cardholder",
            "cvv",
            "cvc",
        ),
        DataClass.FINANCIAL,
        Confidence.HIGH,
        "Names a payment instrument or bank account; card data additionally carries PCI DSS scope.",
    ),
    NameRule(
        "financial.personal",
        (
            "salary",
            "wage",
            "compensation",
            "annual income",
            "gross income",
            "net worth",
            "credit score",
        ),
        DataClass.FINANCIAL,
        Confidence.MEDIUM,
        "Personal financial circumstances, distinct from ordinary transaction amounts.",
    ),
    # -- personal ------------------------------------------------------------------------------
    NameRule(
        "personal.contact",
        (
            "email",
            "email address",
            "e mail",
            "phone",
            "phone number",
            "telephone",
            "mobile number",
            "msisdn",
            "fax",
            "emergency contact",
        ),
        DataClass.PERSONAL,
        Confidence.HIGH,
        "A direct contact identifier for a person.",
    ),
    NameRule(
        "personal.name",
        (
            "first name",
            "last name",
            "given name",
            "family name",
            "surname",
            "middle name",
            "maiden name",
            "full name",
            "legal name",
            "fullname",
            "firstname",
            "lastname",
        ),
        DataClass.PERSONAL,
        Confidence.HIGH,
        "Names a person by name; unambiguous regardless of the table it sits in.",
    ),
    NameRule(
        "personal.account_handle",
        ("username", "user name", "login", "screen name", "display name", "nickname", "handle"),
        DataClass.PERSONAL,
        Confidence.MEDIUM,
        "A pseudonymous account handle: not a legal name, but it identifies one person.",
    ),
    NameRule(
        "personal.birth",
        ("dob", "date of birth", "birth date", "birthdate", "birthday", "birth"),
        DataClass.PERSONAL,
        Confidence.HIGH,
        "Date of birth: with a postcode and a sex it re-identifies most people on its own.",
    ),
    NameRule(
        "personal.device",
        ("ip address", "mac address", "device id", "imei", "user agent", "advertising id"),
        DataClass.PERSONAL,
        Confidence.MEDIUM,
        "A device or network identifier; treated as personal data in the EU and UK.",
    ),
    NameRule(
        "personal.address",
        (
            "address",
            "street address",
            "address line1",
            "address line 1",
            "addr line1",
            "postal code",
            "postcode",
            "zip code",
            "zipcode",
            "latitude",
            "longitude",
            "geolocation",
        ),
        DataClass.PERSONAL,
        Confidence.MEDIUM,
        "Locates a person; precise geolocation and postcode are quasi-identifiers.",
    ),
    NameRule(
        "personal.special_category",
        (
            "ethnicity",
            "race",
            "religion",
            "sexual orientation",
            "gender identity",
            "political affiliation",
            "trade union",
            "biometric",
            "fingerprint",
            "disability",
        ),
        DataClass.PERSONAL,
        Confidence.HIGH,
        "Special-category personal data: a leak is not recoverable by reissuing anything.",
    ),
    NameRule(
        "personal.demographic",
        ("gender", "sex", "marital status", "nationality", "citizenship", "date of death"),
        DataClass.PERSONAL,
        Confidence.MEDIUM,
        "Demographic attributes of a person; weak alone, identifying in combination.",
    ),
    # -- free text -----------------------------------------------------------------------------
    NameRule(
        "free_text.notes",
        (
            "note",
            "notes",
            "comment",
            "comments",
            "remark",
            "remarks",
            "memo",
            "free text",
            "narrative",
            "bio",
            "biography",
            "message body",
        ),
        DataClass.FREE_TEXT,
        Confidence.MEDIUM,
        "Operator-entered prose: not sensitive by name, but it is where PII ends up in practice.",
    ),
)

# Names that are *business* data even though they contain a token another rule might want.
# Checked before NAME_RULES so a rule can never win over an explicit exemption.
EXEMPT_RULES: tuple[NameRule, ...] = (
    NameRule(
        "general.money",
        (
            "amount",
            "price",
            "unit price",
            "total",
            "subtotal",
            "balance",
            "discount",
            "tax amount",
            "revenue",
            "cost",
        ),
        DataClass.GENERAL,
        Confidence.HIGH,
        "A transaction amount, not a person's financial circumstances.",
    ),
    NameRule(
        "general.catalog",
        ("description", "label", "title", "category", "status", "type", "sku", "slug"),
        DataClass.GENERAL,
        Confidence.MEDIUM,
        "Catalogue or workflow metadata; contains no personal data by construction.",
    ),
    NameRule(
        "general.counts",
        ("count", "quantity", "qty", "total count", "rank", "score", "weight", "version"),
        DataClass.GENERAL,
        Confidence.HIGH,
        "A measure, not an attribute of a person.",
    ),
    NameRule(
        "general.timestamps",
        ("created at", "updated at", "deleted at", "created on", "modified at", "timestamp"),
        DataClass.GENERAL,
        Confidence.HIGH,
        "A row lifecycle timestamp.",
    ),
)

# Table names that make an otherwise-ambiguous column ("name", "id", "email") personal.
PERSON_TABLE_TOKENS = frozenset(
    {
        "customer",
        "customers",
        "user",
        "users",
        "patient",
        "patients",
        "resident",
        "residents",
        "employee",
        "employees",
        "staff",
        "member",
        "members",
        "contact",
        "contacts",
        "person",
        "people",
        "guest",
        "guests",
        "client",
        "clients",
        "applicant",
        "applicants",
        "subscriber",
        "subscribers",
    }
)

# Entities that are explicitly *not* people. `company_name` is business data; the caveat is that a
# sole trader trading under their own name blurs the line, which is why this is MEDIUM confidence.
NON_PERSON_ENTITY_TOKENS = frozenset(
    {
        "company",
        "organization",
        "organisation",
        "vendor",
        "supplier",
        "product",
        "item",
        "order",
        "invoice",
        "category",
        "store",
        "warehouse",
        "department",
        "project",
        "facility",
        "unit",
        "region",
        "file",
        "table",
        "report",
    }
)

# Head nouns that only mean something once you know what qualifies them. `name` is personal in
# `patients.name` and is not in `products.name`; `customer_id` is a key, `national_id` is not.
AMBIGUOUS_HEADS = frozenset({"name", "id", "code", "reference", "number"})

# Tokens that name an *entity* rather than an attribute. `customer_id` references a customer row;
# `gender_id` describes the person. Only the first shape is safely downgraded to a surrogate key.
ENTITY_REFERENCE_TOKENS = PERSON_TABLE_TOKENS | frozenset(
    {"address", "contact", "account", "profile", "organization", "organisation", "company"}
)

_BOOLEAN_TYPES = ("bool", "bit", "tinyint(1)")
_INTEGER_TYPES = ("int", "serial", "bigint", "smallint", "number", "numeric", "decimal")
_UNBOUNDED_TEXT_TYPES = ("text", "clob", "json", "jsonb", "xml", "varchar(max)", "nvarchar(max)")

# Suffixes that mark a column as a flag or a measure regardless of the tokens before them.
_FLAG_SUFFIXES = ("verified", "confirmed", "enabled", "opt in", "opt out", "flag", "consent")
_MEASURE_SUFFIXES = ("count", "total", "sum", "avg", "rank", "score", "index")


def _type_is(data_type: str | None, needles: tuple[str, ...]) -> bool:
    if not data_type:
        return False
    lowered = data_type.lower()
    return any(n in lowered for n in needles)


def _ends_with_any(normalized: str, suffixes: tuple[str, ...]) -> bool:
    return any(normalized.endswith(suffix) for suffix in suffixes)


def _person_table(table: str | None) -> bool:
    return bool(table) and bool(set(tokens(table or "")) & PERSON_TABLE_TOKENS)


def _from_comment(facts: ColumnFacts) -> ColumnClassification | None:
    """Last resort, low confidence, and only ever *adds* protection to this one column.

    The comment came from the target database, which is content we do not control (see the hostile
    content threat in the threat model). It cannot reduce a classification because it is consulted
    only when the name produced none.
    """
    if not facts.comment:
        return None
    normalized = normalize_name(facts.comment)
    for rule in NAME_RULES:
        phrase = rule.matches(normalized)
        if phrase and rule.data_class.is_sensitive:
            return ColumnClassification(
                column=facts,
                data_class=rule.data_class,
                confidence=Confidence.LOW,
                rationale=(
                    f"The column comment mentions {phrase!r}. Comments are untrusted database "
                    "content, so this is a prompt to review, not evidence."
                ),
                matched_rule=f"comment:{rule.rule_id}",
                review_recommended=True,
            )
    return None


def _exempt_match(normalized: str, *, exact_only: bool) -> tuple[NameRule, str] | None:
    for rule in EXEMPT_RULES:
        if exact_only:
            if normalized in rule.phrases:
                return rule, normalized
            continue
        phrase = rule.matches(normalized)
        if phrase:
            return rule, phrase
    return None


def _general(facts: ColumnFacts, rule: NameRule, phrase: str) -> ColumnClassification:
    return ColumnClassification(
        column=facts,
        data_class=DataClass.GENERAL,
        confidence=rule.confidence,
        rationale=f"{rule.rationale} (matched {phrase!r})",
        matched_rule=rule.rule_id,
    )


def classify(
    name: str | ColumnFacts,
    data_type: str | None = None,
    *,
    table: str | None = None,
    **kwargs: Any,
) -> ColumnClassification:
    """Classify one column. Accepts a :class:`ColumnFacts` or the loose ``(name, type)`` form.

    The order is: explicit business exemptions, then name rules, then guards that *downgrade* a name
    match on type or constraint evidence, then the ambiguous-name table heuristic, then the column
    comment, then an unbounded-text fallback.
    """
    facts = (
        name
        if isinstance(name, ColumnFacts)
        else ColumnFacts(name=name, data_type=data_type, table=table, **kwargs)
    )
    normalized = normalize_name(facts.name)
    name_tokens = tokens(facts.name)

    # Exact exemptions first: a column literally called `status` or `description` is settled, and no
    # sensitivity rule should get to reinterpret it.
    exact = _exempt_match(normalized, exact_only=True)
    if exact is not None:
        return _general(facts, exact[0], exact[1])

    for rule in NAME_RULES:
        phrase = rule.matches(normalized)
        if phrase:
            return _apply_guards(facts, rule, phrase, normalized)

    # Partial exemptions only now: `credit_score` must reach `financial.personal` above before
    # `score` in `general.counts` can claim it.
    partial = _exempt_match(normalized, exact_only=False)
    if partial is not None:
        return _general(facts, partial[0], partial[1])

    if name_tokens and name_tokens[-1] in AMBIGUOUS_HEADS:
        return _classify_ambiguous(facts, normalized, name_tokens)

    from_comment = _from_comment(facts)
    if from_comment is not None:
        return from_comment

    if _type_is(facts.data_type, _UNBOUNDED_TEXT_TYPES):
        return ColumnClassification(
            column=facts,
            data_class=DataClass.FREE_TEXT,
            confidence=Confidence.LOW,
            rationale=(
                f"Unbounded text ({facts.data_type}) with no recognised name. The contents are "
                "unconstrained, so what is in it is a question for a reviewer, not for a regex."
            ),
            matched_rule="type.unbounded_text",
            review_recommended=True,
        )

    return ColumnClassification(
        column=facts,
        data_class=DataClass.UNKNOWN,
        confidence=Confidence.LOW,
        rationale="No rule matched the name, type or constraints.",
        review_recommended=False,
    )


def _apply_guards(
    facts: ColumnFacts, rule: NameRule, phrase: str, normalized: str
) -> ColumnClassification:
    """Turn a name match into a classification, downgrading when type or constraints disagree.

    Guards only ever *reduce* sensitivity, and each one states the contradiction it found: a boolean
    ``email_verified`` records a state about a person rather than the email address itself, and an
    integer ``customer_id`` references a row rather than describing anybody.
    """
    # A rule that matched the *entire* name has already named the column; the suffix guards below
    # exist for derived columns (`email_count`), not for `credit_score`, where "score" is the thing.
    whole_name_match = phrase == normalized

    if _type_is(facts.data_type, _BOOLEAN_TYPES) or (
        not whole_name_match and _ends_with_any(normalized, _FLAG_SUFFIXES)
    ):
        return ColumnClassification(
            column=facts,
            data_class=DataClass.GENERAL,
            confidence=Confidence.HIGH,
            rationale=(
                f"Matched {phrase!r} but the column is a boolean flag, so it records a state "
                "about the subject rather than the subject's data."
            ),
            matched_rule=f"guard.flag:{rule.rule_id}",
        )

    if not whole_name_match and _ends_with_any(normalized, _MEASURE_SUFFIXES):
        return ColumnClassification(
            column=facts,
            data_class=DataClass.GENERAL,
            confidence=Confidence.HIGH,
            rationale=f"Matched {phrase!r} but the column is an aggregate measure.",
            matched_rule=f"guard.measure:{rule.rule_id}",
        )

    # A key that merely happens to contain a matched token. `national id` and friends are IDENTIFIER
    # rules that matched a *specific* phrase, so they are exempt from this downgrade.
    if rule.data_class is not DataClass.IDENTIFIER and _is_surrogate_key(facts, normalized):
        return ColumnClassification(
            column=facts,
            data_class=DataClass.GENERAL,
            confidence=Confidence.MEDIUM,
            rationale=(
                f"Matched {phrase!r} but the column is a key referencing another row, which "
                "identifies a record rather than describing a person."
            ),
            matched_rule=f"guard.surrogate_key:{rule.rule_id}",
        )

    confidence = rule.confidence
    rationale = f"{rule.rationale} (matched {phrase!r})"
    if facts.is_unique and rule.data_class in (DataClass.PERSONAL, DataClass.IDENTIFIER):
        # A unique constraint on a personal attribute means the column is being used as a key for a
        # person, which raises the stakes rather than lowering them.
        confidence = Confidence.HIGH
        rationale += "; a UNIQUE constraint means it identifies a person, not just describes one"

    return ColumnClassification(
        column=facts,
        data_class=rule.data_class,
        confidence=confidence,
        rationale=rationale,
        matched_rule=rule.rule_id,
        review_recommended=confidence is not Confidence.HIGH,
    )


def _is_surrogate_key(facts: ColumnFacts, normalized: str) -> bool:
    """Is this column a reference to a row rather than an attribute of one?

    A declared key settles it. Otherwise the qualifier has to name an *entity*: ``customer_id``
    references a customer, while ``gender_id`` describes the person even though both end in ``id``.
    """
    if not (normalized == "id" or normalized.endswith(" id")):
        return False
    if facts.is_primary_key or facts.is_foreign_key:
        return True
    if not _type_is(facts.data_type, _INTEGER_TYPES):
        return False
    if normalized == "id":
        return True
    qualifier = set(tokens(normalized)[:-1])
    return bool(qualifier & ENTITY_REFERENCE_TOKENS)


def _classify_ambiguous(
    facts: ColumnFacts, normalized: str, name_tokens: tuple[str, ...]
) -> ColumnClassification:
    """A ``name``/``id``/``code`` head: the qualifier or the table decides, or we admit we cannot.

    ``customer_name`` is settled by its own qualifier. Bare ``name`` needs the table. With neither,
    the honest answer is :attr:`DataClass.UNKNOWN` - a confident wrong label is worse than a gap,
    because a reviewer will trust it.
    """
    head = name_tokens[-1]
    qualifier = set(name_tokens[:-1])

    if head == "id":
        if _is_surrogate_key(facts, normalized):
            return ColumnClassification(
                column=facts,
                data_class=DataClass.GENERAL,
                confidence=Confidence.MEDIUM,
                rationale="A key referencing another row, not an attribute of a person.",
                matched_rule="context.surrogate_key",
            )
        return ColumnClassification(
            column=facts,
            data_class=DataClass.UNKNOWN,
            confidence=Confidence.LOW,
            rationale=(
                f"{normalized!r} could be a surrogate key or an externally-issued identifier, and "
                "nothing in the name, type or constraints settles which."
            ),
            matched_rule="context.unknown_id",
            review_recommended=True,
        )

    by_qualifier = bool(qualifier & PERSON_TABLE_TOKENS)
    if by_qualifier or _person_table(facts.table):
        source = "the qualifier" if by_qualifier else f"table {facts.table!r}"
        return ColumnClassification(
            column=facts,
            data_class=DataClass.PERSONAL,
            confidence=Confidence.HIGH if head == "name" else Confidence.MEDIUM,
            rationale=(
                f"{normalized!r} is ambiguous alone, but {source} says it belongs to a person."
            ),
            matched_rule="context.person",
            review_recommended=head != "name",
        )

    if qualifier & NON_PERSON_ENTITY_TOKENS:
        return ColumnClassification(
            column=facts,
            data_class=DataClass.GENERAL,
            confidence=Confidence.MEDIUM,
            rationale=(
                f"{normalized!r} is qualified by a non-person entity, so it describes a thing "
                "rather than somebody."
            ),
            matched_rule="context.non_person_entity",
        )

    if facts.table:
        return ColumnClassification(
            column=facts,
            data_class=DataClass.GENERAL,
            confidence=Confidence.MEDIUM,
            rationale=(
                f"{normalized!r} is ambiguous alone, and neither the qualifier nor the table "
                f"{facts.table!r} describes people."
            ),
            matched_rule="context.non_person_table",
        )

    return ColumnClassification(
        column=facts,
        data_class=DataClass.UNKNOWN,
        confidence=Confidence.LOW,
        rationale=(
            f"{normalized!r} means different things in different tables and no table was given. "
            "Guessing here would put a confident wrong label in front of a reviewer."
        ),
        matched_rule="context.unknown_table",
        review_recommended=True,
    )


# ---------------------------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------------------------


@dataclass(slots=True)
class ClassificationCatalog:
    """Classifications for a schema, keyed by ``(schema, table, column)``.

    :meth:`merge` is what makes re-scanning safe: an existing human decision - confirmed *or*
    rejected - survives, and only unreviewed entries are replaced by the new suggestion. Without
    that, every enrollment would silently undo the reviewer's work.
    """

    entries: dict[tuple[str, str, str], ColumnClassification] = field(default_factory=dict)

    def add(self, classification: ColumnClassification) -> None:
        self.entries[classification.column.key] = classification

    def get(self, schema: str, table: str, column: str) -> ColumnClassification | None:
        return self.entries.get((schema.lower(), table.lower(), column.lower()))

    def merge(self, incoming: Iterable[ColumnClassification]) -> ClassificationCatalog:
        """Apply new suggestions without discarding reviewed decisions."""
        merged = ClassificationCatalog(dict(self.entries))
        for candidate in incoming:
            existing = merged.entries.get(candidate.column.key)
            if existing is not None and existing.review_state is not ReviewState.SUGGESTED:
                continue
            merged.entries[candidate.column.key] = candidate
        return merged

    def suggested(self) -> tuple[ColumnClassification, ...]:
        return tuple(c for c in self.entries.values() if c.review_state is ReviewState.SUGGESTED)

    def needs_review(self) -> tuple[ColumnClassification, ...]:
        return tuple(
            c for c in self.suggested() if c.review_recommended or c.data_class.is_sensitive
        )

    def enforced(self) -> tuple[ColumnClassification, ...]:
        return tuple(c for c in self.entries.values() if c.is_enforced)

    def enforced_column_keys(self) -> set[tuple[str, str, str]]:
        """The set shape ``SchemaScope.sensitive_columns`` expects - confirmed entries only."""
        return {c.column.key for c in self.enforced()}


def classify_columns(facts: Iterable[ColumnFacts]) -> ClassificationCatalog:
    """Classify a whole schema. Pure: no I/O, no model, no ordering dependence."""
    catalog = ClassificationCatalog()
    for column in facts:
        catalog.add(classify(column))
    return catalog


# ---------------------------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------------------------

_EMAIL = re.compile(r"^([^@\s]+)@([^@\s]+)$")


def _partial(value: str) -> str:
    """Keep enough to recognise a row, not enough to reconstruct the value.

    An email keeps its domain, because the domain is usually the analytical signal ("how many
    @nhs.uk addresses") while the local part is the identifier. Anything else keeps its last four
    characters, the convention every card statement already uses.
    """
    email = _EMAIL.match(value)
    if email:
        local, domain = email.groups()
        return f"{local[0]}***@{domain}" if local else f"***@{domain}"
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def _pseudonym(value: str, *, salt: str) -> str:
    """A stable pseudonym so grouping and counting still work.

    This is pseudonymisation, not anonymisation: with the salt and a candidate list, the mapping is
    reversible by brute force. It is the right tool for "count distinct patients" and the wrong tool
    for "this data is now safe to share".
    """
    digest = hashlib.sha256(f"{salt}|{value}".encode()).hexdigest()
    return f"px_{digest[:_HASH_PREFIX_LEN]}"


def mask_value(
    value: Any,
    classification: ColumnClassification | DataClass,
    *,
    strategy: MaskStrategy | None = None,
    salt: str = "",
    enforce_unreviewed: bool = False,
) -> Any:
    """Mask one value according to its classification.

    By default a suggestion does nothing: only :attr:`ReviewState.CONFIRMED` classifications mask,
    which is the whole point of the review state. ``enforce_unreviewed=True`` opts into masking
    everything the heuristics flagged - appropriate for a preview screen or a demo, and stated
    explicitly at the call site rather than being the quiet default.

    Passing a bare :class:`DataClass` masks unconditionally; that form is for callers that have
    already decided.
    """
    if isinstance(classification, DataClass):
        active = strategy or DEFAULT_STRATEGY[classification]
    else:
        if not classification.is_enforced and not (
            enforce_unreviewed and classification.is_sensitive
        ):
            return value
        active = strategy or classification.strategy

    if value is None or active is MaskStrategy.NONE:
        return value
    text = value if isinstance(value, str) else str(value)
    if active is MaskStrategy.REDACT:
        return REDACTED
    if active is MaskStrategy.HASH:
        return _pseudonym(text, salt=salt)
    return _partial(text)


def mask_record(
    record: Mapping[str, Any],
    catalog: ClassificationCatalog,
    *,
    schema: str = "",
    table: str = "",
    salt: str = "",
    enforce_unreviewed: bool = False,
) -> dict[str, Any]:
    """Mask a result row. Columns with no classification pass through untouched.

    Passing through the unknown is deliberate: silently blanking columns nobody classified would
    make the tool unusable and would teach operators to turn masking off entirely.
    """
    out: dict[str, Any] = {}
    for column, value in record.items():
        classification = catalog.get(schema, table, column)
        out[column] = (
            value
            if classification is None
            else mask_value(
                value,
                classification,
                salt=salt,
                enforce_unreviewed=enforce_unreviewed,
            )
        )
    return out


__all__ = [
    "AMBIGUOUS_HEADS",
    "DEFAULT_STRATEGY",
    "ENTITY_REFERENCE_TOKENS",
    "EXEMPT_RULES",
    "NAME_RULES",
    "NON_PERSON_ENTITY_TOKENS",
    "PERSON_TABLE_TOKENS",
    "REDACTED",
    "ClassificationCatalog",
    "ColumnClassification",
    "ColumnFacts",
    "Confidence",
    "DataClass",
    "MaskStrategy",
    "NameRule",
    "ReviewState",
    "classify",
    "classify_columns",
    "mask_record",
    "mask_value",
    "normalize_name",
    "tokens",
]
