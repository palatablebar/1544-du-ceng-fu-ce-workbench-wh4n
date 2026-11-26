import copy
import json
from pathlib import Path

import pytest

from platebath.acceptance_review.contracts import (GRADE_CLEAR, GRADE_HANDLED, GRADE_MISSED,
                                                   GRADE_PENDING, VERDICT_HOLD, VERDICT_PASS,
                                                   VERDICT_REJECT)
from platebath.acceptance_review.feeds import parse_feed
from platebath.acceptance_review.flow import run_acceptance, handle_acceptance
from platebath.acceptance_review.service import review_shift, acceptance_payload
from platebath.pipeline import open_fixture, preview, execute

FEED_PATH = Path(__file__).resolve().parents[1] / 'sample_data' / 'acceptance_feed_sample.json'


@pytest.fixture(scope='module')
def sealed():
    return preview()


@pytest.fixture(scope='module')
def sample_feed():
    return json.loads(FEED_PATH.read_text(encoding='utf-8'))


def grade_map(report):
    return {grade.key: grade for grade in report.grades}


def test_live_outcomes_never_leak_into_training_or_dispatch():
    # 晚到实绩即使混进原始单据，当班窗内没有成熟标签，训练和派单必须保持冻结。
    fixture = open_fixture()
    entries = [dict(row) for row in fixture['records']]
    contact = next(row for row in entries if row['key'] == 'PL-1260')
    contact['outcome'] = 2.7418
    contact['result_day'] = 98
    before = preview()
    after = execute(entries, before['capacity'], before['mode'], as_of=fixture['as_of'])
    assert after['feature_stamp'] == before['feature_stamp']
    assert after['rows'] == before['rows']
    assert after['sample_hash'] != before['sample_hash']


def test_rerun_matches_sealed_run_id_and_review_is_repeatable(sample_feed):
    fixture = open_fixture()
    sealed, review = run_acceptance(sample_feed['records'], sample_feed['allowance'],
                                    document=fixture['records'], as_of=fixture['as_of'])
    again_sealed, again = run_acceptance(sample_feed['records'], sample_feed['allowance'],
                                         document=fixture['records'], as_of=fixture['as_of'])
    assert sealed['run_id'] == review.run_id == again_sealed['run_id']
    assert review.acceptance_id == again.acceptance_id
    assert acceptance_payload(review) == acceptance_payload(again)


def test_sealed_mismatch_blocks_acceptance(sample_feed, sealed):
    tampered = copy.deepcopy(sealed)
    tampered['run_id'] = '0000000000000000'
    fixture = open_fixture()
    with pytest.raises(ArithmeticError, match='不能验收'):
        run_acceptance(sample_feed['records'], sample_feed['allowance'],
                       sealed_report=tampered, document=fixture['records'],
                       as_of=fixture['as_of'])


def test_sample_grades_cover_miss_clear_pending_and_handled(sample_feed, sealed):
    review = review_shift(sealed, sample_feed['records'], sample_feed['allowance'])
    grades = grade_map(review)
    assert grades['PL-1260'].grade == GRADE_MISSED
    assert grades['PL-1259'].grade == GRADE_MISSED
    assert grades['PL-1269'].grade == GRADE_CLEAR
    assert grades['PL-1272'].grade == GRADE_PENDING      # 同版次冲突
    assert grades['PL-1273'].grade == GRADE_PENDING      # 留架右截尾
    assert grades['PL-1257'].grade == GRADE_HANDLED      # 已分派
    assert grades['PL-1258'].grade == GRADE_HANDLED      # 暂停
    assert grades['PL-1255'].grade == GRADE_HANDLED      # 人工判读
    assert review.confirmed_misses == 3
    assert review.pending == 2
    assert review.confirmed_over_line == 3


