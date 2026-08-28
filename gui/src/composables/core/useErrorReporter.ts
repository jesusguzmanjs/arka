import { shallowRef } from "vue";

const webhookUrl = import.meta.env.VITE_DISCORD_WEBHOOK_URL?.trim();
const activeError = shallowRef<string | null>(null);

function errorMessage(error: unknown): string {
  if (typeof error === "string") return error;
  if (error instanceof Error) return error.stack ?? error.message;
  if (typeof error === "object" && error !== null && "message" in error && typeof error.message === "string") {
    return error.message;
  }

  return String(error);
}

function truncate(value: string, maxLength: number): string {
  return value.length <= maxLength ? value : `${value.slice(0, maxLength - 15)}\n[truncated]`;
}

export function useErrorReporter() {
  function triggerError(error: unknown): void {
    activeError.value = errorMessage(error);
  }

  function dismissError(): void {
    activeError.value = null;
  }

  async function reportToDiscord(errorMsg: string, userNotes: string): Promise<void> {
    if (!webhookUrl) {
      throw new Error("Crash reporting is not configured. Copy the error details and contact support.");
    }

    const errorDetails = truncate(errorMsg, 4_000);
    const notes = userNotes.trim();
    const response = await fetch(webhookUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: "🚨 **CueGrid crash report**",
        allowed_mentions: { parse: [] },
        embeds: [
          {
            title: "CueGrid operation failure",
            description: errorDetails,
            color: 14_442_869,
            ...(notes
              ? { fields: [{ name: "User notes", value: truncate(notes, 1_000) }] }
              : {}),
          },
        ],
      }),
    });

    if (!response.ok) {
      throw new Error(`Discord webhook request failed (${response.status}).`);
    }
  }

  return { activeError, triggerError, dismissError, reportToDiscord };
}
