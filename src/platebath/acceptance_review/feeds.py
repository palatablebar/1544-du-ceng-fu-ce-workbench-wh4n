"""晚到实绩解析。

验收回执与原始单据分开导入：它只携带单号、版次、实绩到达工日和厚差读数。
解析失败的行隔离并保留原因，绝不回灌训练窗。
"""
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from unicodedata import normalize

from .contracts import AcceptanceIssue

OUTCOME_MIN, OUTCOME_MAX = 0.0, 10000.0


@dataclass
class FeedError(Exception):
    field: str
    code: str


def _text(value, field):
    if not isinstance(value, str):
        raise FeedError(field, 'TEXT_REQUIRED')
    text = normalize('NFKC', value).strip()
    if not text or len(text) > 64 or not text.isprintable():
        raise FeedError(field, 'TEXT_INVALID')
    return text


def _integer(value, field, low, high):
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise FeedError(field, 'NUMBER_REQUIRED')
    if isinstance(value, str) and len(value) > 32:
        raise FeedError(field, 'NUMBER_INVALID')
    try:
        parsed = Decimal(str(value).strip())
    except InvalidOperation:
        raise FeedError(field, 'NUMBER_INVALID')
    if not parsed.is_finite() or not low <= parsed <= high or parsed != parsed.to_integral_value():
        raise FeedError(field, 'WHOLE_REQUIRED')
    return int(parsed)


def _outcome(value, field):
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise FeedError(field, 'NUMBER_REQUIRED')
    try:
        parsed = Decimal(str(value).strip())
    except InvalidOperation:
        raise FeedError(field, 'NUMBER_INVALID')
    if not parsed.is_finite() or not OUTCOME_MIN <= float(parsed) <= OUTCOME_MAX:
        raise FeedError(field, 'NUMBER_RANGE')
    return float(parsed)


class LateResult:
    __slots__ = ('row_no', 'key', 'revision', 'result_day', 'received', 'outcome', 'observed')

    def __init__(self, row_no, key, revision, result_day, received, outcome, observed):
        self.row_no = row_no
        self.key = key
        self.revision = revision
        self.result_day = result_day
        self.received = received
        self.outcome = outcome
        self.observed = observed

    def signature(self):
        return (self.key, self.revision, self.result_day, self.received, self.outcome, self.observed)


def parse_feed(rows):
    if not isinstance(rows, (tuple, list)) or not 1 <= len(rows) <= 2500:
        raise ValueError('验收回执需要 1 至 2500 条晚到实绩')
    accepted, issues = [], []
    for row_no, raw in enumerate(rows, start=1):
        if not isinstance(raw, Mapping):
            issues.append(AcceptanceIssue(row_no, '', 'row', 'OBJECT_REQUIRED'))
            continue
        fallback_key = str(raw.get('key', ''))[:64] if isinstance(raw, Mapping) else ''
        try:
            key = _text(raw.get('key'), 'key')
            revision = _integer(raw.get('revision', 1), 'revision', 1, 1000)
            result_day = _integer(raw.get('result_day'), 'result_day', 0, 3650)
            received = _integer(raw.get('received', result_day), 'received', 0, 3650)
            if received < result_day:
                raise FeedError('received', 'RECEIVED_BEFORE_RESULT')
            observed = raw.get('observed', True)
            if type(observed) is not bool:
                raise FeedError('observed', 'INVALID_CENSOR_MARKER')
            outcome = None
            if observed:
                outcome = _outcome(raw.get('outcome'), 'outcome')
        except FeedError as error:
            issues.append(AcceptanceIssue(row_no, fallback_key, error.field, error.code))
            continue
        accepted.append(LateResult(row_no, key, revision, result_day, received, outcome, observed))
    return tuple(accepted), tuple(issues)
