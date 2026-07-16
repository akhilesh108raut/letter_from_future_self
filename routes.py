"""
Letter From Your Future Self — routes.

Blueprint-mounted at /letter. Self-contained: own templates, static files,
models, and payment flow.
"""
import json
import time
import logging
import re
from datetime import datetime

from flask import (Blueprint, render_template, request, jsonify, session,
                   current_app, abort)

from database import db
from models import Order, Letter, PaymentEvent
from pricing import get_price_info
import payments

SUPPORTED_LANGUAGES = {"en", "hi", "ta", "te", "bn", "mr", "kn", "gu", "ml", "pa"}

log = logging.getLogger("letter")

letter_bp = Blueprint(
    "letter_store", __name__,
    url_prefix="/letter",
    template_folder="templates",
    static_folder="static",
    static_url_path="/letter-static",
)

# ── Lightweight per-IP rate limiting (mutating endpoints) ───────────────────
_hits: dict[str, list[float]] = {}


def _rate_limited(key: str, limit: int = 12, window: int = 60) -> bool:
    now = time.time()
    bucket = _hits.setdefault(key, [])
    bucket[:] = [t for t in bucket if now - t < window]
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False


def _dev_mode() -> bool:
    return (not payments.razorpay_configured()) and current_app.debug


def _grant_ownership(letter_uuid: str):
    owned = session.get("letter_owned", [])
    if letter_uuid not in owned:
        owned.append(letter_uuid)
    session["letter_owned"] = owned


def _can_view_letter(letter_uuid: str, order) -> bool:
    """A PAID letter is accessible by its unguessable UUID — the link IS the
    capability, so it survives refreshes, new devices, and Razorpay's
    cross-site redirect (which drops the session cookie)."""
    if order.status != "paid":
        return False
    _grant_ownership(letter_uuid)
    return True


def _valid_email(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""))


# ── AI generation (isolated from the payment path — see /generate below) ──

def _ensure_ai(letter: Letter) -> dict:
    """Generate the letter once Claude succeeds; afterwards always serve from
    DB. Never called inside the payment-verify request — the AI call can
    take 30-100s, which must not block or risk the money path."""
    if letter.ai_json:
        return json.loads(letter.ai_json)
    from services.letter_ai import generate_letter_ai
    ai = generate_letter_ai(letter.chart(), name=letter.order.name or "",
                            language=letter.order.language or "en")
    if ai.get("_source") == "claude":
        letter.ai_json = json.dumps(ai, separators=(",", ":"), default=str)
        letter.generated_at = datetime.utcnow()
        db.session.commit()
    return ai


def _cached_or_fallback(letter: Letter, force_fallback: bool):
    if letter.ai_json:
        return json.loads(letter.ai_json), True
    if force_fallback:
        from services.letter_ai import _engine_fallback
        return _engine_fallback(letter.chart(), letter.order.name or ""), True
    return None, False


# ── Pages ────────────────────────────────────────────────────────────────

@letter_bp.route("/")
def landing():
    return render_template("letter/landing.html", price=get_price_info())


@letter_bp.route("/checkout/<order_uuid>")
def checkout(order_uuid):
    order = Order.query.filter_by(uuid=order_uuid).first_or_404()
    letter = order.letter
    if not letter:
        abort(404)
    if order.status == "paid":
        _grant_ownership(letter.uuid)
        return render_template("letter/success.html", letter_uuid=letter.uuid)
    return render_template(
        "letter/payment.html",
        order=order,
        price=get_price_info(),
        dev_mode=_dev_mode(),
        razorpay_key_id=(payments.get_keys()[0] or ""),
    )


@letter_bp.route("/read/<letter_uuid>")
def view_letter(letter_uuid):
    letter = Letter.query.filter_by(uuid=letter_uuid).first_or_404()
    order = letter.order

    if not (_can_view_letter(letter_uuid, order) or _dev_mode()):
        return render_template("letter/cancel.html",
                               title="Letter locked",
                               message="This letter isn't unlocked yet — complete "
                                       "your payment to read it."), 403

    ai, ready = _cached_or_fallback(letter, bool(request.args.get("fallback")))
    if not ready:
        return render_template("letter/loading.html", letter_uuid=letter_uuid)
    if ai.get("letter") and order.name:
        first = order.name.strip().split()[0]
        ai = {**ai, "letter": ai["letter"].replace("Dear friend,", f"Dear {first},")}
    return render_template("letter/read.html", ai=ai, order=order, letter=letter)


