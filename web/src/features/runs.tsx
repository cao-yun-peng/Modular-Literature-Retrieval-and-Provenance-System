import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Activity, ArrowRight, RefreshCw } from "lucide-react";
import { api, post, type Run, type Page } from "../lib/api";
import { useRun, useEvents } from "../lib/hooks";
import { useNav, usePageSearch } from "../lib/navigation";
import { date } from "../lib/utils";
import { Button, Status, ErrorBox, Empty, Loading } from "../components/ui";
export function RunsPage() {
  const go = useNav(),
    search = usePageSearch(),
    [cursor, setCursor] = useState("");
  const history = useQuery({
    queryKey: ["runs", cursor],
    queryFn: () => api<Page<Run>>(`/runs?cursor=${cursor}`),
    refetchInterval: 5000,
  });
  const rid = search.run ?? history.data?.items[0]?.id,
    current = useRun(rid),
    events = useEvents(rid);
  const retry = useMutation({
    mutationFn: () => post<Run>(`/runs/${rid}/retry`, { stage: "auto" }),
    onSuccess: (r) => go("/runs", { run: r.id }),
  });
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">OBSERVE EVERY STEP</p>
          <h1>运行记录</h1>
          <p className="muted">查看处理阶段、真实结果与可恢复的失败。</p>
        </div>
        <Activity size={25} className="muted" />
      </div>
      <ErrorBox error={history.error || current.error || retry.error} />
      <div className="runs-grid">
        <section className="panel run-list">
          {history.isLoading ? (
            <Loading />
          ) : history.data?.items.length ? (
            history.data.items.map((r) => (
              <button
                key={r.id}
                className={`run-list-item ${rid === r.id ? "selected" : ""}`}
                onClick={() => go("/runs", { run: r.id })}
              >
                <div className="inline between">
                  <span className="tag">
                    {
                      {
                        ingestion: "论文处理",
                        retrieval: "检索问答",
                        research: "研究 Agent",
                      }[r.kind]
                    }
                  </span>
                  <Status value={r.status} />
                </div>
                <strong>{r.title}</strong>
                <small>
                  {date(r.created_at)} ·{" "}
                  {r.source === "artifact_import" ? "历史导入" : "实时执行"}
                </small>
              </button>
            ))
          ) : (
            <Empty title="还没有运行记录" />
          )}
          <div className="table-footer">
            <Button
              variant="ghost"
              size="sm"
              disabled={!cursor}
              onClick={() => setCursor("")}
            >
              首页
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={!history.data?.next_cursor}
              onClick={() => setCursor(history.data!.next_cursor!)}
            >
              下一页
            </Button>
          </div>
        </section>
        <section className="panel run-detail">
          {current.data ? (
            <>
              <div className="inline between">
                <Status value={current.data.status} />
                <span className="muted">
                  {current.data.source === "artifact_import"
                    ? "历史结果回放"
                    : "实时任务"}
                </span>
              </div>
              <h2>{current.data.title}</h2>
              <p className="muted">
                开始：{date(current.data.started_at)} · 结束：
                {date(current.data.finished_at)}
              </p>
              <ErrorBox error={current.data.error?.message} />
              {["failed", "interrupted"].includes(current.data.status) && (
                <Button
                  disabled={retry.isPending}
                  onClick={() => retry.mutate()}
                >
                  <RefreshCw size={15} />
                  {current.data.kind === "research"
                    ? "重新运行研究"
                    : "重试失败阶段"}
                </Button>
              )}
              <div className="timeline">
                {events.data?.length ? (
                  events.data.map((e) => (
                    <div key={e.event_id} className="timeline-event">
                      <span
                        className={`timeline-dot ${e.status === "failed" || e.phase === "failed" ? "bad" : ""}`}
                      />
                      <div>
                        <strong>{e.message || e.stage}</strong>
                        <small>
                          {e.stage} · {date(e.timestamp)}
                          {e.elapsed_ms !== undefined && e.elapsed_ms !== null
                            ? ` · ${(e.elapsed_ms / 1000).toFixed(2)} s`
                            : ""}
                        </small>
                        {e.total_units != null && (
                          <div className="progress-detail">
                            <progress
                              value={e.completed_units ?? 0}
                              max={e.total_units}
                            />
                            <span>
                              {e.completed_units} / {e.total_units} 块
                            </span>
                          </div>
                        )}
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="notice">
                    历史快照未记录逐阶段耗时。下面展示当时保存的实际结果。
                  </div>
                )}
              </div>
              <h3>处理结果</h3>
              <pre className="json-block">
                {JSON.stringify(current.data.result, null, 2)}
              </pre>
              <details className="metadata">
                <summary>本次模型与参数快照</summary>
                <pre>{JSON.stringify(current.data.config, null, 2)}</pre>
              </details>
              <Button
                variant="outline"
                onClick={() =>
                  go(
                    current.data!.kind === "ingestion"
                      ? `/documents/${current.data!.document_id}`
                      : current.data!.kind === "research"
                        ? "/research"
                        : "/retrieval",
                    { run: rid },
                  )
                }
              >
                打开
                {
                  {
                    ingestion: "论文工作台",
                    retrieval: "检索结果",
                    research: "研究过程与报告",
                  }[current.data.kind]
                }
                <ArrowRight size={16} />
              </Button>
            </>
          ) : (
            <Empty title="选择一条运行记录" />
          )}
        </section>
      </div>
    </>
  );
}
