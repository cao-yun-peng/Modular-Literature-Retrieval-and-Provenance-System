import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import {
  AlertCircle,
  CheckCircle2,
  Clock3,
  LoaderCircle,
  X,
} from "lucide-react";
import { cn } from "../lib/utils";

const buttonVariants = cva("button", {
  variants: {
    variant: {
      default: "button-primary",
      outline: "button-outline",
      ghost: "button-ghost",
      danger: "button-danger",
    },
    size: { default: "", sm: "button-sm", icon: "button-icon" },
  },
  defaultVariants: { variant: "default", size: "default" },
});
export function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : "button";
  return (
    <Comp
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  );
}
export function Input(props: React.ComponentProps<"input">) {
  return <input {...props} className={cn("input", props.className)} />;
}
export function Select(props: React.ComponentProps<"select">) {
  return <select {...props} className={cn("input select", props.className)} />;
}
export function Status({ value }: { value: string }) {
  const titles: Record<string, string> = {
    pending: "待处理",
    queued: "排队中",
    running: "处理中",
    succeeded: "已完成",
    failed: "失败",
    interrupted: "已中断",
    ready: "可用",
    writing: "写入中",
    needs_repair: "需要恢复",
  };
  const Icon =
    value === "succeeded" || value === "ready"
      ? CheckCircle2
      : value === "failed" || value === "needs_repair"
        ? AlertCircle
        : value === "running" || value === "writing"
          ? LoaderCircle
          : Clock3;
  return (
    <span className={`status status-${value}`}>
      <Icon size={13} className={value === "running" ? "spin" : ""} />
      {titles[value] ?? value}
    </span>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Clock3 size={25} />
      </span>
      <h3>{title}</h3>
      <div className="muted">{children}</div>
    </div>
  );
}
export function ErrorBox({ error }: { error: unknown }) {
  return error ? (
    <div role="alert" className="error-box">
      <AlertCircle size={17} />
      {error instanceof Error ? error.message : String(error)}
    </div>
  ) : null;
}
export function Loading() {
  return (
    <div className="loading" role="status">
      <LoaderCircle className="spin" size={20} />
      正在读取数据…
    </div>
  );
}
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="dialog-overlay" />
        <DialogPrimitive.Content className="dialog-content">
          <DialogPrimitive.Title>{title}</DialogPrimitive.Title>
          <DialogPrimitive.Description className="muted">
            {description}
          </DialogPrimitive.Description>
          <DialogPrimitive.Close className="dialog-close" aria-label="关闭">
            <X size={18} />
          </DialogPrimitive.Close>
          {children}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
export const Tabs = TabsPrimitive.Root;
export function TabsList(
  props: React.ComponentProps<typeof TabsPrimitive.List>,
) {
  return (
    <TabsPrimitive.List
      {...props}
      className={cn("tabs-list", props.className)}
    />
  );
}
export function TabsTrigger(
  props: React.ComponentProps<typeof TabsPrimitive.Trigger>,
) {
  return (
    <TabsPrimitive.Trigger
      {...props}
      className={cn("tabs-trigger", props.className)}
    />
  );
}
export const TabsContent = TabsPrimitive.Content;
