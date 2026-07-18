"""
Pricing for Letter From Your Future Self.

Single flat price, in INR — plus a share-to-unlock path: sharing the
product (WhatsApp/Instagram) SHARE_TARGET times unlocks the letter for
free instead of paying. See routes.py's /api/order/<uuid>/share.
"""
import os

PRICE_INR = int(os.getenv("LETTER_PRICE_INR", "49"))
CURRENCY = "INR"
SHARE_TARGET = int(os.getenv("LETTER_SHARE_TARGET", "3"))


def get_price_info() -> dict:
    return {"current_price": PRICE_INR, "currency": CURRENCY, "share_target": SHARE_TARGET}
