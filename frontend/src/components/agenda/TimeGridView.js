import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { format, isSameDay, isToday } from "date-fns";
import { ptBR } from "date-fns/locale";
import { useAuth } from "@/context/AuthContext";
import {
  HOUR_HEIGHT, STATUS, THREE_DAYS, defaultNewSessionStart, dragState, formatTimeRange, layoutDay, sessionInterval,
  snapMinutes,
} from "@/components/agenda/agendaUtils";

// ---------------------------------------------------------------------------
// Visões SEMANA, 3 DIAS (celular) e DIA (produto): grade de horários.
//   - cada sessão ocupa a altura da sua duração; sobrepostas ficam lado a
//     lado (conflito fica visível em vez de uma esconder a outra);
//   - clicar num horário vazio cria a sessão ali (encaixe de 15 min);
//   - arrastar uma sessão para outro horário/dia remarca, mantendo a duração
//     e o ponto onde ela foi "pega" (a Agenda pergunta se houver conflito);
//   - linha vermelha marca a hora atual.
// Arrastar é atalho de mouse; pelo teclado, a sessão abre no formulário.
//
// Análise de UI (P7): antes a grade tinha as 24 horas e abria rolada nas
// 07:00 (rolagem para chegar à tarde, madrugada inteira à toa). Agora mostra o
// HORÁRIO DE ATENDIMENTO do profissional (Configurações > Agenda; padrão
// 07:00 às 21:00). Nada fica escondido: uma sessão fora do horário estende a
// grade até ela. "Mostrar 24 horas" volta ao dia inteiro (lembrado neste navegador).
// ---------------------------------------------------------------------------

const DEFAULT_HOURS = [7, 21]; // mesmo padrão do servidor (DEFAULT_AGENDA_HOURS)
const FULL_DAY_KEY = "psi:agenda-full-day";
const readFullDay = () => {
  try {
    return window.localStorage.getItem(FULL_DAY_KEY) === "1";
  } catch {
    return false;
  }
};
const storeFullDay = (value) => {
  try {
    window.localStorage.setItem(FULL_DAY_KEY, value ? "1" : "0");
  } catch {
    // sem armazenamento: vale até sair da agenda
  }
};
const hourLabel = (h) => `${String(h).padStart(2, "0")}:00`;

/**
 * P7: faixa de horas da grade [first, last). Parte do horário de atendimento
 * e estende para caber toda sessão dos dias visíveis (nada fica escondido).
 */
