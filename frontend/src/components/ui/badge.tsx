import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Badge({
  className,
  tone = "neutral",
  children,
}: {
  className?: string;
  tone?: "neutral" | "accent" | "success" | "danger" | "warn";
  children: ReactNode;
}) {
  const tones = {
    neutral: "bg-elevated text-muted",
    accent: "bg-accent/15 text-accent",
    success: "bg-success/15 text-success",
    danger: "bg-danger/15 text-danger",
    warn: "bg-warn/15 text-warn",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
