import { createFileRoute } from "@tanstack/react-router";
import { CommandShell } from "@/components/dashboard/shell";

export const Route = createFileRoute("/")({ component: Home });

function Home() {
  return <CommandShell />;
}
