import copy
import json
from pathlib import Path

import pytest

from src.rag.paper_context_renderer_v2 import (
    CONDITIONS,
    render_abc,
    render_generation_input,
)


DRAFT_DIR = (
    Path(__file__).resolve().parents[1]
    / "data/paper_experiment_v2/dev_drafts"
)
FILES = [
    DRAFT_DIR / f"dev-backend-{number:03d}.draft.json"
    for number in range(1, 6)
]
KINDS = [
    "none", "forged_signature", "revoked", "expired", "untrusted_issuer"
]
BUDGET = 512  # Development test value, not an approved experiment setting.


def load_draft(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def payload(generation_input):
    return json.loads(generation_input["messages"][1]["content"])


def ids(generation_input):
    return [item["record_id"] for item in payload(generation_input)["evidence"]]


def assert_record_allowlist(record):
    # Independent expected whitelist, including nested field types.
    assert set(record) <= {
        "record_id", "issuer", "achievement", "skills",
        "issued_at", "awarded_date",
    }
    assert isinstance(record["record_id"], str)
    if "issuer" in record:
        assert set(record["issuer"]) <= {"name"}
        assert all(isinstance(v, str) for v in record["issuer"].values())
    if "achievement" in record:
        assert set(record["achievement"]) <= {
            "name", "description", "criteria_narrative"
        }
        assert all(isinstance(v, str) for v in record["achievement"].values())
    if "skills" in record:
        assert isinstance(record["skills"], list)
        for skill in record["skills"]:
            assert set(skill) <= {"label_ko", "label_en"}
            assert all(isinstance(v, str) for v in skill.values())
    for field in ("issued_at", "awarded_date"):
        if field in record:
            assert isinstance(record[field], str)


@pytest.mark.parametrize("path,kind", zip(FILES, KINDS))
def test_five_development_cases(path, kind):
    before_bytes = path.read_bytes()
    draft = load_draft(path)
    before = copy.deepcopy(draft)
    assert draft["noise"]["kind"] == kind
    assert len(draft["candidate_record_ids"]) == (1 if kind == "none" else 2)
    assert len(draft["eligible_record_ids"]) == 1

    rendered = render_abc(draft, max_new_tokens=BUDGET)
    assert len(rendered) == 3
    assert ids(rendered["A_no_rag"]) == []
    assert ids(rendered["B_naive_rag"]) == draft["candidate_record_ids"]
    assert ids(rendered["C_verified_rag"]) == [
        item for item in draft["candidate_record_ids"]
        if item in draft["eligible_record_ids"]
    ]
    for generation in rendered.values():
        assert set(generation) == {"messages", "max_new_tokens"}
        assert generation["max_new_tokens"] == BUDGET
        assert [item["role"] for item in generation["messages"]] == [
            "system", "user"
        ]
        assert all(
            set(item) == {"role", "content"}
            for item in generation["messages"]
        )
        content = payload(generation)
        assert set(content) == {"jd", "profile", "request", "evidence"}
        assert content["jd"] == draft["jd"]["text"]
        assert content["profile"] == draft["profile"]["text"]
        assert content["request"] == draft["request"]
        for record in content["evidence"]:
            assert_record_allowlist(record)

    assert len({
        item["messages"][0]["content"] for item in rendered.values()
    }) == 1
    assert draft == before
    assert path.read_bytes() == before_bytes


def test_unlisted_fields_and_nested_metadata_never_enter_model_input():
    draft = load_draft(FILES[0])
    sentinel = "PRIVATE_METADATA_SENTINEL"
    draft["gold_proposal"] = {"secret": sentinel}
    draft["noise"]["answer"] = sentinel
    draft["unexpected_field"] = sentinel
    draft["profile"]["secret"] = sentinel
    draft["jd"]["secret"] = sentinel
    record = draft["source_records"][0]
    record["secret"] = sentinel
    record["issuer"]["trust_level"] = sentinel
    record["achievement"]["gold"] = sentinel
    record["verification"]["private_note"] = sentinel
    for skill in record.get("skills", []):
        skill["confidence"] = sentinel
        skill["taxonomy"] = {"secret": sentinel}
    draft["record_audits"][0]["reported_verification"] = copy.deepcopy(
        record["verification"]
    )

    for generation in render_abc(draft, max_new_tokens=BUDGET).values():
        assert sentinel not in json.dumps(generation, ensure_ascii=False)
        for projected in payload(generation)["evidence"]:
            assert_record_allowlist(projected)


def test_allowed_text_fields_cannot_hide_nested_objects():
    draft = load_draft(FILES[0])
    draft["source_records"][0]["achievement"]["name"] = {"checks": "secret"}
    with pytest.raises(ValueError, match="Invalid allowed value"):
        render_generation_input(draft, "B_naive_rag", max_new_tokens=BUDGET)


def test_candidate_order_and_no_refill():
    draft = load_draft(FILES[1])
    draft["candidate_record_ids"].reverse()
    rendered = render_abc(draft, max_new_tokens=BUDGET)
    assert ids(rendered["B_naive_rag"]) == draft["candidate_record_ids"]
    assert ids(rendered["C_verified_rag"]) == draft["eligible_record_ids"]

    excluded = next(
        item for item in draft["candidate_record_ids"]
        if item not in draft["eligible_record_ids"]
    )
    draft["candidate_record_ids"] = [excluded]
    rendered = render_abc(draft, max_new_tokens=BUDGET)
    assert ids(rendered["B_naive_rag"]) == [excluded]
    assert ids(rendered["C_verified_rag"]) == []


@pytest.mark.parametrize("change", [
    "unknown_candidate", "duplicate_candidate", "duplicate_record",
    "wrong_subject", "eligibility", "audit_flag", "audit_snapshot",
])
def test_inconsistent_inputs_fail_closed(change):
    draft = load_draft(FILES[1])
    if change == "unknown_candidate":
        draft["candidate_record_ids"].append("unknown")
    elif change == "duplicate_candidate":
        draft["candidate_record_ids"].append(draft["candidate_record_ids"][0])
    elif change == "duplicate_record":
        draft["source_records"].append(copy.deepcopy(draft["source_records"][0]))
    elif change == "wrong_subject":
        draft["profile"]["subject_did"] = "did:fixture:other"
    elif change == "eligibility":
        draft["eligible_record_ids"] = list(draft["candidate_record_ids"])
    elif change == "audit_flag":
        draft["record_audits"][0]["eligible"] = False
    else:
        draft["record_audits"][0]["reported_verification"]["status"] = "pending"
    with pytest.raises(ValueError):
        render_abc(draft, max_new_tokens=BUDGET)


def test_verified_status_alone_is_insufficient():
    draft = load_draft(FILES[0])
    record = draft["source_records"][0]
    for check in record["verification"]["checks"]:
        if check["check"] == "signature":
            check["result"] = "skip"
    audit = draft["record_audits"][0]
    audit["reported_verification"] = copy.deepcopy(record["verification"])
    audit["eligible"] = False
    draft["eligible_record_ids"] = []
    rendered = render_abc(draft, max_new_tokens=BUDGET)
    assert len(ids(rendered["B_naive_rag"])) == 1
    assert ids(rendered["C_verified_rag"]) == []


def test_outputs_are_independent_of_source_and_each_other():
    draft = load_draft(FILES[0])
    before = copy.deepcopy(draft)
    rendered = render_abc(draft, max_new_tokens=BUDGET)
    rendered["B_naive_rag"]["messages"][0]["content"] = "changed"
    assert rendered["C_verified_rag"]["messages"][0]["content"] != "changed"
    assert draft == before


@pytest.mark.parametrize("budget", [0, -1, True, "512"])
def test_invalid_budget(budget):
    with pytest.raises(ValueError):
        render_abc(load_draft(FILES[0]), max_new_tokens=budget)


def test_unknown_condition():
    with pytest.raises(ValueError):
        render_generation_input(
            load_draft(FILES[0]), "unknown", max_new_tokens=BUDGET
        )


def test_total_generation_inputs():
    inputs = [
        generation
        for path in FILES
        for generation in render_abc(
            load_draft(path), max_new_tokens=BUDGET
        ).values()
    ]
    assert len(inputs) == len(FILES) * len(CONDITIONS) == 15