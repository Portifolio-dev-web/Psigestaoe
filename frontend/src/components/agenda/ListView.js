import { format, isToday, isTomorrow } from "date-fns";
import { ptBR } from "date-fns/locale";
import { CalendarDays } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { STATUS, dayKey, formatTimeRange } from "@/components/agenda/agendaUtils";

// ---------------------------------------------------------------------------
// Visão LISTA (produto): as sessões das próximas 4 semanas agrupadas por dia.
// É a visão padrão no celular, onde uma grade de 7 colunas não cabe.
// ---------------------------------------------------------------------------

function dayLabel(day) {
  const label = format(day, "EEEE, d 'de' MMMM", { locale: ptBR });
  if (isToday(day)) return `Hoje · ${label}`;
  if (isTomorrow(day)) return `Amanhã · ${label}`;
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export default function ListView({ sessions, onOpenSession }) {
  const groups = [];
  sessions.forEach((session) => {
    const day = new Date(session.start);
    const key = dayKey(day);
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.items.push(session);
    else groups.push({ key, day, items: [session] });
  });

  if (groups.length === 0) {
    return (
      <div className="py-12 text-center">
        <CalendarDays className="mx-auto h-8 w-8 text-slate-300" />
        <p className="mt-3 text-sm text-slate-500">Nenhuma sessão neste período.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="agenda-list">
      {groups.map(({ key, day, items }) => (
        <section key={key} aria-labelledby={`list-day-${key}`}>
          <h3 id={`list-day-${key}`} className={`mb-2 text-sm font-semibold ${isToday(day) ? "text-[#1E3A8A]" : "text-slate-600"}`}>
            {dayLabel(day)}
          </h3>
          <ul className="space-y-2">
            {items.map((s) => (
              <li key={s.id}>
                <button type="button" onClick={() => onOpenSession(s)}
                  className="flex w-full items-center gap-3 rounded-md border border-slate-100 p-3 text-left transition-colors hover:bg-slate-50"
                  data-testid={`list-session-${s.id}`}>
                  <span className="w-24 shrink-0 text-sm font-medium tabular-nums text-[#0F172A]">{formatTimeRange(s)}</span>
                  <span className="min-w-0 flex-1">
                    <span className={`block truncate text-sm text-[#0F172A] ${s.status === "cancelada" ? "line-through opacity-60" : ""}`}>
                      {s.patient_name || "Sem paciente"}
                    </span>
                    <span className="block truncate text-xs text-slate-500">{s.title}</span>
                  </span>
                  <Badge className={`${STATUS[s.status]?.cls} hover:opacity-100`}>{STATUS[s.status]?.label}</Badge>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
