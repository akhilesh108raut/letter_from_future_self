"""
Letter From Your Future Self — database models.

Own table namespace (`lfs_` prefix) so this product's DB is fully
independent of anything else.
"""
import json
import uuid as uuid_lib
from datetime import datetime

from database import db


def _new_uuid() -> str:
    return uuid_lib.uuid4().hex


class Order(db.Model):
    """One row per letter purchase attempt (created → paid / failed).

    No accounts table: a paid letter is reachable through its unguessable
    32-hex UUID (the link IS the capability), so it survives refreshes, new
    devices, and the cross-site Razorpay redirect that drops the session
    cookie."""
    __tablename__ = "lfs_orders"

    id     = db.Column(db.Integer, primary_key=True)
    uuid   = db.Column(db.String(32), unique=True, nullable=False,
                       index=True, default=_new_uuid)

    # Birth details (what the chart was built from)
    name        = db.Column(db.String(120), default="")
    email       = db.Column(db.String(255), nullable=True, index=True)
    mobile      = db.Column(db.String(20), nullable=True)
    birth_place = db.Column(db.String(200), default="")
    year        = db.Column(db.Integer, nullable=False)
    month       = db.Column(db.Integer, nullable=False)
    day         = db.Column(db.Integer, nullable=False)
    hour        = db.Column(db.Integer, nullable=False)
    minute      = db.Column(db.Integer, nullable=False)
    lat         = db.Column(db.Float, nullable=False)
    lon         = db.Column(db.Float, nullable=False)
    timezone    = db.Column(db.Float, default=5.5)
    language    = db.Column(db.String(8), default="en")

    # Money — price is locked server-side at order creation time
    price_paid  = db.Column(db.Float, nullable=True)      # in `currency`'s major unit
    currency    = db.Column(db.String(8), default="INR")
    share_count = db.Column(db.Integer, default=0, nullable=False)
    status      = db.Column(db.String(24), default="created",
                            index=True)  # created | paid | failed

    razorpay_order_id   = db.Column(db.String(64), nullable=True, index=True)
    razorpay_payment_id = db.Column(db.String(64), nullable=True)
    razorpay_signature  = db.Column(db.String(160), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    paid_at    = db.Column(db.DateTime, nullable=True)

    letter = db.relationship("Letter", backref="order", uselist=False, lazy=True)

    @staticmethod
    def total_paid() -> int:
        return Order.query.filter_by(status="paid").count()


class Letter(db.Model):
    """The generated letter — chart JSON + AI JSON, cached forever."""
    __tablename__ = "lfs_letters"

    id           = db.Column(db.Integer, primary_key=True)
    uuid         = db.Column(db.String(32), unique=True, nullable=False,
                             index=True, default=_new_uuid)
    order_id     = db.Column(db.Integer, db.ForeignKey("lfs_orders.id"),
                             nullable=False)
    chart_json   = db.Column(db.Text, nullable=False)   # compact engine output
    ai_json      = db.Column(db.Text, nullable=True)    # Claude letter (cached)
    generated_at = db.Column(db.DateTime, nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def chart(self) -> dict:
        return json.loads(self.chart_json)

    def ai(self) -> dict | None:
        return json.loads(self.ai_json) if self.ai_json else None


class PaymentEvent(db.Model):
    """Audit log of every payment lifecycle event."""
    __tablename__ = "lfs_payment_events"

    id         = db.Column(db.Integer, primary_key=True)
    order_id   = db.Column(db.Integer, db.ForeignKey("lfs_orders.id"),
                           nullable=False, index=True)
    event      = db.Column(db.String(40), nullable=False)
    detail     = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
