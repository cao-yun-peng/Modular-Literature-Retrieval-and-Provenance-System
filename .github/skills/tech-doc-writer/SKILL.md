---
name: tech-doc-writer
description: Generate technical documentation by reading a project's overall structure and source code, with special support for agent-style systems (tools/orchestration/memory/observability/evals). Use when the user asks to write/refresh technical docs, architecture docs, DEV_SPEC.md, system design docs, “为这个项目写技术文档/架构文档/开发者规范”, OR when the user provides a "主体函数/核心代码" and wants a traceable function-centered technical document. The skill MUST scan the repo (README/config/entrypoints/src/tests) and produce a doc grounded in real code (no hallucinated modules).
metadata:
  category: documentation
  triggers: "write tech doc, technical documentation, architecture doc, DEV_SPEC, system design, 写技术文档, 架构文档, 开发者规范, 生成DEV_SPEC, 主体函数, 核心函数, 代码溯源, agent文档"
allowed-tools: Read Write Bash(python:*)
---

# Tech Doc Writer (DEV_SPEC Style + Function-Centered Trace)

You write a **technical document** by reading the repo’s **framework + code**. You can generate:

- **Repo-level DEV_SPEC-style doc** (project-wide)
- **Function-centered technical doc** (starting from a user-provided “主体函数/核心代码”，then tracing to dependent/related project functions/modules)

Both modes MUST be **traceable to code**.

## Core principles

1. **Grounded in code**: Every claim about modules/flows/config MUST be traceable to files in the repo (paths, module names, function/class names). If uncertain, say "Unknown" and list what you checked.
2. **Progressive disclosure**: Start from repo entrypoints and top-level docs, then drill into `src/` and `tests/` only as needed.
3. **Spec-like structure**: Use the same top-level chapter structure as `DEV_SPEC.md` unless the user explicitly asks otherwise.
4. **Engineering writing**: Prefer clear headings, tables for comparisons, bullets for lists, and fenced code blocks for commands or schemas.
5. **Agent-first extraction**: When the project is agent-like (tool calling/orchestration), prioritize documenting: tool registry/contracts, planning/execution loop, memory/state, safety boundaries, tracing/metrics, and evaluation.
6. **No meta “next doc” defaults**: Do NOT write generic guidance like “下一步再写一个类似的技术文档/按本文档格式继续写”. Recommendations must be specific to the repo or the focal function.

## Quick interview (ask only if ambiguous)

Ask up to 3 questions max. If the user already provided answers, do not ask again.

1. **Scope**: Repo-wide doc OR function-centered doc? (default: follow user request; if user pasted code, choose function-centered)
2. **Focal target (only for function-centered)**: Provide one of:
   - file path + symbol name (preferred)
   - or a code snippet + where it lives
   If not provided, attempt best-effort identification from the snippet.
3. **Output file**:
   - Repo-wide: default `DEV_SPEC.md`
   - Function-centered: default `TECH_DOC.md` (to avoid overwriting the repo spec)

## Step 1: Repo framing (fast scan)

### 1.1 Inventory the repo

Collect a minimal, high-signal snapshot:
- Root files: `README*`, `pyproject.toml`/`requirements.txt`, `package.json` (if any), `main.*`, `Makefile`, `docker*`, `.env.example`.
- Configuration: `config/`, `settings.*`, `*.yaml`, `*.toml`.
- Source roots: `src/`, `app/`, `lib/`, `server/`, `scripts/`.
- Tests: `tests/`, CI config (`.github/workflows/` if present).

### 1.2 Identify entrypoints

Determine how the project runs:
- CLI entry: `main.py`, `src/__main__.py`, console scripts in `pyproject.toml`.
- Server entry: `server.py`, `app.py`, `src/.../server.py`, ASGI/WSGI config.
- Scripts: `scripts/*.py`.

Record the entrypoints and the runtime modes (dev/prod/test) if detectable.

