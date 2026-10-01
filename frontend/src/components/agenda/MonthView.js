import { useState } from "react";
import { format, isSameMonth, isToday } from "date-fns";
import {
  STATUS, WEEKDAY_LABELS, daysBetween, dayKey, dragState, sessionInterval, sessionsOfDay, viewRange,
} from "@/components/agenda/agendaUtils";

// ---------------------------------------------------------------------------
// Visão MÊS (produto). Melhorias em relação à grade antiga:
//   - semanas completas (dias do mês vizinho aparecem esmaecidos);
//   - até 3 sessões por dia e "+N" que abre o dia na visão Dia;
//   - no celular, pontos coloridos no lugar do texto (não cabe);
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
          <div key={w} className="py-2 text-xs font-medium uppercase tracking-wide text-slate-400">{w}</div>
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
              className={`min-h-[64px] cursor-pointer rounded-md border p-1 text-left transition-colors hover:bg-slate-50 sm:min-h-[96px] sm:p-1.5 ${
                today ? "border-[#1E3A8A] bg-blue-50/40" : "border-slate-100"
              } ${outside ? "bg-slate-50/60" : ""} ${dropKey === key ? "ring-2 ring-[#0284C7]" : ""}`}
              data-testid={`day-${key}`}>
              <button type="button" onClick={(e) => { e.stopPropagation(); onOpenDay(day); }}
                className={`rounded px-1 text-xs font-medium hover:underline ${today ? "text-[#1E3A8A]" : outside ? "text-slate-300" : "text-slate-500"}`}
                aria-label={`Abrir ${format(day, "dd/MM")} na visão Dia`}>
                {format(day, "d")}
              </button>

              <div className="mt-1 hidden space-y-1 sm:block">
                {list.slice(0, 3).map((s) => (
                  <div key={s.id} role="button" tabIndex={0} draggable
                    onDragStart={(e) => {
                      dragState.current = { session: s, grabMinutes: 0 };
                      e.dataTransfer.effectAllowed = "move";
                      e.dataTransfer.setData("text/plain", s.id); // obrigatório no Firefox para iniciar o arraste
                    }}
                    onClick={(e) => { e.stopPropagation(); onOpenSession(s); }}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); onOpenSession(s); } }}
                    className={`truncate rounded px-1 py-0.5 text-[10px] ${STATUS[s.status]?.cls || STATUS.agendada.cls} ${s.status === "cancelada" ? "line-through" : ""}`}
                    data-testid={`month-session-${s.id}`}>
                    {format(new Date(s.start), "HH:mm")} {s.patient_name || s.title}
                  </div>
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
                <div className="mt-1 flex flex-wrap gap-0.5 sm:hidden" aria-label={`${list.length} sessão(ões)`}>
                  {list.slice(0, 4).map((s) => (
                    <span key={s.id} className={`h-1.5 w-1.5 rounded-full ${s.status === "cancelada" ? "bg-red-300" : s.status === "realizada" ? "bg-emerald-500" : "bg-[#1E3A8A]"}`} />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
