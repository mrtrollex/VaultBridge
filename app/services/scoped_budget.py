"""Request-local admission counters for Markdown federation (never legacy calls)."""

from dataclasses import dataclass, field
from threading import Event

from app.services.knowledge_spaces import SpaceError, SpaceId


@dataclass(frozen=True, slots=True)
class CancellationToken:
    _event: Event = field(default_factory=Event, repr=False)

    def cancel(self):
        self._event.set()

    def check(self):
        if self._event.is_set():
            raise SpaceError("operation_cancelled")


class BudgetExhausted(Exception):
    def __init__(self, resource):
        self.resource = resource
        super().__init__(resource)


# (aggregate, per-space); injectable downward-only limits aid primitive tests.
CEILINGS = {
    "paths": (20_000, 10_000),
    "entries": (100_000, 25_000),
    "directories": (10_000, 2_500),
    "probes": (2_000_000, 500_000),
    "aliases": (20_000, 10_000),
    "directory_aliases": (20_000, 10_000),
    "bytes": (268_435_456, 268_435_456),
    "relationships": (200_000, 50_000),
    "comparisons": (320_000, 320_000),
}


class ScopedBudget:
    def __init__(self, ids: tuple[SpaceId, ...], *, limits=None):
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("invalid budget selection")
        limits = {} if limits is None else limits
        if set(limits) - CEILINGS.keys():
            raise ValueError("invalid budget resource")
        self.ceilings = dict(CEILINGS)
        for name, pair in limits.items():
            if len(pair) != 2 or any(type(x) is not int or x < 0 for x in pair):
                raise ValueError("invalid budget ceiling")
            if any(x > maximum for x, maximum in zip(pair, CEILINGS[name], strict=True)):
                raise ValueError("budget cannot raise ceiling")
            self.ceilings[name] = pair
        self.allocations = {
            sid: {name: min(local, total // len(ids))
                  for name, (total, local) in self.ceilings.items()}
            for sid in ids
        }
        self.total = dict.fromkeys(CEILINGS, 0)
        self.used = {sid: dict.fromkeys(CEILINGS, 0) for sid in ids}
        self.reads = 0
        self.candidate_reads = {sid: 0 for sid in ids}
        self.incoming_reads = set()
        self.parse_probes = 0
        self.space_parse_probes = dict.fromkeys(ids, 0)
        self.relationship_sources = set()
        self.source_bytes = dict.fromkeys(ids, 0)

    def remaining(self, sid, name):
        return min(self.ceilings[name][0] - self.total[name],
                   self.allocations[sid][name] - self.used[sid][name])

    def charge(self, sid, name, count=1):
        if type(count) is not int or count < 0:
            raise ValueError("invalid charge")
        if count > self.remaining(sid, name):
            raise BudgetExhausted(name)
        self.total[name] += count
        self.used[sid][name] += count

    def admit_read(self, sid, path, *, incoming=False):
        if self.reads >= 20_016:
            raise BudgetExhausted("bytes")
        if incoming:
            key = sid, path
            if key in self.incoming_reads or len(self.incoming_reads) == 16:
                raise BudgetExhausted("bytes")
            self.incoming_reads.add(key)
        else:
            if self.candidate_reads[sid] == self.allocations[sid]["paths"]:
                raise BudgetExhausted("bytes")
            self.candidate_reads[sid] += 1
        self.reads += 1

    def relationships(self, sid, cancel):
        return RelationshipBudgetView(self, sid, cancel)


class RelationshipBudgetView:
    """One view per space; source credit cannot reset occurrence counters."""

    def __init__(self, owner, sid, cancel):
        self.owner, self.sid, self.cancel = owner, sid, cancel
        self._parse_remaining = 0

    def source(self, path, byte_length):
        self.cancel.check()
        if type(byte_length) is not int or byte_length < 0:
            raise ValueError("invalid authored byte credit")
        key = self.sid, path
        if key in self.owner.relationship_sources:
            raise ValueError("source snapshot already derived")
        # Content must come from the accounted verified-read owner.
        if byte_length + self.owner.source_bytes[self.sid] > self.owner.used[self.sid]["bytes"]:
            raise ValueError("unaccounted authored content")
        self.owner.relationship_sources.add(key)
        self.owner.source_bytes[self.sid] += byte_length
        self._parse_remaining = 16 * byte_length

    @property
    def available(self):
        return self.owner.remaining(self.sid, "relationships") > 0

    @property
    def parse_exhausted(self):
        return self._parse_remaining == 0

    def probe(self, count=1):
        if type(count) is not int or count < 0:
            raise ValueError("invalid parse charge")
        if count > self._parse_remaining:
            raise BudgetExhausted("relationships")
        while count:
            self.cancel.check()
            batch = min(1024, count)
            self._parse_remaining -= batch
            self.owner.parse_probes += batch
            self.owner.space_parse_probes[self.sid] += batch
            count -= batch

    def try_admit(self):
        self.cancel.check()
        if self.parse_exhausted:
            raise BudgetExhausted("relationships")
        self.owner.charge(self.sid, "relationships")
