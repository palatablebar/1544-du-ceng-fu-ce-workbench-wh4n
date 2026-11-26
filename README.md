# 镀层复测工作台

机加工电镀质检员用这套工作台处理电镀厚差回归树与槽位复测。进入工作台可调整本班复核额度，查看每张挂具批的预测、命中规则和处置，导出单班可复算记录。

## 本地运行

```sh
docker compose up -d --build
```

访问 `http://127.0.0.1:18643`。样例是可复算的模拟班次，涵盖正常历史、较迟回执、最新冲突版、缺测单和临时分区。

```sh
docker compose exec -T app python -m platebath.entry evaluate --capacity 8 --strategy focus
docker compose exec -T app python -m platebath.entry acceptance --feed sample_data/acceptance_feed_sample.json
docker compose exec -T app pytest -q
docker compose down --remove-orphans
```

签认后的漏判验收在「漏判验收」环节查看：晚到实绩只作验收标签，训练与派单按签发可见单据重跑防泄漏；同版冲突或缺实绩的未派单挂具算待测，按最坏情形漏判与额度比对，补测不影响结论时才提前签通过或驳回，补测重算不改原派单和凭证。

[字段和时间窗](docs/SPEC.md) · [模块与回归表](docs/DEPTH.md) · [HTTP 与自备数据](docs/RUNBOOK.md)
