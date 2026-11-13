"""Pure derivation logic: linking, effective disposition, buckets.

Kept free of SQL and file I/O so the rules most likely to break are unit-testable on
hand-built inputs.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from .catalog import Disposition

OVERRIDE = "disposition.override"
SET = "disposition.set"


@dataclass
class Event:
    event_id: str
    event_type: str | None
    workflow_id: str | None
    call_id: str | None
    occurred_at: datetime | None      # aware UTC
    phone_hmac: str | None
    payload: dict


@dataclass
class Call:
    call_id: str
    lead: tuple[str, str]             # (workflow_id, phone_hmac)
    initiated_at: datetime
    connected: bool = False
    telephony_failed: bool = False
    effective_disposition: str | None = None


@dataclass
class Linked:
    calls: dict[str, Call] = field(default_factory=dict)
    orphan_events: int = 0
    unknown_disposition_events: int = 0


def _sort_key(e: Event):
    # latest by occurred_at; ties broken by event_id so the result is deterministic
    return (e.occurred_at or datetime.min, e.event_id)


def effective_disposition(events: list[Event], catalog: dict[tuple[str, str], Disposition],
                          workflow_id: str) -> str | None:
    """Latest known override wins; else latest known set; else None."""
    def known(e: Event) -> bool:
        return (workflow_id, (e.payload or {}).get("disposition_code")) in catalog

    overrides = [e for e in events if e.event_type == OVERRIDE and known(e)]
    if overrides:
        return max(overrides, key=_sort_key).payload["disposition_code"]
    sets = [e for e in events if e.event_type == SET and known(e)]
    if sets:
        return max(sets, key=_sort_key).payload["disposition_code"]
    return None


def link_events(events: list[Event], lead_keys: set[tuple[str, str]],
                catalog: dict[tuple[str, str], Disposition]) -> Linked:
    out = Linked()
    linked: dict[str, tuple[str, str]] = {}           # event_id -> lead key
    call_lead: dict[str, tuple[str, str]] = {}        # call_id -> lead key (from phone-linked events)

    # Pass 1: link everything that carries a phone.
    for e in events:
        if e.event_type == OVERRIDE:
            continue
        key = (e.workflow_id, e.phone_hmac)
        if e.phone_hmac and key in lead_keys:
            linked[e.event_id] = key
            if e.call_id:
                # call.initiated is authoritative for a call's lead; otherwise first seen
                if e.event_type == "call.initiated" or e.call_id not in call_lead:
                    call_lead[e.call_id] = key

    # Pass 2: overrides link through their call_id.
    for e in events:
        if e.event_type != OVERRIDE:
            continue
        key = call_lead.get(e.call_id) if e.call_id else None
        if key and (e.workflow_id in (None, key[0])):
            linked[e.event_id] = key

    out.orphan_events = sum(1 for e in events if e.event_id not in linked)

    by_call: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        key = linked.get(e.event_id)
        if key is None:
            continue
        if e.event_type in (SET, OVERRIDE) and (key[0], (e.payload or {}).get("disposition_code")) not in catalog:
            out.unknown_disposition_events += 1
            continue
        if e.call_id:
            by_call[e.call_id].append(e)

    for call_id, evs in by_call.items():
        lead = call_lead.get(call_id)
        initiated = [e for e in evs if e.event_type == "call.initiated" and linked[e.event_id] == lead
                     and e.occurred_at is not None]
        if lead is None or not initiated:
            continue  # a call only counts if its call.initiated is present and linked
        evs = [e for e in evs if linked[e.event_id] == lead]
        out.calls[call_id] = Call(
            call_id=call_id,
            lead=lead,
            initiated_at=min(e.occurred_at for e in initiated),
            connected=any(e.event_type == "call.connected" for e in evs),
            telephony_failed=any(e.event_type == "call.telephony_failed" for e in evs),
            effective_disposition=effective_disposition(evs, catalog, lead[0]),
        )
    return out


def bucket(calls: list[Call], catalog: dict[tuple[str, str], Disposition]) -> str:
    if not calls:
        return "yet_to_reach_out"
    for c in calls:
        d = catalog.get((c.lead[0], c.effective_disposition)) if c.effective_disposition else None
        if d and d.is_terminal:
            return "completed"
    if all(c.telephony_failed for c in calls):
        return "in_progress_telephony_failed"
    return "in_progress_dialed"
