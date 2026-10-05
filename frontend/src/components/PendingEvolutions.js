import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { format, startOfDay } from "date-fns";
import { ptBR } from "date-fns/locale";
import { toast } from "sonner";
import { ClipboardList, Loader2 } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { PENDING_EVOLUTIONS_EVENT, notifyPendingEvolutionsChanged } from "@/lib/pendingEvolutions";
import { STATUS, evolutionPath, formatTimeRange, sessionInterval, sessionPayload } from "@/components/agenda/agendaUtils";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

// ---------------------------------------------------------------------------
// Fluxo clínico (C2): evoluções pendentes, no painel.
//
// O bloco "Hoje" mostra só as sessões de hoje: uma sessão de ontem sem
// evolução sumia de vista e era esquecida. O registro de cada atendimento é
// obrigação do psicólogo (Resolução CFP nº 001/2009). Aqui aparecem as
// sessões de DIAS ANTERIORES (as de hoje já estão no "Hoje") que:
//   - têm paciente, já aconteceram e não têm evolução;
//   - não são falta nem cancelada;
//   - são dos últimos 90 dias (regra do servidor, PENDING_EVOLUTIONS_DAYS).
// Para cada uma: "Registrar evolução" (editor já com a data e a hora da
// sessão) ou "Marcar falta" (o paciente não veio; com "Desfazer").
// Sem pendências, o bloco não aparece.
// ---------------------------------------------------------------------------
const LIMIT = 10;

export default function PendingEvolutions() {
  const navigate = useNavigate();
  const inFlight = useSingleFlight();
  const [data, setData] = useState(null);

  const load = useCallback(() => {
    // "Dias anteriores" no fuso do navegador: antes da meia-noite de hoje.
    const params = new URLSearchParams({ before: startOfDay(new Date()).toISOString(), limit: String(LIMIT) });
    api.get(`/sessions/pending-evolutions?${params}`)
      .then(({ data: page }) => setData(page))
      .catch(() => setData({ items: [], total: 0 }));
  }, []);

  useEffect(() => {
    load();
    window.addEventListener(PENDING_EVOLUTIONS_EVENT, load);
    return () => window.removeEventListener(PENDING_EVOLUTIONS_EVENT, load);
  }, [load]);

  // C4: o paciente não veio. A sessão vira "Faltou" e sai da lista.
  const setStatus = async (session, status) => {
    const { start, end } = sessionInterval(session);
    await api.put(`/sessions/${session.id}`, sessionPayload(session, start, session.end ? end : null, { status }));
    notifyPendingEvolutionsChanged();
  };

  const markNoShow = (session) => inFlight.run(`noshow:${session.id}`, async () => {
    try {
      await setStatus(session, "faltou");
      toast.success(`Falta registrada: ${session.patient_name}, ${format(new Date(session.start), "dd/MM 'às' HH:mm")}.`, {
        duration: 10000,
        action: {
          label: "Desfazer",
          onClick: () => setStatus(session, session.status)
            .catch((err) => toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message)),
        },
      });
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    }
  });

  if (!data || !data.total) return null;

  return (
    <Card className="border-amber-200 p-6 shadow-sm" data-testid="pending-evolutions">
      <div className="flex flex-wrap items-center gap-2">
        <ClipboardList className="h-4 w-4 text-amber-700" aria-hidden="true" />
        <h2 className="font-head text-lg font-bold text-[#0F172A]">Evoluções pendentes</h2>
        <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-900">{data.total}</span>
      </div>
      <p className="mt-1 text-sm text-slate-500">
        Sessões de dias anteriores sem evolução registrada. Registre a evolução ou marque a falta.
      </p>

      <ul className="mt-4 divide-y divide-slate-100">
        {data.items.map((s) => {
          const day = format(new Date(s.start), "EEE, dd/MM", { locale: ptBR });
          return (
            <li key={s.id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:gap-4" data-testid={`pending-${s.id}`}>
              <div className="flex min-w-0 flex-1 items-center gap-4">
                <span className="w-28 shrink-0 text-sm tabular-nums text-[#0F172A]">
                  <span className="block font-medium capitalize">{day}</span>
                  <span className="block text-xs text-slate-500">{formatTimeRange(s)}</span>
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-[#0F172A]">{s.patient_name}</p>
                  {/* Marcada como realizada, mas ainda sem o registro. */}
                  {s.status === "realizada" && (
                    <span className={`mt-0.5 inline-block rounded-md px-2 py-0.5 text-xs font-medium ${STATUS.realizada.cls}`}>
                      Realizada, sem evolução
                    </span>
                  )}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2 pl-32 sm:pl-0">
                <Button size="sm" className="bg-[#1E3A8A] hover:bg-[#0F2C59]" onClick={() => navigate(evolutionPath(s))}
                  aria-label={`Registrar evolução da sessão de ${s.patient_name} em ${day}`} data-testid={`pending-register-${s.id}`}>
                  Registrar evolução
                </Button>
                <Button size="sm" variant="outline" className="border-slate-300" disabled={inFlight.isBusy(`noshow:${s.id}`)}
                  onClick={() => markNoShow(s)} aria-label={`Marcar falta de ${s.patient_name} em ${day}`}
                  data-testid={`pending-noshow-${s.id}`}>
                  {inFlight.isBusy(`noshow:${s.id}`) ? <Loader2 className="h-4 w-4 animate-spin" /> : "Marcar falta"}
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
      {data.total > data.items.length && (
        <p className="mt-2 text-xs text-slate-500">
          Mostrando as {data.items.length} mais recentes de {data.total}. As demais aparecem conforme estas forem resolvidas.
        </p>
      )}
    </Card>
  );
}
