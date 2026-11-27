"""Home / recent / detail data for the ui-v5 browse shell (no GTK, no GStreamer).

The pages added after the library grid -- the home page (hero + "continue
watching" + "recently added"), the recent page (watch history split into
"continue watching" and "finished") and the detail page (backdrop + episodes +
clip facts) -- are mostly arithmetic over data the project already has:
:class:`video_player.library.LibraryItem` from the folder scan and
:class:`video_player.recents.RecentItem` from the play history.  Keeping that
arithmetic here means the widgets stay thin and the rules (which clip the hero
shows, what counts as an episode, how a bitrate is derived) are unit-testable
on a host without PyGObject.

Two honesty rules drive the mapping, because the mockups drew a streaming app
and this is a local-file player:

* every piece of text on both pages comes from a real file or a real probe --
  no invented synopsis, cast list or rating;
* a folder is the only "series" this player has, so "episodes" are the files
  that sit next to the opened clip (``episode_items``).  A file opened through
  the file dialog has no folder siblings and therefore a one-item episode list.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .library import FINISHED_THRESHOLD, LibraryItem
from .recents import RecentItem
from .ui.chrome import format_duration_ns, format_size, normalise_codec

__all__ = [
    "DETAIL_FACT_KEYS",
    "HOME_CONTINUE_LIMIT",
    "RESUME_MIN_NS",
    "DetailEpisode",
    "DetailFact",
    "DetailView",
    "HomeCard",
    "HomeView",
    "RecentView",
    "bitrate_text",
    "detail_view",
    "episode_items",
    "hardware_decodable",
    "home_view",
    "progress_map",
    "recent_view",
    "remaining_text",
]

#: The home grid is four cards wide; the mockup keeps both rows at that width
#: so the page reads as one screen with no horizontal scrolling.
HOME_CONTINUE_LIMIT = 4

#: Order of the "clip facts" card, top to bottom.  The decode row is appended
#: by :func:`detail_view` because its value depends on the board's VPU support.
DETAIL_FACT_KEYS = (
    "spec_codec",
    "spec_picture",
    "spec_fps",
    "spec_duration",
    "spec_size",
    "spec_bitrate",
)

#: A position below one second is not a "continue watching" state: resuming
#: there is indistinguishable from starting over, and the CTA would read
#: "continue at 00:00".
RESUME_MIN_NS = 1_000_000_000

#: Codec names the RK3576 MPP decoder handles.  The probe stores the caps
#: subtype (``h264`` / ``h265``); anything else is reported as software decode
#: rather than pretending the VPU handled it.
_HW_CODECS = frozenset({"h264", "avc", "h265", "hevc", "hvc1", "hev1"})


@dataclass(frozen=True)
class HomeCard:
    """One history-backed tile: a library item plus the history cursor.

    Shared by the home "continue watching" row and the recent page; the former
    caps the list at four cards, the latter groups them by watch state.
    """

    item: LibraryItem
    progress: float = 0.0
    position_ns: int = 0
    duration_ns: int = 0
    #: History timestamp (``RecentItem.updated_at``); the recent page sorts by
    #: it.  Zero for the "recently added" fallback, which has no history.
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        # A history entry may be older than the probe; the duration from the
        # file itself wins whenever the history did not record one.
        if self.duration_ns <= 0 and self.item.metadata is not None:
            object.__setattr__(self, "duration_ns", self.item.duration_ns)
        if self.duration_ns > 0 and self.progress <= 0.0 and self.position_ns > 0:
            object.__setattr__(
                self,
                "progress",
                min(1.0, max(0.0, self.position_ns / self.duration_ns)),
            )

    @property
    def finished(self) -> bool:
        """True once the clip was watched past :data:`FINISHED_THRESHOLD`."""
        return self.duration_ns > 0 and self.progress >= FINISHED_THRESHOLD

    @property
    def remaining_ns(self) -> int:
        if self.duration_ns <= 0:
            return 0
        return max(0, self.duration_ns - max(0, self.position_ns))

    @property
    def resumable(self) -> bool:
        """True when the history position is worth a "continue at" CTA."""
        return not self.finished and self.position_ns >= RESUME_MIN_NS


@dataclass(frozen=True)
class HomeView:
    """Everything the home page renders, already ordered."""

    continue_cards: tuple[HomeCard, ...] = ()
    added_items: tuple[LibraryItem, ...] = ()

    @property
    def empty(self) -> bool:
        """No library item at all: the page shows its "empty library" card."""
        return not self.continue_cards and not self.added_items

    @property
    def hero(self) -> HomeCard | None:
        """The big top card: the most recent clip, else the newest file."""
        if self.continue_cards:
            return self.continue_cards[0]
        if self.added_items:
            return HomeCard(item=self.added_items[0])
        return None

    @property
    def hero_is_resume(self) -> bool:
        hero = self.hero
        return hero is not None and hero.resumable


@dataclass(frozen=True)
class RecentView:
    """The recent page: history cards split by watch state, newest first."""

    continue_cards: tuple[HomeCard, ...] = ()
    finished_cards: tuple[HomeCard, ...] = ()

    @property
    def empty(self) -> bool:
        """No history entry points at a file the library still scans."""
        return not self.continue_cards and not self.finished_cards

    @property
    def total(self) -> int:
        return len(self.continue_cards) + len(self.finished_cards)

    @property
    def resumable_count(self) -> int:
        """Cards with a position worth a "continue" tap (excludes finished)."""
        return sum(1 for card in self.continue_cards if card.resumable)

    def groups(self) -> tuple[tuple[str, tuple[HomeCard, ...]], ...]:
        """Non-empty ``(group key, cards)`` pairs in display order."""
        groups = (
            ("continue", self.continue_cards),
            ("finished", self.finished_cards),
        )
        return tuple((key, cards) for key, cards in groups if cards)


@dataclass(frozen=True)
class DetailEpisode:
    """One row of the detail page's episode grid."""

    item: LibraryItem
    progress: float = 0.0
    position_ns: int = 0
    duration_ns: int = 0
    current: bool = False

    @property
    def finished(self) -> bool:
        return self.duration_ns > 0 and self.progress >= FINISHED_THRESHOLD

    @property
    def remaining_ns(self) -> int:
        if self.duration_ns <= 0:
            return 0
        return max(0, self.duration_ns - max(0, self.position_ns))


