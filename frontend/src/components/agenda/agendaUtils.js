import {
  addDays, addMinutes, addMonths, addWeeks, endOfMonth, format, isSameDay, startOfDay, startOfMonth, startOfWeek,
} from "date-fns";
import { ptBR } from "date-fns/locale";

// ---------------------------------------------------------------------------
// PRODUTO (visões da agenda): regras de data compartilhadas pelas visões
// Mês, Semana, 3 dias, Dia e Lista. Tudo em hora LOCAL; a API recebe ISO com fuso.
// ---------------------------------------------------------------------------

// Celular (UX): "Semana" espremia 7 colunas numa tela de celular (só domingo a
// terça apareciam, e as sessões de hoje ficavam escondidas numa rolagem
// lateral). No celular, o lugar dela fica com "3 dias", a partir da data
// escolhida ("Hoje" mostra hoje, amanhã e depois). `screen`: em qual tela a
// opção aparece no seletor ("wide" = computador/tablet, "narrow" = celular).
export const VIEWS = [
  { id: "mes", label: "Mês" },
  { id: "semana", label: "Semana", screen: "wide" },
  { id: "3dias", label: "3 dias", screen: "narrow" },
  { id: "dia", label: "Dia" },
  { id: "lista", label: "Lista" },
];
export const VIEW_IDS = VIEWS.map((v) => v.id);
export const THREE_DAYS = 3;

/**
 * Visão de fato mostrada nesta tela. Um link com ?visao=semana aberto no
 * celular mostra 3 dias, e ?visao=3dias aberto no computador mostra a semana.
 */
export function viewForScreen(view, wide) {
  if (!wide && view === "semana") return "3dias";
  if (wide && view === "3dias") return "semana";
  return view;
}

export const viewsForScreen = (wide) => VIEWS.filter((v) => !v.screen || v.screen === (wide ? "wide" : "narrow"));

// Mesma duração padrão do servidor (DEFAULT_SESSION_MINUTES em server.py):
// sessão sem horário de término ocupa 50 min na grade e na detecção de conflito.
export const DEFAULT_SESSION_MINUTES = 50;
export const SNAP_MINUTES = 15;    // arrastar e clicar "encaixam" de 15 em 15 min
export const HOUR_HEIGHT = 48;     // px por hora na grade de horários
export const LIST_DAYS = 28;       // a visão Lista mostra 4 semanas
export const WEEKDAY_LABELS = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"];
const WEEK = { weekStartsOn: 0 };  // domingo, como o cabeçalho que a agenda já usava

export const STATUS = {
  agendada: { label: "Agendada", cls: "bg-blue-100 text-[#1E3A8A]", block: "border-[#1E3A8A] bg-blue-50 text-[#1E3A8A]" },
  realizada: { label: "Realizada", cls: "bg-emerald-100 text-emerald-700", block: "border-emerald-600 bg-emerald-50 text-emerald-800" },
  // Fluxo clínico (C4): o paciente não veio. Âmbar: diferente de cancelada (vermelho).
  faltou: { label: "Faltou", cls: "bg-amber-100 text-amber-800", block: "border-amber-500 bg-amber-50 text-amber-900" },
  // Acessibilidade (varredura de UI): vermelho-600 sobre rosa = 3,9:1; o 700 passa de 4,5:1.
  cancelada: { label: "Cancelada", cls: "bg-red-100 text-red-700", block: "border-red-300 bg-red-50/70 text-red-700 line-through" },
};

// Fluxo clínico (C4): status SEM atendimento. Não pedem evolução, não contam
// como consulta e liberam o horário (mesma regra do servidor, NO_SHOW_STATUSES).
export const NO_SHOW_STATUSES = ["faltou", "cancelada"];
export const isNoShow = (status) => NO_SHOW_STATUSES.includes(status);

// Fluxo clínico (P1): sessões recorrentes (mesmas opções do servidor).
export const RECURRENCE = {
  weekly: { label: "Toda semana", adjective: "semanal", days: 7 },
  biweekly: { label: "A cada 2 semanas", adjective: "quinzenal", days: 14 },
};
export const SERIES_COUNTS = [4, 8, 12, 16, 24, 52];

// Dados completos de uma sessão para o PUT, com novos horários.
// Era local da Agenda.js; o painel também usa (C2: "Marcar falta").
export const sessionPayload = (session, start, end, overrides = {}) => ({
  patient_id: session.patient_id || "",
  patient_name: session.patient_name || "",
  title: session.title,
  start: start.toISOString(),
  end: end ? end.toISOString() : null,
  status: session.status,
  notes: session.notes || "",
  ...overrides,
});

/** "2026-10-01" (parâmetro da URL) -> Date local; inválido ou ausente -> hoje. */
export function parseDateParam(value) {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return startOfDay(new Date());
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  return Number.isNaN(date.getTime()) ? startOfDay(new Date()) : date;
}

export const formatDateParam = (date) => format(date, "yyyy-MM-dd");
export const dayKey = (date) => format(date, "yyyy-MM-dd");

/** Período buscado na API para cada visão. `end` é exclusivo. */
export function viewRange(view, date) {
  if (view === "mes") {
    // Semanas completas: a grade mostra também os dias vizinhos do mês.
    const start = startOfWeek(startOfMonth(date), WEEK);
    return { start, end: addDays(startOfWeek(endOfMonth(date), WEEK), 7) };
  }
  if (view === "semana") {
    const start = startOfWeek(date, WEEK);
    return { start, end: addDays(start, 7) };
  }
  if (view === "3dias") {
    const start = startOfDay(date);
    return { start, end: addDays(start, THREE_DAYS) };
  }
  if (view === "dia") {
    const start = startOfDay(date);
    return { start, end: addDays(start, 1) };
  }
  const start = startOfDay(date);
  return { start, end: addDays(start, LIST_DAYS) };
}

