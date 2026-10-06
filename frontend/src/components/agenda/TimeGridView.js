import { useEffect, useRef, useState } from "react";
import { format, isToday } from "date-fns";
import { ptBR } from "date-fns/locale";
import {
  HOUR_HEIGHT, STATUS, THREE_DAYS, defaultNewSessionStart, dragState, formatTimeRange, layoutDay, snapMinutes,
} from "@/components/agenda/agendaUtils";

// ---------------------------------------------------------------------------
// Visões SEMANA, 3 DIAS (celular) e DIA (produto): grade de horários.
//   - cada sessão ocupa a altura da sua duração; sobrepostas ficam lado a
//     lado (conflito fica visível em vez de uma esconder a outra);
//   - clicar num horário vazio cria a sessão ali (encaixe de 15 min);
//   - arrastar uma sessão para outro horário/dia remarca, mantendo a duração
//     e o ponto onde ela foi "pega" (a Agenda pergunta se houver conflito);
//   - linha vermelha marca a hora atual; a grade abre rolada nas 07:00.
// Arrastar é atalho de mouse; pelo teclado, a sessão abre no formulário.
// ---------------------------------------------------------------------------

const HOURS = Array.from({ length: 24 }, (_, h) => h);

function useNow(intervalMs) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

// Minutos desde a meia-noite na posição vertical do ponteiro dentro da coluna.
const minutesAt = (event, element) => ((event.clientY - element.getBoundingClientRect().top) / HOUR_HEIGHT) * 60;

