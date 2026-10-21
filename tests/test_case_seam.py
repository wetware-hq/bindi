"""The clinical-immunology case seam: target from a patient record, with the hold enforced.

A green here means: a case record is validated against the documented fields, the design
target is extracted from it (fixture fallback intact), the three hold conditions are
enforced in code, and one end-to-end run writes one JSONL trace whose stages carry honest
statuses. It does NOT mean a design run happened, a margin was computed, or the
human-reference gate exists; the tests below pin those as placeholder statuses on purpose.
"""

import json
from pathlib import Path

import pytest

from bindi import case as case_mod
from bindi import data, gates, margin
from bindi.e2e import EXIT_HOLD, EXIT_OK, case_run
from bindi.generate import generate
from bindi.provenance import sequence_text

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "immunology_case.v1.schema.json"
PLACEHOLDER = ROOT / "fixtures" / "immunology_case_PLACEHOLDER.json"
def _configure_gates(monkeypatch, tmp_path):
    human = tmp_path / "clean_human.fasta"
    human.write_text(">unrelated\nACDEFGHIKLMNPQRSTVWYACDEFGHIK\n", encoding="utf-8")
    hazard = ROOT / "fixtures" / "hazard_screen_clean.fasta"
    monkeypatch.setenv(gates.REFERENCE_ENV, str(human))
    monkeypatch.setenv(gates.REFERENCE_SOURCE_ENV, "synthetic-test-human-v1")
    monkeypatch.setenv(gates.HAZARD_ENV, str(hazard))
    monkeypatch.setenv(gates.HAZARD_SOURCE_ENV, "synthetic-test-hazard-v1")


def _case():
    return json.loads(PLACEHOLDER.read_text(encoding="utf-8"))


# ---- schema file -------------------------------------------------------------

def test_schema_file_declares_the_clinical_fields():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["title"] == "bindi.immunology_case.v1"
    required = set(schema["required"])
    assert {"presentation", "hla", "neoantigen", "consent", "data_class", "signature"} <= required
    neo = schema["properties"]["neoantigen"]
    assert {"mutant_peptide", "wild_type_peptide"} <= set(neo["required"]), (
        "the wild-type counterpart is required: a margin cannot be stated without it"
    )


def test_schema_and_validator_agree_on_required_top_level_fields():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    for field in schema["required"]:
        if field == "schema":
            continue
        broken = _case()
        broken.pop(field)
        assert case_mod.validate_case(broken), f"validator accepts a record missing {field}"


# ---- the placeholder record ---------------------------------------------------

def test_placeholder_record_validates_and_is_not_signed():
    case = _case()
    assert case_mod.validate_case(case) == []
    assert case_mod.is_signed(case) is False
    assert "PLACEHOLDER" in json.dumps(case)


def test_repo_ships_no_signed_case_record():
    """Only the Physician signs a patient. No signed record may be committed here."""
    for path in (ROOT / "fixtures").glob("immunology_case*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record.get("signature", {}).get("signed") is not True, (
            f"{path.name} claims to be signed; a signed synthetic patient comes from the "
            "clinical half, not from a fixture in this repo"
        )


# ---- validation ---------------------------------------------------------------

@pytest.mark.parametrize(
    "mutate,fragment",
    [
        (lambda c: c["data_class"].update(synthetic=False), "synthetic"),
        (lambda c: c.update(case_id="PT-12345"), "SYNTH-"),
        (
            lambda c: c["neoantigen"].update(
                wild_type_peptide=c["neoantigen"]["mutant_peptide"]
            ),
            "equals wild_type_peptide",
        ),
        (
            lambda c: c["neoantigen"].update(
                wild_type_peptide={
                    "sequence": "YLGPETGDWAA",
                    "provenance": {"kind": "reference", "source": "test"},
                }
            ),
            "differ in length",
        ),
        (lambda c: c["hla"].update(restriction_allele="HLA-A*11:01"), "not in hla.class_i"),
        (
            lambda c: c["neoantigen"].update(
                mutant_peptide={
                    "sequence": "YLBPETGDW",
                    "provenance": {"kind": "reference", "source": "test"},
                }
            ),
            "one-letter residues",
        ),
        (lambda c: c["consent"].update(scope="whatever"), "consent.scope"),
    ],
)
def test_validator_rejects(mutate, fragment):
    case = _case()
    mutate(case)
    problems = case_mod.validate_case(case)
    assert any(fragment in p for p in problems), problems


def test_load_case_raises_on_invalid(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "bindi.immunology_case.v1"}), encoding="utf-8")
    with pytest.raises(case_mod.CaseError):
        case_mod.load_case(bad)


# ---- the seam itself -----------------------------------------------------------

def test_target_comes_from_the_case_record():
    target = case_mod.resolve_target({}, case=_case())
    assert target["source"] == "case_record"
    assert sequence_text(target["mutant_peptide"]) == "YLDPETGDW"
    assert sequence_text(target["wild_type_peptide"]) == "YLGPETGDW"
    assert target["restriction_allele"] == "HLA-A*02:01"
    assert target["signed"] is False