def test_conflicting_same_revision_is_pending_and_order_independent(sample_feed, sealed):
    rows = sample_feed['records']
    first = review_shift(sealed, rows, 3)
    reordered = list(reversed(rows))
    second = review_shift(sealed, reordered, 3)
    assert grade_map(first)['PL-1272'].grade == GRADE_PENDING
    assert grade_map(second)['PL-1272'].grade == GRADE_PENDING
    assert first.acceptance_id == second.acceptance_id
    conflict_errors = [issue for issue in first.issues if issue.code == 'CONFLICTING_REVISION']
    assert {issue.key for issue in conflict_errors} == {'PL-1272'}


def test_higher_revision_supersedes_older_one(sealed):
    feed = [
        {'key': 'PL-1264', 'revision': 1, 'result_day': 99, 'received': 100, 'outcome': 2.91},
        {'key': 'PL-1264', 'revision': 2, 'result_day': 102, 'received': 103, 'outcome': 1.42},
    ]
    review = review_shift(sealed, feed, 1)
    grade = grade_map(review)['PL-1264']
    assert grade.grade == GRADE_CLEAR and grade.outcome == 1.42
    assert any(issue.code == 'OLDER_REVISION' and issue.key == 'PL-1264' for issue in review.issues)


def test_robust_verdict_worst_case_drives_early_signoff(sample_feed, sealed):
    hold = review_shift(sealed, sample_feed['records'], 3)
    assert hold.verdict == VERDICT_HOLD
    assert hold.confirmed_misses == 3 and hold.worst_case_misses == 5
    signed_off = review_shift(sealed, sample_feed['records'], 5)
    assert signed_off.verdict == VERDICT_PASS
    rejected = review_shift(sealed, sample_feed['records'], 2)
    assert rejected.verdict == VERDICT_REJECT
    # 待核时补测结果怎么变都可能翻盘；通过/驳回已不受补测影响
    assert VERDICT_PASS in signed_off.reasoning and VERDICT_REJECT in rejected.reasoning


def test_resolving_pending_recomputes_without_touching_dispatch(sample_feed, sealed):
    hold = review_shift(sealed, sample_feed['records'], 3)
    assert hold.verdict == VERDICT_HOLD
    resolved = [row for row in sample_feed['records'] if row['key'] != 'PL-1272']
    resolved.append({'key': 'PL-1272', 'revision': 3, 'result_day': 104,
                     'received': 105, 'outcome': 1.82})
    resolved.append({'key': 'PL-1273', 'revision': 3, 'result_day': 104,
                     'received': 105, 'outcome': 1.77})
    final = review_shift(sealed, resolved, 3)
    assert final.verdict == VERDICT_PASS
    assert final.pending == 0 and final.confirmed_misses == 3
    # 原派单凭证与处置行完全不变，验收只追加证据
    assert final.run_id == hold.run_id == sealed['run_id']
    assert sealed['rows'] == preview()['rows']


def test_only_released_states_can_count_as_misses(sealed):
    # 已分派/暂停/人工判读挂具即便实绩超线也不算漏判
    handled_keys = [row['key'] for row in sealed['rows'] if row['state'] != '观察'
                    and row['state'] != '候补']
    feed = [{'key': key, 'revision': 1, 'result_day': 100, 'received': 101, 'outcome': 9.9}
            for key in handled_keys]
    review = review_shift(sealed, feed, 0)
    assert review.confirmed_misses == 0
    assert all(grade.grade == GRADE_HANDLED for grade in review.grades
               if grade.key in handled_keys)


def test_outside_keys_and_parse_issues_are_quarantined(sealed):
    feed = [
        {'key': 'PL-1101', 'revision': 1, 'result_day': 99, 'received': 100, 'outcome': 2.05},
        {'key': 'PL-1260', 'revision': 1, 'result_day': 98, 'received': 99, 'outcome': 2.74},
        {'key': 'PL-1269', 'revision': 1, 'result_day': 98, 'received': 97},  # 到达早于实绩
        {'revision': 1, 'result_day': 98, 'received': 99, 'outcome': 1.0},   # 缺单号
    ]
    review = review_shift(sealed, feed, 1)
    assert review.outside == ('PL-1101',)
    codes = {(issue.key, issue.code) for issue in review.issues}
    assert ('PL-1269', 'RECEIVED_BEFORE_RESULT') in codes
    assert any(issue.code == 'TEXT_REQUIRED' for issue in review.issues)
    assert grade_map(review)['PL-1260'].grade == GRADE_MISSED


