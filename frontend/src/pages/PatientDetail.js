import { useEffect, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import api, { API, formatApiErrorDetail } from "@/lib/api";
import { motion } from "framer-motion";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import {
  ArrowLeft, Plus, FileText, Download, Loader2, Lock, Pencil, Save, Phone, Mail,
  CalendarClock, ShieldCheck, MapPin, Briefcase, GraduationCap, IdCard, Archive
} from "lucide-react";

const fmtDate = (s) => {
  if (!s) return "—";
  const [year, month, day] = String(s).slice(0, 10).split("-");
  return year && month && day ? `${day}/${month}/${year}` : "—";
};
const fmtDateTime = (s) => (s ? new Date(s).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
// Termo por profissional: o status do consentimento mostra a EVIDÊNCIA
// (quando foi dado ou revogado), não só "Concordo"/"Não concordo".
const consentLabel = (p) => {
  if (p.consent_terms) {
    return p.consent_given_at ? `Concordo (em ${fmtDateTime(p.consent_given_at)})` : "Concordo (data não registrada)";
  }
  return p.consent_revoked_at ? `Revogado em ${fmtDateTime(p.consent_revoked_at)}` : "Não concordo";
};

const nowLocal = () => {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
};

export default function PatientDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [patient, setPatient] = useState(null);
  const [records, setRecords] = useState([]);
  // ESCALA: a evolução clínica vem paginada (20 por vez, a mais recente
  // primeiro). Antes, abrir a ficha trazia TODAS as evoluções do paciente.
  const [recordsCursor, setRecordsCursor] = useState(null);
  const [recordsTotal, setRecordsTotal] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingRec, setEditingRec] = useState(null);
  const [rec, setRec] = useState({ session_datetime: nowLocal(), content: "", diagnosis: "" });
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const [p, r] = await Promise.all([
        api.get(`/patients/${id}`),
        api.get(`/patients/${id}/records`),
      ]);
      setPatient(p.data);
      setRecords(r.data.items);
      setRecordsCursor(r.data.next_cursor);
      setRecordsTotal(r.data.total);
    } catch (e) {
      toast.error("Paciente não encontrado.");
      navigate("/pacientes");
    }
  }, [id, navigate]);

  useEffect(() => { load(); }, [load]);

  const loadMoreRecords = async () => {
    if (!recordsCursor) return;
    setLoadingMore(true);
    try {
      const params = new URLSearchParams({ cursor: recordsCursor, limit: 20 });
      const { data } = await api.get(`/patients/${id}/records?${params}`);
      setRecords((current) => [...current, ...data.items]);
      setRecordsCursor(data.next_cursor);
      setRecordsTotal(data.total);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    } finally {
      setLoadingMore(false);
    }
  };

  const openNew = () => {
    setEditingRec(null);
    setRec({ session_datetime: nowLocal(), content: "", diagnosis: "" });
    setEditorOpen(true);
  };
  const openEdit = (r) => {
    setEditingRec(r);
    setRec({ session_datetime: (r.session_datetime || "").slice(0, 16), content: r.content, diagnosis: r.diagnosis });
    setEditorOpen(true);
  };

  const saveRecord = async () => {
    if (!rec.content.trim()) { toast.warning("A anotação clínica não pode estar vazia."); return; }
    setSaving(true);
    try {
      if (editingRec) {
        await api.put(`/records/${editingRec.id}`, rec);
        toast.success("Prontuário atualizado (nova versão registrada em auditoria).");
      } else {
        await api.post(`/patients/${id}/records`, rec);
        toast.success("Prontuário registrado.");
      }
      setEditorOpen(false);
      load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    } finally {
      setSaving(false);
    }
  };

  // Item 12: reativar um paciente arquivado (arquivar fica na lista de pacientes).
  const unarchive = async () => {
    try {
      await api.post(`/patients/${id}/unarchive`);
      toast.success("Paciente reativado.");
      load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    }
  };

  const exportFile = async (format) => {
    try {
      const res = await fetch(`${API}/patients/${id}/export?format=${format}`, { credentials: "include" });
      if (!res.ok) throw new Error("Falha na exportação");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `prontuario_${id}.${format}`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success(`Exportado em ${format.toUpperCase()}.`);
    } catch (e) {
      toast.error("Não foi possível exportar.");
    }
  };

  if (!patient) {
    return <div className="flex justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-[#1E3A8A]" /></div>;
  }

  return (
    <div className="space-y-6">
      <button onClick={() => navigate("/pacientes")} className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-[#1E3A8A]" data-testid="back-btn">
        <ArrowLeft className="h-4 w-4" /> Voltar
      </button>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Left: patient info */}
        <div className="space-y-4 lg:col-span-1">
          <Card className="border-slate-200 p-6 shadow-sm">
            <div className="flex items-center gap-3">
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[#1E3A8A] text-lg font-semibold text-white">
                {(patient.full_name || "?")[0]}
              </div>
              <div className="min-w-0">
                <h1 className="font-head text-xl font-bold text-[#0F172A]">{patient.full_name}</h1>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {patient.anonymized && <Badge className="bg-amber-100 text-amber-700 hover:bg-amber-100">Anonimizado</Badge>}
                  {patient.archived && <Badge className="bg-slate-200 text-slate-700 hover:bg-slate-200">Arquivado</Badge>}
                </div>
              </div>
            </div>
            <div className="mt-5 space-y-3 text-sm">
              <InfoRow icon={CalendarClock} label="Nascimento" value={`${fmtDate(patient.birth_date)} (${patient.age || '—'} anos)`} />
              <InfoRow icon={Lock} label="CPF" value={patient.cpf || "—"} />
              <InfoRow icon={IdCard} label="RG" value={patient.rg || "—"} />
              <InfoRow icon={GraduationCap} label="Escolar." value={patient.education || "—"} />
              <InfoRow icon={IdCard} label="Estado civil" value={patient.marital_status || "—"} />
              <InfoRow icon={Briefcase} label="Profissão" value={patient.profession || "—"} />
              <InfoRow icon={Phone} label="Telefone" value={patient.phone || "—"} />
              <InfoRow icon={Mail} label="E-mail" value={patient.email || "—"} />
              <InfoRow icon={MapPin} label="Endereço" value={patient.address || "—"} />
              <InfoRow icon={ShieldCheck} label="Emergência" value={patient.emergency_contact || "—"} />
              <InfoRow icon={ShieldCheck} label="Consentimento" value={consentLabel(patient)} />
            </div>
            {/* Item 12: transparência sobre a guarda obrigatória dos prontuários. */}
            {patient.retention_until && (
              <p className="mt-3 flex items-start gap-2 rounded-md bg-slate-50 p-2.5 text-xs text-slate-500">
                <Archive className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                Prontuário sob guarda obrigatória (CFP) até {fmtDate(patient.retention_until)}. Até lá ele não pode ser excluído.
              </p>
            )}
            <details className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-3">
              <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-slate-500">
                Termo de consentimento
              </summary>
              <p className="mt-2 whitespace-pre-line text-xs leading-relaxed text-slate-600">{patient.consent_terms_text}</p>
            </details>
            {patient.initial_notes && (
              <div className="mt-4 rounded-md bg-[#F0F4F8] p-3">
                <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Observações iniciais</p>
                <p className="mt-1 text-sm text-slate-700">{patient.initial_notes}</p>
              </div>
            )}
            <div className="mt-5 flex flex-col gap-2">
              {patient.archived && (
                <Button onClick={unarchive} variant="outline" className="w-full gap-2 border-slate-300" data-testid="unarchive-btn">
                  <Archive className="h-4 w-4" /> Reativar paciente
                </Button>
              )}
              <Button onClick={openNew} className="w-full gap-2 bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="new-record-btn">
                <Plus className="h-4 w-4" /> Nova anotação
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" className="w-full gap-2 border-slate-300" data-testid="export-btn">
                    <Download className="h-4 w-4" /> Exportar prontuário
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent className="w-56">
                  <DropdownMenuItem onClick={() => exportFile("pdf")} data-testid="export-pdf"><FileText className="mr-2 h-4 w-4" /> Exportar em PDF</DropdownMenuItem>
                  <DropdownMenuItem onClick={() => exportFile("json")} data-testid="export-json"><Download className="mr-2 h-4 w-4" /> Exportar em JSON</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </Card>
        </div>

        {/* Right: records */}
        <div className="lg:col-span-2">
          <div className="mb-4 flex items-center gap-2">
            <FileText className="h-5 w-5 text-[#1E3A8A]" />
            <h2 className="font-head text-xl font-bold text-[#0F172A]">Evolução clínica</h2>
            {/* Total real (contado no servidor), não só os já carregados. */}
            <Badge variant="secondary" className="ml-1">{recordsTotal}</Badge>
          </div>

          {records.length === 0 ? (
            <Card className="border-slate-200 py-16 text-center shadow-sm">
              <p className="text-sm text-slate-500">Nenhum prontuário registrado ainda.</p>
              <Button onClick={openNew} variant="link" className="mt-2 text-[#1E3A8A]">Registrar primeira anotação</Button>
            </Card>
          ) : (
            <motion.div initial="hidden" animate="show"
              variants={{ hidden: {}, show: { transition: { staggerChildren: 0.05 } } }}
              className="space-y-4">
              {records.map((r) => (
                <motion.div key={r.id} variants={{ hidden: { opacity: 0, y: 8 }, show: { opacity: 1, y: 0 } }}>
                  <Card className="border-slate-200 p-5 shadow-sm" data-testid={`record-${r.id}`}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex items-center gap-2 text-sm font-medium text-[#0F172A]">
                        <CalendarClock className="h-4 w-4 text-[#0284C7]" />
                        {fmtDateTime(r.session_datetime)}
                        {r.version > 1 && <Badge variant="secondary" className="text-[10px]">v{r.version}</Badge>}
                      </div>
                      <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => openEdit(r)} data-testid={`edit-record-${r.id}`}>
                        <Pencil className="h-4 w-4 text-slate-400" />
                      </Button>
                    </div>
                    {r.diagnosis && (
                      <div className="mt-3">
                        <span className="text-xs font-medium uppercase tracking-wide text-slate-500">Diagnóstico / hipótese</span>
                        <p className="text-sm text-slate-700">{r.diagnosis}</p>
                      </div>
                    )}
                    <p className="mt-3 whitespace-pre-wrap text-[15px] leading-relaxed text-slate-700">{r.content}</p>
                    <p className="mt-3 flex items-center gap-1 text-[11px] text-slate-400">
                      <Lock className="h-3 w-3" /> Registro imutável · criptografado
                    </p>
                  </Card>
                </motion.div>
              ))}
              {recordsCursor && (
                <div className="flex justify-center pt-2">
                  <Button variant="outline" onClick={loadMoreRecords} disabled={loadingMore}
                    className="gap-2 border-slate-300" data-testid="records-load-more">
                    {loadingMore && <Loader2 className="h-4 w-4 animate-spin" />}
                    Carregar registros anteriores ({recordsTotal - records.length} restantes)
                  </Button>
                </div>
              )}
            </motion.div>
          )}
        </div>
      </div>

      {/* Record editor */}
      <Dialog open={editorOpen} onOpenChange={setEditorOpen}>
        <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="font-head">{editingRec ? "Editar anotação" : "Nova anotação clínica"}</DialogTitle>
            <DialogDescription>
              {editingRec
                ? "A versão anterior será arquivada automaticamente na trilha de auditoria."
                : "O conteúdo é criptografado antes de ser salvo."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <Label>Data/Hora da sessão</Label>
                <Input type="datetime-local" value={rec.session_datetime}
                  onChange={(e) => setRec({ ...rec, session_datetime: e.target.value })}
                  className="mt-1.5" data-testid="record-datetime-input" />
              </div>
              <div>
                <Label>Diagnóstico / hipótese (opcional)</Label>
                <Input value={rec.diagnosis} onChange={(e) => setRec({ ...rec, diagnosis: e.target.value })}
                  className="mt-1.5" placeholder="CID / hipótese diagnóstica" data-testid="record-diagnosis-input" />
              </div>
            </div>
            <div>
              <Label>Anotação clínica</Label>
              <Textarea value={rec.content} onChange={(e) => setRec({ ...rec, content: e.target.value })}
                rows={12} className="mt-1.5 psi-scroll font-normal leading-relaxed"
                placeholder="Descreva a evolução da sessão..." data-testid="record-content-input" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditorOpen(false)}>Cancelar</Button>
            <Button onClick={saveRecord} disabled={saving} className="gap-2 bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="save-record-btn">
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Salvar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function InfoRow({ icon: Icon, label, value }) {
  return (
    <div className="flex items-center gap-3">
      <Icon className="h-4 w-4 shrink-0 text-slate-400" />
      <span className="w-24 shrink-0 text-xs uppercase tracking-wide text-slate-400">{label}</span>
      <span className="truncate text-slate-700">{value}</span>
    </div>
  );
}
