import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { format } from "date-fns";
import { ptBR } from "date-fns/locale";
import { CalendarDays, Loader2, Plus } from "lucide-react";
import api from "@/lib/api";
import { countLabel } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import SessionDialog from "@/components/agenda/SessionDialog";
import { useSessionActions } from "@/components/agenda/useSessionActions";
import { useWriteAccess } from "@/context/WriteAccessContext";
import { STATUS, defaultNewSessionStart, formatTimeRange, isNoShow } from "@/components/agenda/agendaUtils";

// ---------------------------------------------------------------------------
// Agenda dentro da ficha do paciente (análise de UI, P2).
//
// Antes a ficha não mostrava as próximas sessões nem permitia agendar: era
// preciso ir à agenda, abrir "Nova sessão" e procurar o paciente de novo.
// Agora a ficha mostra:
//   - as próximas sessões (abrem o mesmo formulário da agenda: remarcar,
//     marcar falta, cancelar, remover);
//   - "Agendar sessão", com o paciente já escolhido (inclusive sessões
//     recorrentes, P1);
//   - o histórico em números: realizadas, faltas e cancelamentos (adesão ao
//     tratamento à vista, fluxo clínico C4).
// Salvar e remover usam as mesmas regras da agenda (useSessionActions):
// conflito de horário, "Desfazer", séries.
//
// `canSchedule`: falso para paciente arquivado ou anonimizado (a lista
// continua visível, mas não se agenda para quem saiu da lista ativa).
// `refreshKey`: muda quando algo de fora altera a agenda (ex.: uma evolução
// registrada a partir de uma sessão marca a sessão como realizada).
// ---------------------------------------------------------------------------
const PAST_LABELS = [
  ["realizada", "realizada", "realizadas"],
  ["faltou", "falta", "faltas"],
  ["cancelada", "cancelada", "canceladas"],
];

export default function PatientAgendaCard({ patient, canSchedule, refreshKey }) {
  const [data, setData] = useState(null); // { upcoming, upcoming_total, past } | false (erro)
  const [dialog, setDialog] = useState({ open: false, initial: null });
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data: result } = await api.get(`/patients/${patient.id}/sessions`);
      setData(result);
    } catch {
      setData(false); // 401/423 já tratados pela api.js; sem agenda a ficha continua útil
    }
  }, [patient.id]);

  useEffect(() => { load(); }, [load, refreshKey]);

  const { attemptSave, deleteSession, conflictDialog, isDeleting } = useSessionActions(load);

  // Produto (assinatura): no somente leitura, "Agendar sessão" explica e não abre o
  // formulário; as sessões existentes abrem só para consulta (SessionDialog readOnly).
  const { canWrite, guard } = useWriteAccess();
  const openNew = guard(() => setDialog({
    open: true,
    initial: { start: defaultNewSessionStart(new Date()), patient_id: patient.id, patient_name: patient.full_name },
  }));

  const submit = async (id, payload) => {
    setSaving(true);
    const saved = await attemptSave(id, payload);
    setSaving(false);
    if (saved) setDialog({ open: false, initial: null });
  };

  const remove = async (id, scope) => {
    const session = data?.upcoming.find((s) => s.id === id) || dialog.initial;
    if (session && await deleteSession(session, scope)) setDialog({ open: false, initial: null });
  };

  if (data === false) return null;

  const past = data ? PAST_LABELS.filter(([key]) => data.past[key] > 0)
    .map(([key, one, many]) => countLabel(data.past[key], one, many)) : [];
  // Próximas que ocupam horário e não couberam na lista (o total do servidor não conta falta/cancelada).
  const more = data ? data.upcoming_total - data.upcoming.filter((s) => !isNoShow(s.status)).length : 0;

  return (
    <Card className="border-slate-200 p-6 shadow-sm" data-testid="patient-agenda">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <CalendarDays className="h-4 w-4 text-[#1E3A8A]" aria-hidden="true" />
          <h2 className="font-head text-lg font-bold text-[#0F172A]">Agenda</h2>
        </div>
        {canSchedule && (
          <Button size="sm" onClick={openNew} className="gap-1.5 bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="schedule-session-btn">
            <Plus className="h-4 w-4" aria-hidden="true" /> Agendar sessão
          </Button>
        )}
      </div>

      {data === null ? (
        <div className="flex justify-center py-6"><Loader2 className="h-5 w-5 animate-spin text-[#1E3A8A]" aria-label="Carregando a agenda" /></div>
      ) : (
        <>
          <h3 className="mt-4 text-xs font-medium uppercase tracking-wide text-slate-600">Próximas sessões</h3>
          {data.upcoming.length === 0 ? (
            <p className="mt-2 text-sm text-slate-500">Nenhuma sessão marcada.</p>
          ) : (
            <ul className="mt-2 space-y-1.5">
              {data.upcoming.map((s) => {
                const status = STATUS[s.status] || STATUS.agendada;
                const when = format(new Date(s.start), "EEE, dd/MM", { locale: ptBR });
                return (
                  <li key={s.id}>
                    {/* Abre o mesmo formulário da agenda (remarcar, marcar falta, remover). */}
                    <button type="button" onClick={() => setDialog({ open: true, initial: s })}
                      aria-label={`${when}, ${formatTimeRange(s)}, ${status.label}. Abrir sessão`}
                      className="flex w-full items-center gap-3 rounded-md border border-slate-100 px-3 py-2 text-left text-sm transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]"
                      data-testid={`patient-session-${s.id}`}>
                      <span className={`min-w-0 flex-1 ${s.status === "cancelada" ? "line-through" : ""}`}>
                        <span className="font-medium capitalize text-[#0F172A]">{when}</span>
                        <span className="text-slate-600"> · {formatTimeRange(s)}</span>
                      </span>
                      <span className={`whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium ${status.cls}`}>{status.label}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {more > 0 && (
            <p className="mt-2 text-xs text-slate-500">
              e mais {countLabel(more, "sessão marcada", "sessões marcadas")} depois destas.
            </p>
          )}
          {/* Fluxo clínico (C4): adesão ao tratamento em números. */}
          <p className="mt-4 text-sm text-slate-600" data-testid="patient-agenda-history">
            <span className="font-medium text-slate-700">Histórico:</span>{" "}
            {past.length ? past.join(" · ") : "nenhuma sessão anterior na agenda."}
          </p>
          <Link to="/agenda?visao=lista" className="mt-3 inline-block rounded-sm text-sm font-medium text-[#1E3A8A] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]">
            Abrir a agenda
          </Link>
        </>
      )}

      <SessionDialog open={dialog.open} initial={dialog.initial} saving={saving}
        onOpenChange={(open) => setDialog((d) => ({ ...d, open }))} onSubmit={submit} onDelete={remove}
        deleting={isDeleting(dialog.initial?.id)} readOnly={!canWrite} />
      {conflictDialog}
    </Card>
  );
}
