import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  BookOpen,
  Check,
  Download,
  FlaskConical,
  History,
  LoaderCircle,
  Play,
  RefreshCw,
} from "lucide-react";
import {
  api,
  post,
  terminal,
  type Run,
  type Page,
  type Event,
  type ResearchResult,
} from "../lib/api";
import { useEvents, useRun } from "../lib/hooks";
import { useNav, usePageSearch } from "../lib/navigation";
import { date } from "../lib/utils";
import {
  Button,
  Empty,
  ErrorBox,
  Loading,
  Select,
  Status,
} from "../components/ui";
import { Markdown } from "../components/markdown";

const stages = [
  ["retrieval", "检索文献"],
  ["evidence", "汇总证据"],
  ["assessment", "判断缺口"],
  ["rewrite", "改写查询"],
  ["synthesis", "生成草稿"],
  ["validation", "检查引用"],
] as const;
const quality = {
  in_progress: "研究进行中",
  draft: "已生成研究草稿",
  partial: "部分结果 · 存在局限",
  insufficient: "证据不足",
  failed: "未完成研究",
};
const reasons: Record<string, string> = {
  sufficient_evidence: "达到最低证据覆盖要求",
  round_limit: "达到最大研究轮数",
  no_new_queries: "没有新的补充查询",
  no_new_evidence: "本轮没有新增证据",
  no_evidence: "未检索到证据",
  no_relevant_evidence: "未获得相关证据",
  assessment_failed: "覆盖判断失败",
  synthesis_failed: "草稿生成失败",
};
function warningText(value: string) {
  if (value.startsWith("retrieval_failed:"))
    return "某次检索失败，研究可能缺少部分证据。";
  if (value.startsWith("assessment_failed:"))
    return "模型未能完成证据覆盖判断。";
  if (value.startsWith("synthesis_failed:"))
    return "模型未能生成草稿，已保留检索证据。";
  if (value === "unsupported_claim_or_date_removed")
    return "部分论断或年份未通过引用检查，已从报告中移除。";
  return value;
}
function duration(
  start?: string | null,
  end?: string | null,
  now = Date.now(),
) {
  if (!start) return "等待开始";
  const seconds = Math.max(
    0,
    Math.floor(((end ? Date.parse(end) : now) - Date.parse(start)) / 1000),
  );
  return seconds < 60
    ? `${seconds} 秒`
    : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

export function ResearchFlow({
  events,
  running,
  stage,
}: {
  events: Event[];
  running: boolean;
  stage?: string;
}) {
  return (
    <ol className="research-flow" aria-label="研究执行阶段">
      {stages.map(([key, title], index) => {
        const latest = events.filter((e) => e.stage === key).at(-1);
        const active = running && stage === key && latest?.phase === "started";
        const state = active
          ? "active"
          : latest?.phase === "failed"
            ? "error"
            : latest?.phase === "completed"
              ? "done"
              : "waiting";
        const label = active
          ? "执行中"
          : state === "done"
            ? "已执行"
            : state === "error"
              ? "异常"
              : running
                ? "等待"
                : "未执行";
        return (
          <li className={`research-node ${state}`} key={key}>
            <span className="research-node-icon">
              {active ? (
                <LoaderCircle size={17} className="spin" />
              ) : state === "done" ? (
                <Check size={17} />
              ) : (
                index + 1
              )}
            </span>
            <strong>{title}</strong>
            <small>{label}</small>
          </li>
        );
      })}
    </ol>
  );
}

export function ResearchPage() {
  const go = useNav(),
    search = usePageSearch(),
    client = useQueryClient();
  const [topic, setTopic] = useState("");
  const [mode, setMode] = useState("review");
  const [rounds, setRounds] = useState(3),
    [top, setTop] = useState(5);
  const [cursor, setCursor] = useState(""),
    [selected, setSelected] = useState<string | null>(null);
  const [clock, setClock] = useState(Date.now());
  const request = useRef<{ body: string; key: string } | null>(null);
  const retryRequest = useRef<{ run: string; key: string } | null>(null);
  const evidencePanel = useRef<HTMLElement>(null);
  const current = useRun(search.run),
    events = useEvents(search.run);
  const run = current.data,
    activeRun = !!run && !terminal(run.status);
  const history = useQuery({
    queryKey: ["research-history", cursor],
    queryFn: () =>
      api<Page<Run>>(`/runs?kind=research&limit=10&cursor=${cursor}`),
    refetchInterval: 5000,
  });
  const result = useQuery({
    queryKey: ["result", search.run, "research"],
    queryFn: () => api<ResearchResult>(`/research-runs/${search.run}/result`),
    enabled: !!search.run && (!run || run.kind === "research"),
    refetchInterval: search.run && !terminal(run?.status) ? 2000 : false,
  });
  const data = result.data;
  const start = useMutation({
    mutationFn: () => {
      const payload = {
        topic: topic.trim(),
        mode,
        max_rounds: rounds,
        queries_per_round: 2,
        top_k: top,
      };
      const body = JSON.stringify(payload);
      if (request.current?.body !== body)
        request.current = { body, key: crypto.randomUUID() };
      return post<Run>("/research-runs", payload, request.current!.key);
    },
    onSuccess: (created) => {
      request.current = null;
      setCursor("");
      void client.invalidateQueries({ queryKey: ["research-history"] });
      void go("/research", { run: created.id });
    },
  });
  const retry = useMutation({
    mutationFn: () => {
      if (retryRequest.current?.run !== search.run)
        retryRequest.current = { run: search.run!, key: crypto.randomUUID() };
      return post<Run>(
        `/runs/${search.run}/retry`,
        { stage: "auto" },
        retryRequest.current!.key,
      );
    },
    onSuccess: (created) => {
      retryRequest.current = null;
      void client.invalidateQueries({ queryKey: ["research-history"] });
      void go("/research", { run: created.id });
    },
  });
  useEffect(() => {
    setSelected(null);
  }, [search.run]);
  useEffect(() => {
    if (!activeRun) return;
    const tick = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(tick);
  }, [activeRun]);
  useEffect(() => {
    if (terminal(run?.status))
      void client.invalidateQueries({ queryKey: ["research-history"] });
  }, [run?.status, client]);
  const choose = (id: string) => {
    if (!data?.evidence.some((e) => e.id === id)) return;
    setSelected(id);
    evidencePanel.current?.scrollIntoView({
      behavior: "smooth",
      block: "nearest",
    });
  };
  const chosen =
    data?.evidence.find((e) => e.id === selected) ?? data?.evidence[0];
  const maxRounds = Number(data?.run_options.max_rounds ?? rounds);
  const roundNumber = data?.iterations.at(-1)?.round ?? 0;
  const currentStage =
    stages.find(([key]) => key === run?.stage)?.[1] ??
    (run?.status === "queued" ? "等待调度" : "研究任务");

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">FOLLOW THE EVIDENCE</p>
          <h1>研究 Agent</h1>
          <p className="muted">从一个问题出发，追踪每一轮检索、判断与证据。</p>
        </div>
        <span className="research-local">
          <FlaskConical size={17} /> 本地文献研究
        </span>
      </div>
      <section className="panel research-compose">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            start.mutate();
          }}
        >
          <label htmlFor="research-topic">你想研究什么？</label>
          <textarea
            id="research-topic"
            className="input"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            maxLength={2000}
            required
            rows={2}
            placeholder="例如：活性向列相中的拓扑缺陷如何影响集体运动？现有证据与局限是什么？"
          />
          <div className="research-controls">
            <label>
              输出形式
              <Select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="review">文献综述</option>
                <option value="timeline">发展时间线</option>
                <option value="answer">研究问答</option>
              </Select>
            </label>
            <label>
              最大轮数
              <Select
                value={rounds}
                onChange={(e) => setRounds(Number(e.target.value))}
              >
                {[1, 2, 3, 4, 5].map((n) => (
                  <option key={n} value={n}>
                    {n} 轮
                  </option>
                ))}
              </Select>
            </label>
            <label>
              每次检索
              <Select
                value={top}
                onChange={(e) => setTop(Number(e.target.value))}
              >
                {[3, 5, 10].map((n) => (
                  <option key={n} value={n}>
                    {n} 条证据
                  </option>
                ))}
              </Select>
            </label>
            <Button type="submit" disabled={!topic.trim() || start.isPending}>
              <Play size={15} />
              {start.isPending ? "正在提交…" : "开始研究"}
            </Button>
          </div>
          <p className="muted research-hint">
            每轮最多 2 个查询 · 仅查询当前知识库 · 关闭页面后任务继续运行
          </p>
        </form>
      </section>
      <ErrorBox
        error={
          start.error ||
          retry.error ||
          current.error ||
          result.error ||
          events.error ||
          history.error
        }
      />
      <div className="research-layout">
        <aside className="panel research-history">
          <div className="inline">
            <History size={17} />
            <h2>研究历史</h2>
          </div>
          {history.isLoading ? (
            <Loading />
          ) : history.data?.items.length ? (
            history.data.items.map((item) => (
              <button
                key={item.id}
                className={`run-list-item ${search.run === item.id ? "selected" : ""}`}
                onClick={() => go("/research", { run: item.id })}
              >
                <div className="inline between">
                  <Status value={item.status} />
                  <small>{date(item.created_at)}</small>
                </div>
                <strong>{item.title}</strong>
                <small>
                  {
                    quality[
                      (item.result?.research_status as keyof typeof quality) ??
                        "in_progress"
                    ]
                  }
                </small>
              </button>
            ))
          ) : (
            <p className="muted research-hint">
              还没有研究任务。输入主题，开始第一轮探索。
            </p>
          )}
          <div className="inline between">
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
              disabled={!history.data?.next_cursor}
              onClick={() => setCursor(history.data!.next_cursor!)}
            >
              下一页
            </Button>
          </div>
        </aside>
        <div className="research-main">
          {!search.run ? (
            <section className="panel">
              <Empty title="让研究过程清晰可见">
                任务开始后，这里会逐轮展示查询、证据、覆盖缺口与研究报告。
              </Empty>
            </section>
          ) : current.isLoading ? (
            <Loading />
          ) : run?.kind !== "research" ? (
            <ErrorBox error="请选择研究类型的任务" />
          ) : (
            <>
              <section className="panel research-progress">
                <div className="inline between">
                  <span className="eyebrow">研究过程</span>
                  <Status value={run.status} />
                </div>
                <h2>{data?.topic ?? run.title}</h2>
                <div className="research-metrics" aria-live="polite">
                  <div>
                    <small>研究轮次</small>
                    <strong>
                      {roundNumber} <span>/ {maxRounds}</span>
                    </strong>
                  </div>
                  <div>
                    <small>证据片段</small>
                    <strong>{data?.evidence.length ?? 0}</strong>
                  </div>
                  <div>
                    <small>来源论文</small>
                    <strong>{data?.source_count ?? 0}</strong>
                  </div>
                  <div>
                    <small>{activeRun ? "已运行" : "执行用时"}</small>
                    <strong>
                      {duration(run.started_at, run.finished_at, clock)}
                    </strong>
                  </div>
                </div>
                <ResearchFlow
                  events={events.data ?? []}
                  running={activeRun}
                  stage={run.stage}
                />
                <p className="research-hint muted">
                  {activeRun
                    ? `当前：${currentStage}。覆盖不足时会回到检索，继续补充证据。`
                    : `停止原因：${reasons[data?.stop_reason ?? ""] ?? (run.status === "interrupted" ? "服务重启，任务已中断" : run.status === "failed" ? "执行失败" : "流程结束")}`}
                </p>
                <ErrorBox error={run.error?.message} />
                {["failed", "interrupted"].includes(run.status) && (
                  <Button
                    variant="outline"
                    disabled={retry.isPending}
                    onClick={() => retry.mutate()}
                  >
                    <RefreshCw size={15} />
                    从头重新运行
                  </Button>
                )}
              </section>
              {!!data?.iterations.length && (
                <section className="panel research-rounds">
                  <h2>逐轮探索</h2>
                  {data.iterations.map((step) => (
                    <details
                      className="research-round"
                      key={`${run.id}-${step.round}`}
                      open={step.round === roundNumber}
                    >
                      <summary>
                        <span className="round-number">{step.round}</span>
                        <strong>第 {step.round} 轮</strong>
                        <span className="muted">
                          新增 {step.new_evidence} 条证据
                        </span>
                        <span className="tag">
                          {step.sufficient === true
                            ? "达到覆盖要求"
                            : step.sufficient === false
                              ? "存在缺口"
                              : "评估中"}
                        </span>
                      </summary>
                      <div className="research-round-body">
                        <h3>检索查询</h3>
                        <ul>
                          {step.queries.map((q) => (
                            <li key={q}>{q}</li>
                          ))}
                        </ul>
                        {!!step.gaps.length && (
                          <div className="research-gap">
                            <h3>待补充的证据</h3>
                            <ul>
                              {step.gaps.map((gap, i) => (
                                <li key={i}>{gap}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {!!step.next_queries.length && (
                          <>
                            <h3>建议补充查询</h3>
                            <ul>
                              {step.next_queries.map((q) => (
                                <li key={q}>{q}</li>
                              ))}
                            </ul>
                          </>
                        )}
                        <div className="research-event-log">
                          {events.data
                            ?.filter((e) => e.round === step.round)
                            .map((e) => (
                              <div
                                key={e.event_id}
                                className={
                                  e.phase === "failed"
                                    ? "research-event-error"
                                    : ""
                                }
                              >
                                <span>{e.message}</span>
                                <small>
                                  {e.phase === "started"
                                    ? "开始"
                                    : e.phase === "failed"
                                      ? "异常"
                                      : "完成"}
                                  {e.elapsed_ms != null
                                    ? ` · ${(e.elapsed_ms / 1000).toFixed(1)} s`
                                    : ""}
                                </small>
                              </div>
                            ))}
                        </div>
                      </div>
                    </details>
                  ))}
                </section>
              )}
              <div className="research-output">
                <section className="panel research-report">
                  <div className="inline between">
                    <h2>研究报告</h2>
                    <span
                      className={`tag ${data?.status === "draft" ? "research-good" : ""}`}
                    >
                      {quality[data?.status ?? "in_progress"]}
                    </span>
                  </div>
                  {data?.markdown ? (
                    <Markdown
                      text={data.markdown}
                      onResearchCitation={choose}
                    />
                  ) : (
                    <Empty
                      title={activeRun ? "正在收集与核对证据" : "暂无报告"}
                    >
                      已找到的证据会显示在右侧。报告生成后可点击引用查看来源。
                    </Empty>
                  )}
                  {!!data?.warnings.length && (
                    <details className="research-warnings">
                      <summary>查看 {data.warnings.length} 条运行警告</summary>
                      <ul>
                        {data.warnings.map((w, i) => (
                          <li key={i}>{warningText(w)}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                  {terminal(run.status) && data?.markdown && (
                    <div className="inline research-downloads">
                      <a
                        href={`/api/v1/runs/${run.id}/artifacts/research_report`}
                        download
                      >
                        <Download size={14} />
                        下载 Markdown
                      </a>
                      <a
                        href={`/api/v1/runs/${run.id}/artifacts/research_data`}
                        download
                      >
                        <Download size={14} />
                        下载研究记录
                      </a>
                    </div>
                  )}
                </section>
                <section
                  className="panel research-evidence"
                  ref={evidencePanel}
                  aria-label="研究证据"
                >
                  <div className="inline">
                    <BookOpen size={17} />
                    <h2>证据来源</h2>
                    <span className="tag">{data?.evidence.length ?? 0}</span>
                  </div>
                  {data?.evidence.length ? (
                    <>
                      <div className="research-evidence-list">
                        {data.evidence.map((e) => (
                          <button
                            key={e.id}
                            className={chosen?.id === e.id ? "selected" : ""}
                            aria-pressed={chosen?.id === e.id}
                            onClick={() => setSelected(e.id)}
                          >
                            <span className="citation">{e.id}</span>
                            <span>
                              {e.title}
                              <small>
                                {e.year ?? "年份未记录"}
                                {e.section ? ` · ${e.section}` : ""}
                              </small>
                            </span>
                          </button>
                        ))}
                      </div>
                      {chosen && (
                        <div className="research-excerpt">
                          <strong>
                            {chosen.id} · {chosen.title}
                          </strong>
                          <p>{chosen.text}</p>
                          {chosen.excerpt_truncated && (
                            <small className="muted">
                              此处为截取片段，可打开分块查看完整内容。
                            </small>
                          )}
                          {chosen.document_id && chosen.run_id ? (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() =>
                                go(`/documents/${chosen.document_id}`, {
                                  run: chosen.run_id!,
                                  chunk: chosen.chunk_id,
                                  view: "pdf",
                                })
                              }
                            >
                              打开原文与分块
                              <ArrowRight size={14} />
                            </Button>
                          ) : (
                            <p className="muted">
                              该证据尚未关联到网页文献，已保留研究片段。
                            </p>
                          )}
                        </div>
                      )}
                    </>
                  ) : (
                    <p className="muted research-hint">等待检索到可用证据。</p>
                  )}
                </section>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