export default function TimeGridView({ days, sessions, onCreateAt, onOpenSession, onOpenDay, onMoveSession }) {
  const scrollRef = useRef(null);
  const now = useNow(60000);
  const multiDay = days.length > 1;

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 7 * HOUR_HEIGHT - 8; // começa às 07:00
  }, []);

  const columns = `56px repeat(${days.length}, minmax(0, 1fr))`;

  return (
    <div className="overflow-x-auto">
      {/* Semana (7 colunas) em tela estreita: rolagem horizontal em vez de colunas
          espremidas. Celular (UX): a visão "3 dias" cabe na tela, sem a largura mínima
          (antes ela herdava os 720 px da semana e só um dia aparecia). */}
      <div style={{ minWidth: days.length > THREE_DAYS ? 720 : undefined }}>
        <div className="grid border-b border-slate-200" style={{ gridTemplateColumns: columns }}>
          <div />
          {days.map((day) => (
            <div key={day.toISOString()} className="relative">
            <button type="button" onClick={() => onOpenDay(day)} disabled={!multiDay}
              className="w-full py-2 text-center enabled:hover:bg-slate-50" aria-label={multiDay ? `Abrir ${format(day, "dd/MM")} na visão Dia` : undefined}>
              {/* Acessibilidade (varredura de UI): slate-400 -> slate-500 (contraste 2,6:1 -> 4,8:1). */}
              <span className="block text-[11px] font-medium uppercase tracking-wide text-slate-500">
                {format(day, "EEE", { locale: ptBR })}
              </span>
              <span className={`mx-auto mt-0.5 flex h-7 w-7 items-center justify-center rounded-full text-sm font-semibold ${
                isToday(day) ? "bg-[#1E3A8A] text-white" : "text-[#0F172A]"
              }`}>
                {format(day, "d")}
              </span>
            </button>
            {/* Acessibilidade (análise de UI, A12): criar sessão clicando num horário vazio
                só funcionava com mouse. Este botão aparece com o foco do teclado e cria a
                sessão neste dia (no próximo horário cheio; o horário se ajusta no formulário). */}
            <button type="button" onClick={() => onCreateAt(defaultNewSessionStart(day))}
              className="sr-only focus:not-sr-only focus:absolute focus:inset-x-1 focus:bottom-1 focus:z-20 focus:rounded-md focus:bg-[#1E3A8A] focus:px-2 focus:py-1 focus:text-xs focus:font-medium focus:text-white"
              aria-label={`Nova sessão em ${format(day, "EEEE, dd/MM", { locale: ptBR })}`}>
              + Nova sessão
            </button>
            </div>
          ))}
        </div>

        <div ref={scrollRef} className="psi-scroll relative max-h-[65vh] overflow-y-auto" data-testid="time-grid">
          <div className="grid" style={{ gridTemplateColumns: columns }}>
            <div className="relative" style={{ height: 24 * HOUR_HEIGHT }} aria-hidden="true">
              {HOURS.map((h) => (
                <span key={h} className="absolute right-2 -translate-y-1/2 text-[10px] text-slate-500" style={{ top: h * HOUR_HEIGHT }}>
                  {h === 0 ? "" : `${String(h).padStart(2, "0")}:00`}
                </span>
              ))}
            </div>
            {days.map((day) => (
              <DayColumn key={day.toISOString()} day={day} sessions={sessions} now={now}
                onCreateAt={onCreateAt} onOpenSession={onOpenSession} onMoveSession={onMoveSession} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function DayColumn({ day, sessions, now, onCreateAt, onOpenSession, onMoveSession }) {
  const [dropTop, setDropTop] = useState(null);
  const placed = layoutDay(sessions, day);
  const nowTop = isToday(day) ? ((now.getHours() * 60 + now.getMinutes()) / 60) * HOUR_HEIGHT : null;

  const timeFromEvent = (event, offsetMinutes = 0) => {
    const minutes = Math.min(Math.max(snapMinutes(minutesAt(event, event.currentTarget) - offsetMinutes), 0), 24 * 60 - 15);
    const at = new Date(day);
    at.setHours(0, minutes, 0, 0);
    return { at, minutes };
  };

  return (
    <div className="relative border-l border-slate-100"
      style={{
        height: 24 * HOUR_HEIGHT,
        // linhas das horas (cheia) e meias horas (pontilhada) desenhadas no fundo
        backgroundImage: `linear-gradient(to bottom, #e2e8f0 1px, transparent 1px), linear-gradient(to bottom, #f1f5f9 1px, transparent 1px)`,
        backgroundSize: `100% ${HOUR_HEIGHT}px, 100% ${HOUR_HEIGHT / 2}px`,
      }}
      onClick={(event) => onCreateAt(timeFromEvent(event).at)}
      onDragOver={(event) => {
        event.preventDefault();
        const grab = dragState.current?.grabMinutes || 0;
        setDropTop((timeFromEvent(event, grab).minutes / 60) * HOUR_HEIGHT);
      }}
      onDragLeave={() => setDropTop(null)}
      onDrop={(event) => {
        event.preventDefault();
        setDropTop(null);
        const dragged = dragState.current;
        dragState.current = null;
        if (dragged) onMoveSession(dragged.session, timeFromEvent(event, dragged.grabMinutes).at);
      }}
      data-testid={`day-column-${format(day, "yyyy-MM-dd")}`}>
      {dropTop !== null && (
        <div className="pointer-events-none absolute inset-x-1 z-20 border-t-2 border-dashed border-[#0284C7]" style={{ top: dropTop }} />
      )}
      {nowTop !== null && (
        <div className="pointer-events-none absolute inset-x-0 z-20 border-t-2 border-red-500" style={{ top: nowTop }} aria-hidden="true">
          <span className="absolute -left-1 -top-[5px] h-2 w-2 rounded-full bg-red-500" />
        </div>
      )}
      {placed.map(({ session, top, height, col, cols }) => (
        <button key={session.id} type="button" draggable
          onDragStart={(event) => {
            // Guarda onde a sessão foi "pega" (ex.: no meio) para soltar no lugar certo.
            const grabMinutes = ((event.clientY - event.currentTarget.getBoundingClientRect().top) / HOUR_HEIGHT) * 60;
            dragState.current = { session, grabMinutes };
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("text/plain", session.id); // obrigatório no Firefox para iniciar o arraste
          }}
          onDragEnd={() => setDropTop(null)}
          onClick={(event) => { event.stopPropagation(); onOpenSession(session); }}
          className={`absolute z-10 overflow-hidden rounded-md border-l-4 px-1.5 py-0.5 text-left text-[11px] leading-tight shadow-sm transition-shadow hover:shadow-md ${STATUS[session.status]?.block || STATUS.agendada.block}`}
          style={{ top, height, left: `calc(${(col / cols) * 100}% + 2px)`, width: `calc(${100 / cols}% - 4px)` }}
          aria-label={`${formatTimeRange(session)}, ${session.patient_name || session.title}, ${STATUS[session.status]?.label || ""}`}
          data-testid={`grid-session-${session.id}`}>
          <span className="block font-semibold">{formatTimeRange(session)}</span>
          <span className="block truncate">{session.patient_name || session.title}</span>
        </button>
      ))}
    </div>
  );
}