@dataclass(frozen=True)
class DetailFact:
    """One ``key: value`` row of the clip-facts card."""

    key: str
    value: str
    accent: bool = False


@dataclass(frozen=True)
class DetailView:
    """Everything the detail page renders for one clip."""

    item: LibraryItem
    episodes: tuple[DetailEpisode, ...]
    index: int
    total_size: int
    facts: tuple[DetailFact, ...]
    progress: float = 0.0
    position_ns: int = 0
    duration_ns: int = 0

    @property
    def current(self) -> DetailEpisode:
        return self.episodes[self.index]

    @property
    def finished(self) -> bool:
        return self.duration_ns > 0 and self.progress >= FINISHED_THRESHOLD

    @property
    def remaining_ns(self) -> int:
        if self.duration_ns <= 0:
            return 0
        return max(0, self.duration_ns - max(0, self.position_ns))

    @property
    def resumable(self) -> bool:
        return not self.finished and self.position_ns >= RESUME_MIN_NS

    @property
    def siblings(self) -> tuple[DetailEpisode, ...]:
        """Every episode except the opened one, in grid order."""
        return tuple(ep for ep in self.episodes if not ep.current)

    @property
    def folder_name(self) -> str:
        return self.item.path.parent.name or str(self.item.path.parent)


def progress_map(recents: Iterable[RecentItem]) -> dict[Path, RecentItem]:
    """Index the history by resolved path (later entries win, as they are newest)."""
    mapping: dict[Path, RecentItem] = {}
    for entry in recents:
        mapping[entry.path] = entry
    return mapping


def home_view(
    items: Sequence[LibraryItem],
    recents: Sequence[RecentItem] = (),
    *,
    limit: int = HOME_CONTINUE_LIMIT,
) -> HomeView:
    """Build the home page model.

    "Continue watching" only lists clips that are *in the library*: a file
    opened once through the file dialog has no place in the browse pages (it
    is not part of the scanned folders), so the home page would otherwise
    promise to resume a file the library no longer shows.  The player's own
    "recently played" list still keeps it.
    """
    ordered = list(items)
    limit = max(0, int(limit))
    by_path = {item.path: item for item in ordered}
    cards: list[HomeCard] = []
    if limit:
        for entry in recents:
            item = by_path.get(entry.path)
            if item is None:
                continue
            cards.append(
                HomeCard(
                    item=item,
                    progress=entry.progress,
                    position_ns=entry.position_ns,
                    duration_ns=entry.duration_ns,
                    updated_at=entry.updated_at,
                )
            )
            if len(cards) >= limit:
                break
    added = tuple(
        sorted(ordered, key=lambda item: (-item.mtime, item.path.name.lower()))[:limit]
    )
    return HomeView(continue_cards=tuple(cards), added_items=added)


def recent_view(
    items: Sequence[LibraryItem],
    recents: Sequence[RecentItem] = (),
) -> RecentView:
    """Build the recent page model, newest watch first.

    Like the home page's "continue watching" row, only clips the library still
    scans are listed: a file opened once through the file dialog has no card
    (no poster, no folder siblings), so the page would promise a detail view it
    cannot render.  The player's own "recently played" list still keeps those
    entries.

    A card is "finished" once the history position crosses
    :data:`FINISHED_THRESHOLD`; everything else lands in "continue watching",
    including a clip that was only opened.  Entries are de-duplicated by path
    even though the store already keeps one entry per file.
    """
    by_path = {item.path: item for item in items}
    continue_cards: list[HomeCard] = []
    finished_cards: list[HomeCard] = []
    seen: set[Path] = set()
    for entry in sorted(recents, key=lambda item: -float(item.updated_at)):
        item = by_path.get(entry.path)
        if item is None or item.path in seen:
            continue
        seen.add(item.path)
        card = HomeCard(
            item=item,
            progress=entry.progress,
            position_ns=entry.position_ns,
            duration_ns=entry.duration_ns,
            updated_at=entry.updated_at,
        )
        if card.finished:
            finished_cards.append(card)
        else:
            continue_cards.append(card)
    return RecentView(tuple(continue_cards), tuple(finished_cards))


