"""T-041 — chunking, indexing and retrieval over the policy corpus (docs/06 §7)."""

from __future__ import annotations

import socket
from functools import lru_cache
from pathlib import Path

import pytest

from ai.rag.chunk import MAX_TOKENS, chunk_corpus, chunk_document, estimate_tokens
from ai.rag.evaluate import PROSE_QUESTIONS, TARGET, TOP_K, evaluate_retrieval
from ai.rag.index import LEXICAL_WEIGHT, VECTOR_WEIGHT, index_corpus, retrieve
from synthetic.corpus import CORPUS_ROOT, build_corpus

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def postgres_is_up() -> bool:
    try:
        with socket.create_connection(("localhost", 5432), 2):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def corpus() -> Path:
    build_corpus()
    return CORPUS_ROOT


@pytest.fixture
def _database() -> None:
    if not postgres_is_up():
        pytest.skip("PostgreSQL not reachable; run `make up`")


# ---------------------------------------------------------------------------
# the corpus
# ---------------------------------------------------------------------------
def test_the_corpus_is_generated_from_the_policy_packs(corpus: Path) -> None:
    """A corpus maintained by hand drifts from the rules within a release, and
    then a citation looks like provenance without being it."""
    names = {d.path.name for d in build_corpus()}
    assert {
        "credit_policy.md",
        "product_PF-STD.md",
        "product_PF-SHARIAH.md",
        "authority_matrix.md",
        "collections_procedure.md",
        "hardship_policy.md",
    } <= names


def test_every_rule_in_a_pack_becomes_a_clause(corpus: Path) -> None:
    import yaml

    pack = yaml.safe_load((ROOT / "policy_packs" / "PF-STD" / "2026.09.1" / "policy.yaml").read_text())
    expected = {
        rule["id"]
        for section in pack.values()
        if isinstance(section, dict)
        for rule in (section.get("rules") or [])
    }
    indexed = {c.clause_id for c in chunk_document(corpus / "product_PF-STD.md")}
    assert expected <= indexed


def test_every_document_carries_its_product_and_version(corpus: Path) -> None:
    for clause in chunk_corpus(corpus):
        assert clause.product
        assert clause.version


# ---------------------------------------------------------------------------
# chunking
# ---------------------------------------------------------------------------
def test_a_clause_is_the_unit_not_a_token_window(corpus: Path) -> None:
    """Splitting by token count would cut a rule in half and produce two chunks
    neither of which is the rule."""
    clauses = chunk_corpus(corpus)
    assert clauses
    assert all(c.text.lstrip().startswith("###") for c in clauses)


def test_no_clause_exceeds_the_token_limit(corpus: Path) -> None:
    assert all(c.tokens <= MAX_TOKENS for c in chunk_corpus(corpus))


def test_clause_keys_are_unique(corpus: Path) -> None:
    clauses = chunk_corpus(corpus)
    assert len({c.key for c in clauses}) == len(clauses)


def test_the_same_clause_id_can_appear_in_several_documents(corpus: Path) -> None:
    """A product sheet and the credit policy both carry ELG-02; both are
    retrievable, and each says which document it came from."""
    assert len({c.doc for c in chunk_corpus(corpus) if c.clause_id == "ELG-02"}) > 1


def test_a_section_heading_is_not_a_clause(corpus: Path) -> None:
    ids = {c.clause_id for c in chunk_corpus(corpus)}
    assert "Eligibility" not in ids
    assert all("-" in i for i in ids)


def test_a_long_clause_is_split_at_paragraphs_with_its_heading_repeated(tmp_path: Path) -> None:
    paragraph = " ".join(["word"] * 300)
    document = tmp_path / "long.md"
    document.write_text(
        "---\nproduct: ALL\nversion: 1\ndoc: long\n---\n\n"
        f"### XYZ-01\n\n{paragraph}\n\n{paragraph}\n\n{paragraph}\n"
    )
    pieces = chunk_document(document)
    assert len(pieces) > 1
    assert all(p.clause_id == "XYZ-01" for p in pieces)
    assert all(p.text.lstrip().startswith("### XYZ-01") for p in pieces)
    assert all(p.tokens <= MAX_TOKENS for p in pieces)


def test_token_estimation_grows_with_length() -> None:
    assert estimate_tokens("one two three") < estimate_tokens("one two three four five")


def test_routing_clauses_are_not_dropped(corpus: Path) -> None:
    """Their prefix is two letters where the rest are three; requiring three
    silently dropped every routing clause from the index."""
    assert {c.clause_id for c in chunk_corpus(corpus) if c.clause_id.startswith("RT-")}


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------
async def test_a_clause_id_question_finds_its_clause(_database: None, corpus: Path) -> None:
    await index_corpus(corpus)
    hits = await retrieve("ELG-02", limit=TOP_K)
    assert hits
    assert hits[0].clause_id == "ELG-02"


async def test_retrieval_can_be_filtered_by_product(_database: None, corpus: Path) -> None:
    """docs/06 §7 — the snapshot's policy version scopes what an agent may read."""
    await index_corpus(corpus)
    hits = await retrieve("eligibility", product="PF-SHARIAH", limit=10)
    assert hits
    assert all(h.doc != "product_PF-STD" for h in hits)


async def test_a_question_in_plain_words_finds_the_clause(_database: None, corpus: Path) -> None:
    """Requiring every term would score zero for one extra noun."""
    await index_corpus(corpus)
    hits = await retrieve("can a guarantor be approached before the member?", limit=TOP_K)
    assert "COL-05" in {h.clause_id for h in hits}


async def test_an_unanswerable_question_returns_something_rather_than_failing(
    _database: None, corpus: Path
) -> None:
    await index_corpus(corpus)
    assert len(await retrieve("what is the weather in Lisbon?", limit=TOP_K)) <= TOP_K


async def test_every_hit_carries_its_provenance(_database: None, corpus: Path) -> None:
    await index_corpus(corpus)
    for hit in await retrieve("affordability", limit=3):
        evidence = hit.as_evidence()
        assert evidence["clause_id"]
        assert evidence["doc"]
        assert evidence["version"]
        assert set(evidence["components"]) == {"lexical", "vector", "rerank"}


def test_the_two_halves_are_weighted_equally() -> None:
    assert LEXICAL_WEIGHT == VECTOR_WEIGHT == 0.5


async def test_indexing_reports_whether_it_had_embeddings(_database: None, corpus: Path) -> None:
    """An index built without them is lexical-only and says so, rather than
    ranking against a zero vector as though it meant something."""
    report = await index_corpus(corpus)
    assert report["retrieval"] in {"hybrid", "lexical only"}
    assert (report["note"] is None) == report["embedded"]


# ---------------------------------------------------------------------------
# the acceptance
# ---------------------------------------------------------------------------
@pytest.mark.slow
async def test_clause_id_questions_meet_the_target(_database: None, corpus: Path) -> None:
    """T-041 acceptance: the right clause in the top three, at least 90%."""
    await index_corpus(corpus)
    report = await evaluate_retrieval(corpus)
    assert report.rate >= TARGET, report.as_dict()["misses"]


@pytest.mark.slow
async def test_officer_questions_are_measured_beside_it(_database: None, corpus: Path) -> None:
    """Not an acceptance, but worth knowing: this is what a copilot is asked."""
    await index_corpus(corpus)
    report = await evaluate_retrieval(corpus)
    assert report.prose_asked == len(PROSE_QUESTIONS)
    assert report.prose_rate >= 0.7, report.as_dict()["misses"]
