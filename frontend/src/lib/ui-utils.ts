import { ApiError } from "../api/client";
import type { MessageKey } from "./i18n";

export function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

export function errorMessage(error: unknown, t: (key: MessageKey) => string): string {
  if (error instanceof ApiError) {
    if (error.code === "network_error") return t("error.network");
    if (error.message && !error.message.startsWith("HTTP ")) return error.message;
    if (error.code === "llm_unavailable") return t("error.llm_unavailable");
    if (error.code === "database_unavailable") return t("error.database_unavailable");
  }
  return t("error.generic");
}

export function statusTone(status: string): "positive" | "negative" | "warning" | "neutral" {
  if (status === "answered") return "positive";
  if (status === "error") return "negative";
  if (status === "limit_reached") return "warning";
  return "neutral";
}
