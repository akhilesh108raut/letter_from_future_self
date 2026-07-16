"""
Pricing for Letter From Your Future Self.

Single flat price — unlike astro-report's tiered pricing, this is a smaller,
single-artifact product where a scaling price ladder isn't needed yet.
"""
import os

PRICE_INR = int(os.getenv("LETTER_PRICE_INR", "99"))


def get_price_info() -> dict:
    return {"current_price": PRICE_INR, "currency": "INR"}
