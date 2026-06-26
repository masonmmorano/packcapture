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
    # 4 commons, 3 uncommons, then a 3-card premium block (reverse / hit / rare).
    t = standard_template()
    assert len(t) == 10
    assert [s.variant for s in t] == [VARIANT_NORMAL] * 7 + [VARIANT_REVERSE] * 3
    assert [s.expect_rarity for s in t[7:]] == [None, None, None]   # premium block: any rarity


def test_full_pack_without_a_rare_is_not_complete():
    # Every pack guarantees a rare+ in the premium block; 10 cards with none
    # can't have flipped cleanly -> not COMPLETE.
    s = Session("me2")
    _add_all(s, [("c%d" % i, "Common") for i in range(4)]
                + [("u%d" % i, "Uncommon") for i in range(3)]
                + [("revC", "Common"), ("revU", "Uncommon"), ("u4", "Uncommon")])
    pack = s.close_pack()
    assert pack.status != STATUS_COMPLETE and not pack.reconciled
    assert any("rare" in i.lower() for i in pack.issues)


def test_rare_can_sit_anywhere_in_the_premium_block():
    # The guaranteed rare need not be the very last card; a valid composition
    # completes via an edit and labels the rare as the hit, the rest reverse.
    s = Session("me2")
    _add_all(s, [("c1", "Common"), ("c2", "Common"), ("c3", "Common"), ("c4", "Common"),
                 ("u1", "Uncommon"), ("u2", "Uncommon"), ("u3", "Uncommon"),
                 ("hit", "Ultra Rare"), ("revC", "Common")])   # 9 cards, rare mid-pack
    s.close_pack()
    s.add(card_id="revU", name="revU", number="1", base_rarity="Uncommon")  # 10th
    assert s.move_card(9, 1) is True
    p = s.packs[0]
    assert len(p.cards) == 10 and p.status == STATUS_COMPLETE
    hit = next(c for c in p.cards if c.base_rarity == "Ultra Rare")
    assert hit.variant == VARIANT_NORMAL and hit.is_holo        # the hit is a holo, not a reverse
    assert sum(1 for c in p.cards if c.variant == VARIANT_REVERSE) == 2


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


def test_remove_card_by_flattened_index_and_clear():
    s = Session("me2")
    s.add(card_id="a", name="A", number="1", base_rarity="Common")
    s.add(card_id="b", name="B", number="2", base_rarity="Common")
    s.close_pack()                                             # pack 1: [a, b]
    s.add(card_id="c", name="C", number="3", base_rarity="Common")  # open segment: [c]

    assert s.remove_card(1) is True                            # remove b (in pack 1)
    assert [c.card_id for c in s.packs[0].cards] == ["a"]
    assert s.remove_card(1) is True                            # index 1 now = c (open)
    assert s.pending == 0
    assert s.remove_card(9) is False                           # out of range

    s.clear()
    assert s.packs == [] and s.pending == 0


def test_move_card_between_packs_relabels_both():
    # A boundary fired one card early: pack 1 got 11 cards (its slot-10 rare plus
    # pack 2's rare), pack 2 got 9 (missing its rare). Moving the stray rare to
    # pack 2 should make both reconcile as COMPLETE.
    s = Session("me2")
    _add_all(s, CLEAN_PACK)                                     # 10, reconcilable
    s.add(card_id="rare2", name="rare2", number="1", base_rarity="Double Rare")  # 11th
    s.close_pack()                                              # pack 1: 11 cards
    _add_all(s, CLEAN_PACK[:-1])                                # pack 2: 9 (no rare)
    s.close_pack()
    assert len(s.packs[0].cards) == 11 and len(s.packs[1].cards) == 9
    assert s.packs[0].status != STATUS_COMPLETE                # over-full, flagged

    # Flattened index of the stray (last card of pack 1) = 10.
    assert s.move_card(10, 2) is True
    assert len(s.packs[0].cards) == 10 and len(s.packs[1].cards) == 10
    assert s.packs[0].status == STATUS_COMPLETE                # both reconcile now
    assert s.packs[1].status == STATUS_COMPLETE


def test_move_card_to_open_segment():
    s = Session("me2")
    s.add(card_id="a", name="A", number="1", base_rarity="Common")
    s.add(card_id="b", name="B", number="2", base_rarity="Common")
    s.close_pack()                                             # pack 1: [a, b]
    assert s.move_card(0, None) is True                       # move a -> open segment
    assert [c.card_id for c in s.packs[0].cards] == ["b"]
    assert [c.card_id for c in s._current] == ["a"]


def test_move_card_empties_source_pack_and_renumbers():
    s = Session("me2")
    s.add(card_id="solo", name="Solo", number="1", base_rarity="Common")
    s.close_pack()                                             # pack 1: [solo]
    s.add(card_id="x", name="X", number="2", base_rarity="Common")
    s.close_pack()                                             # pack 2: [x]
    assert len(s.packs) == 2
    assert s.move_card(0, 2) is True                          # empties pack 1
    assert len(s.packs) == 1                                  # dropped
    assert s.packs[0].index == 1                              # renumbered
    assert [c.card_id for c in s.packs[0].cards] == ["x", "solo"]


def test_move_completes_pack_from_valid_composition_any_order():
    # Operator drags the right 10 cards into a pack but jumbled; it should still
    # reconcile to COMPLETE once the count is right (cards rearranged to slots).
    s = Session("me2")
    _add_all(s, [("c1", "Common"), ("rare1", "Double Rare"), ("u1", "Uncommon"),
                 ("revC", "Common"), ("c2", "Common"), ("u2", "Uncommon"),
                 ("revU", "Uncommon"), ("u3", "Uncommon"), ("c3", "Common")])
    s.close_pack()                                            # pack 1: 9 cards
    s.add(card_id="c4", name="c4", number="1", base_rarity="Common")  # open: the 10th
    assert s.packs[0].status != STATUS_COMPLETE

    assert s.move_card(9, 1) is True                          # drag the 10th into pack 1
    assert len(s.packs[0].cards) == 10
    assert s.packs[0].status == STATUS_COMPLETE               # composition reconciles
    assert s.packs[0].reconciled


def test_move_keeps_pack_incomplete_when_composition_cannot_fit():
    # 10 uncommons can't fill the four common slots -> never COMPLETE.
    s = Session("me2")
    _add_all(s, [("u%d" % i, "Uncommon") for i in range(9)])
    s.close_pack()
    s.add(card_id="u9", name="u9", number="1", base_rarity="Uncommon")
    assert s.move_card(9, 1) is True
    assert len(s.packs[0].cards) == 10
    assert s.packs[0].status != STATUS_COMPLETE


def test_remove_from_complete_pack_relabels():
    s = Session("me2")
    _add_all(s, CLEAN_PACK)
    s.close_pack()
    assert s.packs[0].status == STATUS_COMPLETE
    assert s.remove_card(0) is True                           # now 9 cards
    assert len(s.packs[0].cards) == 9
    assert s.packs[0].status != STATUS_COMPLETE               # no longer reconciles


def test_move_card_bad_index_or_noop():
    s = Session("me2")
    s.add(card_id="a", name="A", number="1", base_rarity="Common")
    s.close_pack()
    assert s.move_card(9, 1) is False                         # bad index
    assert s.move_card(0, 1) is False                         # already in pack 1 (no-op)
    assert s.move_card(0, 5) is False                         # bad destination
