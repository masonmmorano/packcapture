"""Session layer: variant-by-position and the per-pack checksum."""
from __future__ import annotations

from packcapture.pipeline.session import (
    RARITY_COMMON,
    RARITY_RARE_PLUS,
    RARITY_UNCOMMON,
    VARIANT_NORMAL,
    VARIANT_REVERSE,
    Session,
    rarity_class,
    standard_template,
)

# A clean me2-style pack in factory order: 4 commons, 3 uncommons,
# 2 reverses (any base rarity — here commons), 1 rare-or-higher.
CLEAN_PACK = (
    [("c%d" % i, "Common") for i in range(1, 5)]
    + [("u%d" % i, "Uncommon") for i in range(1, 4)]
    + [("rev1", "Common"), ("rev2", "Uncommon")]
    + [("rare1", "Double Rare")]
)


def _add_all(session, cards):
    last_pack = None
    for cid, rarity in cards:
        _, pack = session.add(card_id=cid, name=cid, number="1", base_rarity=rarity)
        if pack is not None:
            last_pack = pack
    return last_pack


def test_rarity_class_buckets():
    assert rarity_class("Common") == RARITY_COMMON
    assert rarity_class("Uncommon") == RARITY_UNCOMMON
    for r in ["Rare", "Double Rare", "Illustration Rare", "Special Illustration Rare"]:
        assert rarity_class(r) == RARITY_RARE_PLUS


def test_template_shape():
    t = standard_template()
    assert len(t) == 10
    assert [s.variant for s in t] == [VARIANT_NORMAL] * 7 + [VARIANT_REVERSE] * 2 + [VARIANT_NORMAL]


def test_variant_assigned_by_position():
    s = Session("me2")
    pack = _add_all(s, CLEAN_PACK)
    assert pack is not None
    variants = [c.variant for c in pack.cards]
    assert variants == [VARIANT_NORMAL] * 7 + [VARIANT_REVERSE] * 2 + [VARIANT_NORMAL]
    # The reverse slots are flagged holo regardless of base rarity.
    assert pack.cards[7].is_holo and pack.cards[8].is_holo
    assert not pack.cards[0].is_holo


def test_clean_pack_reconciles():
    s = Session("me2")
    pack = _add_all(s, CLEAN_PACK)
    assert pack.reconciled and pack.issues == []


def test_auto_close_at_ten():
    s = Session("me2")
    last = None
    for i, (cid, rarity) in enumerate(CLEAN_PACK):
        _, pack = s.add(card_id=cid, name=cid, number="1", base_rarity=rarity)
        if i < 9:
            assert pack is None, f"closed early at card {i}"
        else:
            last = pack
    assert last is not None and s.pending == 0


def test_wrong_rarity_in_constrained_slot_flagged():
    # A Rare lands where a Common is expected (slot 1) -> checksum catches it.
    bad = [("oops", "Rare")] + CLEAN_PACK[1:]
    s = Session("me2")
    pack = _add_all(s, bad)
    assert not pack.reconciled
    assert any("slot 1" in i for i in pack.issues)


def test_missed_card_shifts_and_flags():
    # Drop one common: the rare now lands in slot 9 and a reverse falls into
    # slot 10's rare+ requirement -> the pack reconciles as flagged.
    shifted = CLEAN_PACK[1:]  # 9 cards
    s = Session("me2")
    pack = None
    for cid, rarity in shifted:
        _, p = s.add(card_id=cid, name=cid, number="1", base_rarity=rarity)
        pack = p or pack
    # Only 9 added: pack hasn't auto-closed yet.
    assert pack is None and s.pending == 9
    pack = s.close_pack()
    assert not pack.reconciled
    assert any("expected" in i for i in pack.issues)


def test_partial_pack_at_finalize_is_flagged():
    s = Session("me2")
    for cid, rarity in CLEAN_PACK[:3]:
        s.add(card_id=cid, name=cid, number="1", base_rarity=rarity)
    pack = s.finalize()
    assert pack is not None and not pack.reconciled
    assert any("expected 10" in i for i in pack.issues)


def test_stats_summary():
    s = Session("me2")
    _add_all(s, CLEAN_PACK)
    st = s.stats()
    assert st["packs"] == 1
    assert st["packs_reconciled"] == 1
    assert st["cards_logged"] == 10
    assert st["by_variant"][VARIANT_REVERSE] == 2