@letter_bp.route("/cancel")
def cancel():
    return render_template("letter/cancel.html",
                           title="Payment cancelled",
                           message="No charge was made. You can try again anytime.")


# ── API: create order (chart + purchase row) ────────────────────────────

@letter_bp.route("/api/preview", methods=["POST"])
def api_preview():
    if _rate_limited(f"preview:{request.remote_addr}", limit=8):
        return jsonify(error="Too many requests. Please wait a moment."), 429

    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip()
    mobile = re.sub(r"[^\d+]", "", str(data.get("mobile", "")))
    if not _valid_email(email):
        return jsonify(error="Enter a valid email address."), 400
    if mobile and not re.match(r"^\+?\d{7,15}$", mobile):
        return jsonify(error="Enter a valid mobile number or leave it blank."), 400
    try:
        year, month, day = int(data["year"]), int(data["month"]), int(data["day"])
        hour, minute = int(data["hour"]), int(data["minute"])
        lat, lon = float(data["lat"]), float(data["lon"])
        tz = float(data.get("timezone", 5.5))
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Invalid birth details."), 400
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 1 <= month <= 12):
        return jsonify(error="Invalid coordinates or date."), 400

    from engine.chart import build_chart
    from rag.rule_engine import query_rules
    try:
        chart = build_chart(year, month, day, hour, minute, lat, lon,
                            timezone_offset=tz, name=str(data.get("name", ""))[:100])
        chart["classical_rules"] = query_rules(chart, top_k=18)
    except Exception as e:                       # noqa: BLE001
        log.exception("chart build failed")
        return jsonify(error=f"Chart calculation failed: {e}"), 500

    language = str(data.get("language", "en")).strip().lower()
    if language not in SUPPORTED_LANGUAGES:
        language = "en"

    order = Order(
        name=str(data.get("name", ""))[:100],
        email=email[:255],
        mobile=mobile[:20] or None,
        birth_place=str(data.get("birth_place", ""))[:180],
        year=year, month=month, day=day, hour=hour, minute=minute,
        lat=lat, lon=lon, timezone=tz,
        language=language,
    )
    db.session.add(order)
    db.session.flush()
    letter = Letter(order_id=order.id,
                    chart_json=json.dumps(chart, separators=(",", ":"), default=str))
    db.session.add(letter)
    db.session.commit()

    return jsonify(checkout_url=f"/letter/checkout/{order.uuid}")


@letter_bp.route("/api/create-order", methods=["POST"])
def api_create_order():
    """Lock the server-side price and create a Razorpay order."""
    if _rate_limited(f"order:{request.remote_addr}", limit=10):
        return jsonify(error="Too many requests."), 429

    data = request.get_json(silent=True) or {}
    order = Order.query.filter_by(uuid=data.get("order_uuid", "")).first()
    if not order:
        return jsonify(error="Order not found."), 404
    if order.status == "paid":
        return jsonify(success=True, already_paid=True,
                       report_url=f"/letter/read/{order.letter.uuid}")

    price = get_price_info()["current_price"]
    order.price_paid = price
    db.session.commit()

    if _dev_mode():
        return jsonify(order_id=f"dev_order_{order.uuid}", amount=price * 100,
                       key_id="", dev_mode=True)

    try:
        rp_order = payments.create_order(price, receipt=order.uuid)
    except RuntimeError as e:
        log.exception("Razorpay order creation failed")
        return jsonify(error=str(e)), 502

    order.razorpay_order_id = rp_order["id"]
    db.session.add(PaymentEvent(order_id=order.id, event="order_created",
                                detail=rp_order["id"]))
    db.session.commit()
    return jsonify(order_id=rp_order["id"], amount=rp_order["amount"],
                   key_id=payments.get_keys()[0], dev_mode=False)


