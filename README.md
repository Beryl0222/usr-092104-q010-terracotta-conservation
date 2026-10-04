# 秦俑发掘保护交接后端

把**探方地层 → 出土/拆分 → 候选身份 → 残片拼合 → 环境时序 → 充氮保湿封装 →
超声/三维采集 → 修复干预 → 双方交接 → 入库位置**连成一条可回溯的事件谱系。

系统是一个只追加（append-only）的事件溯源后端：所有状态都从事件日志重放得到，
读模型实时更新，**不等四套系统日终汇总**——值班人员看到湿度越界、设备失准、
封装异常时，告警直接指向受影响的残片。

仓库现有事件版本继续作为现场数据入口（`POST /events`），
人工操作走命令式接口（`POST /commands/<name>`），二者写入同一条事件日志。

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `contracts/domain.schema.json` | 领域事件信封、事件类型与聚合类型枚举 |
| `src/contract.py` | 事件目录、稳定枚举、信封/载荷校验（单一事实源） |
| `src/store.py` | 只追加事件存储：流版本号、event_id 去重、原子批量、JSONL 持久化 |
| `src/projection.py` | 事件重放读模型：对象、关系、时序、告警、封装、交接、入库 |
| `src/service.py` | 应用服务：命令 → 事件，集中强制全部业务不变量 |
| `src/auth.py` | 角色与分级授权（职工/研究者/公众、敏感坑位、未发布发现） |
| `src/views.py` | 谱系回溯、俑体总览、值班实时告警面板、时序查询 |
| `src/api.py` | 仅依赖标准库的 HTTP 接口 |
| `scripts/walkthrough.py` | 全链路联调演示（HTTP，无需第三方依赖） |
| `data/sample.json` | 中文联调样例 |
| `tests/` | 契约、存储、不变量、授权、HTTP 共 50+ 用例 |

## 领域规则如何落地

- **时间窗与实时定位**：采样事件即时进日志并重放，`GET /alerts`
  返回每条未结告警及其 `affected_fragments`（残片、俑体、探方地层、责任方、库位）。
- **拆分→暂拼→确认拼合不丢早期检测**：残片拆分即独立成流，检测事件挂在残片自身；
  拼合只是新增一条 `tentative_join` 关系，确认只改关系状态。
- **撤销错误拼合**：不修改、不删除任何历史，追加一条 `join_revoked` 关系，
  原关系被标记为 `revoked`，完整轨迹仍可回溯。
- **断线补数**：补传事件的流版本号照常追加在末尾，但投影按**采集时间**
  `collected_at` 归位；质量标记区分 `ok` / `measured_backfill`（真实测量补传）/
  `interpolated`（插值，非原始测量）。
- **校准失效不冒充可靠测量**：采集时刻无有效校准时，即使上报 `ok` 也被
  **强制降级**为 `uncalibrated`，同时产生“设备失准”告警；
  只有 `ok` 与 `measured_backfill` 能驱动阈值告警，越界值 95% 也不会误报。
- **告警只促成人工处置**：系统从不自动生成清理/加固等 `conservation_action`；
  干预只能由修复人员 `propose → authorize → complete`。
- **异常只冻结对应对象**：湿度越界冻结被测残片，封装泄漏只冻结封装内对象，
  其他残片照常作业；冻结期间不能暂拼、封箱发运、入库。
  告警经人工签收、处置后，其带来的冻结自动解除。
- **责任变化需双方共同签署**：`HANDOFF_SIGNED` 必须同时带交出人与接收人；
  签署时处于冻结的对象**不转移**（留在交出方），其余对象责任即时变更，
  交接记录逐件标明是否转移。
- **分级授权**：

  | 角色 | 已发布资料 | 未发布发现 | 敏感坑位坐标 | 告警面板 |
  | --- | --- | --- | --- | --- |
  | duty_officer / conservator / registrar | ✔ | ✔ | ✔ | ✔ |
  | researcher（默认） | ✔ | ✘（需 `unpublished_finds` 授权） | ✘（需 `sensitive_locations` 授权） | ✘ |
  | public | ✔（公开字段） | ✘ | ✘ | ✘ |

  未发布对象对普通接口表现为“查无此项”，不泄露存在性；敏感坐标在无授权时
  返回 `null` 并标注 `location_restricted`。

## 事件目录（30 类，只允许追加）

探方地层：`TRENCH_OPENED`、`STRATUM_RECORDED`
对象：`OBJECT_LIFTED`、`FRAGMENT_DETACHED`、`EXAMINATION_RECORDED`、
`MONITORING_LIMITS_SET`、`OBJECT_HOLD_PLACED`、`OBJECT_HOLD_RELEASED`、
`OBJECT_ACCESSIONED`
候选身份：`CANDIDATE_IDENTITY_PROPOSED/CONFIRMED/REJECTED`
残片关系：`RELATION_OPENED`（detached / tentative_join / join_revoked）、
`RELATION_CONFIRMED`
监测：`INSTRUMENT_REGISTERED`、`CALIBRATION_RECORDED`、`ENVIRONMENT_SAMPLED`、
`ALERT_RAISED/ACKNOWLEDGED/RESOLVED`
封装：`PACKAGE_PREPARED/SEALED/N2_REFRESHED/STATUS_RECORDED`
修复：`ACTION_PROPOSED/AUTHORIZED/COMPLETED`
交接与入库：`HANDOFF_PREPARED/SIGNED`、`STORAGE_LOCATION_REGISTERED`

事件版本是**聚合流内**从 1 开始的严格递增序号（与业务时间无关）；
`event_id` 全局唯一去重；多聚合命令在一个原子批次内提交。

## 本地运行

```bash
python3 -m unittest discover -s tests     # 全部测试
python3 scripts/walkthrough.py           # 全链路 HTTP 演示
python3 -m src.api --port 8080 --store data/events.jsonl
```

## HTTP 接口速览

身份通过请求头表达：`X-Role: duty_officer|conservator|registrar|researcher|public`，
附加许可 `X-Scopes: unpublished_finds,sensitive_locations`。

```bash
# 现场系统原始事件入口（只做契约校验）
POST /events

# 人工命令（节选）
POST /commands/open-trench
POST /commands/lift-object
POST /commands/detach-fragment
POST /commands/record-examination
POST /commands/open-join | confirm-join | revoke-join
POST /commands/register-instrument | record-calibration
POST /commands/record-sample           # 补数/降级/告警/冻结在此联动
POST /commands/prepare-package | seal-package | record-package-status
POST /commands/propose-action | authorize-action | complete-action
POST /commands/prepare-handoff | sign-handoff
POST /commands/accession

# 查询（实时重放，非日终汇总）
GET  /alerts[?all=1]                       # 值班面板（职工）
GET  /objects?figure_id=...                # 授权过滤的对象清单
GET  /objects/{id}/lineage                 # 残片完整谱系（修复定位）
GET  /figures/{figureId}                   # 当前俑体残片与拼合状态
GET  /series/{seriesId}                    # 按采集时间归位的环境时序
GET  /trenches/{id}                        # 敏感坐标按授权裁剪
```

命令非法返回 `422`（中文错误说明违反的规则），越权读取返回 `403`，
未发布对象返回 `404`。
