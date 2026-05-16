---
name: academic-translation
description: "将单个中文文件高质量翻译为学术英文（期刊投稿风格），并保持原文逻辑链条、术语一致性与公式/代码/占位符位置不变。Use when user says: 翻译这份文件/中译英/学术英文/期刊投稿/英文润色/把这篇中文论文翻成英文/保留公式/公式位置要对/translate this paper/manuscript translation/journal style."
---

# Academic Translation (Single-file CN→EN)

把**一份中文文件**翻译成适合期刊投稿的学术英文，重点是：

- **理顺逻辑**：把中文的“省略主语/跳跃衔接/并列堆叠”改写成英文可读的论证链条，但**不改变原意**。
- **术语一致**：同一概念全篇统一译法（含缩写、变量名、实体名）。
- **公式与位置正确**：识别并冻结公式/LaTeX 环境/代码块/图片占位符等不可翻译片段，保证它们在英文中仍处于正确位置。

本 skill 面向“翻译”而不是“改写论文内容”：
- 允许：轻度重排句子顺序以澄清逻辑、补充必要主语/连接词、学术化表述。
- 禁止：编造新结果、删除关键条件、改变公式含义、引入未在原文出现的新实验/数据。

---

## 快速入口（你要做什么）

当用户说“翻译这份文件”“把这篇中文论文翻成英文”“保留公式位置”，按下面工作流执行。

输出必须包含：
1) 翻译后的英文文件（与原文件同目录，文件名带 `.en` 后缀）
2) 一份简短 `translation_report.md`（术语表 + 关键歧义点 + 处理说明）

---

## Phase 0：最少澄清（最多 3 个问题）

如果用户没有给足信息，用 `vscode_askQuestions` 一次问完（最多 3 题）：

1. **目标体裁**：期刊论文 / 研究报告 / 简历 / 技术文档（默认：期刊论文）
2. **学科领域**：例如 condensed matter / CV / NLP / economics（默认：按原文推断）
3. **输出格式**：Markdown（`.md`）/ LaTeX（`.tex`）/ 纯文本（`.txt`）（默认：跟随输入文件类型；PDF 默认输出 `.md`）

如果用户明确说“只是翻译，不润色”，则减少改写幅度：只做必要的主语补全与连接词补全，逻辑结构保持更贴近中文。

---

## Phase 1：读取输入文件

### 1.1 文本文件（推荐）
支持：`.md`/`.txt`/`.tex`/`.rst` 等可直接读取的文本文件。
- 使用工作区工具读取内容（`read_file` 分段读取即可）。

### 1.2 PDF（可选）
如果输入是 PDF：优先将其转为 Markdown，再翻译。
- 若项目环境已安装 `markitdown`（以及可选 `pymupdf`、`Pillow`），可使用项目内 `PdfLoader`（见 `src/libs/loader/pdf_loader.py`）先抽取文本与图片占位符 `[IMAGE: ...]`。
- 若依赖缺失：不要硬猜 PDF 内容；请用户提供导出的 `.md/.txt/.tex`，或在用户允许后安装依赖。

可选：用户允许安装依赖时，优先安装以下包（按需）：
- `pip install markitdown`
- `pip install pymupdf pillow`（仅当需要抽取 PDF 图片时）

可选：将 PDF 抽取成 Markdown 文件（示意，用于本项目结构）：
```bash
python -c "from pathlib import Path; from src.libs.loader.pdf_loader import PdfLoader; d=PdfLoader(extract_images=True).load(r'INPUT.pdf'); Path(r'OUTPUT.md').write_text(d.text, encoding='utf-8')"
```

注意：对 PDF 抽取出来的 `[IMAGE: ...]` 占位符，必须原样保留并放回英文的对应位置（通常在相关段落后）。

---

## Phase 2：建立“逻辑概念图”（先理解再翻译）

在开始逐句翻译前，先做一个轻量的结构化理解（写在 `translation_report.md`）：

- **文档大纲**：标题 → 章节 → 小节（保留原编号/层级）
- **核心概念表**（5–20 项，视篇幅）：
  - 中文术语 / 英文候选译法 / 缩写 / 备注（是否首次出现需解释）
- **论证链条**（3–8 条）：
  - “问题 → 方法 → 关键假设 → 推导/实验 → 结论/意义”

目的：防止“句子都翻对了但逻辑断裂/术语漂移”。

---

## Phase 3：冻结不可翻译片段（公式/代码/占位符）

在翻译正文前，必须识别并**冻结**以下片段，使其在翻译过程中保持不变：

- 数学公式：`$...$`、`$$...$$`、`\\(...\\)`、`\\[...\\]`
- LaTeX 环境：`\\begin{equation}`/`align`/`gather`/`cases` 等到对应 `\\end{...}`
- 代码块：Markdown fenced code ```...```；LaTeX `verbatim`/`lstlisting`
- 图片/表格占位符：例如项目抽取出的 `[IMAGE: <id>]`
- URL、DOI、引用编号（如 `[12]`、`(Smith, 2023)`）——通常不翻译其内容，仅调整周围语法

