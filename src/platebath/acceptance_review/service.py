"""漏判验收服务。

流程分三步：
1. 用签发时可见的单据（原始批次按签发截止日过滤）重跑训练、厚差预测和派单，
   得到与原凭证一致的冻结方案；晚到实绩在这一步完全不可见，杜绝标签泄漏。
2. 解析晚到实绩，只对当班挂具逐条定性：漏判、未超线、待测、已拦截复核。
3. 按漏判额度做稳健裁决——补测结果无论如何都不改变结论时才提前签通过或驳回，
   否则一律待核。原派单和凭证保持不变，验收只追加证据。
"""
from collections import Counter
from hashlib import blake2s

from .. import pipeline
from ..coating_disposition.service import data_fingerprint, emit_bytes
from ..retest_bench.contracts import RISK_CUTOFF
from .contracts import (ACCEPTANCE_VERSION, GRADE_CLEAR, GRADE_HANDLED, GRADE_MISSED,
                        GRADE_PENDING, RELEASED_STATES, AcceptanceIssue,
                        AcceptanceReport, TicketGrade, VERDICT_PASS, VERDICT_REJECT,
                        VERDICT_HOLD)
from .feeds import parse_feed


def _sealed_run(records, as_of, capacity, mode):
    """用签发可见单据重跑整条链路；验收回执不在 records 内，无法影响模型。"""
    report = pipeline.execute(records, capacity, mode, as_of=as_of)
    return report


def verify_frozen(report, expected_run_id):
    if report['run_id'] != expected_run_id:
        raise ArithmeticError(
            f'重跑凭证 {report["run_id"]} 与签发凭证 {expected_run_id} 不一致，不能验收')
    return report['run_id']


def _latest_feed_by_key(feed):
    """每个单号取最高版次；同版次出现不同读数按冲突处理，保留全部行号。"""
    grouped = {}
    for result in feed:
        grouped.setdefault(result.key, []).append(result)
    selected, conflicts = {}, []
    for key, rows in grouped.items():
        highest = max(row.revision for row in rows)
        current = [row for row in rows if row.revision == highest]
        signatures = {row.signature() for row in current}
        if len(signatures) > 1:
            conflicts.extend(AcceptanceIssue(row.row_no, key, 'revision', 'CONFLICTING_REVISION')
                             for row in current)
            continue
        chosen = min(current, key=lambda row: (row.received, row.row_no))
        for dropped in rows:
            if dropped.row_no != chosen.row_no:
                conflicts.append(AcceptanceIssue(
                    dropped.row_no, key, 'revision',
                    'OLDER_REVISION' if dropped.revision != highest else 'REPEATED_REVISION'))
        selected[key] = chosen
    return selected, conflicts


def _grade_ticket(row, latest, threshold):
    key, state, estimate = row['key'], row['state'], row['estimate']
    base = dict(key=key, cohort=row['zone'], state=state, estimate=estimate,
                threshold=threshold, sealed=True)
    if state not in RELEASED_STATES:
        return TicketGrade(outcome=None, result_day=None, grade=GRADE_HANDLED,
                           basis='签发时已拦截并安排复核，不纳入漏判', **base)
    result = latest.get(key)
    if result is None:
        return TicketGrade(outcome=None, result_day=None, grade=GRADE_PENDING,
                           basis='尚无晚到实绩，挂具未派单，结果未知', **base)
    if not result.observed:
        return TicketGrade(outcome=None, result_day=result.result_day, grade=GRADE_PENDING,
                           basis='实绩仍为留架右截尾，需补测后定性', **base)
    outcome, result_day = result.outcome, result.result_day
    if outcome >= threshold:
        return TicketGrade(outcome=outcome, result_day=result_day, grade=GRADE_MISSED,
                           basis=f'晚到实绩 {outcome:g} μm ≥ {threshold:g}，签发时未派单', **base)
    return TicketGrade(outcome=outcome, result_day=result_day, grade=GRADE_CLEAR,
                       basis=f'晚到实绩 {outcome:g} μm 未超线', **base)