def episode_items(
    item: LibraryItem, items: Iterable[LibraryItem]
) -> tuple[LibraryItem, ...]:
    """Files that share *item*'s folder, name-sorted; always contains *item*.

    The scan is one level deep, so a folder is the only grouping this player
    has; that is what the detail page presents as "episodes".
    """
    parent = item.path.parent
    matches = [candidate for candidate in items if candidate.path.parent == parent]
    if not any(candidate.path == item.path for candidate in matches):
        matches.append(item)
    return tuple(
        sorted(matches, key=lambda candidate: (candidate.path.name.lower(), candidate.path.name))
    )


def hardware_decodable(codec: str, hw_ready: bool) -> bool:
    """True when the VPU is available *and* it decodes this codec."""
    if not hw_ready:
        return False
    name = str(codec or "").strip().lower()
    if name.startswith("x-"):
        name = name[2:]
    return name.replace("-", "").replace("_", "") in _HW_CODECS


def bitrate_text(size: int, duration_ns: int) -> str:
    """Average bitrate derived from file size and duration (``4.8 Mbps``)."""
    if size <= 0 or duration_ns <= 0:
        return "—"
    mbps = int(size) * 8.0 / (int(duration_ns) / 1_000_000_000.0) / 1_000_000.0
    return f"{mbps:.0f} Mbps" if mbps >= 100 else f"{mbps:.1f} Mbps"


def remaining_text(position_ns: int, duration_ns: int) -> str:
    """``07:12`` remaining (rounded up), empty when the duration is unknown.

    Rounding up matters: 0.8 s left displayed as ``00:00`` next to a progress
    bar that is not full reads as a glitch.
    """
    if duration_ns <= 0:
        return ""
    remaining = max(0, int(duration_ns) - max(0, int(position_ns)))
    seconds = (remaining + 999_999_999) // 1_000_000_000
    return format_duration_ns(seconds * 1_000_000_000)


def detail_view(
    item: LibraryItem,
    items: Iterable[LibraryItem],
    *,
    recents: Sequence[RecentItem] = (),
    hw_ready: bool = False,
    t: Callable[[str], str],
) -> DetailView:
    """Build the detail page model for *item*.

    Args:
        item: the clip the card opened.
        items: the scanned library, used to find folder siblings.
        recents: play history, used for the progress hairline and the resume CTA.
        hw_ready: whether the RK3576 VPU decoder is registered.
        t: translator used for the two decode labels (the only prose here).
    """
    history = progress_map(recents)
    episodes: list[DetailEpisode] = []
    current_index = 0
    for position, candidate in enumerate(episode_items(item, items)):
        entry = history.get(candidate.path)
        progress = entry.progress if entry is not None else 0.0
        duration = (
            entry.duration_ns if entry is not None and entry.duration_ns > 0 else candidate.duration_ns
        )
        position_ns = entry.position_ns if entry is not None else 0
        if candidate.path == item.path:
            current_index = position
        episodes.append(
            DetailEpisode(
                item=candidate,
                progress=progress,
                position_ns=position_ns,
                duration_ns=duration,
                current=candidate.path == item.path,
            )
        )
    current = episodes[current_index]
    # The item itself may be newer than the history (just copied): the scan
    # metadata is the fallback for both duration and progress.
    duration_ns = current.duration_ns or item.duration_ns
    position_ns = current.position_ns
    progress = current.progress
    if duration_ns > 0 and progress <= 0.0 and position_ns > 0:
        progress = min(1.0, position_ns / duration_ns)

    metadata = item.metadata
    width = metadata.width if metadata is not None else 0
    height = metadata.height if metadata is not None else 0
    fps = metadata.fps if metadata is not None else 0.0
    codec = metadata.codec if metadata is not None else ""
    total_size = sum(episode.item.size for episode in episodes)
    facts = (
        DetailFact("spec_codec", normalise_codec(codec)),
        DetailFact(
            "spec_picture",
            f"{width} × {height}" if width > 0 and height > 0 else "—",
        ),
        DetailFact("spec_fps", f"{fps:.3f} fps" if fps > 0 else "—"),
        DetailFact(
            "spec_duration",
            format_duration_ns(duration_ns) if duration_ns > 0 else "—",
        ),
        DetailFact("spec_size", format_size(item.size)),
        DetailFact("spec_bitrate", bitrate_text(item.size, duration_ns)),
    )
    decode_hw = hardware_decodable(codec, hw_ready)
    facts = facts + (
        DetailFact(
            "spec_decode",
            t("spec_decode_hw" if decode_hw else "spec_decode_sw"),
            accent=decode_hw,
        ),
    )
    return DetailView(
        item=item,
        episodes=tuple(episodes),
        index=current_index,
        total_size=total_size,
        facts=facts,
        progress=progress,
        position_ns=position_ns,
        duration_ns=duration_ns,
    )
