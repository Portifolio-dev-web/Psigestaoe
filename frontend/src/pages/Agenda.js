import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { format } from "date-fns";
import { ptBR } from "date-fns/locale";
import { toast } from "sonner";
import { CalendarDays, ChevronLeft, ChevronRight, Clock, Loader2, Plus } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import SessionDialog from "@/components/agenda/SessionDialog";
import MonthView from "@/components/agenda/MonthView";
import TimeGridView from "@/components/agenda/TimeGridView";
import ListView from "@/components/agenda/ListView";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import {
  STATUS, VIEWS, VIEW_IDS, daysBetween, defaultNewSessionStart, evolutionPath, formatDateParam, formatTimeRange,
  parseDateParam, sessionInterval, shiftDate, viewRange, viewTitle,
} from "@/components/agenda/agendaUtils";

// ---------------------------------------------------------------------------
// AGENDA (produto: visões da agenda).
//
// Antes: só uma grade mensal, apertada no celular, sem horários visíveis e
// sem aviso quando dois pacientes eram marcados no mesmo horário. Havia
// ainda botões "Conectar Google Calendar/Calendly" que não faziam nada
// (removidos: um botão que parece funcionar e não funciona quebra a
// confiança no sistema; voltam quando a integração existir de fato).
//
// Agora:
//   - visões Mês, Semana, Dia e Lista. Padrão: Semana no computador,
//     Lista no celular;
//   - visão e data ficam na URL (?visao=semana&data=2026-10-01): recarregar
//     a página ou voltar da ficha de um paciente mantém onde você estava;
//   - busca só o período visível (paginação por período, ver backend);
//   - arrastar uma sessão remarca (Semana/Dia: horário e dia; Mês: dia), com
//     "Desfazer" no aviso;
//   - conflito de horário: o servidor recusa e aqui a pessoa decide se salva
//     mesmo assim (ex.: um horário propositalmente encaixado).
// ---------------------------------------------------------------------------

const defaultView = () =>
  (typeof window !== "undefined" && window.matchMedia?.("(min-width: 768px)").matches ? "semana" : "lista");

// Dados completos de uma sessão para o PUT, com novos horários.
const sessionPayload = (session, start, end) => ({
  patient_id: session.patient_id || "",
  patient_name: session.patient_name || "",
  title: session.title,
  start: start.toISOString(),
  end: end ? end.toISOString() : null,
  status: session.status,
  notes: session.notes || "",
});

