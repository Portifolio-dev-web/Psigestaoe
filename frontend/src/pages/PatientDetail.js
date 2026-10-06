import { useEffect, useLayoutEffect, useRef, useState, useCallback } from "react";
import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import api, { API, formatApiErrorDetail } from "@/lib/api";
import { motion } from "framer-motion";
import { useAuth } from "@/context/AuthContext";
import { draftInfo, draftSlot, purgeExpiredDrafts } from "@/lib/drafts";
import { ageFromBirthDate, formatDateOnly, formatDateTime } from "@/lib/datetime";
import RecordEditorDialog from "@/components/RecordEditorDialog";
import RecordHistoryDialog from "@/components/RecordHistoryDialog";
import PatientAgendaCard from "@/components/PatientAgendaCard";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { toast } from "sonner";
import {
  ArrowLeft, Plus, FileText, Download, Loader2, Lock, Pencil, Phone, Mail,
  CalendarClock, ShieldCheck, MapPin, Briefcase, GraduationCap, IdCard, Archive, HardDriveDownload,
  History, CalendarCheck, ChevronDown, UserRound,
} from "lucide-react";

// Data sem hora tratada como texto, sem fuso (versão única em lib/datetime.js;
// antes havia uma cópia desta função em cada tela).
const fmtDate = formatDateOnly;
const fmtDateTime = formatDateTime; // sempre na hora local (ver lib/datetime.js)

// Correção (varredura de UI, defeito 3): "14/05/1990 (36 anos)". Antes aparecia
// "(— anos)" mesmo com a data de nascimento cadastrada, porque só o campo manual
// "Idade" era usado. Agora a idade vem da data; o campo manual fica como reserva
// (cadastros antigos ou vindos do Google Forms sem data). Sem nenhum dos dois,
// mostra só a data, sem o "(— anos)".
const birthLabel = (p) => {
  const age = ageFromBirthDate(p.birth_date) ?? (String(p.age || "").trim() || null);
  const date = fmtDate(p.birth_date);
  if (age === null) return date;
  return date === "—" ? `${age} anos` : `${date} (${age} anos)`;
};
// Termo por profissional: o status do consentimento mostra a EVIDÊNCIA
// (quando foi dado ou revogado), não só "Concordo"/"Não concordo".
const consentLabel = (p) => {
  if (p.consent_terms) {
    // C3: menor de idade -> quem consentiu foi o responsável legal.
    const who = p.consent_by_guardian ? "pelo responsável, " : "";
    return p.consent_given_at ? `Concordo (${who}em ${fmtDateTime(p.consent_given_at)})` : `Concordo (${who || ""}data não registrada)`;
  }
  return p.consent_revoked_at ? `Revogado em ${fmtDateTime(p.consent_revoked_at)}` : "Não concordo";
};

