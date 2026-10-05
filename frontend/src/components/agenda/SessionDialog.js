import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { addDays, addMinutes, format } from "date-fns";
import { FileText, Loader2, Repeat, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { localInputToISO, toLocalInput } from "@/lib/datetime";
import PatientPicker from "@/components/PatientPicker";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  DEFAULT_SESSION_MINUTES, RECURRENCE, SERIES_COUNTS, evolutionPath, isNoShow,
} from "@/components/agenda/agendaUtils";

// ---------------------------------------------------------------------------
// Formulário de sessão da agenda (criar/editar).
// Produto:
//   - término preenchido (padrão 50 min) e atalhos de duração;
//   - ao mudar o início, o término acompanha mantendo a duração;
//   - horário enviado com fuso (antes ficava 3 h deslocado no Brasil);
//   - quem trata conflito de horário é a página (Agenda.js), que pergunta
//     antes de salvar por cima de outra sessão;
//   - UX (agenda -> evolução): sessão com paciente que já aconteceu (ou foi
//     marcada como realizada) leva direto a "Registrar evolução"; se a
//     evolução já existe, a "Ver evolução";
//   - fluxo clínico (C4): status "Faltou" (o paciente não veio);
//   - fluxo clínico (P1): sessão nova pode se repetir (toda semana / a cada 2
//     semanas, N sessões); numa sessão de série, alterar ou remover "só esta"
//     ou "esta e as próximas".
// ---------------------------------------------------------------------------

const DURATIONS = [30, 50, 60, 90];

function toForm(initial) {
  if (initial?.id) {
    const start = new Date(initial.start);
    const end = initial.end ? new Date(initial.end) : addMinutes(start, DEFAULT_SESSION_MINUTES);
    return {
      id: initial.id,
      patient_id: initial.patient_id || "",
      patient_name: initial.patient_name || "",
      title: initial.title || "Sessão",
      start: toLocalInput(start),
      end: toLocalInput(end),
      status: initial.status || "agendada",
      notes: initial.notes || "",
      // P1: série já existente -> a quem aplicar as alterações.
      applyTo: "this",
    };
  }
  const start = initial?.start || new Date();
  return {
    id: null, patient_id: "", patient_name: "", title: "Sessão",
    start: toLocalInput(start), end: toLocalInput(addMinutes(start, DEFAULT_SESSION_MINUTES)),
    status: "agendada", notes: "",
    // P1: repetição da sessão nova ("" = não repete).
    repeat: "", repeatCount: 12,
  };
}

const durationMinutes = (form) => (new Date(form.end) - new Date(form.start)) / 60000;

// Mesma margem do painel "Hoje": 10 min antes do início já vale registrar.
const STARTED_MARGIN_MS = 10 * 60 * 1000;

/** A sessão SALVA (não o formulário) pede o atalho para a evolução? */
function showsEvolution(session) {
  // C4: falta e cancelada não pedem evolução (falta não liga evolução à sessão).
  if (!session?.id || !session.patient_id || isNoShow(session.status)) return false;
  return Boolean(session.record_id) || session.status === "realizada"
    || new Date(session.start).getTime() <= Date.now() + STARTED_MARGIN_MS;
}