### 推荐做法：使用随附脚本生成占位符
本 skill 自带脚本（可选但强烈推荐）用于把“不可翻译片段”替换成 token，避免在翻译时被改坏：

- 生成冻结版：`.github/skills/academic-translation/scripts/freeze_nontranslatables.py`
- 回填冻结片段：`.github/skills/academic-translation/scripts/rehydrate_nontranslatables.py`

工作方式：
1) freeze：把公式/代码块替换成 `<<MATH:0001>>`/`<<CODE:0001>>` 等 token，并生成 map JSON。
2) 翻译：只翻译 token 之外的文本。
3) rehydrate：把 token 精确替换回原始内容。

**硬性要求**：翻译完成后，token 必须“一个不多、一个不少”。

如需更详细的冻结范围与边界情况，阅读：
- [references/formula_and_tokens.md](references/formula_and_tokens.md)

---

## Phase 4：执行翻译（学术英文 + 逻辑一致）

### 4.1 结构保持
- 章节标题、编号、列表层级尽量保持与原文一致。
- 如果原文是论文结构（Abstract/Introduction/Methods/Results/Conclusion），英文也使用对应惯用结构与标题。

### 4.2 学术英文风格规则（默认期刊论文）
- 用精确、克制、可验证的措辞：`suggest`/`indicate`/`demonstrate`/`we show`，避免口语化。
- 连接词显式化：把“此外/同时/因此/但是/换言之/特别地”分别映射为 `Moreover/Meanwhile/Therefore/However/In other words/In particular` 等，并确保前后关系正确。
- 主语补齐：中文常省略主语，英文必须补齐（`we/this study/the model/the results`）。
- 时态：方法与本文组织通常用一般现在时；实验过程可用一般过去时（按学科习惯调整）。

### 4.3 术语一致性（强制）
- 第一次出现：`中文概念 → 英文全称（缩写）`，后续只用缩写或约定译法。
- 同一概念绝不在不同段落用不同译法（除非用户明确要求）。
- 变量/符号：保持原样，不要把 `E_k` 翻成 `Ek` 或改动下标。

### 4.4 公式与上下文的“位置正确”
- 公式 token 必须保留在对应句子附近：
  - 若原文是“如式(3)所示：$$...$$”，英文应是 “As shown in Eq. (3), <<MATH:xxxx>>”.
- “式/图/表”引用格式统一：例如 `Eq. (1)`, `Fig. 2`, `Table 1`。
- 若原文在公式前后解释变量含义：英文必须紧贴公式附近，避免解释跑到别处导致读者断裂。

---

## Phase 5：回填与质量检查（交付前必做）

### 5.1 回填
如果使用了 freeze/rehydrate：执行 rehydrate，得到最终英文稿。

### 5.2 QA 清单（必须逐项自检）
- **token 校验**：所有 `<<MATH:*>>`/`<<CODE:*>>` 都被回填且未丢失。
- **结构校验**：章节/小节未丢、列表编号未乱。
- **一致性校验**：术语表里的译法在全文一致；缩写首次出现已定义。
- **逻辑校验**：每段至少有一个明确的逻辑连接点（原因/对比/递进/结论）。
- **公式语义不变**：公式内容未被改写；变量名与上下文一致。
- **引用不破坏**：DOI/URL/引用编号未被翻译或改写。

---

## 输出规范（强制）

### 产物 1：英文译文文件
- 保存路径：与输入文件同目录
- 命名规则：
  - `xxx.md` → `xxx.en.md`
  - `xxx.tex` → `xxx.en.tex`
  - `xxx.txt` → `xxx.en.txt`
  - `xxx.pdf` → `xxx.en.md`（从 PDF 抽取到 Markdown 后翻译）

### 产物 2：translation_report.md
必须包含这三段（简短即可）：

1) **Glossary（术语表）**：中文 → 英文 → 缩写/备注
2) **Ambiguities（歧义点）**：列出 1–5 个你无法确定的译法/指代，并给出 2 个候选译法 + 推荐项 + 理由
3) **Formatting notes（格式/公式处理说明）**：说明是否使用 token 冻结、是否存在 `[IMAGE: ...]`、以及你如何保持公式位置

---

## 示例触发语

- “把这份中文论文翻译成可以投 SCI 的英文，公式别乱。”
- “翻译这个 .tex 文件，保持章节结构和公式位置。”
- “把 PDF 翻成英文并保留图像占位符。”

---

## 常见失败模式（必须避免）

- 把公式里的变量/下标翻译了或改写了
- 同一术语前后译法不一致（例如“凝聚态物理”一会儿 `condensed matter physics` 一会儿 `condensed state physics`）
- 英文连接词乱用导致逻辑反转（把“但是”翻成 `therefore`）
- 过度润色导致新增原文没有的结论/动机
