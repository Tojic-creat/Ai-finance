# backend/apps/finances/utils.py
"""
Utility helpers for the `finances` app.

Contains small, well-tested helpers used across views, tasks and management commands:
- money formatting / parsing
- simple currency conversion helper (requires explicit rate for MVP)
- duplicate detection for transactions (naive but fast)
- lightweight summarization (by category / by account)
- simple rule-based category suggestion (keyword-based; used before ML kicks in)
- prepare sankey-like payload (nodes/links) for frontend charts
- safe file save helper using Django default storage
- friendly timedelta formatter

Design decisions:
- Keep everything deterministic and side-effect-free where possible.
- For currency conversion we require an explicit rate argument for MVP. In future this can call
  an external FX service (task) and cache rates.
- Rule-based category suggestion is intentionally simple (keyword/merchant mapping). The ML module
  will replace/augment it later.
"""

from __future__ import annotations

import collections
import os
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone

# Local import of models only inside functions to avoid import cycles at module import time.

DECIMAL_QUANT = Decimal("0.01")


# -------------------------
# Money helpers
# -------------------------
def parse_decimal(value: Any) -> Decimal:
    """
    Safely parse value into Decimal rounded to cents.

    Accepts Decimal, int, float, or numeric string. Raises InvalidOperation on bad input.
    """
    if isinstance(value, Decimal):
        dec = value
    else:
        try:
            dec = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise InvalidOperation(
                f"Cannot parse decimal from {value!r}: {exc}")
    return dec.quantize(DECIMAL_QUANT, rounding=ROUND_HALF_UP)


def format_money(amount: Any, currency: str = "USD", symbol_map: Optional[Dict[str, str]] = None) -> str:
    """
    Format money with 2 decimals and a currency code or symbol.

    Example: format_money('12.5','EUR') -> "12.50 EUR"
    If symbol_map provided (e.g. {'USD':'$'}), it will use symbol when available.
    """
    dec = parse_decimal(amount)
    symbol_map = symbol_map or getattr(
        settings, "CURRENCY_SYMBOL_MAP", None) or {}
    symbol = symbol_map.get(currency.upper(), None)
    if symbol:
        return f"{symbol}{dec:,f}".rstrip("0").rstrip(".") if dec % 1 != 0 else f"{symbol}{dec:,d}"
    return f"{dec:.2f} {currency.upper()}"


def currency_convert(amount: Any, from_currency: str, to_currency: str, rate: Optional[Decimal]) -> Decimal:
    """
    Convert amount from `from_currency` to `to_currency` using given `rate`.

    - rate: Decimal amount of `to_currency` per 1 unit of `from_currency` (i.e. multiply).
    - If rate is None this function will raise ValueError (MVP: explicit rate required).
    """
    if from_currency.upper() == to_currency.upper():
        return parse_decimal(amount)
    if rate is None:
        raise ValueError(
            "Conversion rate required for currency conversion in MVP.")
    dec = parse_decimal(amount)
    try:
        r = parse_decimal(rate)
    except InvalidOperation:
        raise ValueError("Invalid conversion rate provided.")
    converted = (dec * r).quantize(DECIMAL_QUANT, rounding=ROUND_HALF_UP)
    return converted


# -------------------------
# Duplicate detection
# -------------------------
def detect_duplicate_transactions(
    transactions: Iterable[Any],
    key_fields: Sequence[str] = (
        "account_id", "amount", "date", "counterparty"),
) -> List[Tuple[Any, List[Any]]]:
    """
    Naive duplicate detector.

    - `transactions` is an iterable of Transaction-like objects (can be queryset).
    - `key_fields` list of attribute names used to define a duplicate key.
    Returns list of tuples: (key_repr, [tx1, tx2, ...]) where list length > 1.
    """
    groups: Dict[Tuple[str, ...], List[Any]] = {}
    for tx in transactions:
        key_elems = []
        for f in key_fields:
            # prefer attribute, fallback to dict-like access
            val = getattr(tx, f, None)
            if val is None and isinstance(tx, dict):
                val = tx.get(f)
            # Normalize decimals/dates to string
            if isinstance(val, Decimal):
                val = str(val)
            key_elems.append(str(val))
        key = tuple(key_elems)
        groups.setdefault(key, []).append(tx)
    # filter groups with more than one element
    duplicates = [(k, v) for k, v in groups.items() if len(v) > 1]
    return duplicates


