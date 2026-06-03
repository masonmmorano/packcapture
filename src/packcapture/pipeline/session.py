"""Pack model + session accumulator: variant-by-position and the per-pack checksum.

A standard modern booster is exactly 10 tracked cards in a fixed factory order
(plus an inserted basic energy and a code card, both worthless and excluded).
ORB tells us *which* card a slot holds and the bundle gives its base rarity, but
position is the only reliable signal for the two reverse-holo slots (a reverse
holo can be any base rarity). So we assign the variant from the card's slot
within the pack and let its own base rarity confirm the rest.

After a pack fills (10 tracked cards) we reconcile it against the slot template:
the right rarity should land in each rarity-constrained slot. A pack that does
not add up is flagged rather than silently logged, so a missed or misread card
surfaces instead of corrupting the count — that is what lets a ripper not
babysit it.

The slot template is configurable per set (a few sets / promo configs differ);
`standard_template()` is the common 10-card layout used by me2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Variant labels.
VARIANT_NORMAL = "normal"
VARIANT_REVERSE = "reverse"

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
    slot: int
    card_id: str
    name: str
    number: str
    base_rarity: str
    variant: str
    is_holo: bool        # position-inferred for now; foil detection (slots 8-10) will confirm later
    inliers: int


@dataclass
class Pack:
    index: int
    cards: list[LoggedCard]
    reconciled: bool
    issues: list[str] = field(default_factory=list)


class Session:
    """Accumulates recognized cards into packs and reconciles each against the template.

    Feed only accepted, set-matching recognitions via :meth:`add`; excluded
    cards (energy, code card, low-confidence) must not be passed, so the
    count-to-10 stays honest.
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
    ) -> tuple[LoggedCard, Optional[Pack]]:
        """Log one recognized card into the open pack.

        Returns the logged card and, when this card fills the pack, the closed
        (and reconciled) Pack; otherwise the second element is None.
        """
        slot = self.template[len(self._current)]
        card = LoggedCard(
            slot=slot.index,
            card_id=card_id,
            name=name,
            number=number,
            base_rarity=base_rarity,
            variant=slot.variant,
            is_holo=(slot.variant == VARIANT_REVERSE),
            inliers=inliers,
        )
        self._current.append(card)
        pack = self._close() if len(self._current) >= self.pack_size else None
        return card, pack

    def close_pack(self) -> Optional[Pack]:
        """Force-close the open pack (e.g. a partial pack at session end or a manual boundary).

        A partial pack reconciles as a mismatch (wrong card count) so it is flagged.
        """
        if not self._current:
            return None
        return self._close()

    def finalize(self) -> Optional[Pack]:
        """End the session: close any partial pack. Returns it if one was open."""
        return self.close_pack()

    def stats(self) -> dict:
        """Light session summary (full pull-rate analytics come later)."""
        logged = [c for p in self.packs for c in p.cards]
        by_variant: dict[str, int] = {}
        by_rarity: dict[str, int] = {}
        for c in logged:
            by_variant[c.variant] = by_variant.get(c.variant, 0) + 1
            by_rarity[c.base_rarity or "Unknown"] = by_rarity.get(c.base_rarity or "Unknown", 0) + 1
        return {
            "packs": len(self.packs),
            "packs_reconciled": sum(1 for p in self.packs if p.reconciled),
            "packs_flagged": sum(1 for p in self.packs if not p.reconciled),
            "cards_logged": len(logged),
            "pending": self.pending,
            "by_variant": by_variant,
            "by_rarity": by_rarity,
        }

    def _close(self) -> Pack:
        cards = self._current
        self._current = []
        issues = self._reconcile(cards)
        pack = Pack(index=len(self.packs) + 1, cards=cards, reconciled=not issues, issues=issues)
        self.packs.append(pack)
        return pack

    def _reconcile(self, cards: list[LoggedCard]) -> list[str]:
        issues: list[str] = []
        if len(cards) != self.pack_size:
            issues.append(f"pack has {len(cards)} card(s), expected {self.pack_size}")
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
