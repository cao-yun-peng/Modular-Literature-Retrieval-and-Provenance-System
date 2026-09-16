import { useQuery } from "@tanstack/react-query";
import { Cpu, Database, FileText, Layers3, ShieldCheck } from "lucide-react";
import { api, type Config } from "../lib/api";
import { Status, ErrorBox, Loading } from "../components/ui";
export function SettingsPage() {
  const config = useQuery({
    queryKey: ["config"],
    queryFn: () => api<Config>("/config/public"),
  });
  const collections = useQuery({
    queryKey: ["collections"],
    queryFn: () => api<{ name: string; status: string }[]>("/collections"),
  });
  if (config.isLoading) return <Loading />;
  if (!config.data) return <ErrorBox error={config.error} />;
  const c = config.data;
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">YOUR LOCAL ENVIRONMENT</p>
          <h1>系统状态</h1>
          <p className="muted">固定的处理方案，清晰的服务边界。</p>
        </div>
        <span className="tag">
          <ShieldCheck size={15} />
          本机运行
        </span>
      </div>
      <div className="settings-grid">
        <section className="panel setting-card">
          <FileText />
          <h2>PDF 解析</h2>
          <strong>MinerU Agent</strong>
          <p>保留原始 Markdown 与解析缓存</p>
          <div className="setting-line">
            <span>文件限制</span>
            <b>50 MB / 200 页</b>
          </div>
          <div className="setting-line">
            <span>来源定位</span>
            <b>文档与章节</b>
          </div>
        </section>
        <section className="panel setting-card">
          <Layers3 />
          <h2>统一分块</h2>
          <strong>
            {c.chunk_limit} / {c.target_overlap} tokens
          </strong>
          <p>硬上限 / 目标重叠</p>
          <div className="setting-line">
            <span>计数口径</span>
            <b>{c.tokenizer}</b>
          </div>
          <div className="setting-line">
            <span>正文改写</span>
            <b>关闭</b>
          </div>
        </section>
        <section className="panel setting-card">
          <Cpu />
          <h2>Embedding 与回答</h2>
          <strong>{c.embedding_model}</strong>
          <p>{c.embedding_dimensions} 维 · 阿里云 DashScope</p>
          <div className="setting-line">
            <span>Embedding 密钥</span>
            <b>{c.embedding_configured ? "已配置" : "未配置"}</b>
          </div>
          <div className="setting-line">
            <span>{c.answer_model}</span>
            <b>{c.answer_configured ? "已配置" : "未配置"}</b>
          </div>
        </section>
        <section className="panel setting-card">
          <Database />
          <h2>知识库</h2>
          <strong>{c.collection}</strong>
          <p>Chroma + BM25 双索引</p>
          <div className="setting-line">
            <span>索引状态</span>
            <Status value={collections.data?.[0]?.status ?? "pending"} />
          </div>
          <div className="setting-line">
            <span>处理并发</span>
            <b>摄入 1 / 查询 2</b>
          </div>
        </section>
      </div>
      <div className="notice">
        服务配置只读。此页面不会自动调用收费模型接口；密钥留在本机 Python 后端。
      </div>
    </>
  );
}
