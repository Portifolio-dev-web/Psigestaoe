import { useState } from "react";
import { format, isSameMonth, isToday } from "date-fns";
import { ptBR } from "date-fns/locale";
import { Check, TriangleAlert, X } from "lucide-react";
import {
  STATUS, WEEKDAY_LABELS, daysBetween, dayKey, defaultNewSessionStart, dragState, sessionInterval, sessionsOfDay, viewRange,
} from "@/components/agenda/agendaUtils";
import { countLabel } from "@/lib/utils";

// Acessibilidade (análise de UI, A10): no celular, cada sessão do dia era só
// um ponto COLORIDO (azul, verde, vermelho): quem não distingue cores não via
// o status. Agora cada status tem também uma FORMA: ponto (agendada),
// ✓ (realizada), ! (faltou) e × (cancelada). Para o leitor de tela, o botão do
// dia diz o resumo ("2 sessões: 1 agendada, 1 realizada").
const STATUS_MARK = {
  realizada: { Icon: Check, cls: "text-emerald-600" },
  faltou: { Icon: TriangleAlert, cls: "text-amber-600" },
  cancelada: { Icon: X, cls: "text-red-600" },
};

function StatusMark({ status }) {
  const mark = STATUS_MARK[status];
  if (!mark) return <span className="h-1.5 w-1.5 rounded-full bg-[#1E3A8A]" />;
  return <mark.Icon className={`h-2.5 w-2.5 ${mark.cls}`} strokeWidth={3.5} />;
}

// Singular e plural de cada status no resumo falado ("2 agendadas", "1 falta").
const STATUS_WORDS = {
  agendada: ["agendada", "agendadas"],
  realizada: ["realizada", "realizadas"],
  faltou: ["falta", "faltas"],
  cancelada: ["cancelada", "canceladas"],
};

/** "3 sessões: 2 agendadas, 1 realizada" (resumo do dia para o leitor de tela). */
function daySummary(list) {
  if (!list.length) return "sem sessões";
  const counts = list.reduce((acc, s) => ({ ...acc, [s.status || "agendada"]: (acc[s.status || "agendada"] || 0) + 1 }), {});
  const parts = Object.entries(counts).map(([status, n]) => {
    const [one, many] = STATUS_WORDS[status] || [status, status];
    return countLabel(n, one, many);
  });
  return `${countLabel(list.length, "sessão", "sessões")}: ${parts.join(", ")}`;
}

// ---------------------------------------------------------------------------
// Visão MÊS (produto). Melhorias em relação à grade antiga:
//   - semanas completas (dias do mês vizinho aparecem esmaecidos);
//   - até 3 sessões por dia e "+N" que abre o dia na visão Dia;
//   - no celular, marcas por status no lugar do texto (não cabe): forma e cor (A10);
//   - arrastar uma sessão para outro dia remarca, mantendo o horário.
// ---------------------------------------------------------------------------

