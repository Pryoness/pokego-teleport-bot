import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { api } from "@/lib/api";
import { useHuntStore } from "@/lib/pokego/store";

export function FeedPanel() {
  const [loggingIn, setLoggingIn] = useState(false);
  const running = useHuntStore((s) => s.hunter.running);

  const handleSxLogin = async () => {
    setLoggingIn(true);
    try {
      await api.sxLogin();
      toast("Opening SX login window — log in and it will continue automatically");
    } catch {
      toast("Failed to open login window — start the bot first");
    } finally {
      setLoggingIn(false);
    }
  };

  const handleSxLoginBack = async () => {
    try {
      await api.sxLoginBack();
      toast("Navigated back to SX dashboard");
    } catch {
      toast("Failed to navigate back");
    }
  };

  return (
    <Panel title="SX Login">
      <p className="mb-2 text-xs text-muted">
        If the bot session expires, open a visible browser window to re-authenticate with SX.
      </p>
      <div className="flex flex-col gap-1.5">
        <Button
          variant="primary"
          size="sm"
          onClick={handleSxLogin}
          disabled={loggingIn || !running}
        >
          {loggingIn ? "Opening…" : "Open SX Login"}
        </Button>
        <Button size="sm" variant="secondary" onClick={handleSxLoginBack} disabled={!running}>
          Return to Dashboard
        </Button>
      </div>
    </Panel>
  );
}