def test_fixture_fallback_is_intact_when_no_case_is_given(monkeypatch):
    monkeypatch.delenv(case_mod.CASE_ENV, raising=False)
    target = case_mod.resolve_target({"boltz": {"target": "hPDL1"}})
    assert target["source"] == "fixture"
    assert target["target_id"] == "hPDL1"
    assert target["mutant_peptide"] is None


def test_case_path_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv(case_mod.CASE_ENV, str(tmp_path / "from_env.json"))
    assert case_mod.case_path_from({}, None) == tmp_path / "from_env.json"
    assert case_mod.case_path_from({"case": "from_policy.json"}, None) == Path("from_policy.json")
    assert case_mod.case_path_from({"case": "from_policy.json"}, "explicit.json") == Path("explicit.json")


def test_no_case_and_no_target_is_an_error(monkeypatch):
    monkeypatch.delenv(case_mod.CASE_ENV, raising=False)
    with pytest.raises(case_mod.CaseError):
        case_mod.resolve_target({"boltz": {}})


def test_legacy_design_block_still_resolves_a_target(monkeypatch):
    """Backwards compatibility only: an existing policy file keyed `bindcraft` still reads."""
    monkeypatch.delenv(case_mod.CASE_ENV, raising=False)
    target = case_mod.resolve_target({"bindcraft": {"target": "hPDL1"}})
    assert target["target_id"] == "hPDL1"


# ---- the hold -------------------------------------------------------------------

def test_hold_names_all_three_conditions_for_the_placeholder():
    reasons = case_mod.design_hold_reasons(case_mod.resolve_target({}, case=_case()))
    blob = " ".join(reasons)
    assert "not signed" in blob
    assert "human-reference gate" in blob
    assert "margin" in blob


def test_hold_clears_only_when_signed_gated_and_margin_stated():
    case = _case()
    case["signature"] = {"signed": True, "signed_by": "synthetic clinical sign-off (test)"}
    target = case_mod.resolve_target({}, case=case)
    assert case_mod.design_hold_reasons(target) != []
    assert case_mod.design_hold_reasons(
        target,
        human_reference={"status": "pass"},
        margin={"status": "stated", "threshold_status": "established"},
    ) == []


def test_hold_blocks_a_real_generate_before_the_hosted_api_is_reached(tmp_path, monkeypatch):
    """The hold fires before the generator: no request is built and the API is never called.

    Rewritten from the BOLTZGEN_CMD version: there is no local binary to point at a
    nonexistent path any more, so the check is that the hosted transport is never used.
    """
    from bindi import boltz_hosted

    def explode(*args, **kwargs):
        raise AssertionError("the hosted Boltz API was contacted despite the design hold")

    monkeypatch.setenv(boltz_hosted.API_KEY_ENV, "sk_bc_ws_test_FAKE-KEY-NOT-A-CREDENTIAL")
    monkeypatch.setattr(boltz_hosted, "_urllib_transport", explode)
    monkeypatch.setattr(boltz_hosted, "submit_design", explode)
    policy = {
        "boltz": {
            "target": "hPDL1",
            "design_spec": "campaigns/nyeso1_a0201_pmhc.yaml",
            "project_folder": str(tmp_path / "results"),
        },
        "case": str(PLACEHOLDER),
    }
    with pytest.raises(case_mod.DesignHold):
        generate(policy, workdir=ROOT, dry_run=False)


def test_unsigned_consent_refusal():
    case = _case()
    case["signature"] = {"signed": True, "signed_by": "test"}
    case["consent"]["design_use"] = False
    reasons = case_mod.design_hold_reasons(case_mod.resolve_target({}, case=case))
    assert any("consent.design_use" in r for r in reasons)


# ---- the two Scientist conditions --------------------------------------------------

def test_margin_is_not_computed_without_a_scorer(monkeypatch):
    monkeypatch.delenv(margin.SCORER_ENV, raising=False)
    record = margin.score_wild_type_margin(case_mod.resolve_target({}, case=_case()), [])
    assert record["status"] == "not_computed"
    assert "no design run" in record["reason"].lower() or "No scorer" in record["reason"]


def test_margin_is_stated_when_a_scorer_is_injected():
    target = case_mod.resolve_target({}, case=_case())
    designs = [{"design": "d1", "sequence": "ACDEFGHIKL"}]

    mutant = sequence_text(target["mutant_peptide"])

    def scorer(peptide, sequence):
        return 1.0 if peptide == mutant else 0.25

    record = margin.score_wild_type_margin(target, designs, scorer=scorer)
    assert record["status"] == "stated"
    assert record["per_design"][0]["margin"] == "0.750000"


def test_margin_not_applicable_for_a_fixture_target(monkeypatch):
    monkeypatch.delenv(case_mod.CASE_ENV, raising=False)
    target = case_mod.resolve_target({"boltz": {"target": "hPDL1"}})
    assert margin.score_wild_type_margin(target, []) ["status"] == "not_applicable"


