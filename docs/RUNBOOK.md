# BATH CONTROL运行手册

更换班次或复算派单时使用。先确认 `/health` 返回 `ok`，再检查以下入口和凭证中的 `run_id`。

| 文档或路由 | 用途 |
|---|---|
| `sample_data/hanger_tickets_sample.json` | 同时提供拟合、校准、回放和当班的样例 |
| `sample_data/acceptance_feed_sample.json` | 当班晚到实绩验收回执，含冲突、右截尾与非当班对象 |
| `GET /api/platebath/overview` | 默认额度和样例处置 |
| `POST /api/platebath/run` | 用 `capacity` 和 `strategy` 调整派单，也可同时提供 `records` 与 `as_of` |
| `GET /api/platebath/stages/<环节>` | 样例的分环节核对，可传相同额度、策略 |
| `POST /api/platebath/acceptance` | 晚到实绩漏判验收，入参 `feed`、`allowance`，可附 `records` 重跑 |

导入 JSON 对象时 `records` 为 1–2500 条单据，`as_of` 为整数工日；默认第 96 日。额度接受 1–60，策略接受 `balanced` 或 `focus`。入站异常保留于第一环节。整批缺少成熟窗口时响应 422 并给出原因。

验收 `feed` 为 1–2500 条晚到实绩，`allowance` 为 0–60 的漏判额度。回执只用于验收，不进入训练；重跑凭证与签发 `run_id` 不符返回 409。结论为`通过`、`驳回`或`待核`，待测挂具补测后用新回执再次调用即可重算，原派单和凭证不变。

```sh
docker compose exec -T app python -m platebath.entry evaluate --input sample_data/hanger_tickets_sample.json --capacity 9
docker compose exec -T app python -m platebath.entry acceptance --feed sample_data/acceptance_feed_sample.json
```

在网页筛选单号、作业区或原因，点击结果中的单号查看完整字段与处置证据。侧栏「导入晚到实绩验收」可载入自备回执刷新「漏判验收」环节。下载 JSON 可检视结果、对照原始资料摘要和模型特征版本。