# -------------------------
# Summaries / aggregation
# -------------------------
def summarize_transactions_by(
    transactions: Iterable[Any],
    by: str = "category",
    sum_field: str = "amount",
) -> Dict[str, Decimal]:
    """
    Summarize transactions by a chosen attribute.

    Returns dict: { group_value_str -> Decimal(sum) }
    """
    sums: Dict[str, Decimal] = collections.defaultdict(lambda: Decimal("0.00"))
    for tx in transactions:
        # get group key
        val = getattr(tx, by, None)
        if val is None and isinstance(tx, dict):
            val = tx.get(by)
        key = str(val) if val is not None else "Unspecified"
        # get amount
        amt = getattr(tx, sum_field, None)
        if amt is None and isinstance(tx, dict):
            amt = tx.get(sum_field, 0)
        try:
            dec = parse_decimal(amt)
        except Exception:
            # skip rows with invalid amount
            continue
        sums[key] += dec
    # quantize results
    return {k: v.quantize(DECIMAL_QUANT) for k, v in sums.items()}


# -------------------------
# Simple rule-based categorizer
# -------------------------
def suggest_category_rule_based(
    description: Optional[str] = None,
    counterparty: Optional[str] = None,
    merchant_id: Optional[str] = None,
    rules: Optional[Dict[str, str]] = None,
    merchant_map: Optional[Dict[str, str]] = None,
    top_k: int = 3,
) -> List[Dict[str, Any]]:
    """
    Suggest categories using simple rule-based heuristics.

    - `rules`: mapping keyword (lowercase) -> category name
    - `merchant_map`: mapping merchant_id or normalized merchant name -> category
    Returns list of suggestions: [{"category": "...", "confidence": 0.9, "matched_on": "keyword|merchant"}]

    Confidence is heuristic:
      - merchant exact match -> 0.95
      - keyword full word match -> 0.85
      - partial substring match -> 0.6
    """
    rules = rules or getattr(settings, "RULE_BASED_CATEGORY_MAP", {}) or {}
    merchant_map = merchant_map or getattr(
        settings, "MERCHANT_CATEGORY_MAP", {}) or {}

    candidates: Dict[str, float] = {}

    # merchant id / name exact match
    if merchant_id:
        key = merchant_map.get(merchant_id)
        if key:
            candidates[key] = max(candidates.get(key, 0.0), 0.95)

    # try counterparty exact
    if counterparty:
        norm = counterparty.strip().lower()
        m = merchant_map.get(norm)
        if m:
            candidates[m] = max(candidates.get(m, 0.0), 0.95)

    text = " ".join(
        filter(None, [description or "", counterparty or ""])).lower()

    # keyword matches
    for kw, cat in rules.items():
        kw_norm = kw.lower().strip()
        if not kw_norm:
            continue
        if f" {kw_norm} " in f" {text} ":
            candidates[cat] = max(candidates.get(cat, 0.0), 0.85)
        elif kw_norm in text:
            # substring match
            candidates[cat] = max(candidates.get(cat, 0.0), 0.6)

    # sort by confidence
    sorted_cands = sorted(candidates.items(),
                          key=lambda kv: kv[1], reverse=True)[:top_k]
    return [{"category": k, "confidence": float(v), "matched_on": "rule"} for k, v in sorted_cands]