export default function Agenda() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const view = VIEW_IDS.includes(searchParams.get("visao")) ? searchParams.get("visao") : defaultView();
  const dateParam = searchParams.get("data");
  const date = useMemo(() => parseDateParam(dateParam), [dateParam]);
  const range = useMemo(() => viewRange(view, date), [view, date]);

  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [upcoming, setUpcoming] = useState([]);
  const [dialog, setDialog] = useState({ open: false, initial: null });
  const [saving, setSaving] = useState(false);
  // Segurança (varredura, item 9): "Remover" não travava durante o envio; um
  // duplo clique mandava a exclusão duas vezes (e a segunda virava "Erro ao remover").
  const inFlight = useSingleFlight();
  const [conflict, setConflict] = useState(null); // { conflicts, resolve }
  const requestSeq = useRef(0);

  // `replace`: navegar pelos períodos não enche o histórico do navegador.
  const navigateTo = useCallback((nextView, nextDate) => {
    setSearchParams({ visao: nextView, data: formatDateParam(nextDate) }, { replace: true });
  }, [setSearchParams]);

  const load = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    try {
      const params = new URLSearchParams({ start: range.start.toISOString(), end: range.end.toISOString(), limit: 1000 });
      const [periodResponse, upcomingResponse] = await Promise.all([
        api.get(`/sessions?${params}`),
        api.get(`/sessions?${new URLSearchParams({ start: new Date().toISOString(), limit: 8 })}`),
      ]);
      if (seq !== requestSeq.current) return; // a pessoa já mudou de período
      setSessions(periodResponse.data);
      setUpcoming(upcomingResponse.data);
    } catch (err) {
      if (seq === requestSeq.current && ![401, 423].includes(err.response?.status)) {
        toast.error("Não foi possível carregar a agenda.");
      }
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [range]);

  useEffect(() => { load(); }, [load]);

  /**
   * Salva (cria ou altera) tratando conflito de horário: se o servidor
   * responder 409, pergunta antes de salvar por cima ("allow_conflict").
   * Devolve a sessão salva (verdadeiro) ou false.
   */
  const attemptSave = useCallback(async (id, payload, { successMessage, undo } = {}) => {
    try {
      const { data: savedSession } = id ? await api.put(`/sessions/${id}`, payload) : await api.post("/sessions", payload);
      // UX (agenda -> evolução): sessão marcada como realizada, com paciente e
      // ainda sem evolução: o aviso já oferece "Registrar evolução".
      const registerPath = savedSession?.status === "realizada" && !savedSession.record_id ? evolutionPath(savedSession) : null;
      const action = undo
        ? { action: { label: "Desfazer", onClick: undo } }
        : registerPath ? { action: { label: "Registrar evolução", onClick: () => navigate(registerPath) }, duration: 10000 } : undefined;
      toast.success(successMessage || "Sessão salva.", action);
      load();
      return savedSession || true;
    } catch (err) {
      const detail = err.response?.data?.detail;
      if (err.response?.status === 409 && detail?.code === "schedule_conflict") {
        return new Promise((resolve) => {
          // Só a PRIMEIRA resposta vale: o clique em "Salvar mesmo assim" também
          // fecha o diálogo (onOpenChange), que responderia "não" logo depois.
          let answered = false;
          setConflict({
            conflicts: detail.conflicts,
            resolve: async (confirmed) => {
              if (answered) return;
              answered = true;
              setConflict(null);
              if (!confirmed) {
                load(); // desfaz a remarcação otimista do arraste
                resolve(false);
                return;
              }
              resolve(await attemptSave(id, { ...payload, allow_conflict: true }, { successMessage, undo }));
            },
          });
        });
      }
      if (![401, 423].includes(err.response?.status)) {
        toast.error(formatApiErrorDetail(detail) || err.message);
      }
      load();
      return false;
    }
  }, [load, navigate]);

  const submitDialog = async (id, payload) => {
    setSaving(true);
    const saved = await attemptSave(id, payload);
    setSaving(false);
    if (saved) setDialog({ open: false, initial: null });
  };

  const deleteSession = (id) => inFlight.run(`delete:${id}`, async () => {
    const session = sessions.find((s) => s.id === id) || dialog.initial;
    try {
      await api.delete(`/sessions/${id}`);
      setDialog({ open: false, initial: null });
      load();
      toast.success("Sessão removida.", {
        // Recria a sessão com os mesmos dados (pode ganhar um novo id).
        action: session ? {
          label: "Desfazer",
          onClick: () => {
            const { start, end } = sessionInterval(session);
            attemptSave(null, { ...sessionPayload(session, start, session.end ? end : null), allow_conflict: true },
              { successMessage: "Sessão restaurada." });
          },
        } : undefined,
      });
    } catch (err) {
      if (![401, 423].includes(err.response?.status)) toast.error("Erro ao remover.");
    }
  });

  // Arrastar-e-soltar: remarca mantendo a duração, com "Desfazer".
  const moveSession = (session, newStart) => {
    const { start: oldStart, end: oldEnd } = sessionInterval(session);
    if (newStart.getTime() === oldStart.getTime()) return;
    const newEnd = new Date(newStart.getTime() + (oldEnd - oldStart));
    // Otimista: a sessão aparece no novo lugar na hora; se falhar/cancelar, a agenda recarrega.
    setSessions((list) => list.map((s) => (s.id === session.id ? { ...s, start: newStart.toISOString(), end: newEnd.toISOString() } : s)));
    attemptSave(session.id, sessionPayload(session, newStart, newEnd), {
      successMessage: `Sessão remarcada para ${format(newStart, "EEE, dd/MM 'às' HH:mm", { locale: ptBR })}.`,
      undo: () => attemptSave(session.id, { ...sessionPayload(session, oldStart, session.end ? oldEnd : null), allow_conflict: true },
        { successMessage: "Remarcação desfeita." }),
    });
  };

  const openNew = (start) => setDialog({ open: true, initial: { start } });
  const openSession = (session) => setDialog({ open: true, initial: session });

  const viewProps = {
    sessions, onCreateAt: openNew, onOpenSession: openSession, onMoveSession: moveSession,
    onOpenDay: (day) => navigateTo("dia", day),
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="font-head text-3xl font-extrabold tracking-tight text-[#0F172A]">Agenda</h1>
          <p className="mt-1 text-sm text-slate-500">Gerencie horários e sessões</p>
        </div>
        <Button onClick={() => openNew(defaultNewSessionStart(date))} className="gap-2 bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="new-session-btn">
          <Plus className="h-4 w-4" /> Nova sessão
        </Button>
      </div>

      <Card className="border-slate-200 p-4 shadow-sm sm:p-6">
        <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-2">
            <Button variant="outline" size="icon" className="h-8 w-8" onClick={() => navigateTo(view, shiftDate(view, date, -1))}
              aria-label="Período anterior" data-testid="prev-period"><ChevronLeft className="h-4 w-4" /></Button>
            <Button variant="outline" size="sm" className="h-8 text-xs" onClick={() => navigateTo(view, new Date())}>Hoje</Button>
            <Button variant="outline" size="icon" className="h-8 w-8" onClick={() => navigateTo(view, shiftDate(view, date, 1))}
              aria-label="Próximo período" data-testid="next-period"><ChevronRight className="h-4 w-4" /></Button>
            <h2 className="ml-1 font-head text-lg font-bold text-[#0F172A]" aria-live="polite">{viewTitle(view, date)}</h2>
            {loading && <Loader2 className="h-4 w-4 animate-spin text-slate-400" aria-label="Carregando" />}
          </div>
          <div className="inline-flex self-start rounded-lg bg-slate-100 p-1" role="group" aria-label="Visão da agenda">
            {VIEWS.map((v) => (
              <button key={v.id} type="button" onClick={() => navigateTo(v.id, date)} aria-pressed={view === v.id}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  // Acessibilidade (varredura de UI): opção inativa slate-500 sobre slate-100 = 4,3:1 -> slate-600.
                  view === v.id ? "bg-white text-[#0F172A] shadow-sm" : "text-slate-600 hover:text-[#0F172A]"
                }`} data-testid={`view-${v.id}`}>
                {v.label}
              </button>
            ))}
          </div>
        </div>

        {view === "mes" && <MonthView date={date} {...viewProps} />}
        {(view === "semana" || view === "dia") && <TimeGridView days={daysBetween(range.start, range.end)} {...viewProps} />}
        {view === "lista" && <ListView sessions={sessions} onOpenSession={openSession} />}
      </Card>

      {/* Próximas sessões (na visão Lista seria repetido). */}
      {view !== "lista" && (
        <Card className="border-slate-200 p-6 shadow-sm">
          <div className="flex items-center gap-2">
            <CalendarDays className="h-4 w-4 text-[#1E3A8A]" />
            <h2 className="font-head text-lg font-bold text-[#0F172A]">Próximas sessões</h2>
          </div>
          <div className="mt-4 space-y-2">
            {upcoming.length === 0 && <p className="py-6 text-center text-sm text-slate-500">Nenhuma sessão agendada.</p>}
            {upcoming.map((s) => (
              <button key={s.id} type="button" onClick={() => openSession(s)}
                className="flex w-full items-center gap-3 rounded-md border border-slate-100 p-3 text-left transition-colors hover:bg-slate-50">
                <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[#F0F4F8] text-[#1E3A8A]"><Clock className="h-4 w-4" /></div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-[#0F172A]">{s.title}{s.patient_name ? ` · ${s.patient_name}` : ""}</p>
                  <p className="text-xs text-slate-500">
                    {format(new Date(s.start), "EEE, dd 'de' MMM", { locale: ptBR })} · {formatTimeRange(s)}
                  </p>
                </div>
                <Badge className={`${STATUS[s.status]?.cls} hover:opacity-100`}>{STATUS[s.status]?.label}</Badge>
              </button>
            ))}
          </div>
        </Card>
      )}

      <SessionDialog open={dialog.open} initial={dialog.initial} saving={saving}
        onOpenChange={(open) => setDialog((d) => ({ ...d, open }))} onSubmit={submitDialog} onDelete={deleteSession}
        deleting={inFlight.isBusy(`delete:${dialog.initial?.id}`)} />

      {/* Conflito de horário: decisão da pessoa, com o que já está marcado. */}
      <AlertDialog open={Boolean(conflict)} onOpenChange={(open) => { if (!open) conflict?.resolve(false); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Conflito de horário</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div>
                <p>Já existe sessão neste horário:</p>
                <ul className="mt-2 space-y-1" data-testid="conflict-list">
                  {conflict?.conflicts.map((c) => (
                    <li key={c.id} className="rounded-md bg-slate-50 px-2 py-1 text-sm text-slate-700">
                      {format(new Date(c.start), "dd/MM", { locale: ptBR })} · {formatTimeRange(c)} · {c.patient_name || c.title}
                    </li>
                  ))}
                </ul>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => conflict?.resolve(false)}>Voltar</AlertDialogCancel>
            <AlertDialogAction onClick={() => conflict?.resolve(true)} className="bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="confirm-conflict">
              Salvar mesmo assim
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