export default function MonthView({ date, sessions, onCreateAt, onOpenSession, onOpenDay, onMoveSession }) {
  const { start, end } = viewRange("mes", date);
  const days = daysBetween(start, end);
  const [dropKey, setDropKey] = useState(null);

  const dropOn = (day) => (event) => {
    event.preventDefault();
    setDropKey(null);
    const dragged = dragState.current;
    dragState.current = null;
    if (!dragged) return;
    const { start: oldStart } = sessionInterval(dragged.session);
    const newStart = new Date(day);
    newStart.setHours(oldStart.getHours(), oldStart.getMinutes(), 0, 0); // muda o dia, mantém a hora
    onMoveSession(dragged.session, newStart);
  };

  return (
    <div>
      <div className="grid grid-cols-7 gap-1 text-center">
        {WEEKDAY_LABELS.map((w) => (
          // Acessibilidade (varredura de UI): textos em slate-400 (contraste 2,6:1) passaram a
          // slate-500/600 (mínimo de 4,5:1). Dias fora do mês seguem mais claros, mas legíveis.
          <div key={w} className="py-2 text-xs font-medium uppercase tracking-wide text-slate-500">{w}</div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {days.map((day) => {
          const key = dayKey(day);
          const list = sessionsOfDay(sessions, day);
          const outside = !isSameMonth(day, date);
          const today = isToday(day);
          return (
            <div key={key}
              onClick={() => { const at = new Date(day); at.setHours(9, 0, 0, 0); onCreateAt(at); }}
              onDragOver={(e) => { e.preventDefault(); setDropKey(key); }}
              onDragLeave={() => setDropKey((current) => (current === key ? null : current))}
              onDrop={dropOn(day)}
              // relative: o botão "+ Nova sessão" (A12) aparece dentro da célula ao receber foco.
              className={`relative min-h-[64px] cursor-pointer rounded-md border p-1 text-left transition-colors hover:bg-slate-50 sm:min-h-[96px] sm:p-1.5 ${
                today ? "border-[#1E3A8A] bg-blue-50/40" : "border-slate-100"
              } ${outside ? "bg-slate-50/60" : ""} ${dropKey === key ? "ring-2 ring-[#0284C7]" : ""}`}
              data-testid={`day-${key}`}>
              {/* A9: alvo de 24 px (antes 15x16). A10: o nome inclui o resumo do dia. */}
              <button type="button" onClick={(e) => { e.stopPropagation(); onOpenDay(day); }}
                className={`flex h-6 min-w-6 items-center justify-center rounded px-1 text-xs font-medium hover:underline ${today ? "text-[#1E3A8A]" : outside ? "font-normal text-slate-500" : "text-slate-700"}`}
                aria-label={`Abrir ${format(day, "dd/MM")} na visão Dia, ${daySummary(list)}`}>
                {format(day, "d")}
              </button>
              {/* A12: criar sessão neste dia pelo teclado (clicar no dia vazio só funcionava com mouse). */}
              <button type="button" onClick={(e) => { e.stopPropagation(); onCreateAt(defaultNewSessionStart(day)); }}
                className="sr-only focus:not-sr-only focus:absolute focus:inset-x-1 focus:bottom-1 focus:z-20 focus:rounded-md focus:bg-[#1E3A8A] focus:px-2 focus:py-1 focus:text-xs focus:font-medium focus:text-white"
                aria-label={`Nova sessão em ${format(day, "EEEE, dd/MM", { locale: ptBR })}`}>
                + Nova sessão
              </button>

              <div className="mt-1 hidden space-y-1 sm:block">
                {list.slice(0, 3).map((s) => (
                  // A12: botão de verdade (antes um div que abria com Enter, mas não com Espaço).
                  // A9: pelo menos 24 px de altura e texto de 11 px (antes 16 px e 10 px).
                  <button key={s.id} type="button" draggable
                    onDragStart={(e) => {
                      dragState.current = { session: s, grabMinutes: 0 };
                      e.dataTransfer.effectAllowed = "move";
                      e.dataTransfer.setData("text/plain", s.id); // obrigatório no Firefox para iniciar o arraste
                    }}
                    onClick={(e) => { e.stopPropagation(); onOpenSession(s); }}
                    aria-label={`${format(new Date(s.start), "HH:mm")}, ${s.patient_name || s.title}, ${STATUS[s.status]?.label || "Agendada"}`}
                    className={`block min-h-6 w-full truncate rounded px-1 py-0.5 text-left text-[11px] leading-tight ${STATUS[s.status]?.cls || STATUS.agendada.cls} ${s.status === "cancelada" ? "line-through" : ""}`}
                    data-testid={`month-session-${s.id}`}>
                    {format(new Date(s.start), "HH:mm")} {s.patient_name || s.title}
                  </button>
                ))}
                {list.length > 3 && (
                  <button type="button" onClick={(e) => { e.stopPropagation(); onOpenDay(day); }}
                    className="text-[10px] text-slate-500 hover:text-[#1E3A8A] hover:underline">
                    +{list.length - 3} mais
                  </button>
                )}
              </div>

              {/* Celular: só a indicação de que há sessões no dia. */}
              {list.length > 0 && (
                // A10: forma + cor por status; o resumo para o leitor de tela está no botão do dia.
                <div className="mt-1 flex flex-wrap items-center gap-0.5 sm:hidden" aria-hidden="true">
                  {list.slice(0, 4).map((s) => <StatusMark key={s.id} status={s.status} />)}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
