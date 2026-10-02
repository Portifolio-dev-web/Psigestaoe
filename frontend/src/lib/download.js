import { API, formatApiErrorDetail } from "@/lib/api";

// ---------------------------------------------------------------------------
// Baixa um arquivo gerado pela API (ZIP da conta, CSV da auditoria...).
// Usa fetch com o cookie de sessão (credentials: "include") e salva com o
// nome que o servidor mandar em Content-Disposition. Erro do servidor (ex.:
// limite de exportações, 429) vira uma mensagem legível em vez de um arquivo
// quebrado.
// ---------------------------------------------------------------------------
export async function downloadFromApi(path, fallbackName) {
  const response = await fetch(`${API}${path}`, { credentials: "include" });
  if (!response.ok) {
    const detail = (await response.json().catch(() => null))?.detail;
    throw new Error(formatApiErrorDetail(detail) || "Não foi possível baixar o arquivo.");
  }
  const disposition = response.headers.get("content-disposition") || "";
  const name = (disposition.match(/filename="?([^";]+)"?/) || [])[1] || fallbackName;
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
  return name;
}

// Salva um texto gerado no próprio navegador (ex.: códigos de recuperação).
export function downloadText(text, name) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}
