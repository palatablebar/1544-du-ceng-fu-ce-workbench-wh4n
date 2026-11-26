"""验收编排：把签发快照重跑和晚到实绩验收串成一次可复算调用。"""
from . import service


def _open_fixture(path=None):
    # 延迟导入避免与 pipeline 的验收段互相循环引用。
    from ..pipeline import open_fixture, PRESET
    return open_fixture(path or PRESET)


def run_acceptance(feed_rows, allowance, *, sealed_report=None, document=None,
                   capacity=12, mode='balanced', as_of=96):
    """返回 (签发报告, 验收报告)。

    sealed_report 省略时用 document（默认随附样例）按签发截止日重跑整条链路；
    feed_rows 中的晚到实绩不会出现在重跑输入里，因此无法泄漏到训练和派单。
    """
    if sealed_report is None:
        if document is None:
            fixture = _open_fixture()
            document, as_of = fixture['records'], fixture['as_of']
        sealed_report = service._sealed_run(document, as_of, capacity, mode)
    review = service.review_shift(
        sealed_report, feed_rows, allowance,
        records=document, as_of=as_of, capacity=capacity, mode=mode)
    return sealed_report, review


def open_acceptance_fixture(path):
    from json import loads
    document = loads(path.read_text(encoding='utf-8'))
    if not isinstance(document, dict) or not isinstance(document.get('records'), list):
        raise ValueError('验收回执必须包含 records 数组')
    allowance = document.get('allowance', 3)
    if type(allowance) is not int or not 0 <= allowance <= 60:
        raise ValueError('漏判额度须为 0 至 60 的整数')
    return document['records'], allowance, document.get('as_of')


def handle_acceptance(payload):
    """HTTP/CLI 共用的入参校验与响应组装。"""
    if not isinstance(payload, dict):
        raise ValueError('须为 JSON 对象')
    legal = {'records', 'as_of', 'capacity', 'strategy', 'feed', 'allowance'}
    if payload.keys() - legal:
        raise ValueError('验收字段未登记')
    capacity = payload.get('capacity', 12)
    mode = payload.get('strategy', 'balanced')
    allowance = payload.get('allowance', 3)
    feed = payload.get('feed')
    if feed is None:
        raise ValueError('验收须提供 feed 晚到实绩数组')
    if 'records' in payload:
        sealed, review = run_acceptance(
            feed, allowance, document=payload['records'], capacity=capacity,
            mode=mode, as_of=payload.get('as_of', 96))
    else:
        fixture = _open_fixture()
        sealed, review = run_acceptance(
            feed, allowance, document=fixture['records'], capacity=capacity,
            mode=mode, as_of=fixture['as_of'])
    return {'sealed_run_id': sealed['run_id'], **service.acceptance_payload(review)}
