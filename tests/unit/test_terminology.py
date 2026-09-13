import pytest

from backend.models.extraction import TerminologyStatus
from backend.pipeline.terminology.ct import (
    ARM_DATA_ORIGIN_TYPE,
    ARM_TYPE,
    ELIGIBILITY_CATEGORY,
    STUDY_PROTOCOL_STATUS,
    CodelistNotConfiguredError,
    CtField,
    CtResolver,
    get_ct_resolver,
)


@pytest.fixture(scope="module")
def resolver() -> CtResolver:
    return get_ct_resolver()


def test_ct_version_comes_from_the_usdm4_cache(resolver: CtResolver) -> None:
    assert len(resolver.version) == 10 and resolver.version[4] == "-"


@pytest.mark.parametrize(
    ("phrase", "field", "code", "matched_on"),
    [
        ("Placebo Control Arm", ARM_TYPE, "C174268", "preferredTerm"),
        ("placebo comparator arm", ARM_TYPE, "C174268", "submissionValue"),  # case-insensitive
        ("  Investigational   Arm ", ARM_TYPE, "C174266", "preferredTerm"),  # whitespace
        ("C174268", ARM_TYPE, "C174268", "conceptId"),
        ("Sham Intervention Arm", ARM_TYPE, "C174269", "synonym"),
        ("INCLUSION", ELIGIBILITY_CATEGORY, "C25532", "submissionValue"),
        ("Exclusion Criteria", ELIGIBILITY_CATEGORY, "C25370", "preferredTerm"),
        ("Final", STUDY_PROTOCOL_STATUS, "C25508", "submissionValue"),
        ("Data Generated Within Study", ARM_DATA_ORIGIN_TYPE, "C188866", "submissionValue"),
    ],
)
def test_exact_matches(
    resolver: CtResolver, phrase: str, field: CtField, code: str, matched_on: str
) -> None:
    result = resolver.resolve(phrase, field)
    assert result is not None
    assert result.status == TerminologyStatus.EXACT
    assert (result.code, result.matched_on) == (code, matched_on)
    assert result.preferred_term and result.candidates == []


def test_close_phrase_is_fuzzy_with_candidates_and_no_code(resolver: CtResolver) -> None:
    result = resolver.resolve("placebo", ARM_TYPE)
    assert result is not None
    assert result.status == TerminologyStatus.FUZZY
    assert result.code is None, "a fuzzy match must never carry a code"
    assert result.candidates[0].code == "C174268"


def test_unrelated_phrase_is_unresolved(resolver: CtResolver) -> None:
    result = resolver.resolve("double blind", ARM_TYPE)
    assert result is not None
    assert result.status == TerminologyStatus.UNRESOLVED
    assert result.code is None
    assert result.candidates, "reviewers still get candidates to pick from"


def test_hallucinated_code_is_not_accepted(resolver: CtResolver) -> None:
    result = resolver.resolve("C99999", ARM_TYPE)
    assert result is not None and result.status != TerminologyStatus.EXACT and result.code is None


def test_code_from_another_codelist_is_not_accepted(resolver: CtResolver) -> None:
    # C25532 is "Inclusion Criteria": a valid code, but not an arm type.
    result = resolver.resolve("C25532", ARM_TYPE)
    assert result is not None and result.code is None


@pytest.mark.parametrize("phrase", [None, "", "   "])
def test_empty_phrase_resolves_to_none(resolver: CtResolver, phrase: str | None) -> None:
    assert resolver.resolve(phrase, ARM_TYPE) is None


def test_unconfigured_field_raises(resolver: CtResolver) -> None:
    with pytest.raises(CodelistNotConfiguredError):
        resolver.resolve("anything", CtField("StudyArm", "notAnAttribute"))
