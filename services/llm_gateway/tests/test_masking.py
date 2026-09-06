"""T-040 — personal data never leaves the platform (docs/06 §6, docs/13 §2)."""

from __future__ import annotations

from app.pii import KINDS, MaskMap, leaked, mask_payload, mask_text, unmask_text

SENTENCE = (
    "Member M-000042 (Amina Yusuf, id AB1234567) can be reached at "
    "amina@example.com or +1 555 010 2020 about account A-001234."
)


def test_structured_identifiers_are_found_by_shape() -> None:
    masked, mapping = mask_text(SENTENCE)
    for value in ("M-000042", "AB1234567", "amina@example.com", "A-001234"):
        assert value not in masked
    assert mapping.masked >= 5


def test_a_name_is_masked_when_the_caller_supplies_it() -> None:
    """A name has no shape, so the gateway masks what it is told.

    Guessing would either leak a name or corrupt the prompt.
    """
    masked, _ = mask_text(SENTENCE, known={"name": ["Amina Yusuf"]})
    assert "Amina Yusuf" not in masked
    assert "«NAME_1»" in masked


def test_a_name_the_caller_did_not_supply_is_not_guessed_at() -> None:
    masked, _ = mask_text("Ask Amina Yusuf about it.")
    assert "Amina Yusuf" in masked


def test_the_longest_value_is_masked_first() -> None:
    """Masking a surname first would leave the full name half-visible."""
    masked, _ = mask_text("Amina Yusuf and Yusuf", known={"name": ["Yusuf", "Amina Yusuf"]})
    assert "Amina" not in masked


def test_the_same_value_always_gets_the_same_placeholder() -> None:
    masked, mapping = mask_text("M-000042 guaranteed M-000043, and M-000042 applied.")
    assert masked.count("«MEMBER_1»") == 2
    assert mapping.counts["MEMBER"] == 2


def test_placeholders_do_not_carry_across_requests() -> None:
    """A placeholder that meant the same person every time would identify them."""
    first, _ = mask_text("M-000099 applied.")
    second, _ = mask_text("M-000042 applied.")
    assert first == second == "«MEMBER_1» applied."


def test_unmasking_restores_the_original() -> None:
    masked, mapping = mask_text(SENTENCE, known={"name": ["Amina Yusuf"]})
    assert unmask_text(masked, mapping) == SENTENCE


def test_nothing_masked_is_left_in_the_masked_text() -> None:
    masked, mapping = mask_text(SENTENCE, known={"name": ["Amina Yusuf"]})
    assert leaked(masked, mapping) == []


def test_a_phone_keeps_its_plus_inside_the_placeholder() -> None:
    """A stray leading + says what was removed."""
    masked, _ = mask_text("call +1 555 010 2020 now")
    assert "+" not in masked
    assert "«PHONE_1»" in masked


def test_a_long_digit_run_is_treated_as_an_account_number() -> None:
    masked, _ = mask_text("card 4111 1111 1111 1111")
    assert "4111" not in masked


def test_a_short_number_is_left_alone() -> None:
    """Amounts and dates are not identifiers, and masking them would make the
    prompt useless."""
    masked, _ = mask_text("instalment 1130.00 due on day 5 of 24 months")
    assert masked == "instalment 1130.00 due on day 5 of 24 months"


def test_a_whole_structure_shares_one_placeholder_map() -> None:
    payload = [{"role": "user", "content": "M-000042"}, {"role": "assistant", "content": "M-000042 again"}]
    masked, mapping = mask_payload(payload)
    assert masked[0]["content"] == masked[1]["content"].split(" ")[0]
    assert mapping.masked == 1


def test_the_summary_says_what_was_masked_not_what_it_was() -> None:
    _, mapping = mask_text(SENTENCE, known={"name": ["Amina Yusuf"]})
    summary = mapping.as_dict()
    assert set(summary) == {"masked_fields", "kinds"}
    assert "Amina Yusuf" not in str(summary)


def test_every_declared_kind_has_a_pattern() -> None:
    assert all(pattern.pattern for _, pattern in KINDS)
    assert len({kind for kind, _ in KINDS}) == len(KINDS)


def test_an_empty_map_leaves_text_untouched() -> None:
    assert unmask_text("nothing here", MaskMap()) == "nothing here"