def test_censored_feed_result_stays_pending(sealed):
    feed = [{'key': 'PL-1266', 'revision': 1, 'result_day': 104,
             'received': 105, 'outcome': None, 'observed': False}]
    review = review_shift(sealed, feed, 0)
    grade = grade_map(review)['PL-1266']
    assert grade.grade == GRADE_PENDING and grade.outcome is None


def test_feed_size_and_allowance_are_validated():
    with pytest.raises(ValueError):
        parse_feed([])
    with pytest.raises(ValueError):
        parse_feed([{}] * 2501)
    report = preview()
    with pytest.raises(ValueError):
        review_shift(report, [{'key': 'PL-1260', 'result_day': 99,
                               'received': 100, 'outcome': 1.2}], -1)
    with pytest.raises(ValueError):
        review_shift(report, [{'key': 'PL-1260', 'result_day': 99,
                               'received': 100, 'outcome': 1.2}], 61)


def test_http_acceptance_payload_and_error_contract(sample_feed):
    from platebath.entry import app
    client = app.test_client()
    ok = client.post('/api/platebath/acceptance', json={
        'feed': sample_feed['records'], 'allowance': sample_feed['allowance']})
    assert ok.status_code == 200
    body = ok.get_json()
    assert body['verdict'] == VERDICT_HOLD
    assert body['sealed_run_id'] == body['run_id']
    missing = client.post('/api/platebath/acceptance', json={'allowance': 1})
    assert missing.status_code == 422
    assert missing.get_json()['code'] == 'INVALID_BATCH'
    wrong_shape = client.post('/api/platebath/acceptance', json={'feed': 'nope'})
    assert wrong_shape.status_code == 422
    unknown = client.post('/api/platebath/acceptance', json={
        'feed': sample_feed['records'], 'strange': 1})
    assert unknown.status_code == 422
    bad_json = client.post('/api/platebath/acceptance', data='{not json',
                           content_type='application/json')
    assert bad_json.status_code == 400
    # 行级隔离：畸形回执进 errors，其余单据照常验收
    quarantined = client.post('/api/platebath/acceptance',
                              json={'feed': [{'key': 'BROKEN'}] + sample_feed['records'],
                                    'allowance': sample_feed['allowance']})
    body = quarantined.get_json()
    assert quarantined.status_code == 200 and body['errors']
    assert body['errors'][0]['原因'] == 'NUMBER_REQUIRED'


def test_handle_acceptance_with_custom_records_reruns_sealed(sample_feed):
    fixture = open_fixture()
    payload = {'records': fixture['records'], 'as_of': fixture['as_of'],
               'feed': sample_feed['records'], 'allowance': 3,
               'capacity': 9, 'strategy': 'focus'}
    body = handle_acceptance(payload)
    sealed = execute(fixture['records'], 9, 'focus', as_of=fixture['as_of'])
    assert body['sealed_run_id'] == sealed['run_id']
    assert body['verdict'] == VERDICT_HOLD


def test_acceptance_evidence_is_queryable(sample_feed, sealed):
    review = review_shift(sealed, sample_feed['records'], sample_feed['allowance'])
    payload = acceptance_payload(review)
    summary = {row['核对项']: row['数量'] for row in payload['summary']}
    assert summary['签发凭证'] == sealed['run_id']
    assert summary['已确认漏判'] == 3 and summary['漏判额度'] == 3
    assert payload['tally']['最坏情形漏判'] == 5
    assert all(row['依据'] for row in payload['grades'])
