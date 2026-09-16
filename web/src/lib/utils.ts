import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
export function cn(...values: ClassValue[]) {
  return twMerge(clsx(values));
}
export const date = (s?: string | null) =>
  s
    ? new Date(s).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "未记录";
export const kindLabel: Record<string, string> = {
  body: "正文",
  figure: "图注",
  table: "表格",
  reference: "参考文献",
  title_abstract: "标题与摘要",
};
