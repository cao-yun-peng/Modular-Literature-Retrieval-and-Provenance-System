import { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  type ColumnDef,
} from "@tanstack/react-table";
import {
  ArrowUpRight,
  FileText,
  Plus,
  Search,
  UploadCloud,
  Library,
  Layers3,
  ArrowRight,
  LoaderCircle,
} from "lucide-react";
import {
  api,
  post,
  params,
  type Document,
  type Run,
  type Page,
  type Config,
} from "../lib/api";
import { useNav } from "../lib/navigation";
import { date } from "../lib/utils";
import {
  Button,
  Dialog,
  Input,
  Select,
  Status,
  ErrorBox,
  Empty,
  Loading,
} from "../components/ui";

export function DocumentsPage() {
  const go = useNav(),
    client = useQueryClient();
  const [q, setQ] = useState(""),
    [status, setStatus] = useState(""),
    [cursor, setCursor] = useState(""),
    [open, setOpen] = useState(false),
    [file, setFile] = useState<File | null>(null),
    [uploaded, setUploaded] = useState<Document | null>(null);
  const config = useQuery({
    queryKey: ["config"],
    queryFn: () => api<Config>("/config/public"),
  });
  const docs = useQuery({
    queryKey: ["documents", q, status, cursor],
    queryFn: () =>
      api<Page<Document>>(`/documents?${params({ q, status, cursor })}`),
    refetchInterval: 10000,
  });
  const stats = useQuery({
    queryKey: ["collections"],
    queryFn: () =>
      api<
        {
          name: string;
          document_count: number;
          chunk_count: number;
          status: string;
        }[]
      >("/collections"),
    refetchInterval: 10000,
  });
  const upload = useMutation({
    mutationFn: async () => {
      if (!file) throw Error("请选择 PDF");
      const form = new FormData();
      form.append("file", file);
      return api<Document>("/documents/upload", { method: "POST", body: form });
    },
    onSuccess: (d) => {
      setUploaded(d);
      void client.invalidateQueries({ queryKey: ["documents"] });
    },
  });
  const start = useMutation({
    mutationFn: (d: Document) =>
      post<Run>("/ingestion-runs", { document_id: d.id }),
    onSuccess: (r) => {
      setOpen(false);
      void client.invalidateQueries({ queryKey: ["documents"] });
      void go(`/documents/${r.document_id}`, { run: r.id });
    },
  });
  const columns = useMemo<ColumnDef<Document>[]>(
    () => [
      {
        header: "文献",
        accessorKey: "title",
        cell: ({ row }) => (
          <button
            className="document-title"
            onClick={() => go(`/documents/${row.original.id}`)}
          >
            <span className="file-icon">
              <FileText size={21} />
            </span>
            <span>
              <strong>{row.original.title}</strong>
              <small>
                {row.original.filename} · {row.original.page_count} 页
              </small>
            </span>
          </button>
        ),
      },
      {
        header: "状态",
        accessorKey: "status",
        cell: ({ row }) => <Status value={row.original.status} />,
      },
      {
        header: "分块",
        accessorKey: "chunk_count",
        cell: ({ row }) => (
          <span className="mono">{row.original.chunk_count || "—"}</span>
        ),
      },
      {
        header: "添加时间",
        accessorKey: "created_at",
        cell: ({ row }) => (
          <span className="muted">{date(row.original.created_at)}</span>
        ),
      },
      {
        header: "",
        id: "open",
        cell: ({ row }) => (
          <Button
            variant="ghost"
            size="icon"
            aria-label={`打开 ${row.original.title}`}
            onClick={() => go(`/documents/${row.original.id}`)}
          >
            <ArrowUpRight size={18} />
          </Button>
        ),
      },
    ],
    [go],
  );
  const table = useReactTable({
    data: docs.data?.items ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
  });
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">YOUR RESEARCH, CONNECTED</p>
          <h1>文献库</h1>
          <p className="muted">从原文到证据，让每一段知识有据可查。</p>
        </div>
        <Button
          onClick={() => {
            setOpen(true);
            setUploaded(null);
            setFile(null);
            upload.reset();
            start.reset();
          }}
        >
          <Plus size={17} />
          添加论文
        </Button>
      </div>
      <div className="stats-grid">
        <div className="stat-card">
          <Library size={19} />
          <span>收录文献</span>
          <strong>
            {stats.data?.[0]?.document_count ?? "—"}
            <small>篇</small>
          </strong>
        </div>
        <div className="stat-card">
          <Layers3 size={19} />
          <span>知识分块</span>
          <strong>
            {stats.data?.[0]?.chunk_count ?? "—"}
            <small>块</small>
          </strong>
        </div>
        <div className="stat-card accent">
          <span className="live-dot" />
          <span>统一分块方案</span>
          <strong>
            2500 <small>/ 200 tokens</small>
          </strong>
        </div>
      </div>
      <div className="panel">
        <div className="panel-toolbar">
          <div className="search-input">
            <Search size={16} />
            <Input
              aria-label="搜索文献"
              placeholder="搜索标题或文件名…"
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setCursor("");
              }}
            />
          </div>
          <Select
            aria-label="筛选状态"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setCursor("");
            }}
          >
            <option value="">所有状态</option>
            <option value="succeeded">已完成</option>
            <option value="pending">待处理</option>
            <option value="running">处理中</option>
            <option value="failed">失败</option>
            <option value="interrupted">已中断</option>
          </Select>
        </div>
        <ErrorBox error={docs.error} />
        {docs.isLoading ? (
          <Loading />
        ) : docs.data?.items.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                {table.getHeaderGroups().map((h) => (
                  <tr key={h.id}>
                    {h.headers.map((c) => (
                      <th key={c.id}>
                        {flexRender(c.column.columnDef.header, c.getContext())}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map((row) => (
                  <tr key={row.id}>
                    {row.getVisibleCells().map((c) => (
                      <td key={c.id}>
                        {flexRender(c.column.columnDef.cell, c.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          !docs.error && (
            <Empty title="这里还没有匹配的论文">
              添加一篇 PDF，开始建立你的文献知识库。
            </Empty>
          )
        )}
        <div className="table-footer">
          <span>当前知识库 · {config.data?.collection ?? "加载中"}</span>
          <div className="inline">
            <Button
              size="sm"
              variant="ghost"
              disabled={!cursor}
              onClick={() => setCursor("")}
            >
              返回首页
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={!docs.data?.next_cursor}
              onClick={() => setCursor(docs.data!.next_cursor!)}
            >
              下一页
              <ArrowRight size={14} />
            </Button>
          </div>
        </div>
      </div>
      <div className="quiet-note">
        <span className="live-dot" />
        MinerU 解析<span>→</span>结构整理<span>→</span>Token 分块<span>→</span>
        阿里云 Embedding<span>→</span>可追溯检索
      </div>
      <Dialog
        open={open}
        onOpenChange={(v) => {
          if (!upload.isPending && !start.isPending) setOpen(v);
        }}
        title="添加一篇论文"
        description="上传与处理分开进行。保存文件后，再确认开始处理。"
      >
        {!uploaded ? (
          <>
            <label className="drop-zone">
              <UploadCloud size={32} />
              <strong>{file?.name ?? "选择 PDF 文件"}</strong>
              <span>未加密 PDF · 最多 200 页 · 最大 50 MB</span>
              <input
                type="file"
                accept="application/pdf,.pdf"
                aria-label="选择 PDF 文件"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </label>
            <ErrorBox error={upload.error} />
            <Button
              className="full-width"
              disabled={!file || upload.isPending}
              onClick={() => upload.mutate()}
            >
              {upload.isPending ? (
                <LoaderCircle className="spin" size={16} />
              ) : (
                <UploadCloud size={16} />
              )}
              保存文件
            </Button>
          </>
        ) : (
          <>
            <div className="upload-summary">
              <FileText />
              <strong>{uploaded.filename}</strong>
              <span>{uploaded.page_count} 页</span>
            </div>
            <div className="notice">
              固定方案：MinerU → 2500 / 200 tokens → 阿里云 Embedding。处理时
              PDF 将发送到 MinerU，分块文本将发送到已配置的阿里云 API。
            </div>
            <ErrorBox error={start.error} />
            {uploaded.latest_successful_run_id ? (
              <Button
                className="full-width"
                onClick={() => go(`/documents/${uploaded.id}`)}
              >
                已有成功结果，直接打开
                <ArrowUpRight size={16} />
              </Button>
            ) : (
              <Button
                className="full-width"
                disabled={start.isPending}
                onClick={() => start.mutate(uploaded)}
              >
                {start.isPending ? "正在提交…" : "开始处理"}
                <ArrowRight size={16} />
              </Button>
            )}
          </>
        )}
      </Dialog>
    </>
  );
}