/** Anterior/próximo período (direction = -1 ou +1). */
export function shiftDate(view, date, direction) {
  if (view === "mes") return addMonths(date, direction);
  if (view === "semana") return addWeeks(date, direction);
  if (view === "3dias") return addDays(date, direction * THREE_DAYS);
  if (view === "dia") return addDays(date, direction);
  return addDays(date, direction * LIST_DAYS);
}

const capitalize = (text) => text.charAt(0).toUpperCase() + text.slice(1);

export function viewTitle(view, date) {
  const { start, end } = viewRange(view, date);
  if (view === "mes") return capitalize(format(date, "MMMM 'de' yyyy", { locale: ptBR }));
  if (view === "dia") return capitalize(format(date, "EEEE, d 'de' MMMM 'de' yyyy", { locale: ptBR }));
  const last = addDays(end, -1);
  return `${format(start, "d MMM", { locale: ptBR })} – ${format(last, "d MMM yyyy", { locale: ptBR })}`;
}

export function daysBetween(start, end) {
  const days = [];
  for (let day = start; day < end; day = addDays(day, 1)) days.push(day);
  return days;
}

/** Início e fim reais de uma sessão (sem término = duração padrão). */
export function sessionInterval(session) {
  const start = new Date(session.start);
  const end = session.end ? new Date(session.end) : addMinutes(start, DEFAULT_SESSION_MINUTES);
  return { start, end };
}

export function sessionsOfDay(sessions, day) {
  return sessions.filter((s) => isSameDay(new Date(s.start), day));
}

export const snapMinutes = (minutes) => Math.round(minutes / SNAP_MINUTES) * SNAP_MINUTES;

export function formatTimeRange(session) {
  const { start, end } = sessionInterval(session);
  return `${format(start, "HH:mm")}–${format(end, "HH:mm")}`;
}

/** Nova sessão começando no horário pedido, com a duração padrão. */
export function defaultNewSessionStart(date) {
  const now = new Date();
  if (isSameDay(date, now)) {
    const next = new Date(now);
    next.setMinutes(0, 0, 0);
    next.setHours(now.getHours() + 1); // próxima hora cheia
    return next;
  }
  const at = new Date(date);
  at.setHours(9, 0, 0, 0);
  return at;
}

/**
 * Posição das sessões de UM dia na grade de horários.
 * Sessões que se sobrepõem ficam lado a lado (colunas), em vez de uma
 * esconder a outra, que é exatamente o caso que o profissional precisa ver.
 * Devolve { session, top, height, col, cols } em pixels.
 */
export function layoutDay(sessions, day) {
  const dayStart = startOfDay(day).getTime();
  const dayEnd = addDays(startOfDay(day), 1).getTime();
  const items = sessions
    .map((session) => {
      const { start, end } = sessionInterval(session);
      return { session, from: Math.max(start.getTime(), dayStart), to: Math.min(end.getTime(), dayEnd) };
    })
    .filter((item) => item.to > item.from && item.from < dayEnd && item.to > dayStart)
    .sort((a, b) => a.from - b.from || b.to - a.to);

  const placed = [];
  let cluster = [];
  let clusterEnd = 0;
  const closeCluster = () => {
    const cols = Math.max(...cluster.map((c) => c.col)) + 1;
    cluster.forEach((c) => placed.push({ ...c, cols }));
    cluster = [];
  };
  items.forEach((item) => {
    if (cluster.length && item.from >= clusterEnd) closeCluster();
    const busy = new Set(cluster.filter((c) => c.to > item.from).map((c) => c.col));
    let col = 0;
    while (busy.has(col)) col += 1;
    cluster.push({ ...item, col });
    clusterEnd = Math.max(clusterEnd, item.to);
  });
  if (cluster.length) closeCluster();

  const pxPerMs = HOUR_HEIGHT / 3600000;
  return placed.map((p) => ({
    session: p.session,
    top: (p.from - dayStart) * pxPerMs,
    height: Math.max((p.to - p.from) * pxPerMs, 22), // mínimo legível
    col: p.col,
    cols: p.cols,
  }));
}

// Estado do arrastar-e-soltar (uma sessão por vez). Fica fora do React:
// o evento "dragover" não deixa ler os dados do arraste, só o "drop".
export const dragState = { current: null }; // { session, grabMinutes }

// ---------------------------------------------------------------------------
// UX (agenda -> evolução): para onde levar a partir de uma sessão.
//   - já tem evolução: a ficha do paciente, com a evolução destacada;
//   - ainda não tem: a ficha com o editor aberto, já com a data e a hora da
//     sessão e ligado a ela (o servidor marca a sessão como realizada).
// Sessão sem paciente vinculado não tem prontuário: null.
// Usado pelo painel ("Hoje") e pela agenda (diálogo da sessão e aviso ao salvar).
// ---------------------------------------------------------------------------
export function evolutionPath(session) {
  if (!session?.patient_id) return null;
  if (session.record_id) return `/pacientes/${session.patient_id}?evolucao=${session.record_id}`;
  const params = new URLSearchParams({ evolucao: "nova", sessao: session.id, inicio: session.start });
  return `/pacientes/${session.patient_id}?${params}`;
}