def _verdict(confirmed_misses, pending, allowance):
    if confirmed_misses > allowance:
        return VERDICT_REJECT, (
            f'已确认漏判 {confirmed_misses} 张已超过额度 {allowance} 张，补测无法翻案，驳回')
    if confirmed_misses + pending <= allowance:
        return VERDICT_PASS, (
            f'最坏情形漏判 {confirmed_misses + pending} 张仍在额度 {allowance} 张内，补测不影响结论，通过')
    return VERDICT_HOLD, (
        f'已确认漏判 {confirmed_misses} 张，另有 {pending} 张待测；'
        f'若全部超线将达 {confirmed_misses + pending} 张、突破额度 {allowance} 张，待补测后重算')


def _acceptance_hash(run_id, allowance, feed_hash, verdict_tuple):
    contract = {'rule': ACCEPTANCE_VERSION, 'run_id': run_id, 'allowance': allowance,
                'feed': feed_hash, 'grades': verdict_tuple}
    return blake2s(emit_bytes(contract), digest_size=8).hexdigest()


def review_shift(sealed_report, feed_rows, allowance, *, records=None, as_of=None,
                 capacity=None, mode=None):
    """对一份已签发班次做漏判验收。

    sealed_report 为签发凭证；若提供 records/as_of/capacity/mode，则先按签发可见
    单据重跑并核对凭证一致性，保证验收依据可复算。
    """
    if type(allowance) is not int or not 0 <= allowance <= 60:
        raise ValueError('漏判额度须为 0 至 60 的整数')
    run_id = sealed_report['run_id']
    if records is not None:
        rerun = _sealed_run(records, as_of,
                            sealed_report['capacity'] if capacity is None else capacity,
                            sealed_report['mode'] if mode is None else mode)
        verify_frozen(rerun, run_id)
    threshold = RISK_CUTOFF
    feed, parse_issues = parse_feed(feed_rows)
    latest, version_issues = _latest_feed_by_key(feed)
    live_keys = {row['key'] for row in sealed_report['rows']}
    outside = tuple(sorted(set(latest) - live_keys))
    grades = tuple(_grade_ticket(row, latest, threshold) for row in sealed_report['rows'])
    counts = Counter(grade.grade for grade in grades)
    confirmed_misses = counts[GRADE_MISSED]
    pending = counts[GRADE_PENDING]
    verdict, reasoning = _verdict(confirmed_misses, pending, allowance)
    feed_hash = data_fingerprint([dict(row) for row in feed_rows])
    grade_tuple = tuple((g.key, g.state, g.outcome, g.grade) for g in grades)
    acceptance_id = _acceptance_hash(run_id, allowance, feed_hash, grade_tuple)
    return AcceptanceReport(
        run_id=run_id, acceptance_id=acceptance_id, threshold=threshold, allowance=allowance,
        confirmed_misses=confirmed_misses, pending=pending,
        confirmed_over_line=sum(1 for g in grades if g.over_line),
        graded=len(grades), sealed_live=len(live_keys), verdict=verdict, reasoning=reasoning,
        grades=grades, issues=parse_issues + tuple(version_issues),
        outside=outside, feed_hash=feed_hash)


def acceptance_payload(report):
    """工作台/API 使用的 JSON 结构，原派单内容不改动，只追加验收段。"""
    grade_rows = [{
        '单号': g.key, '作业区': g.cohort, '签发处置': g.state, '预测厚差': g.estimate,
        '晚到实绩μm': g.outcome, '实绩工日': g.result_day, '定性': g.grade, '依据': g.basis,
    } for g in report.grades]
    return {
        'sealed_run_id': report.run_id,
        'run_id': report.run_id,
        'acceptance_id': report.acceptance_id,
        'rule': ACCEPTANCE_VERSION,
        'verdict': report.verdict,
        'reasoning': report.reasoning,
        'feed_hash': report.feed_hash,
        'summary': list(report.evidence_rows()),
        'tally': {'漏判': report.confirmed_misses, '待测': report.pending,
                  '最坏情形漏判': report.worst_case_misses,
                  '已确认实绩超线': report.confirmed_over_line,
                  '未超线': sum(1 for g in report.grades if g.grade == GRADE_CLEAR),
                  '已拦截复核': sum(1 for g in report.grades if g.grade == GRADE_HANDLED)},
        'grades': grade_rows,
        'outside_keys': list(report.outside),
        'errors': [issue.evidence() for issue in report.issues],
    }
