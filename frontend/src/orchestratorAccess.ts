import type { SessionInfo } from "./api";

export function isPublicDemoSession(session: SessionInfo | undefined): boolean {
  return session?.principal.account_type === "public_demo";
}

export function canUseDemoOrderPicker(session: SessionInfo | undefined): boolean {
  const role = session?.principal.role;
  return isPublicDemoSession(session) || role === "admin" || role === "store_operator";
}

export function canUseManualOrderInput(session: SessionInfo | undefined): boolean {
  return !isPublicDemoSession(session);
}
