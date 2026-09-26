# 储能环 LTL 联锁复核服务

对联锁规程（有限 Kripke 结构：2–24 个唯一位置 + 带唯一标识的有向切换 + 初态 +
每个位置成立的原子命题）在指定初态下的**每一条无限执行**校验 LTL 放行时序条件。
结论只有两种：

- **holds**：所有从初态出发的无限执行都满足公式；
- **violated**：返回一条"前缀 + 重复闭环"（lasso）的违规执行，含每步位置与切换，
  以及公式每个子式在轨迹每个位置上的真值证据。

## 求解方法（无回放深度、无随机采样、非仅状态标签检查）

1. **规范化**：公式解析后转换为否定范式（NNF），引入 release 算子 `R`
   （`F φ ≡ true U φ`，`G φ ≡ false R φ`）。
2. **否定公式的广义 Büchi 自动机（GBA）**：对 `¬φ` 的 NNF 采用
   Gerth–Peled–Vardi–Wolper tableau 构造 GBA，每个 `U` 子式对应一个接受集。
3. **乘积**：GBA 与规程图做同步乘积——乘积状态 `(位置, 自动机状态)` 要求位置的
   命题标签满足自动机状态的正/负文字。
4. **可达接受环**：在可达乘积图上做 Tarjan 强连通分解，找到与**每个**接受集相交的
   非平凡 SCC，即得可达接受环；由它构造 lasso 反例（BFS 前缀 + 覆盖所有接受集的闭环）。
   不存在这样的环 ⟺ 公式成立（完备性来自 GBA 构造，而非截断回放）。
5. **证据**：在 lasso 上以精确的最小/最大不动点求值每个子式（`F`/`U` 最小不动点、
   `G` 最大不动点），即对无限展开轨迹的精确语义，而非有限前缀近似。

唯一的资源上限是自动机状态数（默认 20000，防止病态公式拖垮服务），
它不是回放深度：自动机一旦建成即被完整分析。

## 构建与启动

```bash
docker compose up --build app          # 默认 http://localhost:8080
APP_PORT=9090 docker compose up --build app   # 端口可调（宿主机侧）
```

- 容器内服务监听 `PORT`（默认 8000），宿主机端口由 `APP_PORT` 调节。
- 健康检查：`GET /healthz`（Dockerfile 与 Compose 均已配置）。

## 验收（verify 服务）

```bash
docker compose up --build --exit-code-from verify verify
echo $?        # 0 = 验收通过，非 0 = 失败
```

`verify` 等待 `app` 健康后依次执行，并以退出码报告结果：

1. **构建检查**：`python -m compileall src tests smoke.py`；
2. **代码测试**：`python -m unittest discover -s tests -t . -v`
   （解析/NNF/GBA、乘积与接受环、lasso 证据一致性，核心场景是
   "永不报警的违规闭环"——`F alarm` 被无限期推迟）；
3. **HTTP 冒烟**：`python smoke.py`，对运行中的服务验证
   违规闭环反例、成立结论、定位拒绝且不生成审计、404 等。

## API

### `POST /reviews`

```json
{
  "positions": ["normal", "degraded", "alerting"],
  "transitions": [
    {"id": "t1", "from": "normal", "to": "degraded"},
    {"id": "t2", "from": "degraded", "to": "normal"},
    {"id": "t3", "from": "normal", "to": "alerting"},
    {"id": "t4", "from": "alerting", "to": "normal"},
    {"id": "t5", "from": "degraded", "to": "degraded"}
  ],
  "initial": "normal",
  "labels": {"alerting": ["alarm"]},
  "formula": "F alarm"
}
```

- `201`：`{"id": "…", "status": "violated", "review": "/reviews/…"}`，编号已保存；
- `400`：`{"error": …, "details": [...]}`，每个问题带定位信息
  （字段、下标、切换标识、位置名或公式偏移），**不生成任何审计记录**。

### `GET /reviews/{id}`

返回保存的复核记录。`result.status` 为 `holds` 或 `violated`；违规时
`result.counterexample` 含：

- `prefix`：位置序列（首步 `via_transition` 为 `null`，其余步带切换标识）；
- `loop`：`{transition, position}` 序列，最后一个位置即闭环起点，无限重复；
- `evidence`：轨迹每个索引处的位置、阶段（prefix/loop）与每个子式的真值；
  公式本身在索引 0 处必为 `false`。

### 其他

- `GET /reviews`：已保存的审计编号列表；
- `GET /healthz`：健康检查。

## 公式语法

```
or   ::= and ("|" and)*
and  ::= until ("&" until)*
until::= unary ("U" until)?        # 右结合
unary::= "!" unary | "X" unary | "F" unary | "G" unary | "(" or ")" | 命题
```

优先级：`! X F G` > `U` > `&` > `|`。`X`、`F`、`G`、`U` 为保留关键字；
公式只能使用在各位置 `labels` 中声明过的原子命题。

## 定位拒绝示例

- 悬空端点：`{"field": "transitions[0].from", "message": "dangling endpoint: position 'x' is not declared", "transition_id": "t1", "position": "x"}`
- 死端位置：`{"message": "dead-end position: 'b' has no outgoing transition", "position": "b"}`
- 非法公式：`{"field": "formula", "message": "unexpected end of formula, expected ')'", "offset": 8}`
- 未声明命题：`{"field": "formula", "message": "formula uses undeclared atomic propositions", "propositions": ["chaos"]}`

## 项目结构

```
src/ltl.py         解析器、否定范式、GBA（tableau）构造
src/modelcheck.py  乘积、Tarjan SCC 可达接受环、lasso 反例与证据求值
src/validate.py    请求校验（定位错误，拒绝即不留审计）
src/server.py      HTTP 接口（仅标准库）
tests/             单元测试（含"永不报警"违规闭环）
smoke.py           HTTP 冒烟验收
Dockerfile / compose.yaml
```