export default function SessionDialog({ open, initial, saving, deleting, onOpenChange, onSubmit, onDelete }) {
  const navigate = useNavigate();
  const [form, setForm] = useState(() => toForm(initial));
  // P1: remover uma sessão de série pergunta "só esta" ou "esta e as próximas".
  const [confirmDelete, setConfirmDelete] = useState(false);
  // Alterou algo no formulário? Então o atalho para a evolução espera o
  // "Salvar" (sair para a ficha agora descartaria as alterações).
  const saved = useMemo(() => toForm(initial), [initial]);
  const dirty = JSON.stringify(form) !== JSON.stringify(saved);

  useEffect(() => {
    if (open) {
      setForm(toForm(initial));
      setConfirmDelete(false);
    }
  }, [open, initial]);

  // Mudou o início: o término anda junto, mantendo a duração escolhida.
  const changeStart = (value) => setForm((current) => {
    const minutes = durationMinutes(current);
    const end = value && minutes > 0 ? toLocalInput(addMinutes(new Date(value), minutes)) : current.end;
    return { ...current, start: value, end };
  });

  const setDuration = (minutes) => setForm((current) => ({
    ...current, end: toLocalInput(addMinutes(new Date(current.start), minutes)),
  }));

  const submit = () => {
    if (!form.title.trim()) {
      toast.warning("Informe um título.");
      return;
    }
    const start = localInputToISO(form.start);
    const end = localInputToISO(form.end);
    if (!start) {
      toast.warning("Informe o início da sessão.");
      return;
    }
    if (end && new Date(end) <= new Date(start)) {
      toast.warning("O término deve ser depois do início.");
      return;
    }
    onSubmit(form.id, {
      patient_id: form.patient_id,
      patient_name: form.patient_name,
      title: form.title,
      start,
      end,
      status: form.status,
      notes: form.notes,
      // P1: só sessão nova e agendada se repete; só sessão de série escolhe o alcance.
      ...(!form.id && form.repeat && form.status === "agendada"
        ? { repeat: { frequency: form.repeat, count: Number(form.repeatCount) } } : {}),
      ...(form.id && initial?.series_id ? { apply_to: form.applyTo } : {}),
    });
  };

  const currentDuration = durationMinutes(form);
  const inSeries = Boolean(form.id && initial?.series_id);
  // P1: resumo da repetição ("12 sessões, toda semana, até 21/12/2026").
  const repeating = !form.id && form.repeat && form.status === "agendada";
  const lastDate = repeating && form.start
    ? addDays(new Date(form.start), RECURRENCE[form.repeat].days * (Number(form.repeatCount) - 1)) : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="font-head">{form.id ? "Editar sessão" : "Nova sessão"}</DialogTitle>
          <DialogDescription>Agende ou edite uma sessão clínica.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <Label htmlFor="session-title">Título</Label>
            <Input id="session-title" value={form.title} maxLength={200}
              onChange={(e) => setForm({ ...form, title: e.target.value })} className="mt-1.5" data-testid="session-title-input" />
          </div>
          <div>
            {/* Acessibilidade (varredura de UI): rótulo ligado ao seletor (htmlFor/id). */}
            <Label htmlFor="session-patient">Paciente</Label>
            <PatientPicker id="session-patient" value={form.patient_id} valueLabel={form.patient_name}
              onChange={(p) => setForm({ ...form, patient_id: p ? p.id : "", patient_name: p ? p.full_name : "" })} />
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="session-start">Início</Label>
              <Input id="session-start" type="datetime-local" value={form.start} onChange={(e) => changeStart(e.target.value)}
                className="mt-1.5" data-testid="session-start-input" />
            </div>
            <div>
              <Label htmlFor="session-end">Término</Label>
              <Input id="session-end" type="datetime-local" value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })}
                className="mt-1.5" data-testid="session-end-input" />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Duração">
            <span className="mr-1 text-xs text-slate-500">Duração:</span>
            {DURATIONS.map((minutes) => (
              <button key={minutes} type="button" onClick={() => setDuration(minutes)} aria-pressed={currentDuration === minutes}
                className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors ${
                  currentDuration === minutes ? "border-[#1E3A8A] bg-[#1E3A8A] text-white" : "border-slate-200 text-slate-600 hover:bg-slate-50"
                }`}>
                {minutes} min
              </button>
            ))}
          </div>
          <div>
            <Label htmlFor="session-status">Status</Label>
            <Select value={form.status} onValueChange={(v) => setForm({ ...form, status: v })}>
              <SelectTrigger id="session-status" className="mt-1.5" data-testid="session-status-select"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="agendada">Agendada</SelectItem>
                <SelectItem value="realizada">Realizada</SelectItem>
                {/* C4: o paciente não veio (sem aviso). Libera o horário e não pede evolução. */}
                <SelectItem value="faltou">Faltou</SelectItem>
                <SelectItem value="cancelada">Cancelada</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* P1: repetir a sessão nova. Antes cada sessão semanal era marcada à mão. */}
          {!form.id && form.status === "agendada" && (
            <div className="rounded-md border border-slate-200 p-3" data-testid="session-repeat">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <Label htmlFor="session-repeat">Repetir</Label>
                  <Select value={form.repeat || "none"} onValueChange={(v) => setForm({ ...form, repeat: v === "none" ? "" : v })}>
                    <SelectTrigger id="session-repeat" className="mt-1.5" data-testid="session-repeat-select"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">Não repete</SelectItem>
                      {Object.entries(RECURRENCE).map(([id, rule]) => (
                        <SelectItem key={id} value={id}>{rule.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {form.repeat && (
                  <div>
                    <Label htmlFor="session-repeat-count">Quantas sessões</Label>
                    <Select value={String(form.repeatCount)} onValueChange={(v) => setForm({ ...form, repeatCount: Number(v) })}>
                      <SelectTrigger id="session-repeat-count" className="mt-1.5" data-testid="session-repeat-count"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {SERIES_COUNTS.map((count) => (
                          <SelectItem key={count} value={String(count)}>{count} sessões</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                )}
              </div>
              {lastDate && !Number.isNaN(lastDate.getTime()) && (
                <p className="mt-2 flex items-center gap-1.5 text-xs text-slate-600" role="status">
                  <Repeat className="h-3.5 w-3.5" aria-hidden="true" />
                  {form.repeatCount} sessões, {RECURRENCE[form.repeat].label.toLowerCase()}, até {format(lastDate, "dd/MM/yyyy")}.
                  Os horários ocupados são conferidos antes de salvar.
                </p>
              )}
            </div>
          )}

          {/* P1: sessão que faz parte de uma série: a quem aplicar as alterações. */}
          {inSeries && (
            <fieldset className="rounded-md border border-slate-200 p-3" data-testid="session-apply-to">
              <legend className="flex items-center gap-1.5 px-1 text-sm font-medium text-slate-700">
                <Repeat className="h-4 w-4" aria-hidden="true" />
                Sessão {RECURRENCE[initial.recurrence]?.adjective || "recorrente"}: aplicar as alterações a
              </legend>
              {[["this", "Só esta sessão"], ["following", "Esta e as próximas sessões agendadas"]].map(([value, label]) => (
                <label key={value} className="flex min-h-[32px] items-center gap-2 text-sm text-slate-700">
                  <input type="radio" name="session-apply-to" value={value} checked={form.applyTo === value}
                    onChange={() => setForm({ ...form, applyTo: value })} data-testid={`apply-to-${value}`} />
                  {label}
                </label>
              ))}
              <p className="mt-1 text-xs text-slate-500">
                O status vale só para esta sessão. As próximas mudam paciente, título, notas, duração e horário
                (andando o mesmo tanto).
              </p>
            </fieldset>
          )}
          <div>
            <Label htmlFor="session-notes">Notas</Label>
            <Textarea id="session-notes" value={form.notes} maxLength={5000}
              onChange={(e) => setForm({ ...form, notes: e.target.value })} rows={2} className="mt-1.5" />
          </div>
          {/* UX (agenda -> evolução): antes a evolução era registrada à parte, na ficha,
              digitando de novo a data e a hora da sessão. */}
          {showsEvolution(initial) && (
            <div className="rounded-md border border-slate-200 bg-slate-50 p-3" data-testid="session-evolution">
              <p className="text-sm text-slate-700">
                {initial.record_id ? "Esta sessão já tem uma evolução registrada." : "Esta sessão ainda não tem evolução registrada."}
              </p>
              <Button type="button" size="sm" variant={initial.record_id ? "outline" : "default"} disabled={dirty}
                onClick={() => navigate(evolutionPath(initial))}
                className={`mt-2 gap-2 ${initial.record_id ? "border-slate-300" : "bg-[#1E3A8A] hover:bg-[#0F2C59]"}`}
                data-testid="session-evolution-btn">
                <FileText className="h-4 w-4" aria-hidden="true" />
                {initial.record_id ? "Ver evolução" : "Registrar evolução"}
              </Button>
              {dirty && <p className="mt-1.5 text-xs text-slate-500">Salve as alterações da sessão antes.</p>}
            </div>
          )}
        </div>
        {/* P1: remover uma sessão de série: só esta, ou esta e as próximas (ex.: o
            paciente encerrou o tratamento). As que já aconteceram ficam. */}
        {confirmDelete && (
          <div className="rounded-md border border-red-200 bg-red-50 p-3" role="group" aria-label="Remover sessão da série"
            data-testid="session-delete-scope">
            <p className="text-sm text-red-900">Esta sessão faz parte de uma série. O que remover?</p>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button type="button" size="sm" variant="outline" className="border-red-300 bg-white text-red-700"
                disabled={deleting} onClick={() => onDelete(form.id, "this")} data-testid="delete-this">
                Só esta
              </Button>
              <Button type="button" size="sm" className="bg-red-600 hover:bg-red-700"
                disabled={deleting} onClick={() => onDelete(form.id, "following")} data-testid="delete-following">
                Esta e as próximas agendadas
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>Voltar</Button>
            </div>
          </div>
        )}
        <DialogFooter className="flex-row justify-between sm:justify-between">
          {/* Varredura, item 9: "Remover" fica travado enquanto a exclusão está em andamento. */}
          {form.id ? (
            <Button variant="ghost" onClick={() => (inSeries ? setConfirmDelete(true) : onDelete(form.id, "this"))}
              disabled={deleting || saving}
              className="gap-2 text-red-500 hover:bg-red-50 hover:text-red-600" data-testid="delete-session-btn">
              {deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />} Remover
            </Button>
          ) : <span />}
          <Button onClick={submit} disabled={saving} className="bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="save-session-btn">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Salvar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
