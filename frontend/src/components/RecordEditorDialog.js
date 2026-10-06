import { useEffect, useMemo, useRef, useState } from "react";
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
import { LIMITS } from "@/lib/validation";
import { RECORD_TEMPLATES, applyTemplate } from "@/lib/recordTemplates";
import { notifyPendingEvolutionsChanged } from "@/lib/pendingEvolutions";
import { useConfirm } from "@/hooks/useConfirm";
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

// `fromSession` (UX, agenda -> evolução): { sessionId, sessionDatetime } quando a
// evolução é registrada a partir de uma sessão da agenda (painel "Hoje" ou a
// própria agenda). A data e a hora já vêm da sessão, e o servidor liga a
// evolução a ela (e marca a sessão como realizada).
// `patientName` (análise de UI, C1): nome do paciente, mostrado no topo do editor.
export default function RecordEditorDialog({ open, onOpenChange, patientId, patientName = "", record, userId, onSaved,
  fromSession = null }) {
  const slot = userId ? draftSlot(userId, record ? "record-edit" : "record-new", record ? record.id : patientId) : null;

  // Base: o registro como está no servidor (edição) ou uma evolução em branco
  // com a hora da sessão da agenda, ou a hora atual (nova). Recalculada a cada abertura.
  const sessionStart = fromSession?.sessionDatetime;
  const baseline = useMemo(() => (record
    ? { session_datetime: toLocalInput(record.session_datetime), content: record.content || "", diagnosis: record.diagnosis || "" }
    : { session_datetime: toLocalInput(sessionStart ? new Date(sessionStart) : new Date()), content: "", diagnosis: "" }),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  [open, record, sessionStart]);

  const [fields, setFields] = useState(baseline);
  const [restored, setRestored] = useState(null); // { savedAt, baseVersion } do rascunho recuperado
  const [saving, setSaving] = useState(false);
  // Análise de UI (F1): erros do formulário no próprio campo (antes avisos que sumiam).
  const [fieldErrors, setFieldErrors] = useState({});
  // Análise de UI (F2): confirmação no padrão do sistema (antes window.confirm).
  const [confirm, confirmDialog] = useConfirm();
  // Segurança (varredura, item 9): outra edição desta anotação foi salva
  // enquanto esta estava aberta (o servidor respondeu 409). { currentVersion }
  const [conflict, setConflict] = useState(null);

  useEffect(() => {
    if (!open) return undefined;
    setFields(baseline);
    setRestored(null);
    setConflict(null);
    setFieldErrors({}); // F1: cada abertura começa sem erros
    if (userId) prepareDraftKey(userId); // chave em memória ANTES de qualquer imprevisto
    let cancelled = false;
    if (slot && draftsSupported()) {
      readDraft(slot).then((draft) => {
        if (cancelled || !draft) return;
        // Só aplica se a pessoa ainda não começou a digitar (a leitura é assíncrona).
        // Vinda da agenda: o rascunho volta, mas a data/hora continua a da sessão.
        const keepSessionTime = sessionStart ? { session_datetime: baseline.session_datetime } : {};
        setFields((current) => (sameFields(current, baseline)
          ? { ...baseline, ...draft.payload.fields, ...keepSessionTime } : current));
        setRestored({ savedAt: draft.savedAt, baseVersion: draft.payload.baseVersion });
      });
    }
    return () => { cancelled = true; };
  }, [open, slot, baseline, userId, sessionStart]);

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

  const set = (key) => (event) => {
    setFields((current) => ({ ...current, [key]: event.target.value }));
    // F1: mexeu no campo, a mensagem de erro dele sai.
    setFieldErrors((current) => (current[key] ? { ...current, [key]: "" } : current));
  };

  // Complementares (evoluções): modelo de texto (SOAP, DAP...). Campo vazio
  // recebe o modelo; com texto, o modelo vai para o fim (nada é apagado). O
  // rascunho automático guarda o resultado como qualquer outra digitação.
  const contentRef = useRef(null);
  const insertTemplate = (template) => {
    setFields((current) => ({ ...current, content: applyTemplate(current.content, template).slice(0, LIMITS.recordContent) }));
    requestAnimationFrame(() => {
      const area = contentRef.current;
      if (area) {
        area.focus();
        area.setSelectionRange(area.value.length, area.value.length);
      }
    });
  };

  const discardDraft = () => {
    draft.discard();
    setFields(baseline);
    setRestored(null);
    toast.success("Rascunho descartado.");
  };

  // Fechar (Cancelar, Esc, clique fora) não perde o texto: ele fica no rascunho.
  const handleOpenChange = async (next) => {
    if (!next && dirty) {
      if (draft.supported) {
        draft.flush();
        toast.info("Rascunho guardado neste dispositivo. Você pode continuar depois.");
      } else if (!(await confirm({
        title: "Descartar o texto não salvo?",
        description: "Este navegador não permite guardar rascunhos: se fechar agora, o texto digitado se perde.",
        confirmLabel: "Descartar texto",
        cancelLabel: "Continuar escrevendo",
        destructive: true,
      }))) {
        return;
      }
    }
    onOpenChange(next);
  };

  // `expectedVersion`: a versão que foi aberta para editar. Só muda em
  // "Salvar mesmo assim", que confirma sobrescrever a versão mais nova.
  const save = async (expectedVersion = record?.version) => {
    if (saving) return; // varredura, item 9: um envio por vez
    // F1: os problemas aparecem no campo (com o foco nele), não num aviso que some.
    const sessionDatetime = localInputToISO(fields.session_datetime);
    const errors = {
      session_datetime: sessionDatetime ? "" : "Informe a data e a hora da sessão.",
      content: fields.content.trim() ? "" : "Escreva o texto da evolução.",
    };
    if (errors.session_datetime || errors.content) {
      setFieldErrors(errors);
      document.getElementById(errors.session_datetime ? "record-datetime" : "record-content")?.focus();
      return;
    }
    setSaving(true);
    try {
      const body = { session_datetime: sessionDatetime, content: fields.content, diagnosis: fields.diagnosis };
      if (record) {
        // Segurança (varredura, item 9): controle otimista de versão. O
        // servidor recusa (409) se outra edição chegou antes desta.
        await api.put(`/records/${record.id}`, { ...body, expected_version: expectedVersion });
        // Terminologia (UX): "prontuário" é o do paciente; cada registro de sessão é uma evolução.
        toast.success("Evolução atualizada. A versão anterior ficou guardada no histórico.");
      } else {
        await api.post(`/patients/${patientId}/records`, { ...body, session_id: fromSession?.sessionId || null });
        toast.success(fromSession?.sessionId
          ? "Evolução registrada. A sessão foi marcada como realizada na agenda."
          : "Evolução registrada.");
      }
      draft.discard(); // salvo no servidor: o rascunho deixa de existir
      setConflict(null);
      onOpenChange(false);
      onSaved();
      notifyPendingEvolutionsChanged(); // C2: uma pendência pode ter sido resolvida (contador do menu)
    } catch (err) {
      const status = err.response?.status;
      const detail = err.response?.data?.detail;
      if (status === 409 && detail?.code === "record_conflict") {
        // Nada foi salvo: o texto continua no editor e vai para o rascunho.
        draft.flush();
        setConflict({ currentVersion: detail.current_version });
      } else if (status === 401) {
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

  const reviewCurrentVersion = () => {
    draft.flush();
    setConflict(null);
    onOpenChange(false);
    onSaved(); // recarrega a lista com a versão mais nova
    toast.info("Seu texto ficou guardado no rascunho. Abra a evolução de novo para continuar de onde parou.");
  };

  const outdatedDraft = record && restored && restored.baseVersion != null && restored.baseVersion !== record.version;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          {/* Segurança do prontuário (análise de UI, C1): o editor não dizia de QUAL
              paciente era a evolução ("Nova evolução", só isso). Registrar no prontuário
              errado é o erro clássico em sistemas clínicos (duas fichas abertas, uma
              troca de aba). Agora o nome do paciente fica no topo, em destaque, e faz
              parte do título: o leitor de tela anuncia "Nova evolução, paciente: ...". */}
          <DialogTitle className="font-head">
            {record ? "Editar evolução" : "Nova evolução"}
            {patientName && (
              <span className="mt-2 flex items-center gap-2 rounded-md bg-[#F0F4F8] px-2.5 py-1.5 text-sm font-normal text-slate-700"
                data-testid="record-editor-patient">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[#1E3A8A] text-[11px] font-semibold text-white"
                  aria-hidden="true">
                  {patientName.trim()[0]?.toUpperCase()}
                </span>
                <span className="min-w-0">
                  <span className="sr-only">, </span>Paciente: <b className="font-semibold text-[#0F172A]">{patientName}</b>
                </span>
              </span>
            )}
          </DialogTitle>
          <DialogDescription>
            {record
              ? "A versão anterior fica guardada no histórico de versões."
              : fromSession?.sessionId
                ? "Evolução da sessão marcada na agenda. O conteúdo é criptografado antes de ser salvo."
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
                aria-invalid={fieldErrors.session_datetime ? true : undefined}
                aria-describedby={fieldErrors.session_datetime ? "record-datetime-error" : undefined}
                className={`mt-1.5 ${fieldErrors.session_datetime ? "border-red-500" : ""}`} data-testid="record-datetime-input" />
              {fieldErrors.session_datetime && (
                <p id="record-datetime-error" className="mt-1 text-xs text-red-600">{fieldErrors.session_datetime}</p>
              )}
            </div>
            <div>
              <Label htmlFor="record-diagnosis">Diagnóstico / hipótese (opcional)</Label>
              <Input id="record-diagnosis" value={fields.diagnosis} onChange={set("diagnosis")}
                maxLength={LIMITS.recordDiagnosis}
                className="mt-1.5" placeholder="CID / hipótese diagnóstica" data-testid="record-diagnosis-input" />
            </div>
          </div>
          <div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Label htmlFor="record-content">Texto da evolução</Label>
              <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Modelos de texto">
                <span className="text-xs text-slate-500">Modelo:</span>
                {RECORD_TEMPLATES.map((template) => (
                  <button key={template.id} type="button" onClick={() => insertTemplate(template)} title={template.description}
                    aria-label={`Usar o modelo ${template.label} (${template.description})`}
                    // A9 (análise de UI): pelo menos 24 px de altura (antes 22 px).
                    className="min-h-6 rounded-full border border-slate-200 px-2.5 py-0.5 text-xs text-slate-600 transition-colors hover:border-[#1E3A8A] hover:text-[#1E3A8A] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]"
                    data-testid={`record-template-${template.id}`}>
                    {template.label}
                  </button>
                ))}
              </div>
            </div>
            {/* Segurança (varredura, item 2): mesmo limite do servidor (50 mil
                caracteres, ~10 páginas). Perto do limite aparece um contador,
                para a anotação não ser cortada sem a pessoa perceber. */}
            <Textarea id="record-content" ref={contentRef} value={fields.content} onChange={set("content")}
              maxLength={LIMITS.recordContent}
              aria-invalid={fieldErrors.content ? true : undefined}
              aria-describedby={[fieldErrors.content ? "record-content-error" : null,
                fields.content.length > LIMITS.recordContent * 0.9 ? "record-content-count" : null].filter(Boolean).join(" ") || undefined}
              rows={12} className={`mt-1.5 psi-scroll font-normal leading-relaxed ${fieldErrors.content ? "border-red-500" : ""}`}
              placeholder="Descreva a evolução da sessão..." data-testid="record-content-input" />
            {fieldErrors.content && <p id="record-content-error" className="mt-1 text-xs text-red-600">{fieldErrors.content}</p>}
            {fields.content.length > LIMITS.recordContent * 0.9 && (
              <p id="record-content-count" className="mt-1 text-right text-xs text-amber-700" role="status">
                {fields.content.length.toLocaleString("pt-BR")} de {LIMITS.recordContent.toLocaleString("pt-BR")} caracteres
              </p>
            )}
          </div>
        </div>

        {conflict && (
          <div role="alert" className="space-y-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900"
            data-testid="record-conflict">
            <p className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>
                Esta evolução foi alterada em outro lugar enquanto você editava (agora está na v{conflict.currentVersion}).
                Nada foi salvo, e o seu texto continua aqui. Se salvar mesmo assim, ele vira a versão atual e a outra
                edição fica guardada no histórico.
              </span>
            </p>
            <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
              <Button type="button" variant="outline" size="sm" onClick={reviewCurrentVersion} disabled={saving}>
                Ver a versão atual
              </Button>
              <Button type="button" size="sm" onClick={() => save(conflict.currentVersion)} disabled={saving}
                className="bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="record-conflict-save">
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Salvar mesmo assim"}
              </Button>
            </div>
          </div>
        )}

        <DialogFooter className="flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <DraftStatus status={draft.status} savedAt={draft.savedAt} dirty={dirty} />
          <div className="flex gap-2 sm:justify-end">
            <Button variant="outline" onClick={() => handleOpenChange(false)}>Cancelar</Button>
            {/* Com um conflito aberto, a decisão é no aviso acima (o Salvar normal levaria outro 409). */}
            <Button onClick={() => save()} disabled={saving || Boolean(conflict)} className="gap-2 bg-[#1E3A8A] hover:bg-[#0F2C59]"
              data-testid="save-record-btn">
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Salvar
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
      {confirmDialog}
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
