# 模拟面试报告（第三轮）

**项目**：Modular RAG MCP Server  
**面试时间**：2026-03-19 23:35:31  
**面试官风格**：CODE（源码拷问型）  
**掷骰结果**：5  
**问答条数**：6  
**综合评分**：6.4/10

---

## 一、面试记录（原文逐字）

| 题号 | 问题（原文） | 候选人回答（原文，不摘要） |
|------|--------------|-----------------------------|
| Q1 | 你简历写了“自动化测试可收集 1219 项（pytest --collect-only）”。请你按代码级别回答：1) 这个统计对应的命令是什么；2) 测试目录分层在哪些路径；3) 如果我要只跑 MCP 相关集成测试，应该执行哪条命令（给出你会实际输入的命令）。 | pytest。在test/ingestion/E2E/unit/在e2e中pytest -q tests/e2e/test_mcp_client.py |
| Q2 | 你这个回答里路径不够准确（项目里是 tests/ 而不是 test/，而且分层不止这几个词）。Q2（CODE 追问）：请直接给出 3 个你确认存在的测试文件路径（完整到文件名），其中至少 1 个是 MCP 相关集成测试；再给出“只跑这个文件”的命令。如果路径说错，我会判定你对测试结构不熟。 | pytest -q tests/e2e/test_mcp_client.py   pytest -q tests/unit/test_batch_processort.py pytest -q tests/unit/test_pipeline_progress.py |
| Q3 | 你说你做了“多 Agent 工作流 + 条件边错误导向”。请给我代码级答案：1) 状态对象里你至少放了哪些字段（举 3 个字段名）；2) 条件路由函数的输入输出分别是什么；3) 错误兜底节点最终返回的数据结构里，至少包含哪 2 个键。 | continue,error,search_attractions continue": "check_weather",
                "error": "handle_error"trip_plan": fallback_plan,
            "current_step": "error_handled",
            "messages": [{"role": "assistant", "content": f"遇到错误，已生成备用计划: {error_msg}"}]
        } |
| Q4 | 收到，你给出的字段和条件分支已经有代码味道了。Q4（方向3：技术深挖，CODE）请继续给代码级细节：1) RRF 融合逻辑在哪个模块（到文件路径级别）；2) 计算融合分数的函数签名大概是什么（参数名可以近似）；3) 你现在说 k 是写死常量，那它在代码里是模块常量、类属性，还是函数默认参数？ | ingestion在fussion中。1/(k+rank).默认参数 |
| Q5 | 这个回答不够通过源码面。你说“ingestion 在 fusion”基本是错位的：ingestion 是摄取链路，不是查询融合层。Q5（CODE 追问）：请你给出你能确认的检索融合实现位置（至少到目录级），并补充：1) Dense 召回结果和 Sparse 召回结果在进入融合前，各自的数据结构大概是什么（例如 List[dict] 里有什么键）；2) 融合后如何去重（按 chunk_id、doc_id 还是别的键）。 | chunk_id sores metedata ,按照chunk_id去重 |
| Q6 | 你现在给到了一个关键点：按 chunk_id 去重，这个是对的；但你仍然缺“可定位的实现细节”。Q6（CODE，最后一题）请你给出一个你能确认存在的“错误兜底”相关代码位置（到文件路径级），并说明：1) 触发兜底时传入的 error 信息是怎么组织的（字符串/对象）；2) 返回的 fallback 结果里，除了 trip_plan 之外还有哪些字段。 | 字符串说明，current_step：固定为 error_handled，见 trip_planner_graph.py:343-344
messages：一条 assistant 提示（“遇到错误，已生成备用计划: ...”），见 trip_planner_graph.py:34 |

---

## 二、参考答案（本场能力点）

### <a id="a-test-structure"></a>1) 测试结构与命令
- 统计命令：`pytest --collect-only`。
- 常见分层：`tests/unit/`、`tests/integration/`、`tests/e2e/`。
- 单文件执行：`pytest -q tests/integration/test_mcp_server.py` 或对应存在文件路径。

### <a id="a-graph-fallback"></a>2) LangGraph 条件边兜底
- State 常见字段：`current_step`、`error`、`messages`、`trip_plan`（示例）。
- 条件路由函数：输入为当前 state，输出为下一节点键名（如 `"check_weather"` 或 `"handle_error"`）。
- 兜底返回：至少包含 `trip_plan`、`current_step`、`messages`，并附错误信息。

### <a id="a-rrf-location"></a>3) RRF 融合代码级回答标准
- 需能定位到查询层检索模块（而非 ingestion）。
- 需说明融合输入（dense/sparse 排名结果）与去重键（常见 `chunk_id`）。
- 需明确 `k` 的具体落点（模块常量/函数参数/配置项）。

---

## 三、简历包装点评

### 包装合理 ✅
- 能给出兜底输出中的具体字段（`current_step`、`messages`）和错误文案风格，说明确实接触过工作流错误处理代码。
- 能明确“按 `chunk_id` 去重”这一关键实现点，方向正确。

### 露馅点 ❌
- 测试文件路径准确性不足（存在拼写/路径误差），与“可直接执行”的源码级要求有差距。**严重性：中**。
- RRF 融合位置回答错位（将 ingestion 与 fusion 混淆），暴露检索链路定位能力不足。**严重性：中高**。
- 代码级问题多次停留在概念层（缺文件/函数/参数精确定位）。**严重性：中**。

### 改进建议
- 准备“文件定位清单”：检索、融合、rerank、trace、mcp server、tests 六大模块各 2-3 个关键文件。
- 每个模块按固定模板背诵：`文件路径 -> 关键函数 -> 输入输出 -> 失败处理`。
- 把易混概念拆开：`ingestion` 与 `query` 链路分别画一张 5 步流程图，避免面试错位。

---

## 四、综合评价

**优势**
- 对错误兜底有一定实操记忆，能给出结构化返回字段。
- 对去重关键键（`chunk_id`）有正确认知。

**薄弱点**
- 检索链路源码定位不稳定，概念与实现层还存在断层。
- 命令与路径的精确性不足，影响“可落地执行”的可信度。

**面试官建议**
- 下一步做 30 分钟“源码快问快答”：我问文件路径，你必须 10 秒内回答到函数名。
- 重点补齐 RRF/Fusion 模块位置与函数签名，做到不混淆 ingestion/query。

---

## 五、评分

| 维度 | 分数（满分 10） | 评分依据 |
|------|------------------|----------|
| 项目架构掌握 | 6.5 | 了解工作流与兜底，但链路边界（ingestion vs query）偶有混淆 |
| 简历真实性 | 6.8 | fallback 字段细节较真实，但部分细节不够可核验 |
| 算法理论深度 | 5.8 | 知道 RRF 形式，但模块与参数落点不够精确 |
| 实现细节掌握 | 6.1 | 有字段级记忆与去重键认知，路径/函数级精度不足 |
| 表达清晰度 | 6.8 | 回答逐步变具体，仍需减少拼写和术语误差 |
| **综合** | **6.4** | 有实操基础，距离“稳定源码面通过”还差一步 |