# -------------------------
# Sankey payload builder
# -------------------------
def prepare_sankey_payload(
    transactions: Iterable[Any],
    source_field: str = "counterparty",
    target_field: str = "category",
    value_field: str = "amount",
    min_value: Optional[Decimal] = None,
) -> Dict[str, Any]:
    """
    Prepare nodes/links for a Sankey diagram (Google Charts style).

    Returns {"nodes": [{"name": ...}], "links": [{"source": i, "target": j, "value": v}, ...]}

    - `transactions` can be queryset or list of dicts/objects
    - `min_value` if set filters out tiny links
    """
    node_index: Dict[str, int] = {}
    links_acc: Dict[Tuple[int, int], Decimal] = collections.defaultdict(
        lambda: Decimal("0.00"))

    def _get_attr(obj, attr: str):
        val = getattr(obj, attr, None)
        if val is None and isinstance(obj, dict):
            val = obj.get(attr)
        return val

    for tx in transactions:
        src = _get_attr(tx, source_field) or "Unknown"
        tgt = _get_attr(tx, target_field) or "Uncategorized"
        amt = _get_attr(tx, value_field) or 0
        try:
            dec_amt = parse_decimal(amt)
        except Exception:
            continue
        # register nodes
        if src not in node_index:
            node_index[src] = len(node_index)
        if tgt not in node_index:
            node_index[tgt] = len(node_index)
        s_idx = node_index[src]
        t_idx = node_index[tgt]
        links_acc[(s_idx, t_idx)] += dec_amt

    # build nodes list
    nodes = [{"name": name}
             for name, _ in sorted(node_index.items(), key=lambda kv: kv[1])]
    links = []
    for (s, t), v in links_acc.items():
        if min_value is not None and v < min_value:
            continue
        links.append({"source": s, "target": t,
                     "value": float(v.quantize(DECIMAL_QUANT))})
    return {"nodes": nodes, "links": links}


# -------------------------
# File helpers
# -------------------------
def safe_save_file(uploaded: UploadedFile, dest_subdir: str = "uploads") -> str:
    """
    Save UploadedFile using Django's default storage under MEDIA_ROOT/<dest_subdir>/filename
    Returns the storage path (relative) or raises on error.

    Note: filenames are not guaranteed unique; this will prefix with timestamp if collision.
    """
    if not isinstance(uploaded, UploadedFile):
        raise ValueError("uploaded must be a Django UploadedFile instance")

    base_dir = os.path.join(
        getattr(settings, "MEDIA_ROOT", "media"), dest_subdir)
    # ensure directory exists in the storage backend if possible (some storages ignore)
    try:
        os.makedirs(base_dir, exist_ok=True)
    except Exception:
        # default_storage may still handle creating directories implicitly
        pass

    name = uploaded.name
    name = os.path.basename(name)
    dest_path = os.path.join(dest_subdir, name)
    # avoid collision by appending timestamp if necessary
    if default_storage.exists(dest_path):
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S%f")
        name_root, ext = os.path.splitext(name)
        name = f"{name_root}_{timestamp}{ext}"
        dest_path = os.path.join(dest_subdir, name)

    # stream save
    saved_path = default_storage.save(dest_path, uploaded)
    return saved_path


# -------------------------
# Misc helpers
# -------------------------
def human_readable_timedelta(dt, reference: Optional[Any] = None) -> str:
    """
    Return a short human-friendly delta between `dt` and now (or `reference` if provided).

    Examples: "3 days ago", "in 2 hours", "just now"
    """
    ref = reference or timezone.now()
    if hasattr(dt, "date") and not hasattr(dt, "tzinfo"):
        # assume naive datetime; compare as-is
        delta = ref - dt
    else:
        delta = ref - dt
    total_seconds = int(abs(delta.total_seconds()))
    if total_seconds < 5:
        return "just now"
    future = delta.total_seconds() < 0
    seconds = total_seconds
    minutes = seconds // 60
    hours = minutes // 60
    days = hours // 24
    if days > 0:
        s = f"{days} day{'s' if days != 1 else ''}"
    elif hours > 0:
        s = f"{hours} hour{'s' if hours != 1 else ''}"
    elif minutes > 0:
        s = f"{minutes} minute{'s' if minutes != 1 else ''}"
    else:
        s = f"{seconds} second{'s' if seconds != 1 else ''}"
    return f"in {s}" if future else f"{s} ago"
