import { spriteUrl } from "@/lib/pokego/pokemon";
import { cn } from "@/lib/utils";

export function Sprite({
  name,
  shiny,
  size = 32,
  className,
}: {
  name: string;
  shiny?: boolean;
  size?: number;
  className?: string;
}) {
  const src = spriteUrl(name, shiny);
  const initials = name.slice(0, 2).toUpperCase();
  if (!src) {
    return (
      <div
        className={cn("grid place-items-center rounded-xs bg-elevated text-[9px] text-subtle", className)}
        style={{ width: size, height: size }}
      >
        {initials}
      </div>
    );
  }
  return (
    <img
      src={src}
      alt=""
      width={size}
      height={size}
      className={cn("pixelated shrink-0", className)}
      style={{ imageRendering: "pixelated", width: size, height: size }}
      crossOrigin="anonymous"
    />
  );
}
