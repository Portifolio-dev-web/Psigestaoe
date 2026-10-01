// ---------------------------------------------------------------------------
// CORREÇÃO DE PRODUTO (horários deslocados).
//
// O campo <input type="datetime-local"> trabalha com a hora LOCAL e sem fuso
// ("2026-10-02T09:00"). O sistema enviava esse texto como estava, e o
// servidor o gravava como UTC: no Brasil (UTC-3), a sessão das 09:00 aparecia
// às 06:00, e ao editar uma anotação o horário "andava" 3 horas.
//
// Regra agora: a tela mostra hora local; a API recebe sempre um instante com
// fuso (ISO em UTC). O servidor recusa data sem fuso (ver server.py).
// ---------------------------------------------------------------------------

/** Instante (Date ou ISO) -> valor do campo datetime-local, na hora local. */
export function toLocalInput(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

/** Valor do campo datetime-local (hora local) -> ISO com fuso para a API. */
export function localInputToISO(value) {
  if (!value) return null;
  const date = new Date(value); // sem fuso no texto = hora local (regra do JavaScript)
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

/** "01/10/2026 14:32" na hora local. */
export function formatDateTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

/** "14:32" na hora local. */
export function formatTime(value) {
  return new Date(value).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}