## Step 2: Architecture extraction (code-driven)

### 2.1 Draw the high-level data flow

From code + docs, infer the primary flows. Typical patterns:
- Ingestion flow: load → parse → split → transform → embed → store
- Query flow: preprocess → retrieve (dense/sparse/hybrid) → fuse → rerank → respond
- Service flow: request → tool/router → engine → response

Express the flow with:
- A short paragraph
- A bullet list of steps
- Optionally a simple ASCII diagram (avoid adding new “design systems”)

### 2.2 Map modules to responsibilities

Create a module map table:

| Module/Dir | Responsibility | Key types | Extension points |
|-----------|----------------|-----------|------------------|

Rules:
- Prefer directories under `src/` as primary modules.
- For each module, cite 1–3 representative files.

### 2.3 Configuration & dependency boundaries

Document:
- Where configuration is loaded/validated.
- How components are wired (factories, dependency injection, registries).
- External services/providers (LLM, DB, vector store, cloud) and fallback behavior.

If the project uses a "pluggable" architecture, explicitly list the abstract interfaces and concrete implementations.

### 2.4 Function-centered trace (ONLY when user provides a focal function/code)

Goal: starting from the focal function (主体函数), build a **traceable dependency view**:

1. **Locate the focal definition**
  - Identify file path and the exact symbol name.
  - Summarize signature: inputs, outputs, side effects, sync/async.
2. **Trace inbound callers (who calls it?)**
  - Search for call sites/imports/registrations.
  - In agent systems, check tool registration tables, routers, protocol handlers.
3. **Trace outbound dependencies (what it calls?)**
  - Walk the function body to list key internal calls and external integrations.
  - Group by category: config, storage, LLM/provider, retrieval, formatting, tracing.
4. **Trace contracts**
  - Identify schemas/types passed through (dataclasses/pydantic/TypedDict/protocol payloads).
  - Identify config keys referenced.
5. **Trace observability**
  - Identify logging/tracing spans/events produced by this path.
6. **Trace tests**
  - Find unit/integration/e2e tests that directly/indirectly cover the focal function.

Evidence rules:
- For each traced edge, record at least one: file path, symbol name, or config key.
- If a dependency is dynamic (reflection/registry), document the mechanism and where the mapping lives.

## Step 3: Testing & quality signals

Summarize test strategy from:
- `tests/` layout (unit/integration/e2e)
- Existing fixtures/golden sets
- How to run tests (commands)

If no tests exist, propose a minimal test pyramid and where to place tests—but mark it as "Proposed".

## Step 4: Write the technical document (DEV_SPEC format)

### 4.1 Output structure

Choose ONE of the following templates based on scope.

#### Template A (Repo-wide): DEV_SPEC top-level chapter order (MUST follow)

Write the document using this exact top-level chapter order and numbering:

1. 项目概述
2. 核心特点
3. 技术选型
4. 测试方案
5. 系统架构与模块设计
6. 项目排期
7. 可扩展性与未来展望

Also include a "目录" section near the top listing these chapters.

#### Template B (Function-centered): 主体函数技术文档（Agent-friendly）

Use this structure exactly:

1. 背景与目标
2. 主体函数概览（定位、职责、边界）
3. 调用链与依赖溯源（Inbound/Outbound）
4. 数据契约与输入输出（Types/Schemas/Config Keys）
5. 与 Agent 相关的设计（Tools/Orchestration/Memory/Safety）
6. 可观测性与调试（Logs/Traces/Metrics）
7. 测试覆盖与回归策略
8. 限制、权衡与已知风险
9. 扩展点与演进建议（必须具体到该仓库/该函数）

Notes:
- Template B is NOT required to include the 7 DEV_SPEC chapters.
- Template B must stay focused on the provided focal function and its traced neighborhood.

### 4.2 Style constraints to match this repo’s DEV_SPEC

