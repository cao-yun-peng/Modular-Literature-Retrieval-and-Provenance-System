# Formula & Non-Translatables Handling

This reference file documents what counts as **non-translatable spans** and how to keep them stable during CN→EN translation.

## Non-translatable spans (freeze as-is)

- Inline math: `$...$` (do not translate inside)
- Display math blocks: `$$...$$`
- LaTeX math delimiters: `\(...\)`, `\[...\]`
- LaTeX environments: `\begin{equation}`/`align`/`cases`/... up to the matching `\end{...}`
- Code blocks:
  - Markdown fenced code blocks: triple backticks
  - LaTeX: `verbatim`, `lstlisting`
- Placeholders produced by this repo:
  - Images: `[IMAGE: <id>]`

## Placement rule

Keep frozen spans at the **same rhetorical position**:
- If the original sentence introduces a formula, the translated sentence should introduce the same formula.
- Keep equation references adjacent: `Eq. (n)` should appear in the same paragraph as the equation.

## Token rule

When using tokenization, treat tokens as immutable:
- `<<MATH:0001>>`, `<<CODE:0001>>`, `<<PLACEHOLDER:0001>>`
- Do not add, remove, rename, or reorder tokens.
- After translation, every token must rehydrate exactly once.

## Notes on edge cases

- Nested `$` in currency or escaped dollars: `\$` should not be treated as math.
- Markdown backticks inside code blocks: code block detection wins.
