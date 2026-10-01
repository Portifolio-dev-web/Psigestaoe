import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, CloudOff, HardDriveDownload, Loader2, Save } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { draftSlot, draftsSupported, prepareDraftKey, readDraft } from "@/lib/drafts";
import { formatDateTime, formatTime, localInputToISO, toLocalInput } from "@/lib/datetime";
import { useDraftAutosave } from "@/hooks/useDraftAutosave";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";

// ---------------------------------------------------------------------------
// Editor de anotação clínica com RASCUNHO AUTOMÁTICO (produto).
//
// Antes o editor ficava dentro da página do paciente e o texto só existia na
// memória: fechar o diálogo sem querer, recarregar a página, a sessão expirar
// ou a rede cair no "Salvar" e a evolução inteira se perdia.
//
// Agora:
//   - o texto é guardado criptografado neste computador enquanto se escreve
//     (lib/drafts.js + hooks/useDraftAutosave.js);
//   - ao abrir de novo a mesma anotação (ou "Nova anotação" do mesmo
//     paciente), o rascunho volta sozinho, com aviso e opção de descartar;
//   - fechar o editor não perde nada: o rascunho fica guardado;
//   - sessão expirada ou tela bloqueada no "Salvar": o texto fica no editor
//     e no rascunho, e basta salvar de novo depois de entrar/desbloquear;
//   - data e hora vão para a API com fuso (correção do horário deslocado).
// ---------------------------------------------------------------------------

const sameFields = (a, b) =>
  a.session_datetime === b.session_datetime && a.content === b.content && a.diagnosis === b.diagnosis;

