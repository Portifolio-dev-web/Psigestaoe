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

// ---------------------------------------------------------------------------
// CORREÇÃO (varredura de UI, defeito 1): datas SEM hora (nascimento, última
// consulta) não podem passar por `new Date("1985-11-02")`: o JavaScript lê
// esse texto como meia-noite em UTC, e no Brasil (UTC-3) isso ainda é o dia
// ANTERIOR. O painel mostrava quem nasceu em 02/11 como nascido em 01/11.
// Aqui a data é tratada como texto (dia, mês, ano), sem fuso nenhum.
// ---------------------------------------------------------------------------

/** "1985-11-02" (ou "1985-11-02T..." ) -> "02/11/1985". Sem data: "—". */
export function formatDateOnly(value) {
  if (!value) return "—";
  const [year, month, day] = String(value).slice(0, 10).split("-");
  return year && month && day ? `${day}/${month}/${year}` : "—";
}

/**
 * Idade em anos completos a partir da data de nascimento ("AAAA-MM-DD"),
 * comparando com a data de HOJE no fuso local. null se não houver data válida.
 * CORREÇÃO (varredura de UI, defeito 3): a ficha mostrava "(— anos)" porque
 * usava só o campo manual "Idade", mesmo com a data de nascimento preenchida.
 */
export function ageFromBirthDate(value, today = new Date()) {
  const [year, month, day] = String(value || "").slice(0, 10).split("-").map(Number);
  if (!year || !month || !day) return null;
  let age = today.getFullYear() - year;
  // Ainda não fez aniversário este ano: um ano a menos.
  if (today.getMonth() + 1 < month || (today.getMonth() + 1 === month && today.getDate() < day)) age -= 1;
  return age >= 0 && age < 150 ? age : null;
}
