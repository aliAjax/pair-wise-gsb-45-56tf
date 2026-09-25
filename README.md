# 港口泊位与航道调度

纯Python标准库实现的港口泊位与航道调度原型，使用SQLite持久化，HTTP接口由`http.server`提供。

## 模块结构

- `app.py`：命令行参数、依赖组装和服务启动。
- `src/domain.py`：领域数据类型、错误和基础校验。
- `src/rules.py`：状态转换、靠泊可行性、吃水安全、时间窗冲突和冲突检查。
- `src/repository.py`：SQLite建表、事务和查询。
- `src/service.py`：用例编排、权限检查、乐观并发和审计。
- `src/tides.py`：潮位资料，逐小时潮位与航道水深计算。
- `src/channel_rules.py`：航道排班判断，单船通航、危险品前后各一小时隔离、当天最近可走窗口搜索。
- `src/channel_repository.py`：潮位表、通航申请与排班事件的SQLite持久化。
- `src/channel_service.py`：潮汐通航台用例编排，申请排班、窗口确认、离港释放。
- `src/http_api.py`：HTTP路由与统一错误响应。
- `src/audit.py`：事件时间线。
- `static/index.html`：最小演示页面。
- `static/channel.html`：潮汐通航台页面。
- `tests/`：完整流程、规则计算、航道排班和失败场景测试。

## 潮汐通航台流程

1. 调度员先录入当日潮位资料（24个整点潮位+航道基准水深）。
2. 录入船舶吃水、预计过闸时刻、危险品类别后提交通航申请，系统自动排班：
   - 航道同一时间一艘船，危险品船前后各留一小时隔离；
   - 时段内最小水深减吃水不足0.5米，或时段撞上已有排班时，自动排到当天最近的可走窗口（同等距离优先顺延）；
   - 当天排不上则进入候泊名单并写明原因（水深不足或档期冲突）。
3. 调度员确认窗口后，对应航次的泊位计划才能靠泊；船舶离港时自动释放航道。

## 启动

```bash
python3 app.py --db ./data.db --port 8321
```

默认端口为`8321`，默认数据库位于项目目录。服务启动时自动建表。

## 主要接口

- `GET /health`：健康检查。
- `GET /`：演示页面。
- `GET /api/records`：记录列表，可带`state`和`limit`参数。
- `GET /api/records/{id}`：记录详情。
- `GET /api/records/{id}/audit`：审计时间线。
- `GET /api/stats`：状态统计。
- `POST /api/records`：创建记录，请求体为`{"reference":"...","data":{...}}`。
- `POST /api/records/{id}/actions/{action}`：执行业务动作，请求体为`{"expected_version":1,"data":{...}}`。
- `GET /api/tides?date=YYYY-MM-DD`：查询当日潮位与逐小时水深。
- `POST /api/tides`：录入/更新潮位，请求体为`{"date":"...","channel_depth_m":12.0,"heights":[24个潮位]}`。
- `POST /api/channel/requests`：提交通航申请并自动排班，请求体为`{"reference":"...","vessel":"...","draft_m":10.2,"date":"...","desired_hour":6,"duration_hours":1,"dangerous_class":""}`。
- `GET /api/channel/board?date=YYYY-MM-DD`：潮汐通航台（潮位、排班、候泊名单）。
- `GET /api/channel/waiting`：候泊名单及原因。
- `GET /api/channel/bookings/{id}`：通航申请详情与事件时间线。
- `POST /api/channel/bookings/{id}/actions/{confirm|release}`：确认窗口或释放航道，请求体为`{"expected_version":1}`。
- `GET /channel`：潮汐通航台页面。

除`/health`和`/`外，请求需提供`X-User-Id`、`X-Role`，可选`X-Org`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖完整流程、规则计算、重复引用、权限拒绝和版本冲突。
