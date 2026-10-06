import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { format } from "date-fns";
import { ptBR } from "date-fns/locale";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { notifyPendingEvolutionsChanged } from "@/lib/pendingEvolutions";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  RECURRENCE, evolutionPath, formatTimeRange, sessionInterval, sessionPayload,
} from "@/components/agenda/agendaUtils";

// ---------------------------------------------------------------------------
// Salvar e remover sessões da agenda (análise de UI, P2).
//
// Esta lógica morava dentro da página Agenda. Com o "Agendar sessão" na ficha
// do paciente, as duas telas precisam das MESMAS regras, e duas cópias
// acabariam diferentes. Ficam aqui:
//   - conflito de horário: o servidor recusa (409) e a pessoa decide se
//     salva mesmo assim (ex.: um horário propositalmente encaixado);
//   - avisos com a ação certa: "Desfazer", "Registrar evolução" (sessão
//     realizada sem evolução) e o resumo da série (P1);
//   - remoção com "Desfazer" (ou "esta e as próximas" de uma série, P1);
//   - contador de evoluções pendentes do menu atualizado (C2).
//
// `onChanged`: chamado sempre que a agenda pode ter mudado (salvou, removeu,
// desistiu de um conflito, deu erro), para a tela recarregar o que mostra.
// Devolve { attemptSave, deleteSession, isDeleting, conflictDialog }; a tela
// renderiza `conflictDialog` uma vez.
// ---------------------------------------------------------------------------
export function useSessionActions(onChanged) {
  const navigate = useNavigate();
  // Sempre a versão mais nova do callback, sem refazer as funções abaixo.
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;
  const changed = () => onChangedRef.current?.();
  const [conflict, setConflict] = useState(null); // { conflicts, resolve }
  // Segurança (varredura, item 9): "Remover" não travava durante o envio; um
  // duplo clique mandava a exclusão duas vezes (e a segunda virava "Erro ao remover").
  const inFlight = useSingleFlight();

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
      // Fluxo clínico (P1): quantas sessões a série criou ou alterou.
      let message = successMessage || "Sessão salva.";
      if (!successMessage && savedSession?.series_count > 1) {
        message = `${savedSession.series_count} sessões agendadas (${RECURRENCE[savedSession.recurrence]?.label.toLowerCase() || "recorrentes"}).`;
      } else if (!successMessage && savedSession?.updated_following > 0) {
        message = `Sessão salva. ${savedSession.updated_following === 1 ? "A próxima sessão da série também foi alterada."
          : `As ${savedSession.updated_following} próximas sessões da série também foram alteradas.`}`;
      }
      toast.success(message, action);
      changed();
      notifyPendingEvolutionsChanged(); // C2: status/horário mudou (ex.: virou falta)
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
                changed(); // desfaz a remarcação otimista do arraste
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
      changed();
      return false;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navigate]);

  /**
   * Remove a sessão. Fluxo clínico (P1): scope "following" remove esta e as
   * próximas sessões agendadas da série (sem "Desfazer": são várias; o
   * diálogo pergunta antes). Devolve true se removeu.
   */
  const deleteSession = (session, scope = "this") => inFlight.run(`delete:${session.id}`, async () => {
    try {
      const { data } = await api.delete(`/sessions/${session.id}?${new URLSearchParams({ scope })}`);
      changed();
      notifyPendingEvolutionsChanged(); // C2: sessão removida deixa de ser pendência
      if (data?.deleted > 1) {
        toast.success(`${data.deleted} sessões removidas (esta e as próximas da série).`);
        return true;
      }
      toast.success("Sessão removida.", {
        // Recria a sessão com os mesmos dados (pode ganhar um novo id).
        action: {
          label: "Desfazer",
          onClick: () => {
            const { start, end } = sessionInterval(session);
            attemptSave(null, { ...sessionPayload(session, start, session.end ? end : null), allow_conflict: true },
              { successMessage: "Sessão restaurada." });
          },
        },
      });
      return true;
    } catch (err) {
      if (![401, 423].includes(err.response?.status)) toast.error("Erro ao remover.");
      return false;
    }
  });

  // Arrastar-e-soltar (Agenda): remarca mantendo a duração, com "Desfazer".
  const moveSession = (session, newStart) => {
    const { start: oldStart, end: oldEnd } = sessionInterval(session);
    const newEnd = new Date(newStart.getTime() + (oldEnd - oldStart));
    return attemptSave(session.id, sessionPayload(session, newStart, newEnd), {
      successMessage: `Sessão remarcada para ${format(newStart, "EEE, dd/MM 'às' HH:mm", { locale: ptBR })}.`,
      undo: () => attemptSave(session.id, { ...sessionPayload(session, oldStart, session.end ? oldEnd : null), allow_conflict: true },
        { successMessage: "Remarcação desfeita." }),
    });
  };

  // Conflito de horário: decisão da pessoa, com o que já está marcado.
  const conflictDialog = (
    <AlertDialog open={Boolean(conflict)} onOpenChange={(open) => { if (!open) conflict?.resolve(false); }}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Conflito de horário</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div>
              {/* P1: uma série pode bater com várias sessões já marcadas. */}
              <p>{conflict?.conflicts.length > 1 ? "Já existem sessões nestes horários:" : "Já existe sessão neste horário:"}</p>
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
  );

  return {
    attemptSave, deleteSession, moveSession, conflictDialog,
    isDeleting: (id) => inFlight.isBusy(`delete:${id}`),
  };
}