# ---- end to end: one case, one trace -------------------------------------------------

def _run(tmp_path, **kwargs):
    result = case_run(PLACEHOLDER, workdir=tmp_path, **kwargs)
    lines = [
        json.loads(line)
        for line in (tmp_path / "case_trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return result, lines


def test_end_to_end_writes_one_trace_covering_every_stage(tmp_path, monkeypatch):
    _configure_gates(monkeypatch, tmp_path)
    monkeypatch.delenv(margin.SCORER_ENV, raising=False)
    result, lines = _run(tmp_path)
    assert result["exit_code"] == EXIT_OK
    stages = [line["stage"] for line in lines]
    assert stages == [
        "run_start",
        "case_load",
        "target_resolve",
        "design",
        "curate",
        "margin_wild_type",
        "human_reference_gate",
        "hazard_screen",
        "ward_handoff",
        "letter",
        "case_summary",
    ]
    assert all(line["schema"] == "bindi.case_trace.v1" for line in lines)
    assert [line["seq"] for line in lines] == list(range(1, len(lines) + 1))
    assert {line["case_id"] for line in lines if line["seq"] > 1} == {"SYNTH-IMM-001"}


def test_trace_marks_the_placeholders_honestly(tmp_path, monkeypatch, require_mmseqs):
    _configure_gates(monkeypatch, tmp_path)
    monkeypatch.delenv(margin.SCORER_ENV, raising=False)
    _, lines = _run(tmp_path)
    by_stage = {line["stage"]: line for line in lines}
    assert by_stage["design"]["status"] == "fixture_backed"
    assert by_stage["design"]["designs_are_real"] is False
    assert by_stage["margin_wild_type"]["status"] == "not_computed"
    assert by_stage["human_reference_gate"]["status"] == "pass"
    assert by_stage["hazard_screen"]["status"] == "pass"
    assert by_stage["ward_handoff"]["status"] == "built"
    assert by_stage["letter"]["status"] == "produced"
    summary = by_stage["case_summary"]
    assert summary["release"] is False
    assert summary["design_hold"], "the summary must carry the hold reasons"


def test_trace_claims_no_absolute_affinity(tmp_path, monkeypatch):
    _configure_gates(monkeypatch, tmp_path)
    _, lines = _run(tmp_path)
    def keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from keys(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from keys(v)
    offenders = [k for line in lines for k in keys(line) if data.looks_like_absolute_affinity(k)]
    assert offenders == [], offenders


def test_trace_is_reproducible_under_the_test_clock(tmp_path, monkeypatch):
    _configure_gates(monkeypatch, tmp_path)
    monkeypatch.setenv("BINDI_CLOCK", "test")
    _, lines = _run(tmp_path)
    assert {line["ts"] for line in lines} == {"1937-01-01T00:00:00Z"}


def test_requesting_a_real_design_run_is_refused_and_exits_two(tmp_path):
    result, lines = _run(tmp_path, allow_design=True)
    assert result["exit_code"] == EXIT_HOLD
    design = [line for line in lines if line["stage"] == "design"][0]
    assert design["status"] == "refused"
    assert design["hold_reasons"]
    assert "curate" not in [line["stage"] for line in lines]


def test_ward_handoff_payload_is_built_but_unranked_and_unreleased(
    tmp_path, monkeypatch, require_mmseqs
):
    _configure_gates(monkeypatch, tmp_path)
    result, _ = _run(tmp_path)
    payload = json.loads(Path(result["handoff"]).read_text(encoding="utf-8"))
    assert payload["synthetic"] is True
    assert payload["candidates_ranked"] is False
    assert payload["release"] is False


def test_an_invalid_case_still_produces_a_trace(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    result = case_run(bad, workdir=tmp_path, trace_path=tmp_path / "t.jsonl")
    assert result["exit_code"] == 1
    lines = [json.loads(l) for l in (tmp_path / "t.jsonl").read_text().splitlines() if l.strip()]
    assert [l["stage"] for l in lines] == ["run_start", "case_load", "case_summary"]
    assert lines[1]["status"] == "error"


# ---- the config swap -----------------------------------------------------------------

def test_placeholder_campaign_resolves_its_target_from_the_case_record(monkeypatch):
    """The real run is a config swap: `case` points at a record, not at code."""
    monkeypatch.chdir(ROOT)
    policy = json.loads((ROOT / "campaigns" / "neoantigen_placeholder.json").read_text(encoding="utf-8"))
    assert policy["case"].endswith("immunology_case_PLACEHOLDER.json")
    assert "target" not in policy["boltz"], (
        "a case-driven campaign must not also hard-code a fixture target"
    )
    target = case_mod.resolve_target(policy)
    assert target["source"] == "case_record"
    assert target["case_id"] == "SYNTH-IMM-001"
    assert case_mod.design_hold_reasons(target), "the hold must still be in force"
