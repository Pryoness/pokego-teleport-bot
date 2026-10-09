import * as SwitchPrimitive from "@radix-ui/react-switch";
import { cn } from "@/lib/utils";

export function Switch({
  checked,
  onCheckedChange,
  id,
  label,
}: {
  checked: boolean;
  onCheckedChange: (v: boolean) => void;
  id?: string;
  label?: string;
}) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-3">
      {label ? <span className="text-sm text-fg">{label}</span> : null}
      <SwitchPrimitive.Root
        id={id}
        checked={checked}
        onCheckedChange={onCheckedChange}
        className={cn(
          "relative h-5 w-9 shrink-0 rounded-full bg-elevated shadow-[var(--shadow-border)] transition-colors",
          "data-[state=checked]:bg-accent",
        )}
      >
        <SwitchPrimitive.Thumb className="block size-4 translate-x-0.5 rounded-full bg-fg transition-transform data-[state=checked]:translate-x-[18px] data-[state=checked]:bg-accent-fg" />
      </SwitchPrimitive.Root>
    </label>
  );
}