- Headings use Markdown `#`/`##`/`###` with Arabic numbering for top chapters (e.g., `## 1. 项目概述`).
- Frequently include bilingual parenthetical glosses for key terms (e.g., "可插拔架构 (Pluggable Architecture)").
- Use **bold** to emphasize key concepts; use bullet lists for breakdowns.
- Use tables for comparisons (providers, components, storage choices, etc.).
- Use fenced code blocks for commands, schemas, pseudo-SQL, or example payloads.
- Use blockquotes (`>`) for "callout" paragraphs when highlighting a key design philosophy or an important constraint.
- Avoid making up metrics, benchmarks, dates, SLAs, or security claims.
- Do NOT include generic meta guidance like “next, write another doc like this”. Keep recommendations repo-specific.

### 4.3 Content requirements

#### Template A chapters: per-chapter requirements

##### Chapter 1: 项目概述
Include:
- One-paragraph mission and scope
- Design philosophy (if discoverable from README/docstrings)
- Intended users and non-goals

##### Chapter 2: 核心特点
For each feature:
- What problem it solves
- How it’s implemented (components involved)
- Trade-offs / limitations

##### Chapter 3: 技术选型
Include:
- Language/runtime and dependency management
- Storage choices (DB/vector store/index)
- Key libraries and why
- Provider integrations (if any)

##### Chapter 4: 测试方案
Include:
- Test pyramid and directory mapping
- Test commands
- Mocking strategy for external calls

##### Chapter 5: 系统架构与模块设计
Include:
- Directory overview (high-level)
- Major modules and boundaries
- Data flows (ingestion/query/service) grounded in code

##### Chapter 6: 项目排期
If the repo has a schedule/spec (milestones, roadmap, DEV_SPEC schedule chapter), summarize it.
If not, write a lightweight roadmap with 3–6 milestones, clearly labeled as "Proposed".

##### Chapter 7: 可扩展性与未来展望
Include:
- Concrete extension points (interfaces, plugin hooks, provider additions)
- 3–8 future improvements tied to current architecture

### 4.4 Evidence policy (to avoid hallucinations)

Whenever you describe an implementation detail, include at least one of:
- File path(s)
- Symbol name(s) (class/function)
- Configuration key(s)

Example (acceptable inside the generated doc):
- “配置从 `config/settings.yaml` 读取，由 `src/core/settings.py::Settings` 校验。”

If you cannot find evidence, write:
- "未在当前仓库中发现" + list what you searched/read.

## Agent-focused checklist (use when applicable)

If the repo looks like an agent system (or the focal function is part of an agent runtime), try to extract:

- **Tool surface**: tool list, schemas, routing/registration mechanism, versioning
- **Orchestration**: planner/executor separation, retries, fallbacks, timeouts
- **Memory/state**: session memory, persistence, caching, idempotency
- **Safety**: input validation, allowlists/denylists, data exposure boundaries
- **Observability**: traces, correlation ids, structured logs, evaluation traces
- **Evaluation**: golden sets, regression metrics, offline/online evaluation loops

## Step 5: Deliverables

Default deliverable depends on scope:
- Repo-wide: Update or create `DEV_SPEC.md` at repo root.
- Function-centered: Create or update `TECH_DOC.md` at repo root (unless user specifies another path).

Optional (only if user asks):
- Split into chapters under `.github/skills/spec-sync/specs/` by running `python .github/skills/spec-sync/sync_spec.py` (if present in the repo).

## Minimal example prompts (for this skill)

- “帮我为这个项目写一份 DEV_SPEC 风格的技术文档，要求从代码里总结架构和模块。”
- “这个仓库没有文档，生成一份包含架构、技术选型、测试策略的技术文档（中文）。”
- “把现有 DEV_SPEC.md 更新一下：按当前代码补全架构与测试章节。”
- “这是主体函数（含文件路径/代码片段）。请从它出发溯源它调用的模块与被谁调用，并写一份以该函数为中心的 Agent 技术文档。”
