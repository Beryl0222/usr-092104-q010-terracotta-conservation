# 秦俑现场保护交接

发掘—检测—拼合—保护—封装—运输—交接—修复—入库全链路的领域词汇、事件约定、不变量校验与只读投影。
仓库中的事件版本继续作为现场数据入口：四套现场系统只需按信封追加事件，值班视图与谱系由后端回放得出，
不必等待日终汇总。

## 目录

- `contracts/domain.schema.json`：领域事件信封、事件目录、聚合类型与稳定枚举（唯一事实源）。
- `data/sample.json`：信封联调样例（仅有基础字段，保持兼容）。
- `data/scenario.json`：串联全链路的中文场景（拆分、暂拼、误拼撤销、断线补数、校准失效、告警、冻结、双签、入库、发布）。
- `src/contract.py`：从 schema 解析事件目录与 payload 必填字段，避免双份维护。
- `src/validator.py`：信封校验 `validate_event`（现场入口既有行为）与完整静态校验 `validate_domain_event`。
- `src/store.py`：只追加事件存储与跨事件不变量（版本递增、引用存在、校准窗口、冻结、双签）。
- `src/projection.py`：值班视图、俑体→残片谱系、残片全程历史。
- `src/access.py`：conservator / researcher / public 三级授权视图与脱敏。
- `tests/`：契约一致性、规则、投影与授权测试。

## 事件目录（28 类事件，13 种聚合）

| 环节 | 事件 |
|---|---|
| 探方地层 | `UNIT_REGISTERED` `STRATUM_RECORDED` |
| 起取与身份 | `OBJECT_LIFTED` `IDENTITY_PROPOSED` `IDENTITY_RESOLVED` |
| 残片关系 | `FRAGMENT_SPLIT` `JOIN_TENTATIVE` `JOIN_CONFIRMED` `JOIN_REVOKED` |
| 环境时序 | `ENVIRONMENT_SAMPLED` `MEASUREMENT_BACKFILLED` |
| 探头校准 | `DEVICE_CALIBRATED` `DEVICE_CALIBRATION_EXPIRED` |
| 充氮保湿/封装 | `NITROGEN_HUMIDITY_RECORDED` `PACKAGING_ANOMALY_DETECTED` |
| 超声与三维采集 | `ACQUISITION_RECORDED`（modality=ultrasound/three_d） |
| 运输封装 | `PACKAGE_PREPARED` |
| 告警 | `ALERT_RAISED` `ALERT_ACKNOWLEDGED` |
| 冻结 | `OBJECT_FROZEN` `OBJECT_UNFROZEN` |
| 修复干预 | `ACTION_AUTHORIZED` `ACTION_EXECUTED` |
| 交接 | `HANDOFF_SIGNED` |
| 入库 | `OBJECT_ACCESSIONED` `LOCATION_ASSIGNED` |
| 授权与发布 | `SENSITIVITY_CHANGED` `PUBLICATION_STATUS_CHANGED` |

## 领域规则（由代码强制）

- **只追加、版本递增**：事件不可修改；同一 `aggregate_id` 的 `version` 从 1 严格递增，冲突写入被拒绝。
- **残片关系不可销毁**：拆分→暂拼→确认的全过程保留；撤销错误拼合只追加 `JOIN_REVOKED`，关系状态变为 `revoked`，
  已撤销关系不能重复撤销，确认只能针对暂拼关系。早期检测挂在残片对象上，任何拼合状态下都不丢失。
- **补数按采集时间进入**：断线恢复的测量走 `MEASUREMENT_BACKFILLED`，必须带 `quality=backfilled`，
  `measured_at`（实际采集时间）早于 `occurred_at`（入库时间），时序按 `measured_at` 排列。
- **校准失效不得冒充可靠测量**：测量前探头必须有有效校准窗口；窗口外测量禁止 `quality=reliable`，
  只能标 `suspect`/`calibration_expired`/`backfilled`。
- **告警只促成人工处置**：`ALERT_RAISED` 必须指向具体对象（湿度越界/设备失准/封装异常三类）；
  清理与加固授权要求 `human_confirmed=true`，系统或告警不能自动生成。
- **异常只冻结对应对象**：`OBJECT_FROZEN` 阻断该对象的修复授权/执行与交接，不牵连同批其他残片；
  封装可先行准备，交接与修复逐对象校验。
- **交接双签转移责任**：同一 `handoff_id` 先由交出方（relinquishing）签署，接收方（receiving）副署后
  `custody_transferred` 才成立；两方签署的对象集合必须一致，任一方不能重复签署。
- **分级授权**：公众接口只返回已发布的普通发现，未发布发现与敏感坑位（含经关系/候选间接引用）整体剔除；
  研究者可见未发布资料，但敏感坑位的探方、层位、库位等精确位置字段脱敏；文保修复人员全量可见。

## 本地检查

```bash
python3 -m unittest discover -s tests
```