def _mark_paid(order: Order, event: str, detail: str) -> str:
    """Mark paid + return the letter URL — FAST. Generation is deliberately
    NOT done here (see _ensure_ai) so the payment path never blocks on or
    risks failing because of the AI call."""
    order.status = "paid"
    order.paid_at = datetime.utcnow()
    db.session.add(PaymentEvent(order_id=order.id, event=event, detail=detail))
    db.session.commit()

    letter = order.letter
    _grant_ownership(letter.uuid)
    return f"/letter/read/{letter.uuid}"


@letter_bp.route("/api/verify", methods=["POST"])
def api_verify():
    """Server-side Razorpay signature verification. Never trusts the frontend."""
    data = request.get_json(silent=True) or {}
    order_id = data.get("razorpay_order_id", "")
    payment_id = data.get("razorpay_payment_id", "")
    signature = data.get("razorpay_signature", "")

    order = Order.query.filter_by(razorpay_order_id=order_id).first()
    if not order:
        return jsonify(error="Unknown order."), 404
    if order.status == "paid":
        return jsonify(success=True, report_url=f"/letter/read/{order.letter.uuid}")

    if not payments.verify_signature(order_id, payment_id, signature):
        order.status = "failed"
        db.session.add(PaymentEvent(order_id=order.id,
                                    event="signature_mismatch", detail=payment_id))
        db.session.commit()
        return jsonify(error="Payment verification failed."), 400

    order.razorpay_payment_id = payment_id
    order.razorpay_signature = signature
    url = _mark_paid(order, "payment_verified", payment_id)
    return jsonify(success=True, report_url=url)


@letter_bp.route("/api/razorpay/webhook", methods=["POST"])
def razorpay_webhook():
    """Razorpay server-to-server payment confirmation."""
    raw_body = request.get_data(cache=True)
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not payments.verify_webhook_signature(raw_body, signature):
        log.warning("Rejected Razorpay webhook with invalid signature")
        return jsonify(error="Invalid webhook signature"), 400
    payload = request.get_json(silent=True) or {}
    if payload.get("event") not in {"payment.captured", "order.paid"}:
        return jsonify(status="ignored"), 200
    payment = ((payload.get("payload") or {}).get("payment") or {}).get("entity") or {}
    order_id = payment.get("order_id", "")
    order = Order.query.filter_by(razorpay_order_id=order_id).first()
    if not order:
        log.warning("Razorpay webhook for unknown order %s", order_id)
        return jsonify(status="unknown_order"), 200
    if order.status == "paid":
        return jsonify(status="already_processed"), 200
    order.razorpay_payment_id = payment.get("id") or order.razorpay_payment_id
    _mark_paid(order, "webhook_payment_captured", payment.get("id", ""))
    return jsonify(status="processed"), 200


@letter_bp.route("/api/letter/<letter_uuid>/generate", methods=["POST"])
def api_generate_letter(letter_uuid):
    """Run the Claude pipeline for a PAID letter, in its own request —
    isolated from payment. Idempotent and retryable by the client."""
    letter = Letter.query.filter_by(uuid=letter_uuid).first_or_404()
    order = letter.order
    if not (_can_view_letter(letter_uuid, order) or _dev_mode()):
        return jsonify(error="Letter locked."), 403

    if letter.ai_json:
        return jsonify(ready=True, source="cached")
    try:
        ai = _ensure_ai(letter)
    except Exception:
        log.exception("Letter generation failed for %s", letter_uuid)
        return jsonify(ready=False, source="error"), 200
    source = ai.get("_source", "")
    return jsonify(ready=(source == "claude"), source=source), 200


@letter_bp.route("/api/dev-pay", methods=["POST"])
def api_dev_pay():
    """DEV-ONLY simulated payment — active only when Razorpay keys are absent
    and Flask debug is on."""
    if not _dev_mode():
        abort(404)
    data = request.get_json(silent=True) or {}
    order = Order.query.filter_by(uuid=data.get("order_uuid", "")).first()
    if not order:
        return jsonify(error="Order not found."), 404
    if order.status != "paid":
        order.razorpay_payment_id = "dev_payment_simulated"
        url = _mark_paid(order, "dev_simulated", "local dev payment")
    else:
        url = f"/letter/read/{order.letter.uuid}"
    return jsonify(success=True, report_url=url)


@letter_bp.route("/api/pricing")
def api_pricing():
    return jsonify(get_price_info())
