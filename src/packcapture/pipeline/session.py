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
*earns* the ``COMPLETE`` label. ORB tells us *which* card a slot holds and the
bundle gives its base rarity, but position is the only reliable signal for the
two reverse-holo slots (a reverse holo can be any base rarity). Position is
only trustworthy when the whole pack was flipped in factory order, so packs
that close at any other count get ``variant="unknown"`` rather than a guess.

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
    """The common modern 10-card layout: 4 commons, 3 uncommons, 2 reverses, 1 rare+."""
    slots = [Slot(i, VARIANT_NORMAL, RARITY_COMMON) for i in range(1, 5)]      # 1-4
    slots += [Slot(i, VARIANT_NORMAL, RARITY_UNCOMMON) for i in range(5, 8)]   # 5-7
    slots += [Slot(i, VARIANT_REVERSE, None) for i in range(8, 10)]            # 8-9 (any rarity)
    slots.append(Slot(10, VARIANT_NORMAL, RARITY_RARE_PLUS))                   # 10
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

        issues: list[str] = []
        if len(cards) == self.pack_size:
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
                c.variant = VARIANT_UNKNOWN
                c.is_holo = False

        if reconciled:
            status = STATUS_COMPLETE
        elif any(rarity_class(c.base_rarity) == RARITY_RARE_PLUS for c in cards):
            status = STATUS_SPEED_RIPPED
        else:
            status = STATUS_NO_HIT

        pack = Pack(
            index=len(self.packs) + 1,
            cards=cards,
            status=status,
            reconciled=reconciled,
            issues=issues,
        )
        self.packs.append(pack)
        return pack

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