function visibleHours(days, sessions, [startHour, endHour], fullDay) {
  let first = fullDay ? 0 : startHour;
  let last = fullDay ? 24 : endHour;
  sessions.forEach((session) => {
    const { start, end } = sessionInterval(session);
    if (!days.some((day) => isSameDay(day, start))) return;
    first = Math.min(first, start.getHours());
    const endsSameDay = isSameDay(start, end);
    last = Math.max(last, endsSameDay ? end.getHours() + (end.getMinutes() > 0 ? 1 : 0) : 24);
  });
  return [first, last];
}

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
  // P7: horário de atendimento do profissional (vem de /auth/me).
  const { user } = useAuth();
  const workHours = [user?.agenda_start_hour ?? DEFAULT_HOURS[0], user?.agenda_end_hour ?? DEFAULT_HOURS[1]];
  const [fullDay, setFullDay] = useState(readFullDay);
  const [first, last] = useMemo(
    () => visibleHours(days, sessions, workHours, fullDay),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [days, sessions, workHours[0], workHours[1], fullDay],
  );
  const hours = Array.from({ length: last - first }, (_, i) => first + i);
  const gridHeight = (last - first) * HOUR_HEIGHT;

  // Dia inteiro: abre rolada no início do atendimento. Só o horário: abre no topo.
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = fullDay ? Math.max(workHours[0] * HOUR_HEIGHT - 8, 0) : 0;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fullDay]);

  const toggleFullDay = () => setFullDay((current) => {
    storeFullDay(!current);
    return !current;
  });

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

        {/* Acessibilidade (WCAG 2.1.1, achado na varredura da análise de componentes
            sem uso): a grade tem rolagem própria. Numa semana sem sessões não havia
            nada focável dentro dela, e quem usa só o teclado não conseguia rolá-la.
            Agora a grade recebe o foco (Tab) e rola com as setas; o nome diz o que é. */}
        <div ref={scrollRef} tabIndex={0} role="region" aria-label="Grade de horários"
          className="psi-scroll relative max-h-[65vh] overflow-y-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]"
          data-testid="time-grid">
          <div className="grid" style={{ gridTemplateColumns: columns }}>
            <div className="relative" style={{ height: gridHeight }} aria-hidden="true">
              {hours.map((h) => (
                // P7: a primeira hora da grade fica alinhada ao topo (centralizada, seria cortada).
                <span key={h} className={`absolute right-2 text-[10px] text-slate-500 ${h === first ? "translate-y-0.5" : "-translate-y-1/2"}`}
                  style={{ top: (h - first) * HOUR_HEIGHT }}>
                  {h === 0 ? "" : hourLabel(h)}
                </span>
              ))}
            </div>
            {days.map((day) => (
              <DayColumn key={day.toISOString()} day={day} sessions={sessions} now={now} first={first} last={last}
                onCreateAt={onCreateAt} onOpenSession={onOpenSession} onMoveSession={onMoveSession} />
            ))}
          </div>
        </div>
      </div>
      {/* P7: o horário mostrado, como ver o dia inteiro e onde mudar o horário. */}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-600" data-testid="agenda-hours">
        <span>
          {fullDay ? "Mostrando as 24 horas." : `Horário de atendimento: ${hourLabel(workHours[0])} às ${hourLabel(workHours[1])}.`}
        </span>
        <button type="button" onClick={toggleFullDay}
          className="min-h-6 rounded-sm font-medium text-[#1E3A8A] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]"
          data-testid="agenda-full-day-toggle">
          {fullDay ? "Mostrar só o horário de atendimento" : "Mostrar 24 horas"}
        </button>
        <Link to="/configuracoes?aba=agenda"
          className="inline-flex min-h-6 items-center rounded-sm font-medium text-[#1E3A8A] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]">
          Alterar o horário
        </Link>
      </div>
    </div>
  );
}

// `first`/`last` (P7): faixa de horas da grade; as posições são medidas a partir de `first`.
function DayColumn({ day, sessions, now, first, last, onCreateAt, onOpenSession, onMoveSession }) {
  const [dropTop, setDropTop] = useState(null);
  const offset = first * HOUR_HEIGHT;
  const placed = layoutDay(sessions, day);
  const nowMinutes = now.getHours() * 60 + now.getMinutes();
  const nowTop = isToday(day) && nowMinutes >= first * 60 && nowMinutes < last * 60
    ? ((nowMinutes - first * 60) / 60) * HOUR_HEIGHT : null;

  const timeFromEvent = (event, offsetMinutes = 0) => {
    const minutes = Math.min(Math.max(snapMinutes(minutesAt(event, event.currentTarget) + first * 60 - offsetMinutes), first * 60),
      last * 60 - 15);
    const at = new Date(day);
    at.setHours(0, minutes, 0, 0);
    return { at, minutes };
  };

  return (
    <div className="relative border-l border-slate-100"
      style={{
        height: (last - first) * HOUR_HEIGHT,
        // linhas das horas (cheia) e meias horas (pontilhada) desenhadas no fundo
        backgroundImage: `linear-gradient(to bottom, #e2e8f0 1px, transparent 1px), linear-gradient(to bottom, #f1f5f9 1px, transparent 1px)`,
        backgroundSize: `100% ${HOUR_HEIGHT}px, 100% ${HOUR_HEIGHT / 2}px`,
      }}
      onClick={(event) => onCreateAt(timeFromEvent(event).at)}
      onDragOver={(event) => {
        event.preventDefault();
        const grab = dragState.current?.grabMinutes || 0;
        setDropTop(((timeFromEvent(event, grab).minutes - first * 60) / 60) * HOUR_HEIGHT);
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
          style={{ top: top - offset, height, left: `calc(${(col / cols) * 100}% + 2px)`, width: `calc(${100 / cols}% - 4px)` }}
          aria-label={`${formatTimeRange(session)}, ${session.patient_name || session.title}, ${STATUS[session.status]?.label || ""}`}
          data-testid={`grid-session-${session.id}`}>
          <span className="block font-semibold">{formatTimeRange(session)}</span>
          <span className="block truncate">{session.patient_name || session.title}</span>
        </button>
      ))}
    </div>
  );
}
