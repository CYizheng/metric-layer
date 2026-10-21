# 指标口径层 · metric-layer

自然语言取数最容易出的错，**不是 SQL 写错，而是同一个词在不同人嘴里指不同东西**。

「投递数」是投过就算，还是去掉已放弃的？「在流程中」包不包括泡池子？
不把口径写下来，每次问出来的数都不一样，而且没人能证明哪个对。

这个工具把口径做成**声明式的一层**：指标叫什么、怎么算、有哪些同义词、
时间字段是哪个、状态枚举有哪几种。然后自然语言问答按这一层翻译成 SQL，
**只读执行**，并把译文和口径一起打出来供审阅。

```
$ python nlq.py -d data/demo.db "近30天面试场次按月"
问：近30天面试场次按月
  解析：指标=面试场次 · 分组=month · 时间=近 30 天
  口径：数的是事件流里的面试事件，一家单位可能面多轮，所以在这里 JOIN events。

  月份     面试场次
  ───────  ────────
  2026-09         6
  2026-10        10

  2 行 · 0.4 ms
```

---

## 一句问句怎么变成 SQL

```
自然语言
   ↓  ① 口径层命中（词典，确定性）
指标 · 维度 · 状态过滤 · 时间范围 · 排序限额
   ↓  ② 只从声明里拼装片段（不拼接任何用户原文）
SQL
   ↓  ③ 三层只读守卫
结果 + 译文 + 口径说明
```

**关键设计：规则优先，模型兜底。**

词表是封闭的——指标名、维度名、状态值都写在 yaml 里。用词典命中就够了，
结果可预测、可测试、可以被人 review。模型（可选 Ollama）只处理规则层覆盖不到的
复杂句，而且每一条输出都必须过守卫、真跑一次，失败就报错回退。

把「能不能查对」押在 3B 模型上，是这个工具最不该做的设计。

---

## 只读是三层，不是一句 `if`

| 层 | 手段 | 兜住什么 |
|---|---|---|
| 连接层 | `file:...?mode=ro` + `PRAGMA query_only=ON` | 即使判断逻辑被绕过，也写不进去 |
| 语句层 | 必须单条 `SELECT`/`WITH`；禁用 `INSERT/UPDATE/DELETE/DROP/ALTER/ATTACH/PRAGMA/VACUUM` 等 | 注入与误操作 |
| 结果层 | 没有 `LIMIT` 就自动补；`fetchmany(max+1)` 判断截断 | 一条误写把内存打爆 |

判断前先**剥掉注释和字符串字面量**，否则字段值里出现 `drop` 就会被误杀：

```python
# 这两条都该放行
SELECT * FROM t WHERE note = 'drop table now'
SELECT 1  -- drop table x
```

---

## 口径怎么声明

```yaml
metrics:
  - key: active
    name: 在流程中
    aliases: [在流程中, 流程中的, 还在跑, 没进行中, 进行中]
    expr: "SUM(CASE WHEN a.status = '流程中' THEN 1 ELSE 0 END)"
    unit: 家
    note: 只含状态为「流程中」的，泡池子不计入——泡池子另有一档。

dimensions:
  - key: month
    name: 月份
    column: "substr(a.applied_at, 1, 7)"
    aliases: [月份, 按月, 每月, 各月]
    time_grain: true     # 按自身升序排，不按指标大小排
```

`note` 会跟结果一起打出来。**数对不对先看口径**——这是这个工具唯一想改变的习惯。

---

## 踩过的坑（都还留在代码里当注释）

**`on:` 在 YAML 里不是字符串。** 事件表的关联条件最初写成
```yaml
events:
  on: e.app_id = a.id
```
PyYAML 按 YAML 1.1 把裸 `on` 解析成布尔 `True`，读出来 `ev.get("on")` 是 `None`，
拼出来的 SQL 是 `LEFT JOIN events e ON None`。现在统一写 `join_on`，
加载器对旧的 `on` 做兜底，并且**声明了事件表却拿不到关联条件时直接报错**，
不允许静默生成坏 SQL。

**「前30天」不是「前 30 条」。** 限额正则最初写成 `前\s*(\d+)`，
于是「近前30天投递数」会被截成 `LIMIT 30`。现在要求必须带单位
（名/个/家/位/条）或出现在句尾。

---

## 快速开始

```bash
pip install -r requirements.txt
python data/build_demo.py                  # 生成 128 条虚构投递记录
python tests/test_core.py                  # 34 项单元测试
python nlq.py -d data/demo.db --list-metrics
python nlq.py -d data/demo.db "本月各城市投递数"
python nlq.py -d data/demo.db --sql "投递最多的前3个城市"
```

常用参数：

| 参数 | 作用 |
|---|---|
| `--sql` | 同时打印生成的 SQL |
| `--schema` | 打印库结构（也给模型提示词用） |
| `--list-metrics` | 列出口径层里定义了哪些指标、维度、枚举 |
| `--today YYYY-MM-DD` | 固定「今天」，让时间类问句可复现 |
| `--llm qwen2.5:3b` | 规则层未命中时用本地 Ollama 兜底 |
| `--force-llm` | 跳过规则层直接走模型（不建议） |

**退出码**：`0` 成功 · `1` 解析或守卫失败 · `2` 参数/数据错误。

---

## 已知边界

1. **只支持单轮问句**。多轮追问（「那上个月呢？」）没有上下文，需要带全条件重问。
2. **不做 join 推断**。跨表指标必须在 `metrics` 里显式声明 `needs_events: true`
   与 `event_filter`，工具不会自己猜该不该连表。
3. **规则层对省略主语容忍度有限**。指标名、维度名必须出现在问句里，
   或由 `default_true` 维度接管。
4. **模型兜底不可靠**。3B 级模型在 CPU 上生成 SQL 的准确率不足以承担正确性，
   它只是给复杂句一个兜底选项，不能替代口径层。

---

## 目录

```
nlq.py                   CLI 入口
metriclayer/
  metrics.py             口径层加载（指标/维度/枚举/关联）
  parse.py               自然语言 → QuerySpec（词典 + 时间解析）
  sqlgen.py              QuerySpec → SQL；也给模型拼提示词
  guard.py               三层只读守卫
  schema.py              库结构描述
  render.py              终端表格（中文按显示宽度对齐）
  llm.py                 可选 Ollama 后端
metrics/ledger.example.yaml   示例口径层（6 指标 / 5 维度）
data/build_demo.py            虚构演示库生成器
tests/test_core.py            34 项单元测试
```

## 许可证

MIT。`data/demo.db` 与口径层内的数据均为虚构。

## 配套

同目录思路的另一个工具：[consistency-audit](https://github.com/CYizheng/consistency-audit)
——把「口径」用在文档上：核查同一字段在多份材料里是否一致。
一个管**定义**（口径层），一个管**落地是否一致**（材料核查）。
