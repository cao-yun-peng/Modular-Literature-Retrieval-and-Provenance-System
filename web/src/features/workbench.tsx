import { lazy, Suspense, useState } from "react";
import { useParams } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { BarChart, Bar, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  ChevronRight,
  FileText,
  Layers3,
  MessageSquare,
  RefreshCw,
} from "lucide-react";
import {
  api,
  post,
  params,
  type Document,
  type Run,
  type Chunk,
  type Page,
} from "../lib/api";
import { useRun, useEvents } from "../lib/hooks";
import { useNav, usePageSearch } from "../lib/navigation";
import { kindLabel, date } from "../lib/utils";
import {
  Button,
  Select,
  Status,
  ErrorBox,
  Empty,
  Loading,
  Tabs,
  TabsList,
  TabsTrigger,
} from "../components/ui";
import { Markdown } from "../components/markdown";
const PdfReader = lazy(() =>
  import("../components/pdf-reader").then((m) => ({ default: m.PdfReader })),
);
const stages = [
  ["integrity", "校验"],
  ["load", "解析"],
  ["structure", "整理"],
  ["split", "分块"],
  ["embed", "向量化"],
  ["upsert", "入库"],
];
export function Steps({ run }: { run: Run }) {
  const { data: events } = useEvents(run.id);
  const reached = new Set(events?.map((e) => e.stage));
  return (
    <div className="steps">
      {stages.map(([key, label], i) => {
        const done =
          run.status === "succeeded" || (reached.has(key) && run.stage !== key);
        return (
          <div
            className={`step ${done ? "done" : ""} ${run.stage === key ? "active" : ""}`}
            key={key}
          >
            <span>{done ? <Check size={13} /> : i + 1}</span>
            {label}
            {i < 5 && <ChevronRight className="step-arrow" size={14} />}
          </div>
        );
      })}
    </div>
  );
}
export function WorkbenchPage() {
  const { documentId } = useParams({ strict: false }) as { documentId: string },
    search = usePageSearch(),
    go = useNav(),
    client = useQueryClient();
  const [type, setType] = useState(""),
    [section, setSection] = useState(""),
    [cursor, setCursor] = useState(""),
    [raw, setRaw] = useState(false),
    [mobile, setMobile] = useState("source");
  const doc = useQuery({
    queryKey: ["document", documentId],
    queryFn: () => api<Document>(`/documents/${documentId}`),
  });
  const runs = useQuery({
    queryKey: ["document-runs", documentId],
    queryFn: () => api<Page<Run>>(`/runs?document_id=${documentId}&limit=100`),
  });
  const rid = search.run ?? doc.data?.latest_run_id ?? undefined,
    run = useRun(rid);
  const stats = useQuery({
    queryKey: ["stats", rid],
    queryFn: () =>
      api<{
        count: number;
        max_tokens: number;
        sections: string[];
        distribution: { index: number; tokens: number }[];
      }>(`/runs/${rid}/chunk-stats`),
    enabled: !!rid,
  });
  const chunks = useQuery({
    queryKey: ["chunks", rid, type, section, cursor],
    queryFn: () =>
      api<Page<Chunk>>(
        `/runs/${rid}/chunks?${params({ type, section, cursor, limit: 20 })}`,
      ),
    enabled: !!rid,
  });
  const selected = search.chunk ?? chunks.data?.items[0]?.id;
  const chunk = useQuery({
    queryKey: ["chunk", rid, selected],
    queryFn: () => api<Chunk>(`/chunks/${selected}?run_id=${rid}`),
    enabled: !!selected && !!rid,
  });
  const view = search.view ?? "pdf";
  const artifact = useQuery({
    queryKey: ["artifact", rid, view],
    queryFn: async () => {
      const res = await fetch(
        `/api/v1/runs/${rid}/artifacts/${view === "raw" ? "raw_markdown" : "normalized_markdown"}`,
      );
      if (!res.ok) throw Error("此阶段尚无解析结果");
      return res.text();
    },
    enabled: !!rid && view !== "pdf",
  });
  const change = (next: Partial<typeof search>) =>
    go(`/documents/${documentId}`, { ...search, run: rid, ...next });
  const start = useMutation({
    mutationFn: () => post<Run>("/ingestion-runs", { document_id: documentId }),
    onSuccess: (r) => {
      void client.invalidateQueries({ queryKey: ["document"] });
      void change({ run: r.id });
    },
  });
  const retry = useMutation({
    mutationFn: () => post<Run>(`/runs/${rid}/retry`, { stage: "auto" }),
    onSuccess: (r) => {
      void client.invalidateQueries({ queryKey: ["document-runs"] });
      void change({ run: r.id });
    },
  });
  const assetIds = [
    ...((chunk.data?.metadata?.linked_figures as string[] | undefined) ?? []),
    ...((chunk.data?.metadata?.linked_tables as string[] | undefined) ?? []),
  ];
  const assets = useQuery({
    queryKey: ["assets", rid],
    queryFn: () =>
      api<Page<Chunk>>(`/runs/${rid}/chunks?limit=100&type=figure`),
    enabled: !!rid && assetIds.length > 0,
  });
  if (doc.isLoading) return <Loading />;
  if (doc.error || !doc.data) return <ErrorBox error={doc.error} />;
  return (
    <>
      <div className="workbench-heading">
        <Button
          variant="ghost"
          size="icon"
          aria-label="返回文献库"
          onClick={() => go("/")}
        >
          <ArrowLeft size={19} />
        </Button>
        <div>
          <div className="inline">
            <span className="eyebrow">PAPER WORKSPACE</span>
            <Status value={run.data?.status ?? doc.data.status} />
          </div>
          <h1>{doc.data.title}</h1>
          <p className="muted">
            {doc.data.page_count} 页<span className="separator">/</span>
            {run.data?.source === "artifact_import"
              ? "历史验证结果"
              : "实时处理记录"}
            <span className="separator">/</span>
            {date(run.data?.created_at)}
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => go("/retrieval", { doc: documentId })}
        >
          <MessageSquare size={16} />
          向这篇论文提问
        </Button>
      </div>
      <ErrorBox error={run.error || start.error || retry.error} />
      {run.data?.error && <ErrorBox error={run.data.error.message} />}
      {!rid ? (
        <div className="panel">
          <Empty title="论文已保存，尚未开始处理">
            点击下方按钮，使用固定的 MinerU 与阿里云方案。
          </Empty>
          <div className="center">
            <Button onClick={() => start.mutate()} disabled={start.isPending}>
              开始处理
              <ArrowRight size={16} />
            </Button>
          </div>
        </div>
      ) : (
        <>
          {run.data && (
            <div className="workflow-bar">
              <Steps run={run.data} />
              <div className="inline">
                <Select
                  aria-label="选择历史运行"
                  value={rid}
                  onChange={(e) => {
                    setCursor("");
                    void change({ run: e.target.value, chunk: undefined });
                  }}
                >
                  {runs.data?.items.map((r) => (
                    <option value={r.id} key={r.id}>
                      {r.source === "artifact_import" ? "历史导入" : "运行"} ·{" "}
                      {date(r.created_at)} · {r.status}
                    </option>
                  ))}
                </Select>
                {["failed", "interrupted"].includes(run.data.status) && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={retry.isPending}
                    onClick={() => retry.mutate()}
                  >
                    <RefreshCw size={14} />
                    重试
                  </Button>
                )}
              </div>
            </div>
          )}
          <div className="mobile-pane-toggle">
            <Button
              variant={mobile === "source" ? "default" : "outline"}
              onClick={() => setMobile("source")}
            >
              原文
            </Button>
            <Button
              variant={mobile === "chunks" ? "default" : "outline"}
              onClick={() => setMobile("chunks")}
            >
              分块
            </Button>
          </div>
          <div className={`workspace mobile-${mobile}`}>
            <section className="panel source-pane">
              <div className="pane-header">
                <span>
                  <BookOpen size={16} />
                  原文阅读
                </span>
                <Tabs value={view} onValueChange={(v) => change({ view: v })}>
                  <TabsList>
                    <TabsTrigger value="pdf">PDF</TabsTrigger>
                    <TabsTrigger value="raw">原始 Markdown</TabsTrigger>
                    <TabsTrigger value="normalized">整理结果</TabsTrigger>
                  </TabsList>
                </Tabs>
              </div>
              {view === "pdf" ? (
                <Suspense fallback={<Loading />}>
                  <PdfReader documentId={documentId} />
                </Suspense>
              ) : (
                <div className="reading-scroll">
                  <ErrorBox error={artifact.error} />
                  {artifact.isLoading ? (
                    <Loading />
                  ) : (
                    artifact.data &&
                    (view === "raw" ? (
                      <pre className="raw-text">{artifact.data}</pre>
                    ) : (
                      <Markdown text={artifact.data} />
                    ))
                  )}
                </div>
              )}
              <div className="pane-footnote">
                文档与章节级来源 · 轻量解析未提供页内坐标
              </div>
            </section>
            <section className="panel chunk-pane">
              <div className="pane-header">
                <span>
                  <Layers3 size={16} />
                  分块检查
                </span>
                <span className="muted">
                  {stats.data?.count ?? 0} 块 · 上限 2500
                </span>
              </div>
              <div className="chunk-filters">
                <Select
                  aria-label="分块类型"
                  value={type}
                  onChange={(e) => {
                    setType(e.target.value);
                    setCursor("");
                  }}
                >
                  <option value="">所有类型</option>
                  {Object.entries(kindLabel).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </Select>
                <Select
                  aria-label="章节筛选"
                  value={section}
                  onChange={(e) => {
                    setSection(e.target.value);
                    setCursor("");
                  }}
                >
                  <option value="">所有章节</option>
                  {stats.data?.sections.map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </Select>
              </div>
              <div className="chunk-body">
                <div className="chunk-list">
                  <ErrorBox error={chunks.error} />
                  {chunks.isLoading ? (
                    <Loading />
                  ) : !chunks.data?.items.length ? (
                    <p className="muted padding">暂无分块</p>
                  ) : (
                    chunks.data.items.map((c) => (
                      <button
                        key={c.id}
                        className={`chunk-item ${selected === c.id ? "selected" : ""}`}
                        onClick={() => {
                          void change({ chunk: c.id });
                          setRaw(false);
                        }}
                      >
                        <span className="chunk-item-top">
                          <b className="mono">
                            {String(c.index + 1).padStart(2, "0")}
                          </b>
                          <small>{c.token_count} t</small>
                        </span>
                        <strong>
                          {kindLabel[c.chunk_type] ?? c.chunk_type}
                        </strong>
                        <small>{c.section || "未标注章节"}</small>
                      </button>
                    ))
                  )}
                  <div className="chunk-pagination">
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={!cursor}
                      onClick={() => setCursor("")}
                    >
                      首页
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={!chunks.data?.next_cursor}
                      onClick={() => setCursor(chunks.data!.next_cursor!)}
                    >
                      更多
                    </Button>
                  </div>
                </div>
                <div className="chunk-detail">
                  <ErrorBox error={chunk.error} />
                  {chunk.isLoading ? (
                    <Loading />
                  ) : chunk.data ? (
                    <>
                      <div className="inline between">
                        <span className="tag">
                          {kindLabel[chunk.data.chunk_type] ??
                            chunk.data.chunk_type}
                        </span>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setRaw(!raw)}
                        >
                          {raw ? "渲染阅读" : "原始文本"}
                        </Button>
                      </div>
                      <h3>{chunk.data.section || "分块正文"}</h3>
                      <div className="chunk-metrics">
                        <span>
                          <b>{chunk.data.token_count}</b> tokens
                        </span>
                        <span>
                          实际重叠 <b>{chunk.data.actual_overlap_tokens}</b>
                        </span>
                      </div>
                      {raw ? (
                        <pre className="raw-text">{chunk.data.text}</pre>
                      ) : (
                        <Markdown text={chunk.data.text ?? ""} />
                      )}
                      {assetIds.length > 0 && (
                        <div className="asset-links">
                          <h4>关联图注</h4>
                          {assetIds.map((id) => (
                            <Button
                              key={id}
                              variant="outline"
                              size="sm"
                              onClick={() => {
                                const asset = assets.data?.items.find((c) =>
                                  c.preview.includes(`[FIGURE: ${id}]`),
                                );
                                if (asset) void change({ chunk: asset.id });
                              }}
                            >
                              <FileText size={13} />
                              {id}
                              <ArrowUpRightIcon />
                            </Button>
                          ))}
                        </div>
                      )}
                      <details className="metadata">
                        <summary>来源与分块元数据</summary>
                        <pre>
                          {JSON.stringify(chunk.data.metadata, null, 2)}
                        </pre>
                      </details>
                    </>
                  ) : (
                    <Empty title="分块尚未生成">
                      处理完成后会显示完整文本。
                    </Empty>
                  )}
                </div>
              </div>
              <div className="pane-footnote">
                cl100k_base · 最大块 {stats.data?.max_tokens ?? "—"} tokens ·
                目标重叠 200
              </div>
            </section>
          </div>
          <details className="panel distribution">
            <summary>查看 token 分布与运行详情</summary>
            <div className="chart">
              <ResponsiveContainer width="100%" height={150}>
                <BarChart data={stats.data?.distribution ?? []}>
                  <XAxis dataKey="index" tickLine={false} axisLine={false} />
                  <Tooltip />
                  <Bar dataKey="tokens" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <Button variant="ghost" onClick={() => go("/runs", { run: rid })}>
              打开运行记录
              <ArrowRight size={16} />
            </Button>
          </details>
        </>
      )}
    </>
  );
}
function ArrowUpRightIcon() {
  return <ChevronRight size={12} />;
}
