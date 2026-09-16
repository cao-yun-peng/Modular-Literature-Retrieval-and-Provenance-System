import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import "katex/dist/katex.min.css";
export function Markdown({
  text,
  onCitation,
}: {
  text: string;
  onCitation?: (index: number) => void;
}) {
  // Parser asset markers followed by panel labels, e.g. [FIG_REF: fig_2](a),
  // are source text, not relative web URLs. Keep their meaning visible.
  const source = text.replace(/\[((?:FIG|TABLE)_REF:[^\]]+)\]/g, "\\[$1\\]");
  const content = onCitation
    ? source
        .replace(/\\\(([\s\S]*?)\\\)/g, (_, math) => `$${math}$`)
        .replace(/\\\[([\s\S]*?)\\\]/g, (_, math) => `$$${math}$$`)
        .replace(/\[(\d+)\](?!\()/g, (_, n) => `[${n}](#evidence-${n})`)
    : source;
  return (
    <div className="prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[
          rehypeRaw,
          [
            rehypeSanitize,
            {
              ...defaultSchema,
              attributes: {
                ...defaultSchema.attributes,
                code: [
                  ["className", /^language-./, "math-inline", "math-display"],
                ],
              },
            },
          ],
          [rehypeKatex, { throwOnError: false, strict: false }],
        ]}
        components={{
          a: ({ href, children }) => {
            const citation = href?.match(/^#evidence-(\d+)$/);
            return citation && onCitation ? (
              <button
                className="citation"
                onClick={() => onCitation(Number(citation[1]))}
              >
                {children}
              </button>
            ) : (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
          img: ({ alt }) => (
            <span className="muted">[图片：{alt ?? "未提供本地图像"}]</span>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
