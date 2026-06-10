"""Session layer: boundary-segmented packs, status labels, and the checksum."""
from __future__ import annotations

from packcapture.pipeline.session import (
    RARITY_COMMON,
    RARITY_RARE_PLUS,
    RARITY_UNCOMMON,
    STATUS_COMPLETE,
    STATUS_NO_HIT,
    STATUS_SPEED_RIPPED,
    VARIANT_NORMAL,
    VARIANT_REVERSE,
    VARIANT_UNKNOWN,
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
    for cid, rarity in cards:
        session.add(card_id=cid, name=cid, number="1", base_rarity=rarity)


def test_rarity_class_buckets():
    assert rarity_class("Common") == RARITY_COMMON
    assert rarity_class("Uncommon") == RARITY_UNCOMMON
    for r in ["Rare", "Double Rare", "Illustration Rare", "Special Illustration Rare"]:
        assert rarity_class(r) == RARITY_RARE_PLUS


def test_template_shape():
    t = standard_template()
    assert len(t) == 10
    assert [s.variant for s in t] == [VARIANT_NORMAL] * 7 + [VARIANT_REVERSE] * 2 + [VARIANT_NORMAL]


def test_no_auto_close_boundary_closes():
    # Counting to 10 must NOT close the pack; only the boundary does.
    s = Session("me2")
    _add_all(s, CLEAN_PACK)
    assert s.packs == [] and s.pending == 10
    pack = s.close_pack()
    assert pack is not None and s.pending == 0 and len(s.packs) == 1


def test_full_factory_pack_is_complete_with_variants():
    s = Session("me2")
    _add_all(s, CLEAN_PACK)
    pack = s.close_pack()
    assert pack.status == STATUS_COMPLETE
    assert pack.reconciled and pack.issues == []
    variants = [c.variant for c in pack.cards]
    assert variants == [VARIANT_NORMAL] * 7 + [VARIANT_REVERSE] * 2 + [VARIANT_NORMAL]
    # The reverse slots are flagged holo regardless of base rarity.
    assert pack.cards[7].is_holo and pack.cards[8].is_holo
    assert not pack.cards[0].is_holo


def test_speed_rip_labels_not_errors():
    # Jump to the hit: 2 commons fan past, the rare is held. <10 cards is NOT an error.
    s = Session("me2")
    _add_all(s, [("c1", "Common"), ("c2", "Common"), ("hit", "Illustration Rare")])
    pack = s.close_pack()
    assert pack.status == STATUS_SPEED_RIPPED
    assert pack.issues == []          # not flagged — this is normal volume ripping
    assert not pack.reconciled
    assert pack.has_hit


def test_hitless_fan_is_no_hit():
    s = Session("me2")
    _add_all(s, [("c1", "Common"), ("u1", "Uncommon")])
    pack = s.close_pack()
    assert pack.status == STATUS_NO_HIT
    assert pack.issues == [] and not pack.has_hit


def test_empty_segment_is_not_a_pack():
    # Idle time between packs can't tick the counter ("track ≥1 card" rule).
    s = Session("me2")
    assert s.close_pack() is None
    assert s.packs == []


def test_partial_close_downgrades_variants_to_unknown():
    # Factory order can't be trusted on a partial segment: no variant guessing.
    s = Session("me2")
    _add_all(s, CLEAN_PACK[:9])  # 9 cards — slots 8-9 would have been "reverse"
    pack = s.close_pack()
    assert all(c.variant == VARIANT_UNKNOWN and not c.is_holo for c in pack.cards)


def test_wrong_rarity_in_constrained_slot_flagged_not_complete():
    # A Rare lands where a Common is expected (slot 1) -> checksum catches it,
    # so the pack can't earn COMPLETE even at exactly 10 cards.
    bad = [("oops", "Rare")] + CLEAN_PACK[1:]
    s = Session("me2")
    _add_all(s, bad)
    pack = s.close_pack()
    assert pack.status != STATUS_COMPLETE
    assert not pack.reconciled
    assert any("slot 1" in i for i in pack.issues)


def test_overfull_segment_flags_missed_boundary():
    s = Session("me2")
    _add_all(s, CLEAN_PACK + [("extra", "Common")])  # 11 cards in one segment
    pack = s.close_pack()
    assert any("boundary" in i for i in pack.issues)
    assert not pack.reconciled


def test_finalize_closes_open_segment():
    s = Session("me2")
    _add_all(s, CLEAN_PACK[:3])
    pack = s.finalize()
    assert pack is not None and pack.status == STATUS_NO_HIT
    assert s.finalize() is None  # nothing left open


def test_stats_summary():
    s = Session("me2")
    _add_all(s, CLEAN_PACK)
    s.close_pack()                                            # COMPLETE
    _add_all(s, [("hit", "Ultra Rare")])
    s.close_pack()                                            # SPEED_RIPPED
    _add_all(s, [("c1", "Common")])
    s.close_pack()                                            # NO_HIT
    st = s.stats()
    assert st["packs"] == 3
    assert st["by_status"] == {
        STATUS_COMPLETE: 1, STATUS_SPEED_RIPPED: 1, STATUS_NO_HIT: 1,
    }
    assert st["packs_flagged"] == 0
    assert st["cards_logged"] == 12
    assert st["by_variant"][VARIANT_REVERSE] == 2     # from the complete pack
    assert st["by_variant"][VARIANT_UNKNOWN] == 2     # from the two partials
