# ml_service/app/utils.py
"""
Text preprocessing utilities for the ML service.

Provides:
 - normalize_text(text) -> str
 - normalize_merchant(merchant) -> str
 - parse_amount_from_text(text) -> (amount: Optional[float], currency: Optional[str])
 - tokenize(text) -> List[str]
 - extract_features(text) -> dict

These utilities are lightweight and dependency-free (only stdlib).
They are intentionally conservative: useful for feature extraction and
for simple rule-based heuristics (merchant normalization, amount extraction).
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Optional, Tuple, Dict

# Try to use unidecode for better transliteration if available; fall back to unicodedata.
try:
    from unidecode import unidecode as _unidecode  # type: ignore
except Exception:
    _unidecode = None  # type: ignore

# common company suffixes to strip when normalizing merchant/store names
_COMPANY_SUFFIXES = (
    r'\b(llc|ltd|limited|inc|incorporated|co\.?|corp\.?|corporation|gmbh|sarl|sa|oy|oü|ab|pte|ag|sp\.?z\.?o\.?o\.?)\b'
)
_SUFFIX_RE = re.compile(_COMPANY_SUFFIXES, flags=re.I)

# currency symbol pattern
_CURRENCY_SYMBOL_RE = re.compile(r'(?P<symbol>[$€£¥₽₴])')

# amount-like patterns: support $12.34, 12,34, 1 234,56, 1,234.56, etc.
_AMOUNT_RE = re.compile(
    r'(?P<symbol>[$€£¥₽₴])?\s*'
    r'(?P<number>'
    # thousand grouped: "1,234" or "1 234"
    r'(?:\d{1,3}(?:[ ,.\u00A0]\d{3})+)'
    r'|(?:\d+[.,]\d+)'                      # decimal like 12.34 or 12,34
    r'|(?:\d+)'                             # integer like 1234
    r')'
)

# tokens considered as "transfer/refund/recurring" hints
_TRANSFER_KEYWORDS = {"transfer", "refund", "reversal",
                      "chargeback", "transferencia", "perevod"}
RECURRING_KEYWORDS = {"monthly", "monthly payment",
                      "recurring", "subscription", "subscr", "autopay"}


def _to_ascii(text: str) -> str:
    """Convert unicode text to closest ASCII representation (best-effort)."""
    if _unidecode:
        return _unidecode(text)
    # fallback: decompose and remove diacritics
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_text(text: str) -> str:
    """
    Normalize free text: trim, lower, transliterate to ascii, remove control chars,
    collapse whitespace and remove many punctuation characters (keeping .,/- when useful).

    Examples:
        >>> normalize_text("  Starbucks - Purchase #1234  ")
        'starbucks - purchase 1234'
    """
    if not text:
        return ""

    # transliterate to ascii
    txt = _to_ascii(text)

    # unify newlines and control characters
    txt = re.sub(r'[\r\n\t]+', ' ', txt)

    # replace common separators with spaces but keep .,/- and digits/currency characters
    # remove weird punctuation
    # keep percent sign and currency symbols? we remove currency symbols later when parsing numbers
    txt = re.sub(r'[“”«»„…•·°©®★☆†‡®]', ' ', txt)

    # Normalize quotes and dashes
    txt = txt.replace('—', '-').replace('–', '-').replace('“',
                                                          '"').replace('”', '"').replace("’", "'")

    # Remove most punctuation except these: . , - / #
    txt = re.sub(r"[!\"$%&'()*+:;<=>?@\[\]^_`{|}~]", " ", txt)

    # collapse multiple spaces
    txt = re.sub(r'\s+', ' ', txt).strip()

    # lowercase
    txt = txt.lower()
    return txt


def normalize_merchant(merchant: str) -> str:
    """
    Normalize merchant/store names by:
     - transliteration
     - lowercasing + trimming
     - removing common company suffixes (Inc, LLC, OOO, GmbH, etc.)
     - collapsing punctuation

    Examples:
        >>> normalize_merchant("Starbucks, Inc.")
        'starbucks'
        >>> normalize_merchant("ООО Ромашка")
        'romashka'
    """
    if not merchant:
        return ""

    txt = _to_ascii(merchant).strip()
    txt = txt.lower()

    # remove punctuation except internal & and slash/dash
    txt = re.sub(r'[^\w\s&/-]', ' ', txt)

    # remove company suffixes
    txt = _SUFFIX_RE.sub(' ', txt)

    # remove common words like 'the'
    txt = re.sub(r'\b(the|company|store|shop|office)\b', ' ', txt)

    # collapse whitespace and strip
    txt = re.sub(r'\s+', ' ', txt).strip()
    return txt


def _parse_amount_string(num_str: str) -> Optional[float]:
    """
    Parse a numeric string with possible thousands separators and decimal comma/dot.
    Heuristics:
     - If both ',' and '.' present: the rightmost of them is decimal separator.
     - If only one of them present:
         - if separator occurs within last 3 characters -> decimal separator
         - else -> thousands separator
     - Spaces and non-breaking spaces are treated as thousand separators.

    Returns float or None on failure.
    """
    s = num_str.strip()
    if not s:
        return None

    # normalize NBSP
    s = s.replace('\u00A0', ' ')
    # remove spaces (they are thousands separators) but first decide decimals
    has_dot = '.' in s
    has_comma = ',' in s

    try:
        if has_dot and has_comma:
            # rightmost separator is decimal
            if s.rfind('.') > s.rfind(','):
                dec = '.'
                thou = ','
            else:
                dec = ','
                thou = '.'
            s_clean = s.replace(thou, '').replace(dec, '.')
        elif has_comma and not has_dot:
            # decide if comma is decimal (e.g., "12,34") or thousands ("1,234")
            if len(s) - s.rfind(',') - 1 in (1, 2):  # comma followed by 1-2 decimals
                s_clean = s.replace(' ', '').replace(',', '.')
            else:
                s_clean = s.replace(',', '')
        else:
            # only dots or no separators
            s_clean = s.replace(' ', '')
        # finally remove any non-digit/non-dot signs
        s_clean = re.sub(r'[^\d\.-]', '', s_clean)
        if s_clean in ('', '-', '.', '-.'):
            return None
        return float(s_clean)
    except Exception:
        return None


def parse_amount_from_text(text: str) -> Tuple[Optional[float], Optional[str]]:
    """
    Try to find the first money amount in the text and return (amount, currency_symbol)
    Currency symbol may be None if not present. amount is float or None.

    Examples:
        >>> parse_amount_from_text("Starbucks $4.50 latte")
        (4.5, '$')
        >>> parse_amount_from_text("Payment 1 234,56 EUR")
        (1234.56, None)
    """
    if not text:
        return None, None

    # quick search for currency symbol near a number
    m = _AMOUNT_RE.search(text)
    if not m:
        return None, None

    symbol = m.groupdict().get('symbol')
    number = m.groupdict().get('number')
    amount = _parse_amount_string(number) if number else None
    return amount, symbol


# simple stop words for tokenization (small set, extend as needed)
_STOPWORDS = {
    "the", "and", "of", "for", "to", "at", "in", "on", "a", "an", "from", "by", "via", "with"
}


def tokenize(text: str, remove_stopwords: bool = True) -> List[str]:
    """
    Tokenize normalized text into tokens (words and numbers).
    Very lightweight: splits on whitespace and strips punctuation.

    Examples:
        >>> tokenize("starbucks purchase #1234 $4.5")
        ['starbucks', 'purchase', '1234', '4.5']
    """
    if not text:
        return []

    txt = normalize_text(text)

    # replace punctuation we left for token separation
    txt = re.sub(r'[.,;:()\[\]{}"\'<>]', ' ', txt)
    tokens = [t.strip() for t in txt.split() if t.strip()]

    if remove_stopwords:
        tokens = [t for t in tokens if t not in _STOPWORDS]

    return tokens


def _heuristic_merchant_candidate(text: str) -> Optional[str]:
    """
    Attempt to extract a merchant-like substring from transaction description.

    Heuristics:
    - Many bank/pos lines have pattern: MERCHANT * ID or MERCHANT - PURCHASE ...
      we take the first token-sequence before keywords like 'purchase', 'payment', 'pos', 'ref', 'id', 'on', 'at'.
    - If the text contains a slash "/" commonly merchant name appears before it.
    - Fallback: return first 2 tokens joined.
    """
    if not text:
        return None

    txt = normalize_text(text)

    # split by common delimiters
    parts = re.split(r'[/\-:|@]', txt)
    # prefer earliest part that looks like letters (not just numeric)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # chop off trailing keywords
        part = re.split(
            r'\b(purchase|payment|pos|ref|id|visa|mastercard|credit|debit|auth)\b', part, flags=re.I)[0]
        # remove stray amounts
        part = re.sub(r'\d+[\d.,\s]*', ' ', part).strip()
        if part and re.search(r'[a-zA-Z]', part):
            # return first 3 significant words
            tokens = [t for t in part.split() if t not in _STOPWORDS]
            if not tokens:
                continue
            candidate = " ".join(tokens[:3])
            return normalize_merchant(candidate)
    # fallback: first 2 tokens
    tokens = [t for t in tokenize(txt, remove_stopwords=True)]
    if not tokens:
        return None
    return normalize_merchant(" ".join(tokens[:2]))


def extract_features(text: str) -> Dict[str, object]:
    """
    Extract a small set of useful features from raw transaction text.

    Returns dictionary with keys:
     - normalized_text: str
     - tokens: List[str]
     - token_count: int
     - amount: Optional[float]
     - currency: Optional[str]
     - has_currency_symbol: bool
     - has_digits: bool
     - is_transfer_like: bool
     - is_recurring_like: bool
     - merchant_candidate: Optional[str]

    Examples:
        >>> extract_features("STARBUCKS 12345 $4.50")
        {
            'normalized_text': 'starbucks 12345 $4.50',
            'tokens': ['starbucks', '12345', '4.50'],
            'token_count': 3,
            'amount': 4.5,
            'currency': '$',
            'has_currency_symbol': True,
            'has_digits': True,
            'is_transfer_like': False,
            'is_recurring_like': False,
            'merchant_candidate': 'starbucks'
        }
    """
    normalized = normalize_text(text or "")
    tokens = tokenize(normalized, remove_stopwords=True)
    amount, currency = parse_amount_from_text(text or "")

    has_digits = any(bool(re.search(r'\d', t)) for t in tokens)
    has_currency_symbol = bool(currency)

    lowered = normalized.lower()
    is_transfer_like = any(k in lowered for k in _TRANSFER_KEYWORDS)
    is_recurring_like = any(k in lowered for k in RECURRING_KEYWORDS)

    merchant_candidate = _heuristic_merchant_candidate(text or "")

    features = {
        "normalized_text": normalized,
        "tokens": tokens,
        "token_count": len(tokens),
        "amount": amount,
        "currency": currency,
        "has_currency_symbol": has_currency_symbol,
        "has_digits": has_digits,
        "is_transfer_like": is_transfer_like,
        "is_recurring_like": is_recurring_like,
        "merchant_candidate": merchant_candidate,
    }
    return features


# Public API
__all__ = [
    "normalize_text",
    "normalize_merchant",
    "parse_amount_from_text",
    "tokenize",
    "extract_features",
]
