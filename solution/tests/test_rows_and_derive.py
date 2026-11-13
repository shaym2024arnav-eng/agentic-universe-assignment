from datetime import datetime, timedelta, timezone

from ledger.catalog import Disposition
from ledger.cli import DEFAULT_SCHEMAS
from ledger.catalog import load_schemas
from ledger.derive import Call, Event, bucket, effective_disposition, link_events
from ledger.ingest import validate_row

SCHEMAS = load_schemas(DEFAULT_SCHEMAS)
MOTOR = SCHEMAS["motor_renewal"]
CAT = {
    ("motor_renewal", "renewed"): Disposition(True, True),
    ("motor_renewal", "callback_requested"): Disposition(False, False),
    ("motor_renewal", "not_interested"): Disposition(True, False),
    ("motor_renewal", "payment_link_sent"): Disposition(False, False),
}
T0 = datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc)


def row(**kw):
    base = {"customer_name": "A B", "phone": "9876543210", "policy_number": "P1",
            "policy_expiry": None, "premium_amount": None, "preferred_language": None}
    base.update(kw)
    return base


# ---------------------------------------------------------------- row validation

def test_valid_row():
    r = validate_row(MOTOR, row(premium_amount="₹1,200", preferred_language="HI"))
    assert r.reason is None and r.warnings == 0
    assert r.values["phone"] == "+919876543210"
    assert r.values["premium_amount"] == "1200.00"
    assert r.values["preferred_language"] == "hi"


def test_first_failure_in_schema_order_wins():
    r = validate_row(MOTOR, row(customer_name=None, phone="123"))
    assert r.reason == "missing_required:customer_name"


def test_invalid_phone_and_missing_phone():
    assert validate_row(MOTOR, row(phone="N/A")).reason == "invalid_phone"
    assert validate_row(MOTOR, row(phone=None)).reason == "missing_required:phone"


def test_optional_invalid_is_warning_not_rejection():
    r = validate_row(MOTOR, row(policy_expiry="31/02/2026", premium_amount="N/A", preferred_language="Hindi"))
    assert r.reason is None
    assert r.warnings == 3
    assert r.values["policy_expiry"] is None


def test_warnings_not_counted_on_rejected_rows():
    r = validate_row(MOTOR, row(policy_expiry="soon", policy_number=None))
    assert r.reason == "missing_required:policy_number"
    assert r.warnings == 0


# ---------------------------------------------------------------- effective disposition

def ev(eid, etype, minutes, code=None, call="c1", phone="h1", wf="motor_renewal"):
    payload = {"disposition_code": code} if code else {}
    return Event(eid, etype, wf, call, T0 + timedelta(minutes=minutes),
                 None if etype == "disposition.override" else phone, payload)


def test_latest_set_wins_without_override():
    evs = [ev("e1", "disposition.set", 10, "callback_requested"), ev("e2", "disposition.set", 5, "renewed")]
    assert effective_disposition(evs, CAT, "motor_renewal") == "callback_requested"


def test_override_beats_later_set():
    evs = [ev("e1", "disposition.set", 100, "renewed"),
           ev("e2", "disposition.override", 10, "not_interested")]
    assert effective_disposition(evs, CAT, "motor_renewal") == "not_interested"


def test_latest_override_by_occurred_at_not_arrival():
    evs = [ev("e9", "disposition.override", 50, "renewed"),
           ev("e1", "disposition.override", 20, "not_interested")]
    assert effective_disposition(evs, CAT, "motor_renewal") == "renewed"


def test_unknown_codes_ignored():
    evs = [ev("e1", "disposition.set", 1, "renewed"), ev("e2", "disposition.set", 9, "RENEWD")]
    assert effective_disposition(evs, CAT, "motor_renewal") == "renewed"
    assert effective_disposition([ev("e2", "disposition.set", 9, "RENEWD")], CAT, "motor_renewal") is None


# ---------------------------------------------------------------- buckets

def call(disp=None, failed=False):
    return Call("c", ("motor_renewal", "h"), T0, telephony_failed=failed, effective_disposition=disp)


def test_buckets():
    assert bucket([], CAT) == "yet_to_reach_out"
    assert bucket([call(failed=True), call("renewed")], CAT) == "completed"   # completed beats tf
    assert bucket([call(failed=True), call(failed=True)], CAT) == "in_progress_telephony_failed"
    assert bucket([call(failed=True), call()], CAT) == "in_progress_dialed"
    assert bucket([call("callback_requested")], CAT) == "in_progress_dialed"


# ---------------------------------------------------------------- linking

def test_override_links_through_call_id_and_orphans_counted():
    evs = [
        ev("e1", "call.initiated", 0), ev("e2", "call.connected", 1),
        ev("e3", "disposition.set", 5, "renewed"),
        ev("e4", "disposition.override", 60, "not_interested"),       # no phone
        ev("e5", "call.initiated", 0, call="c2", phone="unknown"),     # orphan
        ev("e6", "disposition.override", 0, "renewed", call="c3"),     # orphan: unknown call
        ev("e7", "disposition.set", 6, "RENEWD"),                      # unknown disposition
    ]
    out = link_events(evs, {("motor_renewal", "h1")}, CAT)
    assert out.orphan_events == 2
    assert out.unknown_disposition_events == 1
    c = out.calls["c1"]
    assert c.connected and c.effective_disposition == "not_interested"


def test_call_without_initiated_is_not_counted():
    evs = [ev("e2", "call.connected", 1), ev("e3", "disposition.set", 5, "renewed")]
    out = link_events(evs, {("motor_renewal", "h1")}, CAT)
    assert out.calls == {} and out.orphan_events == 0


def test_workflow_is_part_of_identity():
    evs = [ev("e1", "call.initiated", 0, wf="policy_feedback")]
    out = link_events(evs, {("motor_renewal", "h1")}, CAT)
    assert out.orphan_events == 1
