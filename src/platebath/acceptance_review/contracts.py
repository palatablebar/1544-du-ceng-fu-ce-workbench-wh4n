"""当班漏判验收的不可变契约。

验收只回答一个问题：签发时没有送去测厚的挂具里，晚到实绩有多少张实际超线。
训练、预测和派单使用签发快照重跑，验收实绩永远不进入这些环节。
"""
from dataclasses import dataclass

ACCEPTANCE_VERSION = 'platebath.acceptance.20261126.v1'
RISK_LINE = 2.3

# 签发后没有进入测厚流程、理论上可能构成漏判的处置状态。
# 已分派有实测兜底，暂停与人工判读已被强制拦下复核，均不计漏判。
RELEASED_STATES = frozenset(('观察', '候补'))

VERDICT_PASS = '通过'
VERDICT_REJECT = '驳回'
VERDICT_HOLD = '待核'

GRADE_MISSED = '漏判'
GRADE_CLEAR = '未超线'
GRADE_PENDING = '待测'
GRADE_HANDLED = '已拦截复核'
GRADE_OUTSIDE = '非当班对象'


@dataclass(frozen=True, slots=True)
class AcceptanceIssue:
    source_row: int
    key: str
    field: str
    code: str

    def evidence(self):
        return {'行号': self.source_row, '单号': self.key, '字段': self.field, '原因': self.code}


@dataclass(frozen=True, slots=True)
class TicketGrade:
    key: str
    cohort: str
    state: str
    estimate: float
    threshold: float
    outcome: float | None
    result_day: int | None
    grade: str
    basis: str
    sealed: bool

    @property
    def over_line(self):
        return self.outcome is not None and self.outcome >= self.threshold


@dataclass(frozen=True, slots=True)
class AcceptanceReport:
    run_id: str
    acceptance_id: str
    threshold: float
    allowance: int
    confirmed_misses: int
    pending: int
    confirmed_over_line: int
    graded: int
    sealed_live: int
    verdict: str
    reasoning: str
    grades: tuple[TicketGrade, ...]
    issues: tuple[AcceptanceIssue, ...]
    outside: tuple[str, ...]
    feed_hash: str

    @property
    def worst_case_misses(self):
        return self.confirmed_misses + self.pending

    def evidence_rows(self):
        return (
            {'核对项': '签发凭证', '数量': self.run_id},
            {'核对项': '厚差超线判据 μm', '数量': self.threshold},
            {'核对项': '漏判额度', '数量': self.allowance},
            {'核对项': '已确认漏判', '数量': self.confirmed_misses},
            {'核对项': '待测挂具', '数量': self.pending},
            {'核对项': '最坏情形漏判', '数量': self.worst_case_misses},
            {'核对项': '已确认实绩超线', '数量': self.confirmed_over_line},
            {'核对项': '验收结论', '数量': self.verdict},
        )
