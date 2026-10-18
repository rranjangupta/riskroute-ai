"""Pinned demo data (5 storytelling cases). ALL DATA IS FICTIONAL.
generate_data.py adds ~200 more synthetic customers around these."""
import random
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 17, 10, 0, tzinfo=IST)  # fixed demo clock => reproducible

def ts(**d): return (NOW - timedelta(**d)).isoformat()

CUSTOMERS = {
    "CUST-101": {"name": "Asha Traders (MSME)", "segment": "business"},
    "CUST-102": {"name": "Ravi K", "segment": "salaried"},
    "CUST-103": {"name": "Meera S", "segment": "retired"},
}
BENEFICIARIES = {
    "BEN-110": {"customer_id": "CUST-102", "name": "Landlord - P Rao", "added_at": ts(days=400)},
    "BEN-111": {"customer_id": "CUST-102", "name": "Ravi K - savings", "added_at": ts(days=700)},
    "BEN-210": {"customer_id": "CUST-101", "name": "Steel Supplier Pvt Ltd", "added_at": ts(days=500)},
    "BEN-211": {"customer_id": "CUST-101", "name": "Packaging Co", "added_at": ts(days=300)},
    "BEN-310": {"customer_id": "CUST-103", "name": "Son - A S", "added_at": ts(days=900)},
    "BEN-182": {"customer_id": "CUST-102", "name": "R Enterprises", "added_at": ts(minutes=40)},
    "BEN-233": {"customer_id": "CUST-101", "name": "New Alloy Vendor LLP", "added_at": ts(hours=3)},
    "BEN-377": {"customer_id": "CUST-103", "name": "Refund Desk Services", "added_at": ts(minutes=12)},
}
DEVICES = {
    "DEV-120": {"customer_id": "CUST-102", "label": "Android phone", "first_seen": ts(days=500)},
    "DEV-391": {"customer_id": "CUST-102", "label": "Windows browser", "first_seen": ts(minutes=55)},
    "DEV-220": {"customer_id": "CUST-101", "label": "Office desktop", "first_seen": ts(days=600)},
    "DEV-320": {"customer_id": "CUST-103", "label": "iPhone", "first_seen": ts(days=800)},
    "DEV-488": {"customer_id": "CUST-103", "label": "Android phone", "first_seen": ts(hours=2)},
}
ACCOUNT_EVENTS = [
    {"id": "EVT-501", "customer_id": "CUST-102", "type": "LOGIN", "device_id": "DEV-391", "at": ts(minutes=55)},
    {"id": "AUTH-77", "customer_id": "CUST-102", "type": "OTP_PASSED", "device_id": "DEV-391", "at": ts(minutes=41)},
    {"id": "EVT-502", "customer_id": "CUST-102", "type": "BENEFICIARY_ADDED", "ref": "BEN-182", "at": ts(minutes=40)},
    {"id": "EVT-601", "customer_id": "CUST-101", "type": "BENEFICIARY_ADDED", "ref": "BEN-233", "at": ts(hours=3)},
    {"id": "EVT-701", "customer_id": "CUST-103", "type": "MOBILE_NUMBER_CHANGED", "at": ts(hours=2, minutes=30)},
    {"id": "EVT-702", "customer_id": "CUST-103", "type": "LOGIN", "device_id": "DEV-488", "at": ts(hours=2)},
    {"id": "AUTH-91", "customer_id": "CUST-103", "type": "OTP_PASSED", "device_id": "DEV-488", "at": ts(minutes=13)},
    {"id": "EVT-703", "customer_id": "CUST-103", "type": "BENEFICIARY_ADDED", "ref": "BEN-377", "at": ts(minutes=12)},
]
# "expected" = team-set benchmark label (never shown to the AI)
CASES = [
    {"id": "TXN-1042", "customer_id": "CUST-102", "beneficiary_id": "BEN-182", "device_id": "DEV-391",
     "amount": 800000, "at": ts(minutes=35), "channel": "Internet Banking", "expected": "VERIFY"},
    {"id": "TXN-1043", "customer_id": "CUST-102", "beneficiary_id": "BEN-110", "device_id": "DEV-120",
     "amount": 25000, "at": ts(minutes=20), "channel": "Mobile App", "expected": "PROCEED"},
    {"id": "TXN-1044", "customer_id": "CUST-103", "beneficiary_id": "BEN-377", "device_id": "DEV-488",
     "amount": 450000, "at": ts(minutes=10), "channel": "Mobile App", "expected": "HUMAN_REVIEW"},
    {"id": "TXN-1045", "customer_id": "CUST-101", "beneficiary_id": "BEN-210", "device_id": "DEV-220",
     "amount": 600000, "at": ts(minutes=30), "channel": "Internet Banking", "expected": "PROCEED"},
    {"id": "TXN-1046", "customer_id": "CUST-101", "beneficiary_id": "BEN-233", "device_id": "DEV-220",
     "amount": 700000, "at": ts(minutes=25), "channel": "Internet Banking", "expected": "VERIFY"},
]

def build_history():
    rnd = random.Random(42); hist = []; n = 0
    profiles = {"CUST-102": [("BEN-110", 25000, 25000, 30), ("BEN-111", 5000, 40000, 12)],
                "CUST-101": [("BEN-210", 450000, 750000, 30), ("BEN-211", 60000, 150000, 20)],
                "CUST-103": [("BEN-310", 10000, 30000, 45)]}
    devices = {"CUST-102": "DEV-120", "CUST-101": "DEV-220", "CUST-103": "DEV-320"}
    for cust, payees in profiles.items():
        for ben, lo, hi, every in payees:
            for k in range(1, 9):
                n += 1
                amt = lo if lo == hi else rnd.randrange(lo, hi, 1000)
                at = (NOW - timedelta(days=every * k)).replace(hour=rnd.randint(9, 19))
                hist.append({"id": f"HTX-{n:04d}", "customer_id": cust, "beneficiary_id": ben,
                             "device_id": devices[cust], "amount": amt, "at": at.isoformat(), "status": "COMPLETED"})
    return hist

HISTORY = build_history()
POLICY = {"new_beneficiary_hours": 24, "new_device_hours": 24, "contact_change_hours": 72, "high_value_amount": 200000}
