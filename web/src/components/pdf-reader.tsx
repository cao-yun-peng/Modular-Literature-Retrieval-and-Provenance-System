import { useEffect, useRef, useState } from "react";
import { getDocument, GlobalWorkerOptions } from "pdfjs-dist";
import worker from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { ChevronLeft, ChevronRight, Minus, Plus } from "lucide-react";
import { Button, ErrorBox } from "./ui";
GlobalWorkerOptions.workerSrc = worker;
export function PdfReader({ documentId }: { documentId: string }) {
  const canvas = useRef<HTMLCanvasElement>(null),
    host = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  useEffect(() => {
    if (!host.current) return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.max(200, entry.contentRect.width - 40)),
    );
    observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  const [page, setPage] = useState(1),
    [pages, setPages] = useState(0),
    [scale, setScale] = useState(1),
    [error, setError] = useState<unknown>(null);
  useEffect(() => {
    setPage(1);
  }, [documentId]);
  useEffect(() => {
    let disposed = false;
    const task = getDocument({ url: `/api/v1/documents/${documentId}/source` });
    let rendering: { cancel: () => void } | undefined;
    task.promise
      .then(async (pdf) => {
        if (disposed) return;
        setPages(pdf.numPages);
        const p = await pdf.getPage(Math.min(page, pdf.numPages));
        if (disposed || !canvas.current) return;
        const base = p.getViewport({ scale: 1 });
        const viewport = p.getViewport({ scale: (width / base.width) * scale });
        const c = canvas.current;
        c.width = viewport.width;
        c.height = viewport.height;
        rendering = p.render({ canvas: c, viewport });
        await (rendering as unknown as { promise: Promise<void> }).promise;
        setError(null);
      })
      .catch((e) => {
        if (!disposed) setError(e);
      });
    return () => {
      disposed = true;
      rendering?.cancel();
      void task.destroy();
    };
  }, [documentId, page, scale, width]);
  return (
    <div className="pdf-reader">
      <div className="reader-toolbar">
        <div className="inline">
          <Button
            variant="ghost"
            size="icon"
            aria-label="上一页"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            <ChevronLeft size={16} />
          </Button>
          <span>
            {page} / {pages || "…"}
          </span>
          <Button
            variant="ghost"
            size="icon"
            aria-label="下一页"
            disabled={page >= pages}
            onClick={() => setPage((p) => p + 1)}
          >
            <ChevronRight size={16} />
          </Button>
        </div>
        <div className="inline">
          <Button
            variant="ghost"
            size="icon"
            aria-label="缩小"
            disabled={scale <= 0.5}
            onClick={() => setScale((s) => s - 0.15)}
          >
            <Minus size={15} />
          </Button>
          <span>{Math.round(scale * 100)}%</span>
          <Button
            variant="ghost"
            size="icon"
            aria-label="放大"
            disabled={scale >= 2}
            onClick={() => setScale((s) => s + 0.15)}
          >
            <Plus size={15} />
          </Button>
        </div>
      </div>
      <ErrorBox error={error} />
      <div className="pdf-canvas" ref={host}>
        <canvas ref={canvas} aria-label={`PDF 第 ${page} 页`} />
      </div>
    </div>
  );
}