export default function RecordEditorDialog({ open, onOpenChange, patientId, record, userId, onSaved }) {
  const slot = userId ? draftSlot(userId, record ? "record-edit" : "record-new", record ? record.id : patientId) : null;

  // Base: o registro como está no servidor (edição) ou uma anotação em branco
  // com a hora atual (nova). Recalculada a cada abertura.
  const baseline = useMemo(() => (record
    ? { session_datetime: toLocalInput(record.session_datetime), content: record.content || "", diagnosis: record.diagnosis || "" }
    : { session_datetime: toLocalInput(new Date()), content: "", diagnosis: "" }),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  [open, record]);

  const [fields, setFields] = useState(baseline);
  const [restored, setRestored] = useState(null); // { savedAt, baseVersion } do rascunho recuperado
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return undefined;
    setFields(baseline);
    setRestored(null);
    if (userId) prepareDraftKey(userId); // chave em memória ANTES de qualquer imprevisto
    let cancelled = false;
    if (slot && draftsSupported()) {
      readDraft(slot).then((draft) => {
        if (cancelled || !draft) return;
        // Só aplica se a pessoa ainda não começou a digitar (a leitura é assíncrona).
        setFields((current) => (sameFields(current, baseline) ? { ...baseline, ...draft.payload.fields } : current));
        setRestored({ savedAt: draft.savedAt, baseVersion: draft.payload.baseVersion });
      });
    }
    return () => { cancelled = true; };
  }, [open, slot, baseline, userId]);

  // Nova anotação: rascunho só quando há texto. Edição: quando difere do original.
  const dirty = record
    ? !sameFields(fields, baseline)
    : Boolean(fields.content.trim() || fields.diagnosis.trim());

  const draft = useDraftAutosave({
    slot,
    payload: { fields, baseVersion: record ? record.version : null },
    dirty,
    enabled: open,
  });

  const set = (key) => (event) => setFields((current) => ({ ...current, [key]: event.target.value }));

  const discardDraft = () => {
    draft.discard();
    setFields(baseline);
    setRestored(null);
    toast.success("Rascunho descartado.");
  };

  // Fechar (Cancelar, Esc, clique fora) não perde o texto: ele fica no rascunho.
  const handleOpenChange = (next) => {
    if (!next && dirty) {
      if (draft.supported) {
        draft.flush();
        toast.info("Rascunho guardado neste dispositivo. Você pode continuar depois.");
      } else if (!window.confirm("Este navegador não permite guardar rascunhos. Descartar o texto não salvo?")) {
        return;
      }
    }
    onOpenChange(next);
  };

  const save = async () => {
    if (!fields.content.trim()) {
      toast.warning("A anotação clínica não pode estar vazia.");
      return;
    }
    const sessionDatetime = localInputToISO(fields.session_datetime);
    if (!sessionDatetime) {
      toast.warning("Informe a data e a hora da sessão.");
      return;
    }
    setSaving(true);
    try {
      const body = { session_datetime: sessionDatetime, content: fields.content, diagnosis: fields.diagnosis };
      if (record) {
        await api.put(`/records/${record.id}`, body);
        toast.success("Prontuário atualizado (nova versão registrada em auditoria).");
      } else {
        await api.post(`/patients/${patientId}/records`, body);
        toast.success("Prontuário registrado.");
      }
      draft.discard(); // salvo no servidor: o rascunho deixa de existir
      onOpenChange(false);
      onSaved();
    } catch (err) {
      const status = err.response?.status;
      if (status === 401) {
        draft.flush();
        toast.error("Sua sessão expirou. O texto está guardado como rascunho neste dispositivo: entre novamente e salve.", { duration: 10000 });
      } else if (status === 423) {
        draft.flush();
        toast.info("A tela foi bloqueada. Desbloqueie e clique em Salvar de novo: o texto continua aqui.");
      } else {
        toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
      }
    } finally {
      setSaving(false);
    }
  };

  const outdatedDraft = record && restored && restored.baseVersion != null && restored.baseVersion !== record.version;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="font-head">{record ? "Editar anotação" : "Nova anotação clínica"}</DialogTitle>
          <DialogDescription>
            {record
              ? "A versão anterior será arquivada automaticamente na trilha de auditoria."
              : "O conteúdo é criptografado antes de ser salvo."}
          </DialogDescription>
        </DialogHeader>

        {restored && (
          <div className="flex flex-col gap-2 rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-[#1E3A8A] sm:flex-row sm:items-center sm:justify-between"
            role="status" data-testid="draft-restored">
            <span className="flex items-center gap-2">
              <HardDriveDownload className="h-4 w-4 shrink-0" />
              Rascunho recuperado (salvo neste dispositivo em {formatDateTime(restored.savedAt)}).
            </span>
            <Button type="button" variant="ghost" size="sm" onClick={discardDraft} className="h-7 text-[#1E3A8A]">
              Descartar rascunho
            </Button>
          </div>
        )}
        {outdatedDraft && (
          <p className="flex items-start gap-2 rounded-md bg-amber-50 p-3 text-sm text-amber-800">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            Este registro foi alterado depois do rascunho (v{restored.baseVersion} → v{record.version}). Ao salvar, seu
            texto vira a versão atual; a anterior continua no histórico.
          </p>
        )}

        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="record-datetime">Data/Hora da sessão</Label>
              <Input id="record-datetime" type="datetime-local" value={fields.session_datetime} onChange={set("session_datetime")}
                className="mt-1.5" data-testid="record-datetime-input" />
            </div>
            <div>
              <Label htmlFor="record-diagnosis">Diagnóstico / hipótese (opcional)</Label>
              <Input id="record-diagnosis" value={fields.diagnosis} onChange={set("diagnosis")}
                className="mt-1.5" placeholder="CID / hipótese diagnóstica" data-testid="record-diagnosis-input" />
            </div>
          </div>
          <div>
            <Label htmlFor="record-content">Anotação clínica</Label>
            <Textarea id="record-content" value={fields.content} onChange={set("content")}
              rows={12} className="mt-1.5 psi-scroll font-normal leading-relaxed"
              placeholder="Descreva a evolução da sessão..." data-testid="record-content-input" />
          </div>
        </div>

        <DialogFooter className="flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <DraftStatus status={draft.status} savedAt={draft.savedAt} dirty={dirty} />
          <div className="flex gap-2 sm:justify-end">
            <Button variant="outline" onClick={() => handleOpenChange(false)}>Cancelar</Button>
            <Button onClick={save} disabled={saving} className="gap-2 bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="save-record-btn">
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Salvar
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Indicador discreto do rascunho: a pessoa sabe que o texto está protegido. */
function DraftStatus({ status, savedAt, dirty }) {
  let content = null;
  if (status === "unsupported") {
    content = <><CloudOff className="h-3.5 w-3.5" /> Este navegador não permite guardar rascunho.</>;
  } else if (status === "error") {
    content = <><CloudOff className="h-3.5 w-3.5" /> Não foi possível guardar o rascunho agora.</>;
  } else if (status === "saving" || status === "pending") {
    content = <><Loader2 className="h-3.5 w-3.5 animate-spin" /> Guardando rascunho…</>;
  } else if (status === "saved" && savedAt && dirty) {
    content = <><HardDriveDownload className="h-3.5 w-3.5" /> Rascunho guardado neste dispositivo às {formatTime(savedAt)}</>;
  }
  return (
    <p className="flex min-h-[20px] items-center gap-1.5 text-xs text-slate-500" aria-live="polite" data-testid="draft-status">
      {content}
    </p>
  );
}
