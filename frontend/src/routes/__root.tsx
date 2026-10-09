import { createRootRoute, Outlet, redirect } from "@tanstack/react-router";
import { PreviewHostBridge } from "@/components/preview-host-bridge";
import { api } from "@/lib/api";

export const Route = createRootRoute({
  component: RootComponent,
  beforeLoad: async ({ location }) => {
    // Skip auth check on login and monitor pages — prevents redirect loop
    if (location.pathname === "/login" || location.pathname === "/monitor") return;
    try {
      const auth = await api.authCheck();
      if (auth.auth_required && !auth.authenticated) {
        throw redirect({ to: "/login" });
      }
    } catch (e) {
      if (e && typeof e === "object" && "type" in e) throw e;
    }
  },
});

function RootComponent() {
  return (
    <>
      <PreviewHostBridge />
      <Outlet />
    </>
  );
}
