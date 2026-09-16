import { useState, useRef, useEffect } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  ArrowUpRight,
  Search,
  Send,
  MessageSquare,
  BookOpen,
  RefreshCw,
} from "lucide-react";
import {
  api,
  post,
  terminal,
  type Document,
  type Run,
  type Result,
  type Page,
} from "../lib/api";
import { useRun } from "../lib/hooks";
import { useNav, usePageSearch } from "../lib/navigation";
import {
  Button,
  Select,
  Status,
  ErrorBox,
  Empty,
  Loading,
} from "../components/ui";
import { Markdown } from "../components/markdown";
const formSchema = z.object({
  query: z
    .string()
    .trim()
    .min(1, "请输入问题")
    .max(4000, "问题不能超过 4000 字符"),
});
export function RetrievalPage() {
  const go = useNav(),
    search = usePageSearch(),
    [scope, setScope] = useState(search.doc ?? ""),
    [top, setTop] = useState(5),
    [mode, setMode] = useState("answer"),
    [selected, setSelected] = useState<number | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm({
    resolver: zodResolver(formSchema),
    defaultValues: { query: "" },
  });
  const requestKey = useRef(crypto.randomUUID());
  const docs = useQuery({
    queryKey: ["documents", "completed"],
    queryFn: () => api<Page<Document>>("/documents?status=succeeded&limit=100"),
  });
  const history = useQuery({
    queryKey: ["retrieval-history"],
    queryFn: () => api<Page<Run>>("/runs?kind=retrieval&limit=20"),
    refetchInterval: 10000,
  });
  const current = useRun(search.run);
  const result = useQuery({
    queryKey: ["result", search.run],
    queryFn: () => api<Result>(`/retrieval-runs/${search.run}/result`),
    enabled: !!search.run,
    refetchInterval:
      search.run && !terminal(current.data?.status) ? 1500 : false,
  });
  const submit = useMutation({
    mutationFn: (v: { query: string }) =>
      post<Run>(
        "/retrieval-runs",
        {
          query: v.query,
          document_ids: scope ? [scope] : [],
          top_k: top,
          generate_answer: mode === "answer",
        },
        requestKey.current,
      ),
    onSuccess: (r) => {
      requestKey.current = crypto.randomUUID();
      setSelected(null);
      void go("/retrieval", { run: r.id, doc: scope || undefined });
    },
  });
  const retry = useMutation({
    mutationFn: () =>
      post<Run>(`/runs/${search.run}/retry`, {
        stage: current.data?.stage === "answer" ? "answer" : "auto",
      }),
    onSuccess: (r) => go("/retrieval", { run: r.id, doc: scope || undefined }),
  });
  useEffect(() => {
    if (result.data?.evidence?.length && selected === null)
      setSelected(result.data.evidence![0].index);
  }, [result.data, selected]);
  const active = result.data?.evidence?.find((e) => e.index === selected);
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">ASK WITH EVIDENCE</p>
          <h1>检索实验室</h1>
          <p className="muted">带着问题阅读，让回答回到原文。</p>
        </div>
        <span className="tag">
          <BookOpen size={14} />
          证据优先
        </span>
      </div>
      <form
        className="panel query-form"
        onSubmit={handleSubmit((v) => submit.mutate(v))}
      >
        <label htmlFor="question">你想从论文中了解什么？</label>
        <textarea
          id="question"
          placeholder="例如：为什么非互易 XY 模型的各向异性不可避免？"
          {...register("query", {
            onChange: () => {
              requestKey.current = crypto.randomUUID();
            },
          })}
        />
        {errors.query && (
          <p role="alert" className="error-text">
            {errors.query.message}
          </p>
        )}
        <div className="query-controls">
          <Select
            aria-label="查询范围"
            value={scope}
            onChange={(e) => {
              setScope(e.target.value);
              requestKey.current = crypto.randomUUID();
            }}
          >
            <option value="">整个知识库</option>
            {docs.data?.items.map((d) => (
              <option value={d.id} key={d.id}>
                {d.title}
              </option>
            ))}
          </Select>
          <Select
            aria-label="返回证据数量"
            value={top}
            onChange={(e) => {
              setTop(Number(e.target.value));
              requestKey.current = crypto.randomUUID();
            }}
          >
            {[3, 5, 10].map((n) => (
              <option value={n} key={n}>
                Top {n}
              </option>
            ))}
          </Select>
          <Select
            aria-label="检索模式"
            value={mode}
            onChange={(e) => {
              setMode(e.target.value);
              requestKey.current = crypto.randomUUID();
            }}
          >
            <option value="answer">检索并回答</option>
            <option value="search">仅检索</option>
          </Select>
          <Button
            type="submit"
            disabled={submit.isPending || current.data?.status === "running"}
          >
            <Send size={16} />
            {submit.isPending ? "正在提交" : "开始检索"}
          </Button>
        </div>
        <ErrorBox error={submit.error} />
      </form>
      {history.data?.items.length ? (
        <div className="history-switch">
          <span className="muted">历史问题</span>
          <Select
            aria-label="历史检索问题"
            value={search.run ?? ""}
            onChange={(e) => {
              setSelected(null);
              void go("/retrieval", { run: e.target.value || undefined });
            }}
          >
            <option value="">选择一条历史记录</option>
            {history.data.items.map((r) => (
              <option value={r.id} key={r.id}>
                {r.title}
              </option>
            ))}
          </Select>
        </div>
      ) : null}
      <ErrorBox error={current.error || result.error || retry.error} />
      {current.data && (
        <div className="inline result-status">
          <Status value={current.data.status} />
          <span className="muted">{current.data.title}</span>
          {["failed", "interrupted"].includes(current.data.status) && (
            <Button
              size="sm"
              variant="outline"
              disabled={retry.isPending}
              onClick={() => retry.mutate()}
            >
              <RefreshCw size={14} />
              {current.data.stage === "answer" ? "仅重试回答" : "重试任务"}
            </Button>
          )}
        </div>
      )}
      {current.data?.error && <ErrorBox error={current.data.error.message} />}
      {!search.run ? (
        <div className="panel">
          <Empty title="从一个好问题开始">
            限定论文、寻找证据，再生成可追溯的回答。
          </Empty>
        </div>
      ) : (
        <div className="retrieval-grid">
          <section className="panel answer-pane">
            <div className="pane-header">
              <span>
                <MessageSquare size={16} />
                回答
              </span>
              <span className="muted">
                {result.data?.answer_model ?? "依据检索证据"}
              </span>
            </div>
            <div className="answer-content">
              {result.data?.answer ? (
                <Markdown
                  text={result.data.answer}
                  onCitation={(index) => {
                    setSelected(index);
                    document
                      .getElementById("evidence-panel")
                      ?.scrollIntoView({
                        behavior: "smooth",
                        block: "nearest",
                      });
                  }}
                />
              ) : !terminal(current.data?.status) ? (
                <Loading />
              ) : result.data?.answer_status === "not_requested" ? (
                <Empty title="仅检索模式">
                  本次没有调用回答模型。右侧为检索到的证据。
                </Empty>
              ) : result.data?.answer_status === "failed" ? (
                <Empty title="回答生成失败">证据已保留，可单独重试回答。</Empty>
              ) : (
                <Empty title="未找到可用证据">尝试调整问题或查询范围。</Empty>
              )}
            </div>
          </section>
          <section className="panel evidence-pane" id="evidence-panel">
            <div className="pane-header">
              <span>
                <Search size={16} />
                检索证据
              </span>
              <span className="muted">
                {result.data?.evidence?.length ?? 0} 条
              </span>
            </div>
            <div className="evidence-tabs">
              {result.data?.evidence?.map((e) => (
                <button
                  key={e.index}
                  className={selected === e.index ? "selected" : ""}
                  onClick={() => setSelected(e.index)}
                >
                  [{e.index}]
                </button>
              ))}
            </div>
            {active ? (
              <div className="evidence-content">
                <div className="inline between">
                  <span className="tag">证据 [{active.index}]</span>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      go(`/documents/${active.document_id}`, {
                        run: active.run_id ?? undefined,
                        chunk: active.chunk_id,
                      })
                    }
                  >
                    打开分块
                    <ArrowUpRight size={14} />
                  </Button>
                </div>
                <h3>{active.title}</h3>
                <p className="muted">{active.section}</p>
                <div className="score-row">
                  {Object.entries(active.scores ?? {})
                    .filter(([, v]) => v !== null)
                    .map(([k, v]) => (
                      <span key={k}>
                        {k}
                        <b>{Number(v).toFixed(4)}</b>
                      </span>
                    ))}
                </div>
                <Markdown text={active.text} />
                {active.linked_assets?.map((asset) => (
                  <details key={asset.id} className="metadata">
                    <summary>
                      关联图注 · {asset.metadata?.figure_id as string}
                    </summary>
                    <Markdown text={asset.text ?? ""} />
                  </details>
                ))}
              </div>
            ) : (
              <Empty title="等待检索证据">
                检索完成后会先显示证据，再生成回答。
              </Empty>
            )}
          </section>
        </div>
      )}
    </>
  );
}
