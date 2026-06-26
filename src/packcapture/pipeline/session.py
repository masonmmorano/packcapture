"""Pack model + session accumulator: segmented packs with status labels.

Real volume rippers do not show all 10 cards — they fan past commons or jump
straight to the hit and hold it for a second. So packs are NOT closed by
counting to 10; they are closed by an explicit boundary event (the visual
pack-boundary detector, or a manual/end-of-session close). Each closed segment
is then labeled by what it actually showed:

- ``COMPLETE``      exactly 10 tracked cards AND the slot checksum reconciles
                    (the disciplined top-to-top flip).
- ``SPEED_RIPPED``  anything else that logged a rare-or-higher — the ripper
                    jumped to the hit. NOT an error.
- ``NO_HIT``        cards were seen but no rare+ — inferred hitless fan.

A segment with zero recognized cards is not counted as a pack at all (the
"track at least 1 card" rule), so idle time between packs can't inflate the
counter.

The old 10-card checksum + variant-by-position logic is retained — it is what
*earns* the ``COMPLETE`` label. A pack is 4 commons, 3 uncommons, then 3 premium
slots: slot 8 a reverse holo, slot 9 the hit slot (a secret rare, or a 2nd
reverse holo), and slot 10 the guaranteed rare-or-better. ORB tells us *which*
card a slot holds and the bundle gives its base rarity, but position is the only
reliable signal for the reverse holos (a reverse holo can be any base rarity).
Position is only trustworthy when the whole pack was flipped in factory order, so
packs that close at any other count get ``variant="unknown"`` rather than a guess.

The slot template is configurable per set (a few sets / promo configs differ);
`standard_template()` is the common 10-card layout used by me2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Variant labels.
VARIANT_NORMAL = "normal"
VARIANT_REVERSE = "reverse"
VARIANT_UNKNOWN = "unknown"   # pack didn't close in factory order; position untrustworthy

# Pack status labels.
STATUS_COMPLETE = "complete"
STATUS_SPEED_RIPPED = "speed_ripped"
STATUS_NO_HIT = "no_hit"

# Coarse rarity classes used by the checksum. ORB gives the bundle's exact
# rarity string; we bucket it to compare against a slot's expectation.
RARITY_COMMON = "common"
RARITY_UNCOMMON = "uncommon"
RARITY_RARE_PLUS = "rare_plus"

# Supertypes that are filler, never one of the 10 tracked cards. The inserted
# basic energy that ships in every pack false-matches a set's own energy card
# (e.g. me2's Ignition Energy), so it must be dropped before the count — it is
# not a pull. (A genuinely pulled special energy is also dropped; that is an
# accepted trade for never miscounting the per-pack energy as an 11th card.)
EXCLUDED_SUPERTYPES = frozenset({"energy"})


def is_tracked_supertype(supertype: str) -> bool:
    """False for cards that must never be logged toward a pack (energy filler)."""
    return (supertype or "").strip().lower() not in EXCLUDED_SUPERTYPES


def rarity_class(rarity: str) -> str:
    """Bucket a pokemontcg.io rarity string into common / uncommon / rare_plus."""
    r = (rarity or "").strip().lower()
    if r == "common":
        return RARITY_COMMON
    if r == "uncommon":
        return RARITY_UNCOMMON
    # Everything else (Rare, Double Rare, Illustration Rare, Ultra Rare,
    # Special Illustration Rare, Mega Hyper Rare, ...) is "rare or higher".
    return RARITY_RARE_PLUS


@dataclass(frozen=True)
class Slot:
    index: int                       # 1-based slot number within the pack
    variant: str                     # VARIANT_NORMAL or VARIANT_REVERSE
    expect_rarity: Optional[str]     # rarity class the base card must be, or None to skip (reverse slots)


def standard_template() -> list[Slot]:
    """Phantasmal Flames (`me2`) 10-card layout, by slot:

      * **1-4** common (circle)
      * **5-7** uncommon (diamond)
      * **8**  guaranteed *standard reverse holo* (any base rarity)
      * **9**  the *hit* slot — Illustration / Special Illustration / Mega Hyper
               Rare; if no secret rare, it defaults to a 2nd reverse holo
      * **10** guaranteed *rare-or-better* (a holo Rare by default; upgrades to a
               Double Rare / ex or a textured Ultra Rare / Full Art)

    Slots 8-9 accept any base rarity (``expect_rarity=None``) because a reverse
    holo can be any rarity and slot 9 may carry a chase hit; slot 10 is the rarity
    anchor that *must* be rare+. At label time, a rare+ card in this block (the
    slot-9 hit, the slot-10 rare) is marked the hit (a holo); a non-rare+ card in
    slots 8-9 is a reverse holo.
    """
    slots = [Slot(i, VARIANT_NORMAL, RARITY_COMMON) for i in range(1, 5)]      # 1-4 commons
    slots += [Slot(i, VARIANT_NORMAL, RARITY_UNCOMMON) for i in range(5, 8)]   # 5-7 uncommons
    slots += [Slot(8, VARIANT_REVERSE, None)]                                  # 8  reverse holo
    slots += [Slot(9, VARIANT_REVERSE, None)]                                  # 9  hit, or 2nd reverse
    slots.append(Slot(10, VARIANT_REVERSE, RARITY_RARE_PLUS))                  # 10 guaranteed rare+
    return slots


@dataclass
class LoggedCard:
    slot: int            # 1-based position within the segment (factory slot only if COMPLETE)
    card_id: str
    name: str
    number: str
    base_rarity: str
    variant: str         # provisional from template while the pack is open; final on close
    is_holo: bool        # position-inferred for now; foil detection (slots 8-10) will confirm later
    inliers: int


@dataclass
class Pack:
    index: int
    cards: list[LoggedCard]
    status: str                  # STATUS_COMPLETE / STATUS_SPEED_RIPPED / STATUS_NO_HIT
    reconciled: bool             # True only for COMPLETE
    issues: list[str] = field(default_factory=list)

    @property
    def has_hit(self) -> bool:
        return any(rarity_class(c.base_rarity) == RARITY_RARE_PLUS for c in self.cards)


class Session:
    """Accumulates recognized cards into boundary-segmented packs.

    Feed only accepted, set-matching recognitions via :meth:`add`; excluded
    cards (energy, code card, low-confidence) must not be passed. Packs are
    closed by :meth:`close_pack` — called by the visual pack-boundary detector
    in rip mode, or manually / at session end — never by card count.
    """

    def __init__(self, set_code: str, template: Optional[list[Slot]] = None):
        self.set_code = set_code
        self.template = template or standard_template()
        self.pack_size = len(self.template)
        self.packs: list[Pack] = []
        self._current: list[LoggedCard] = []

    @property
    def pending(self) -> int:
        """Cards logged toward the pack currently being filled."""
        return len(self._current)

    def remove_card(self, index: int) -> bool:
        """Remove a logged card by flattened index (closed packs in order, then
        the open segment) — for fixing a mis-scan. Pack labels are left as-is."""
        i = index
        for pack in self.packs:
            if i < len(pack.cards):
                del pack.cards[i]
                if not pack.cards:               # emptied -> drop it, renumber the rest
                    self.packs.remove(pack)
                    self._renumber()
                else:
                    self._relabel(pack)          # status may change (e.g. no longer 10)
                return True
            i -= len(pack.cards)
        if 0 <= i < len(self._current):
            del self._current[i]
            return True
        return False

    def clear(self) -> None:
        """Drop every logged card and pack (start the session over)."""
        self.packs = []
        self._current = []

    def add(
        self,
        *,
        card_id: str,
        name: str,
        number: str,
        base_rarity: str,
        inliers: int = 0,
    ) -> LoggedCard:
        """Log one recognized card into the open segment.

        The slot/variant are provisional (from the template, assuming factory
        order so the live view can show them); they are finalized when the
        pack closes — kept only if the segment turns out to be a full
        factory-order flip, downgraded to ``unknown`` otherwise.
        """
        pos = len(self._current)  # 0-based position within the segment
        if pos < self.pack_size:
            slot = self.template[pos]
            variant, is_holo = slot.variant, slot.variant == VARIANT_REVERSE
        else:
            variant, is_holo = VARIANT_UNKNOWN, False
        card = LoggedCard(
            slot=pos + 1,
            card_id=card_id,
            name=name,
            number=number,
            base_rarity=base_rarity,
            variant=variant,
            is_holo=is_holo,
            inliers=inliers,
        )
        self._current.append(card)
        return card

    def close_pack(self) -> Optional[Pack]:
        """Close the open segment at a pack boundary and label it.

        Returns the closed Pack, or None when the segment logged no cards
        (an empty segment is not a pack — idle time can't tick the counter).
        """
        if not self._current:
            return None
        cards = self._current
        self._current = []
        status, reconciled, issues = self._label(cards)
        pack = Pack(
            index=len(self.packs) + 1,
            cards=cards,
            status=status,
            reconciled=reconciled,
            issues=issues,
        )
        self.packs.append(pack)
        return pack

    def _label(self, cards: list[LoggedCard]) -> tuple[str, bool, list[str]]:
        """Assign provisional slot/variant by position and derive a pack's
        status, reconciliation and issues from its cards.

        Used both when a pack closes and when an edit (delete / move) changes a
        pack's contents, so an edited pack re-checksums against the template.
        """
        for i, c in enumerate(cards):
            c.slot = i + 1
            slot = self.template[i] if i < self.pack_size else None
            if slot is None:
                c.variant, c.is_holo = VARIANT_UNKNOWN, False
            elif slot.variant == VARIANT_REVERSE:
                # Premium block (last 3): the rare+ card is the hit (a holo, not a
                # reverse); the others are reverse holos.
                if rarity_class(c.base_rarity) == RARITY_RARE_PLUS:
                    c.variant, c.is_holo = VARIANT_NORMAL, True
                else:
                    c.variant, c.is_holo = VARIANT_REVERSE, True
            else:
                c.variant, c.is_holo = VARIANT_NORMAL, False

        issues: list[str] = []
        if len(cards) == self.pack_size:
            # Slot 10 expects rare+, so _reconcile already enforces the guaranteed
            # rare; slots 8-9 (reverse / hit) accept any base rarity.
            issues = self._reconcile(cards)
        elif len(cards) > self.pack_size:
            # More cards than a pack holds = a missed boundary; surface it.
            issues.append(
                f"pack has {len(cards)} card(s), expected {self.pack_size} — boundary likely missed"
            )

        reconciled = len(cards) == self.pack_size and not issues
        if not reconciled:
            # Factory order can't be trusted; don't guess variants.
            for c in cards:
                c.variant, c.is_holo = VARIANT_UNKNOWN, False

        if reconciled:
            status = STATUS_COMPLETE
        elif any(rarity_class(c.base_rarity) == RARITY_RARE_PLUS for c in cards):
            status = STATUS_SPEED_RIPPED
        else:
            status = STATUS_NO_HIT
        return status, reconciled, issues

    def move_card(self, index: int, dest_pack: Optional[int]) -> bool:
        """Move a logged card to a different pack — for fixing a missed boundary.

        ``index`` is the flattened position (closed packs in order, then the open
        segment, matching :meth:`remove_card`); ``dest_pack`` is a 1-based pack
        index, or ``None`` for the open segment. The card is appended to the
        destination, the source and destination packs are re-labelled, and a
        source pack left empty is dropped and the rest renumbered. Returns False
        on a bad index or a no-op (already in that container).
        """
        i = index
        src_list: Optional[list[LoggedCard]] = None
        src_pack: Optional[Pack] = None
        for pack in self.packs:
            if i < len(pack.cards):
                src_list, src_pack = pack.cards, pack
                break
            i -= len(pack.cards)
        else:
            if 0 <= i < len(self._current):
                src_list = self._current
            else:
                return False

        if dest_pack is None:
            dest_list, dest_pack_obj = self._current, None
        elif 1 <= dest_pack <= len(self.packs):
            dest_pack_obj = self.packs[dest_pack - 1]
            dest_list = dest_pack_obj.cards
        else:
            return False

        if dest_list is src_list:
            return False  # already in that container — nothing to move

        card = src_list.pop(i)
        dest_list.append(card)

        if src_pack is not None and not src_pack.cards:
            self.packs.remove(src_pack)
            self._renumber()
            src_pack = None
        if src_pack is not None:
            self._relabel(src_pack)
        if dest_pack_obj is not None:
            self._relabel(dest_pack_obj)
        return True

    def _arrange_for_template(self, cards: list[LoggedCard]) -> Optional[list[LoggedCard]]:
        """Reorder an edited full pack so each card lands in a slot whose rarity it
        satisfies (constrained slots filled first, leftovers into the any/reverse
        slots). This lets an operator drag the right 10 cards into a pack in *any*
        order and still have it reconcile to COMPLETE. Returns the new order, or
        None if the composition can't satisfy the template (then it won't COMPLETE).
        """
        if len(cards) != self.pack_size:
            return None
        pools: dict[str, list[LoggedCard]] = {}
        for c in cards:
            pools.setdefault(rarity_class(c.base_rarity), []).append(c)
        result: list[Optional[LoggedCard]] = [None] * self.pack_size
        any_slots: list[int] = []
        for i, slot in enumerate(self.template):
            if slot.expect_rarity is None:           # reverse-holo slot: any rarity
                any_slots.append(i)
                continue
            pool = pools.get(slot.expect_rarity)
            if not pool:                             # not enough of this rarity
                return None
            result[i] = pool.pop(0)
        leftovers = [c for pool in pools.values() for c in pool]
        if len(leftovers) != len(any_slots):
            return None
        for i, c in zip(any_slots, leftovers):
            result[i] = c
        return result  # every slot filled

    def _relabel(self, pack: Pack) -> None:
        """Re-evaluate an edited pack. Unlike close_pack (which trusts factory
        order), a manually edited pack is rearranged to fit the template if its
        composition allows, so moving the right cards in completes it."""
        arranged = self._arrange_for_template(pack.cards)
        if arranged is not None:
            pack.cards[:] = arranged
        pack.status, pack.reconciled, pack.issues = self._label(pack.cards)

    def _renumber(self) -> None:
        for n, pack in enumerate(self.packs, 1):
            pack.index = n

    def finalize(self) -> Optional[Pack]:
        """End the session: close any open segment. Returns it if one was open."""
        return self.close_pack()

    def stats(self) -> dict:
        """Light session summary (full pull-rate analytics come later)."""
        logged = [c for p in self.packs for c in p.cards]
        by_status: dict[str, int] = {
            STATUS_COMPLETE: 0, STATUS_SPEED_RIPPED: 0, STATUS_NO_HIT: 0,
        }
        by_variant: dict[str, int] = {}
        by_rarity: dict[str, int] = {}
        for p in self.packs:
            by_status[p.status] = by_status.get(p.status, 0) + 1
        for c in logged:
            by_variant[c.variant] = by_variant.get(c.variant, 0) + 1
            by_rarity[c.base_rarity or "Unknown"] = by_rarity.get(c.base_rarity or "Unknown", 0) + 1
        return {
            "packs": len(self.packs),
            "by_status": by_status,
            "packs_flagged": sum(1 for p in self.packs if p.issues),
            "cards_logged": len(logged),
            "pending": self.pending,
            "by_variant": by_variant,
            "by_rarity": by_rarity,
        }

    def _reconcile(self, cards: list[LoggedCard]) -> list[str]:
        issues: list[str] = []
        for card in cards:
            slot = self.template[card.slot - 1]
            if slot.expect_rarity is None:
                continue
            actual = rarity_class(card.base_rarity)
            if actual != slot.expect_rarity:
                issues.append(
                    f"slot {slot.index}: expected {slot.expect_rarity} "
                    f"but {card.name or card.card_id} is {actual} ({card.base_rarity})"
                )
        return issues