export default function PatientDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const [patient, setPatient] = useState(null);
  const [records, setRecords] = useState([]);
  // Celular (UX): dados cadastrais completos recolhidos por padrão (ver abaixo).
  const [showDetails, setShowDetails] = useState(false);
  // ESCALA: a evolução clínica vem paginada (20 por vez, a mais recente
  // primeiro). Antes, abrir a ficha trazia TODAS as evoluções do paciente.
  const [recordsCursor, setRecordsCursor] = useState(null);
  const [recordsTotal, setRecordsTotal] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  // Complementares (evoluções): filtro por período ("De"/"Até", dias locais).
  const [period, setPeriod] = useState({ from: "", to: "" });
  const filteredByPeriod = Boolean(period.from || period.to);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingRec, setEditingRec] = useState(null);
  // PRODUTO (rascunho automático): força recalcular os avisos de rascunho
  // depois que o editor fecha (um rascunho pode ter sido criado ou apagado).
  const [, setDraftsVersion] = useState(0);
  // UX (agenda -> evolução): sessão da agenda de onde a nova evolução foi aberta.
  const [fromSession, setFromSession] = useState(null);
  // UX (histórico de versões): evolução cujo histórico está aberto.
  const [historyOf, setHistoryOf] = useState(null);
  // Evolução a destacar ao chegar pela agenda/painel ("Ver evolução").
  const [highlightId, setHighlightId] = useState(null);
  // P2: avisa o cartão da agenda que algo mudou (uma evolução registrada a partir
  // de uma sessão marca a sessão como realizada).
  const [agendaVersion, setAgendaVersion] = useState(0);
  const [searchParams, setSearchParams] = useSearchParams();

  // Período em parâmetros da API: "Até 10/03" inclui o dia 10 inteiro (o
  // servidor recebe o fim exclusivo, 11/03 00:00 no fuso do navegador).
  const recordsParams = useCallback((extra = {}) => {
    const params = new URLSearchParams(extra);
    if (period.from) params.set("start", new Date(`${period.from}T00:00`).toISOString());
    if (period.to) {
      const end = new Date(`${period.to}T00:00`);
      end.setDate(end.getDate() + 1);
      params.set("end", end.toISOString());
    }
    return params;
  }, [period]);

  // Antes a ficha e as evoluções vinham juntas. Separadas, mudar o período
  // recarrega só a lista (sem registrar de novo a abertura da ficha na auditoria).
  const loadPatient = useCallback(async () => {
    try {
      const { data } = await api.get(`/patients/${id}`);
      setPatient(data);
    } catch (e) {
      // Antes QUALQUER erro dizia "Paciente não encontrado" e voltava para a
      // lista. Com a tela bloqueada (423) ou a sessão expirada (401), isso
      // tirava a pessoa da ficha que ela estava usando: agora só sai no 404.
      if (e.response?.status === 404) {
        toast.error("Paciente não encontrado.");
        navigate("/pacientes");
      }
    }
  }, [id, navigate]);

  const loadRecords = useCallback(async () => {
    try {
      const { data } = await api.get(`/patients/${id}/records?${recordsParams()}`);
      setRecords(data.items);
      setRecordsCursor(data.next_cursor);
      setRecordsTotal(data.total);
    } catch (e) {
      // 404 já é tratado por loadPatient; 401/423 pela api.js.
    }
  }, [id, recordsParams]);

  const load = useCallback(() => {
    setAgendaVersion((v) => v + 1); // P2: a agenda da ficha também recarrega
    return Promise.all([loadPatient(), loadRecords()]);
  }, [loadPatient, loadRecords]);

  useEffect(() => { loadPatient(); }, [loadPatient]);
  useEffect(() => { loadRecords(); }, [loadRecords]);
  useEffect(() => { purgeExpiredDrafts(); }, []); // minimização: rascunhos com mais de 7 dias somem

  // Avisos de anotações não salvas neste dispositivo (só metadados, sem descriptografar).
  const userId = user?.user_id;
  const newDraft = userId ? draftInfo(draftSlot(userId, "record-new", id)) : null;
  const editDraftOf = (recordId) => (userId ? draftInfo(draftSlot(userId, "record-edit", recordId)) : null);

  const loadMoreRecords = async () => {
    if (!recordsCursor) return;
    setLoadingMore(true);
    try {
      const params = recordsParams({ cursor: recordsCursor, limit: 20 });
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

  // O editor (com rascunho automático) fica em components/RecordEditorDialog.js.
  const openNew = () => {
    setFromSession(null);
    setEditingRec(null);
    setEditorOpen(true);
  };

  // UX (agenda -> evolução): o painel "Hoje" e a agenda trazem para cá com
  //   ?evolucao=nova&sessao=<id>&inicio=<ISO>  -> abre o editor já com a data e
  //                                               a hora da sessão, ligado a ela;
  //   ?evolucao=<id da evolução>               -> destaca essa evolução na lista.
  // Os parâmetros saem da URL em seguida, para recarregar a página não reabrir o editor.
  useEffect(() => {
    const target = searchParams.get("evolucao");
    if (!target || !patient) return;
    if (target === "nova") {
      setFromSession({ sessionId: searchParams.get("sessao") || null, sessionDatetime: searchParams.get("inicio") || null });
      setEditingRec(null);
      setEditorOpen(true);
    } else {
      setHighlightId(target);
    }
    setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams, patient]);

  useEffect(() => {
    if (!highlightId || !records.some((r) => r.id === highlightId)) return;
    document.querySelector(`[data-testid="record-${highlightId}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [highlightId, records]);
  const openEdit = (r) => {
    setEditingRec(r);
    setEditorOpen(true);
  };
  const onEditorOpenChange = (open) => {
    setEditorOpen(open);
    if (!open) setDraftsVersion((v) => v + 1);
  };

  // Segurança (varredura, item 9): reativar e exportar não travavam durante o
  // envio. Um clique repetido em "Exportar" gerava outra exportação completa
  // (PDF pesado no servidor) e outro registro de auditoria.
  const inFlight = useSingleFlight();
  const exporting = inFlight.isBusy("export");

  // Item 12: reativar um paciente arquivado (arquivar fica na lista de pacientes).
  const unarchive = () => inFlight.run("unarchive", async () => {
    try {
      await api.post(`/patients/${id}/unarchive`);
      toast.success("Paciente reativado.");
      load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    }
  });

  const exportFile = (format) => inFlight.run("export", async () => {
    try {
      const res = await fetch(`${API}/patients/${id}/export?format=${format}`, { credentials: "include" });
      if (!res.ok) {
        // Varredura, item 15: a exportação agora tem limite (10 a cada 5 min).
        // Mostra a mensagem do servidor em vez de um erro genérico.
        const detail = (await res.json().catch(() => null))?.detail;
        throw new Error(res.status === 429 && detail ? formatApiErrorDetail(detail) : "Falha na exportação");
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `prontuario_${id}.${format}`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success(`Exportado em ${format.toUpperCase()}.`);
    } catch (e) {
      toast.error(e.message === "Falha na exportação" ? "Não foi possível exportar." : e.message);
    }
  });

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
                  {/* C3: paciente menor de 18 anos (o contato é o responsável legal). */}
                  {patient.is_minor && <Badge className="bg-blue-100 text-[#1E3A8A] hover:bg-blue-100">Menor de idade</Badge>}
                  {patient.archived && <Badge className="bg-slate-200 text-slate-700 hover:bg-slate-200">Arquivado</Badge>}
                </div>
              </div>
            </div>
            {/* Celular (UX): antes as 11 linhas de dados vinham ANTES de "Nova evolução"
                e das evoluções (o que mais se usa na ficha). No celular ficam à vista só
                nascimento e telefone; o resto abre em "Ver todos os dados cadastrais".
                No computador tudo aparece, na coluna ao lado das evoluções. */}
            <div className="mt-5 space-y-3 text-sm">
              <InfoRow icon={CalendarClock} label="Nascimento" value={birthLabel(patient)} />
              {/* Celular (UX): um toque no telefone liga; no e-mail, abre o aplicativo de e-mail. */}
              <InfoRow icon={Phone} label="Telefone" value={contactLink("tel", patient.phone)} />
              {/* C3: o responsável legal fica sempre à vista (é o contato de referência do menor). */}
              {patient.guardian_name && (
                <>
                  <InfoRow icon={UserRound} label="Responsável"
                    value={`${patient.guardian_name}${patient.guardian_relationship ? ` (${patient.guardian_relationship})` : ""}`} />
                  <InfoRow icon={Phone} label="Tel. do responsável" value={contactLink("tel", patient.guardian_phone)} />
                </>
              )}
            </div>
            <button type="button" onClick={() => setShowDetails((open) => !open)}
              aria-expanded={showDetails} aria-controls="patient-details"
              className="mt-3 flex items-center gap-1 text-sm font-medium text-[#1E3A8A] lg:hidden" data-testid="toggle-details">
              <ChevronDown className={`h-4 w-4 transition-transform ${showDetails ? "rotate-180" : ""}`} aria-hidden="true" />
              {showDetails ? "Ocultar dados cadastrais" : "Ver todos os dados cadastrais"}
            </button>
            <div id="patient-details" className={showDetails ? "" : "hidden lg:block"}>
              <div className="mt-3 space-y-3 text-sm">
                <InfoRow icon={Lock} label="CPF" value={patient.cpf || "—"} />
                {patient.guardian_cpf && <InfoRow icon={Lock} label="CPF do responsável" value={patient.guardian_cpf} />}
                <InfoRow icon={IdCard} label="RG" value={patient.rg || "—"} />
                <InfoRow icon={GraduationCap} label="Escolaridade" value={patient.education || "—"} />
                {/* C3: estado civil e profissão não se aplicam a menores de 18 anos. */}
                {!patient.is_minor && (
                  <>
                    <InfoRow icon={IdCard} label="Estado civil" value={patient.marital_status || "—"} />
                    <InfoRow icon={Briefcase} label="Profissão" value={patient.profession || "—"} />
                  </>
                )}
                <InfoRow icon={Mail} label="E-mail" value={contactLink("mailto", patient.email)} />
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
                  {/* Acessibilidade (análise de UI, A8): slate-500 sobre este fundo azulado dava
                      contraste 4,3:1 (mínimo 4,5:1); slate-600 chega a 6,9:1. */}
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-600">Observações iniciais</p>
                  <p className="mt-1 text-sm text-slate-700">{patient.initial_notes}</p>
                </div>
              )}
            </div>
            <div className="mt-5 flex flex-col gap-2">
              {patient.archived && (
                <Button onClick={unarchive} disabled={inFlight.isBusy("unarchive")} variant="outline"
                  className="w-full gap-2 border-slate-300" data-testid="unarchive-btn">
                  {inFlight.isBusy("unarchive") ? <Loader2 className="h-4 w-4 animate-spin" /> : <Archive className="h-4 w-4" />}
                  Reativar paciente
                </Button>
              )}
              <Button onClick={openNew} className="w-full gap-2 bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="new-record-btn">
                {/* Terminologia (UX): prontuário = o do paciente; evolução = registro de cada sessão. */}
                <Plus className="h-4 w-4" /> Nova evolução
              </Button>
              {/* Acessibilidade (varredura de UI): menu não modal (ver RowMenu em Patients.js). */}
              <DropdownMenu modal={false}>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" disabled={exporting} className="w-full gap-2 border-slate-300" data-testid="export-btn">
                    {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                    {exporting ? "Exportando…" : "Exportar prontuário"}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent className="w-56">
                  <DropdownMenuItem onClick={() => exportFile("pdf")} data-testid="export-pdf"><FileText className="mr-2 h-4 w-4" /> Exportar em PDF</DropdownMenuItem>
                  <DropdownMenuItem onClick={() => exportFile("json")} data-testid="export-json"><Download className="mr-2 h-4 w-4" /> Exportar em JSON</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </Card>
          {/* Análise de UI (P2): próximas sessões e "Agendar sessão" na própria ficha. */}
          <PatientAgendaCard patient={patient} canSchedule={!patient.archived && !patient.anonymized}
            refreshKey={agendaVersion} />
        </div>

        {/* Right: records */}
        <div className="lg:col-span-2">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <FileText className="h-5 w-5 text-[#1E3A8A]" />
            <h2 className="font-head text-xl font-bold text-[#0F172A]">Evolução clínica</h2>
            {/* Total real (contado no servidor), não só os já carregados. */}
            <Badge variant="secondary" className="ml-1">{recordsTotal}</Badge>
            {filteredByPeriod && <span className="text-xs text-slate-500">no período</span>}
          </div>

          {/* Complementares (evoluções): achar as sessões de um período sem rolar
              anos de evoluções. */}
          <div className="mb-4 flex flex-wrap items-end gap-3" role="group" aria-label="Filtrar evoluções por período"
            data-testid="records-period">
            <div>
              <Label htmlFor="records-from" className="text-xs text-slate-600">De</Label>
              <Input id="records-from" type="date" value={period.from} max={period.to || undefined}
                onChange={(e) => setPeriod((current) => ({ ...current, from: e.target.value }))} className="mt-1 h-9 w-40" />
            </div>
            <div>
              <Label htmlFor="records-to" className="text-xs text-slate-600">Até</Label>
              <Input id="records-to" type="date" value={period.to} min={period.from || undefined}
                onChange={(e) => setPeriod((current) => ({ ...current, to: e.target.value }))} className="mt-1 h-9 w-40" />
            </div>
            {filteredByPeriod && (
              <Button type="button" variant="ghost" size="sm" className="text-[#1E3A8A]"
                onClick={() => setPeriod({ from: "", to: "" })}>
                Ver todas
              </Button>
            )}
          </div>

          {/* PRODUTO: uma evolução começada e não salva não fica esquecida. */}
          {newDraft && (
            <div className="mb-4 flex flex-col gap-2 rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-[#1E3A8A] sm:flex-row sm:items-center sm:justify-between"
              data-testid="pending-draft-banner">
              <span className="flex items-center gap-2">
                <HardDriveDownload className="h-4 w-4 shrink-0" />
                Você tem uma evolução não salva (rascunho de {fmtDateTime(newDraft.savedAt)} neste dispositivo).
              </span>
              <Button size="sm" onClick={openNew} className="bg-[#1E3A8A] hover:bg-[#0F2C59]">Continuar escrevendo</Button>
            </div>
          )}

          {records.length === 0 && filteredByPeriod ? (
            <Card className="border-slate-200 py-12 text-center shadow-sm">
              <p className="text-sm text-slate-500">Nenhuma evolução neste período.</p>
            </Card>
          ) : records.length === 0 ? (
            <Card className="border-slate-200 py-16 text-center shadow-sm">
              <p className="text-sm text-slate-500">Nenhuma evolução registrada ainda.</p>
              <Button onClick={openNew} variant="link" className="mt-2 text-[#1E3A8A]">Registrar a primeira evolução</Button>
            </Card>
          ) : (
            <motion.div initial="hidden" animate="show"
              variants={{ hidden: {}, show: { transition: { staggerChildren: 0.05 } } }}
              className="space-y-4">
              {records.map((r) => (
                <motion.div key={r.id} variants={{ hidden: { opacity: 0, y: 8 }, show: { opacity: 1, y: 0 } }}>
                  <Card className={`border-slate-200 p-5 shadow-sm ${highlightId === r.id ? "ring-2 ring-[#1E3A8A]" : ""}`}
                    data-testid={`record-${r.id}`}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex items-center gap-2 text-sm font-medium text-[#0F172A]">
                        <CalendarClock className="h-4 w-4 text-[#0284C7]" />
                        {fmtDateTime(r.session_datetime)}
                        {r.version > 1 && <Badge variant="secondary" className="text-[10px]">v{r.version}</Badge>}
                        {/* UX (agenda -> evolução): evolução registrada a partir de uma sessão da agenda. */}
                        {r.session_id && (
                          <Badge className="gap-1 bg-emerald-50 text-[10px] text-emerald-800 hover:bg-emerald-50">
                            <CalendarCheck className="h-3 w-3" aria-hidden="true" /> Sessão da agenda
                          </Badge>
                        )}
                        {editDraftOf(r.id) && (
                          <Badge className="bg-blue-100 text-[10px] text-[#1E3A8A] hover:bg-blue-100" title="Há uma edição não salva neste dispositivo">
                            Edição não salva
                          </Badge>
                        )}
                      </div>
                      {/* Acessibilidade (varredura de UI): o lápis não tinha nome para o leitor de tela. */}
                      <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => openEdit(r)} data-testid={`edit-record-${r.id}`}
                        aria-label={`Editar evolução de ${fmtDateTime(r.session_datetime)}`}>
                        <Pencil className="h-4 w-4 text-slate-500" aria-hidden="true" />
                      </Button>
                    </div>
                    {r.diagnosis && (
                      <div className="mt-3">
                        <span className="text-xs font-medium uppercase tracking-wide text-slate-500">Diagnóstico / hipótese</span>
                        <p className="text-sm text-slate-700">{r.diagnosis}</p>
                      </div>
                    )}
                    {/* Análise de UI (P4): evoluções longas aparecem recolhidas, com "Ler mais". */}
                    <ClampedText id={`record-text-${r.id}`} text={r.content} startExpanded={highlightId === r.id} />
                    {/* Correção (varredura de UI, defeito 4): dizia "Registro imutável"
                        ao lado do botão de editar. A evolução É editável; o que não se
                        perde são as versões anteriores, guardadas a cada edição. */}
                    <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1">
                      <p className="flex items-center gap-1 text-[11px] text-slate-500">
                        <Lock className="h-3 w-3" /> Criptografado
                        {(r.version || 1) > 1
                          ? ` · editado (versão ${r.version}; as anteriores ficam guardadas)`
                          : " · versão original"}
                      </p>
                      {/* UX (histórico de versões): antes as versões existiam, mas não havia como vê-las. */}
                      {(r.version || 1) > 1 && (
                        <Button type="button" variant="link" size="sm" onClick={() => setHistoryOf(r.id)}
                          className="h-auto gap-1 px-0 text-[11px] text-[#1E3A8A]" data-testid={`history-${r.id}`}>
                          <History className="h-3 w-3" aria-hidden="true" /> Ver histórico de versões
                        </Button>
                      )}
                    </div>
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

      {/* Editor com rascunho automático (components/RecordEditorDialog.js). */}
      {/* C1 (análise de UI): o editor mostra de qual paciente é a evolução. */}
      <RecordEditorDialog open={editorOpen} onOpenChange={onEditorOpenChange} patientId={id}
        patientName={patient.full_name} record={editingRec} userId={userId} onSaved={load}
        fromSession={editingRec ? null : fromSession} />
      <RecordHistoryDialog recordId={historyOf} open={Boolean(historyOf)}
        onOpenChange={(open) => { if (!open) setHistoryOf(null); }} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Análise de UI (P4): a ficha mostrava cada evolução INTEIRA. Com anos de
// atendimento, achar uma sessão exigia rolar páginas de texto. Agora cada
// evolução mostra as primeiras CLAMP_LINES linhas e "Ler mais" abre o resto
// ali mesmo (sem trocar de tela). O botão só aparece quando há o que abrir:
// a altura real do texto é medida (scrollHeight conta também a parte
// escondida), e medida de novo se a largura mudar (girar o celular).
// A evolução destacada (vinda da agenda ou do painel) já abre inteira.
// ---------------------------------------------------------------------------
const CLAMP_LINES = 6;

function ClampedText({ id, text, startExpanded = false }) {
  const ref = useRef(null);
  const [expanded, setExpanded] = useState(startExpanded);
  const [long, setLong] = useState(false);

  useEffect(() => { if (startExpanded) setExpanded(true); }, [startExpanded]);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const measure = () => {
      const lineHeight = parseFloat(window.getComputedStyle(el).lineHeight) || 24;
      setLong(el.scrollHeight > lineHeight * CLAMP_LINES + 2);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [text]);

  return (
    <>
      <p id={id} ref={ref}
        className={`mt-3 whitespace-pre-wrap text-[15px] leading-relaxed text-slate-700 ${expanded ? "" : "line-clamp-6"}`}>
        {text}
      </p>
      {long && (
        <button type="button" onClick={() => setExpanded((open) => !open)} aria-expanded={expanded} aria-controls={id}
          className="mt-1 min-h-6 rounded-sm text-sm font-medium text-[#1E3A8A] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]"
          data-testid={`${id}-toggle`}>
          {expanded ? "Mostrar menos" : "Ler mais"}
        </button>
      )}
    </>
  );
}

// Telefone/e-mail como link (tel:/mailto:). O tel: leva só os dígitos ("(11) 91234-5678" -> 11912345678).
function contactLink(scheme, value) {
  if (!value) return "—";
  const target = scheme === "tel" ? value.replace(/\D/g, "") : value;
  return (
    <a href={`${scheme}:${target}`} className="text-[#1E3A8A] underline-offset-2 hover:underline">{value}</a>
  );
}

function InfoRow({ icon: Icon, label, value }) {
  // Rótulo EM CIMA do valor: o valor usa a largura toda do cartão. Antes, lado
  // a lado, a coluna do valor era estreita; cortado com "…" sumia metade do
  // endereço/e-mail, e quebrando a linha o e-mail partia no meio ("…example.co / m").
  return (
    <div className="flex items-start gap-3">
      <Icon className="mt-1 h-4 w-4 shrink-0 text-slate-400" />
      <div className="min-w-0">
        {/* Acessibilidade (varredura de UI): rótulos em slate-400 (2,6:1) -> slate-500 (4,8:1). */}
        <p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
        <p className="break-words text-slate-700">{value}</p>
      </div>
    </div>
  );
}
