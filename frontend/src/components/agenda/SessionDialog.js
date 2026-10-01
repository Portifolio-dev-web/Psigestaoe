import { useEffect, useState } from "react";
import { addMinutes } from "date-fns";
import { Loader2, Trash2 } from "lucide-react";
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
import { DEFAULT_SESSION_MINUTES } from "@/components/agenda/agendaUtils";

// ---------------------------------------------------------------------------
// Formulário de sessão da agenda (criar/editar).
// Produto:
//   - término preenchido (padrão 50 min) e atalhos de duração;
//   - ao mudar o início, o término acompanha mantendo a duração;
//   - horário enviado com fuso (antes ficava 3 h deslocado no Brasil);
//   - quem trata conflito de horário é a página (Agenda.js), que pergunta
//     antes de salvar por cima de outra sessão.
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
    };
  }
  const start = initial?.start || new Date();
  return {
    id: null, patient_id: "", patient_name: "", title: "Sessão",
    start: toLocalInput(start), end: toLocalInput(addMinutes(start, DEFAULT_SESSION_MINUTES)),
    status: "agendada", notes: "",
  };
}

const durationMinutes = (form) => (new Date(form.end) - new Date(form.start)) / 60000;

export default function SessionDialog({ open, initial, saving, deleting, onOpenChange, onSubmit, onDelete }) {
  const [form, setForm] = useState(() => toForm(initial));

  useEffect(() => {
    if (open) setForm(toForm(initial));
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
    });
  };

  const currentDuration = durationMinutes(form);

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
            <Label>Paciente</Label>
            <PatientPicker value={form.patient_id} valueLabel={form.patient_name}
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
                <SelectItem value="cancelada">Cancelada</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="session-notes">Notas</Label>
            <Textarea id="session-notes" value={form.notes} maxLength={5000}
              onChange={(e) => setForm({ ...form, notes: e.target.value })} rows={2} className="mt-1.5" />
          </div>
        </div>
        <DialogFooter className="flex-row justify-between sm:justify-between">
          {/* Varredura, item 9: "Remover" fica travado enquanto a exclusão está em andamento. */}
          {form.id ? (
            <Button variant="ghost" onClick={() => onDelete(form.id)} disabled={deleting || saving}
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
