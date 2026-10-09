import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Panel({
  title,
  action,
  children,
  className,
  bodyClassName,
  flush,
}: {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  flush?: boolean;
}) {
  return (
    <section
      className={cn(
        "flex min-h-0 flex-col overflow-hidden rounded-lg bg-surface shadow-[var(--shadow-border)]",
        className,
      )}
    >
      {title ? (
        <header className="flex shrink-0 items-center justify-between gap-2 px-3 py-2">
          <h2 className="text-xs font-medium uppercase tracking-[0.14em] text-muted">{title}</h2>
          {action}
        </header>
      ) : null}
      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col overflow-hidden",
          flush ? "" : "px-3 pb-2.5",
          bodyClassName,
        )}
      >
        {children}
      </div>
    </section>
  );
}
