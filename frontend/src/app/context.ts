import { createContext, useContext } from "react";
import type { Membership, Session } from "../lib/types";
export const SessionContext = createContext<Session | null>(null);
export const OrgContext = createContext<
  (Membership & { corpus_version: number }) | null
>(null);
export function useSession() {
  const v = useContext(SessionContext);
  if (!v) throw new Error("Session required");
  return v;
}
export function useOrg() {
  const v = useContext(OrgContext);
  if (!v) throw new Error("Organisation required");
  return v;
}
