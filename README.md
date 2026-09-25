# 港口泊位与航道调度

纯Python标准库实现的港口泊位与航道调度原型，使用SQLite持久化，HTTP接口由`http.server`提供。

## 模块结构

- `app.py`：命令行参数、依赖组装和服务启动。
- `src/domain.py`：领域数据类型、错误和基础校验。
- `src/tides.py`：潮位资料，当日逐时潮位、航道水深与可走小时计算。
- `src/scheduler.py`：排班判断，单船通行、危险品隔离、最近可走窗口与候泊原因。
- `src/rules.py`：状态转换、靠泊可行性、吃水安全、时间窗冲突和冲突检查。
- `src/repository.py`：SQLite建表、事务和查询（含航道占用查询）。
- `src/service.py`：用例编排、权限检查、乐观并发、潮汐通航台视图和审计。
- `src/http_api.py`：HTTP路由与统一错误响应。
- `src/audit.py`：事件时间线。
- `static/index.html`：潮汐通航台演示页面。
- `tests/`：完整流程、规则计算、潮汐排班、候泊释放和失败场景测试。

## 计划流程

`draft` → `confirm`（确认引航）→ `schedule`（潮汐排班）→ `confirm_window`（调度员确认窗口）→ `berth`（靠泊）→ `depart`（离港并释放航道）。

排班规则：

- 航道同一时间一艘船，单次过闸占用一个整点。
- 危险品船前后各留一小时隔离。
- 申请时段水深不足（吃水+0.5m富余水深 > 基准水深+潮位）或与在航计划冲突时，自动调整至当天最近可走窗口。
- 当天无可走窗口时进入`waiting`候泊名单并记录原因；航道释放后可重新`schedule`。
- 调度员`confirm_window`确认窗口后计划才能`berth`；`depart`或`cancel`即释放航道。

## 启动

```bash
python3 app.py --db ./data.db --port 8321
```

默认端口为`8321`，默认数据库位于项目目录。服务启动时自动建表。

## 主要接口

- `GET /health`：健康检查。
- `GET /`：潮汐通航台演示页面。
- `GET /api/records`：记录列表，可带`state`和`limit`参数。
- `GET /api/records/{id}`：记录详情。
- `GET /api/records/{id}/audit`：审计时间线。
- `GET /api/stats`：状态统计。
- `GET /api/tides`：当日潮位与航道水深，可带`draft`参数计算可走时段。
- `GET /api/channel`：潮汐通航台，含潮位、航道占用和候泊名单。
- `POST /api/records`：创建记录，请求体为`{"reference":"...","data":{...}}`，`data`需包含`vessel`、`berth`、`draft_m`、`transit_hour`（预计过闸时刻）、`dangerous_goods`、`dangerous_class`（危险品船必填）等。
- `POST /api/records/{id}/actions/{action}`：执行业务动作（`confirm`/`schedule`/`confirm_window`/`berth`/`depart`/`cancel`），请求体为`{"expected_version":1,"data":{...}}`。

除`/health`和`/`外，请求需提供`X-User-Id`、`X-Role`，可选`X-Org`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖完整流程、规则计算、潮汐排班（最近窗口、危险品隔离、候泊与航道释放）、重复引用、权限拒绝和版本冲突。
