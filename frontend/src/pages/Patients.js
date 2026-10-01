// ============================================================================
// IMPORTS
// ============================================================================
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  Plus, Search, MoreVertical, Pencil, Trash2, ShieldOff, FileText, Users, Loader2,
  Archive, ArchiveRestore, KeyRound,
} from "lucide-react";

// API & Utils
import api, { formatApiErrorDetail } from "@/lib/api";
import { LIMITS, cpfProblem, maskCPF, maskPhone, phoneProblem, todayISO } from "@/lib/validation";
import { useSingleFlight } from "@/hooks/useSingleFlight";

// UI Components
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from "@/components/ui/dialog";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

// ============================================================================
// CONSTANTS & HELPERS
// ============================================================================
const fmtDate = (s) => {
  if (!s) return "—";
  const [year, month, day] = String(s).slice(0, 10).split("-");
  return year && month && day ? `${day}/${month}/${year}` : "—";
};

const EMPTY_FORM = {
  full_name: "", cpf: "", rg: "", birth_date: "", age: "",
  education: "", marital_status: "", profession: "", phone: "", email: "",
  address: "", emergency_contact: "", initial_notes: "", consent_terms: false
};

// Termo por profissional: aqui existia uma CÓPIA do termo com o nome e o CRP de
// uma psicóloga fixos no código ("Fernanda Andrade Da Silva – CRP 06/228054"),
// mostrada no cadastro de pacientes de TODOS os profissionais. O texto agora
// vem do backend (GET /consent-term): o termo atual do profissional logado,
// ou, para quem já consentiu, a cópia exata do texto aceito.

// ESCALA: tamanho da página da lista de pacientes (o servidor aceita até 100).
const PAGE_SIZE = 50;

export default function Patients() {
  // ========================================================================
  // STATE MANAGEMENT
  // ========================================================================
  const navigate = useNavigate();
  const [patients, setPatients] = useState(null);
  const [query, setQuery] = useState("");
  // ESCALA: a busca roda no servidor. `debouncedQuery` espera 300 ms sem
  // digitação antes de consultar, em vez de uma requisição por tecla.
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [nextCursor, setNextCursor] = useState(null);
  const [loadingMore, setLoadingMore] = useState(false);
  // Respostas que chegam fora de ordem (digitação rápida) são descartadas:
  // só vale a resposta da requisição mais recente.
  const requestSeq = useRef(0);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [confirm, setConfirm] = useState(null); // {type, patient}
  // Segurança (varredura, item 9): arquivar, anonimizar e excluir não travavam
  // durante o envio; um clique repetido mandava a ação de novo. Agora cada
  // paciente aceita uma ação por vez (chave `patient:<id>`).
  const inFlight = useSingleFlight();
  // Item 12: pacientes arquivados ficam fora da lista ativa, em um filtro próprio.
  const [showArchived, setShowArchived] = useState(false);
  // Termo atual do profissional (para novos consentimentos).
  const [currentTerm, setCurrentTerm] = useState("");

  // Item 15: estado da integração. O token só existe no navegador logo após ser
  // gerado (`newToken`); depois disso não pode ser recuperado, só trocado.
  const [integrationOpen, setIntegrationOpen] = useState(false);
  const [copySuccess, setCopySuccess] = useState(false);
  const [integration, setIntegration] = useState(null); // {configured, hint, created_at}
  const [newToken, setNewToken] = useState("");
  const [tokenBusy, setTokenBusy] = useState(false);

  // ========================================================================
  // HANDLERS - DATA LOADING
  // ========================================================================
  // ESCALA: a lista vem paginada do servidor (antes vinham TODOS os pacientes
  // e a busca filtrava no navegador).
  // - Sem busca: GET /patients, página por página (cursor).
  // - Com busca: POST /patients/search. O nome digitado vai no CORPO, nunca na
  //   URL, para não ficar gravado em logs de proxy/servidor.
  const fetchPage = (cursor) => {
    const term = debouncedQuery.trim();
    if (term.length >= 2) {
      return api.post("/patients/search", { q: term, archived: showArchived, cursor, limit: PAGE_SIZE });
    }
    const params = new URLSearchParams({ archived: showArchived, limit: PAGE_SIZE });
    if (cursor) params.set("cursor", cursor);
    return api.get(`/patients?${params}`);
  };

  const load = async () => {
    const seq = ++requestSeq.current;
    try {
      const { data } = await fetchPage(null);
      if (seq !== requestSeq.current) return; // chegou uma resposta mais nova
      setPatients(data.items);
      setNextCursor(data.next_cursor);
      // Item 15: antes este load também buscava /auth/me só para pegar o token
      // do webhook, deixando o token em memória em toda visita à tela.
    } catch (err) {
      if (seq !== requestSeq.current) return;
      setPatients([]);
      setNextCursor(null);
      console.error("Erro ao carregar dados:", err);
    }
  };

  const loadMore = async () => {
    if (!nextCursor) return;
    const seq = requestSeq.current;
    setLoadingMore(true);
    try {
      const { data } = await fetchPage(nextCursor);
      if (seq !== requestSeq.current) return; // filtro/busca mudou no meio do caminho
      setPatients((current) => [...(current || []), ...data.items]);
      setNextCursor(data.next_cursor);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    } finally {
      setLoadingMore(false);
    }
  };

  const loadCurrentTerm = async () => {
    try {
      const { data } = await api.get("/consent-term");
      setCurrentTerm(data.text);
    } catch (err) {
      setCurrentTerm("");
    }
  };

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), 300);
    return () => clearTimeout(timer);
  }, [query]);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setPatients(null); load(); }, [showArchived, debouncedQuery]);

  // ========================================================================
  // HANDLERS - DIALOG
  // ========================================================================
  const openNew = () => {
    setEditing(null);
    setForm(EMPTY_FORM);
    loadCurrentTerm(); // sempre o texto mais recente (nome/CRP/modelo podem ter mudado)
    setDialogOpen(true);
  };

  // ESCALA: a lista agora traz só um resumo (nome e datas). A edição busca a
  // ficha completa antes de abrir o formulário: sem isso, salvar enviaria
  // CPF, endereço, observações etc. VAZIOS e apagaria esses dados.
  const openEdit = async (p) => {
    try {
      const { data } = await api.get(`/patients/${p.id}`);
      setEditing(data);
      setForm({ ...EMPTY_FORM, ...data });
      loadCurrentTerm();
      setDialogOpen(true);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    }
  };

  // Segurança (varredura, item 2): CPF e telefone ganham máscara enquanto a
  // pessoa digita (ver lib/validation.js).
  const MASKS = { cpf: maskCPF, phone: maskPhone };
  const upd = (k) => (e) => setForm({
    ...form,
    [k]: k === "consent_terms" ? e.target.value === "true" : (MASKS[k] ? MASKS[k](e.target.value) : e.target.value),
  });

  // ========================================================================
  // HANDLERS - FORM SUBMISSION
  // ========================================================================
  const save = async (e) => {
    e.preventDefault();
    // Segurança (varredura, item 2): avisa antes de enviar. O servidor confere
    // de novo (e é ele quem decide), mas assim a pessoa corrige sem esperar.
    const problem = cpfProblem(form.cpf) || phoneProblem(form.phone);
    if (problem) {
      toast.error(problem);
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await api.put(`/patients/${editing.id}`, form);
        toast.success("Paciente atualizado.");
      } else {
        await api.post("/patients", form);
        toast.success("Paciente cadastrado.");
      }
      setDialogOpen(false);
      load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    } finally {
      setSaving(false);
    }
  };

  // ========================================================================
  // HANDLERS - DELETE / ANONYMIZE
  // ========================================================================
  // Item 12: arquivar é reversível e não apaga nada, então não pede confirmação.
  const setArchived = (p, archived) => inFlight.run(`patient:${p.id}`, async () => {
    try {
      await api.post(`/patients/${p.id}/${archived ? "archive" : "unarchive"}`);
      toast.success(archived ? "Paciente arquivado. Os prontuários continuam guardados." : "Paciente reativado.");
      load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    }
  });

  const doAction = async () => {
    if (!confirm) return;
    const { type, patient } = confirm;
    setConfirm(null);
    await inFlight.run(`patient:${patient.id}`, async () => {
    try {
      if (type === "delete") {
        await api.delete(`/patients/${patient.id}`);
        toast.success("Paciente excluído definitivamente.");
      } else {
        await api.post(`/patients/${patient.id}/anonymize`);
        toast.success("Dados anonimizados (prontuários mantidos p/ guarda legal).");
      }
      load();
    } catch (err) {
      const message = formatApiErrorDetail(err.response?.data?.detail) || err.message;
      // Item 12: 409 = prontuários sob guarda do CFP. A mensagem do servidor
      // traz a data em que a exclusão será possível; oferecemos arquivar na hora.
      if (err.response?.status === 409 && !patient.archived) {
        toast.error(message, { duration: 10000, action: { label: "Arquivar", onClick: () => setArchived(patient, true) } });
      } else {
        toast.error(message);
      }
    }
    });
  };

  // ========================================================================
  // HANDLERS - INTEGRATION
  // ========================================================================
  const copyScript = () => {
    navigator.clipboard.writeText(generatedScript);
    setCopySuccess(true);
    setTimeout(() => setCopySuccess(false), 2000);
  };

  const openIntegration = async () => {
    setNewToken("");
    setIntegration(null);
    setIntegrationOpen(true);
    try {
      const { data } = await api.get("/integrations/google-forms");
      setIntegration(data);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    }
  };

  // Item 15: gerar um token novo revoga o anterior. O valor volta UMA vez e
  // só fica neste estado enquanto a janela estiver aberta.
  const generateToken = async () => {
    setTokenBusy(true);
    try {
      const { data } = await api.post("/integrations/google-forms/token");
      setNewToken(data.token);
      setIntegration(data);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    } finally {
      setTokenBusy(false);
    }
  };

  const revokeToken = async () => {
    setTokenBusy(true);
    try {
      const { data } = await api.delete("/integrations/google-forms/token");
      setNewToken("");
      setIntegration(data);
      toast.success("Integração revogada. O formulário deixa de enviar pacientes.");
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    } finally {
      setTokenBusy(false);
    }
  };

  const closeIntegration = (open) => {
    setIntegrationOpen(open);
    if (!open) setNewToken(""); // o token não fica guardado depois de fechar
  };

  // ========================================================================
  // DERIVED STATE
  // ========================================================================
  // ESCALA: a busca já vem filtrada do servidor (índice cego); não há mais
  // filtro local sobre a lista inteira.
  const filtered = patients || [];

  const generatedScript = `
const WEBHOOK_URL = "${process.env.WEBHOOK_URL}";
const SECURITY_TOKEN = "${newToken}";

function formatCPF(cpf) {
  let v = String(cpf).replace(/\\D/g, "");
  if (v.length === 11) {
    return v.replace(/(\\d{3})(\\d{3})(\\d{3})(\\d{2})/, "$1.$2.$3-$4");
  }
  return cpf;
}

function formatPhone(phone) {
  let v = String(phone).replace(/\\D/g, "");
  if (v.length === 11) {
    return v.replace(/(\\d{2})(\\d{5})(\\d{4})/, "($1) $2-$3");
  } else if (v.length === 10) {
    return v.replace(/(\\d{2})(\\d{4})(\\d{4})/, "($1) $2-$3");
  }
  return phone;
}

function onFormSubmit(e) {
  const response = e.response;
  const itemResponses = response.getItemResponses();

  let patientData = {
    full_name: "", cpf: "", rg: "", birth_date: "", age: "",
    education: "", marital_status: "", profession: "", phone: "", email: "",
    address: "", emergency_contact: "", initial_notes: "Cadastrado via Google Forms.",
    consent_terms: false
  };

  itemResponses.forEach(itemResponse => {
    const question = itemResponse.getItem().getTitle();
    const answer = itemResponse.getResponse();
    const normalizedQuestion = String(question).trim().toLowerCase();
    const normalizedAnswer = String(answer).trim().toLowerCase();
    const answerAsText = Array.isArray(answer) ? answer.join(", ") : String(answer).trim();

    if (normalizedQuestion.includes("nome completo")) {
      patientData.full_name = answerAsText;
    } else if (normalizedQuestion.includes("cpf")) {
      patientData.cpf = formatCPF(answerAsText);
    } else if (
      normalizedQuestion.includes("contato de emergência") ||
      normalizedQuestion.includes("contato de emergencia") ||
      normalizedQuestion.includes("risco iminente")
    ) {
      patientData.emergency_contact = answerAsText;
    } else if (/\\brg\\b/.test(normalizedQuestion) || normalizedQuestion === "rg *") {
      patientData.rg = answerAsText;
    } else if (normalizedQuestion.includes("data de nascimento")) {
      const [day, month, year] = String(answer).trim().split(/[/-]/);
      patientData.birth_date = day && month && year
        ? year + "-" + month.padStart(2, "0") + "-" + day.padStart(2, "0")
        : String(answer).trim();
    } else if (normalizedQuestion.includes("escolaridade")) {
      patientData.education = answerAsText;
    } else if (/\\bidade\\b/.test(normalizedQuestion) || normalizedQuestion === "idade *") {
      patientData.age = answerAsText;
    } else if (normalizedQuestion.includes("estado civil")) {
      patientData.marital_status = answerAsText;
    } else if (normalizedQuestion.includes("profissão") || normalizedQuestion.includes("profissao")) {
      patientData.profession = answerAsText;
    } else if (normalizedQuestion.includes("contato telefônico") || normalizedQuestion.includes("contato telefonico")) {
      patientData.phone = formatPhone(answerAsText);
    } else if (normalizedQuestion.includes("e-mail") || normalizedQuestion.includes("email")) {
      patientData.email = answerAsText;
    } else if (normalizedQuestion.includes("endereço completo") || normalizedQuestion.includes("endereco completo")) {
      patientData.address = answerAsText;
    } else if (
      normalizedQuestion.includes("termo de consentimento") ||
      normalizedQuestion.includes("consentimento livre e esclarecido") ||
      normalizedQuestion.includes("estou de acordo")
    ) {
      // Aceita qualquer frase que contenha uma destas palavras
      patientData.consent_terms = normalizedAnswer.includes("concordo") ||
                                  normalizedAnswer.includes("sim") ||
                                  normalizedAnswer.includes("aceito");
    } else if (normalizedQuestion.includes("notas finais")) {
      patientData.initial_notes = answer;
    }
  });

  const payload = {
    patient_data: patientData
  };

  const options = {
    method: 'post',
    contentType: 'application/json',
    headers: {
      'x-webhook-token': SECURITY_TOKEN
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  const res = UrlFetchApp.fetch(WEBHOOK_URL, options);
  console.log(res.getContentText());
}
  `.trim();

  // ========================================================================
  // RENDER
  // ========================================================================

  return (
    <div className="space-y-6">
      <PatientHeader onNew={openNew} onIntegration={openIntegration} />
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <PatientSearch query={query} onQueryChange={setQuery} />
        <ArchiveFilter showArchived={showArchived} onChange={setShowArchived} />
      </div>
      <PatientContent patients={patients} filtered={filtered} navigate={navigate} showArchived={showArchived}
        searching={debouncedQuery.trim().length >= 2}
        onEdit={openEdit} onDelete={(p) => setConfirm({ type: "delete", patient: p })}
        onAnon={(p) => setConfirm({ type: "anon", patient: p })} onArchive={setArchived} isBusy={inFlight.isBusy} />
      {nextCursor && (
        <div className="flex justify-center">
          <Button variant="outline" onClick={loadMore} disabled={loadingMore} className="gap-2 border-slate-300"
            data-testid="patients-load-more">
            {loadingMore && <Loader2 className="h-4 w-4 animate-spin" />} Carregar mais pacientes
          </Button>
        </div>
      )}

      <PatientFormDialog open={dialogOpen} onOpenChange={setDialogOpen} editing={editing}
        form={form} onFormChange={upd} saving={saving} onSubmit={save} currentTerm={currentTerm} />

      <PatientConfirmDialog confirm={confirm} onConfirm={doAction} onOpenChange={(o) => !o && setConfirm(null)} />

      <IntegrationDialog open={integrationOpen} onOpenChange={closeIntegration}
        integration={integration} newToken={newToken} tokenBusy={tokenBusy}
        onGenerate={generateToken} onRevoke={revokeToken}
        generatedScript={generatedScript} copySuccess={copySuccess} onCopyScript={copyScript} />
    </div>
  );
}

// ============================================================================
// SUB-COMPONENTS
// ============================================================================

function PatientHeader({ onNew, onIntegration }) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 className="font-head text-3xl font-extrabold tracking-tight text-[#0F172A]">Pacientes</h1>
        <p className="mt-1 text-sm text-slate-500">Gerencie cadastros e acesse prontuários</p>
      </div>
      <div className="flex gap-2">
        <Button onClick={onIntegration} variant="outline" className="gap-2 border-slate-300">
          <FileText className="h-4 w-4" /> Integrar Google Forms
        </Button>
        <Button onClick={onNew} className="gap-2 bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="new-patient-btn">
          <Plus className="h-4 w-4" /> Novo paciente
        </Button>
      </div>
    </div>
  );
}

function PatientSearch({ query, onQueryChange }) {
  return (
    <div className="relative max-w-sm">
      <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
      <Input
        maxLength={LIMITS.search} // varredura, item 2: o servidor aceita até 120 caracteres
        placeholder="Buscar por nome (mín. 2 letras)..."
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        className="pl-9"
        data-testid="patient-search-input"
      />
    </div>
  );
}

// Item 12: alterna entre a lista ativa e os pacientes arquivados.
function ArchiveFilter({ showArchived, onChange }) {
  const option = (active) =>
    `rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
      active ? "bg-white text-[#0F172A] shadow-sm" : "text-slate-500 hover:text-[#0F172A]"
    }`;
  return (
    <div className="inline-flex self-start rounded-lg bg-slate-100 p-1" role="group" aria-label="Filtrar pacientes">
      <button type="button" className={option(!showArchived)} aria-pressed={!showArchived}
        onClick={() => onChange(false)} data-testid="filter-active">Ativos</button>
      <button type="button" className={option(showArchived)} aria-pressed={showArchived}
        onClick={() => onChange(true)} data-testid="filter-archived">Arquivados</button>
    </div>
  );
}

function PatientContent({ patients, filtered, navigate, showArchived, searching, onEdit, onDelete, onAnon, onArchive, isBusy }) {
  if (patients === null) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="h-6 w-6 animate-spin text-[#1E3A8A]" />
      </div>
    );
  }

  if (filtered.length === 0) {
    return (
      <Card className="border-slate-200 py-16 text-center shadow-sm">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-[#F0F4F8]">
          <Users className="h-6 w-6 text-[#1E3A8A]" />
        </div>
        <p className="mt-4 font-head text-lg font-bold text-[#0F172A]">
          {showArchived ? "Nenhum paciente arquivado" : "Nenhum paciente encontrado"}
        </p>
        <p className="mt-1 text-sm text-slate-500">
          {searching
            ? "Nenhum nome começa com o que foi digitado. A busca considera o início de cada palavra."
            : showArchived
              ? "Pacientes arquivados saem da lista ativa, mas os prontuários continuam guardados."
              : "Cadastre seu primeiro paciente para começar."}
        </p>
      </Card>
    );
  }

  return (
    <>
      <PatientTable filtered={filtered} navigate={navigate} onEdit={onEdit} isBusy={isBusy}
        onDelete={onDelete} onAnon={onAnon} onArchive={onArchive} />
      <PatientMobileCards filtered={filtered} navigate={navigate} onEdit={onEdit} isBusy={isBusy}
        onDelete={onDelete} onAnon={onAnon} onArchive={onArchive} />
    </>
  );
}

function PatientTable({ filtered, navigate, onEdit, onDelete, onAnon, onArchive, isBusy }) {
  return (
    <Card className="hidden border-slate-200 shadow-sm md:block" data-testid="patients-table">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead>Nome</TableHead>
            <TableHead>Nascimento</TableHead>
            <TableHead>Última consulta</TableHead>
            <TableHead className="w-10"></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {filtered.map((p) => (
            <TableRow key={p.id} className="cursor-pointer hover:bg-slate-50"
              onClick={() => navigate(`/pacientes/${p.id}`)} data-testid={`patient-row-${p.id}`}>
              <TableCell className="font-medium text-[#0F172A]">
                <div className="flex items-center gap-3">
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[#1E3A8A] text-xs font-semibold text-white">
                    {(p.full_name || "?")[0]}
                  </div>
                  {p.full_name}
                </div>
              </TableCell>
              <TableCell className="text-slate-600">{fmtDate(p.birth_date)}</TableCell>
              <TableCell className="text-slate-600">{fmtDate(p.last_consultation_date)}</TableCell>
              <TableCell onClick={(e) => e.stopPropagation()}>
                <RowMenu p={p} busy={isBusy?.(`patient:${p.id}`)} onEdit={onEdit} onOpen={() => navigate(`/pacientes/${p.id}`)}
                  onDelete={onDelete} onAnon={onAnon} onArchive={onArchive} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}

function PatientMobileCards({ filtered, navigate, onEdit, onDelete, onAnon, onArchive, isBusy }) {
  return (
    <div className="space-y-3 md:hidden">
      {filtered.map((p) => (
        <Card key={p.id} className="border-slate-200 p-4 shadow-sm" data-testid={`patient-card-${p.id}`}>
          <div className="flex items-start justify-between">
            <button className="flex items-center gap-3 text-left" onClick={() => navigate(`/pacientes/${p.id}`)}>
              <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[#1E3A8A] text-sm font-semibold text-white">
                {(p.full_name || "?")[0]}
              </div>
              <div>
                <p className="font-medium text-[#0F172A]">{p.full_name}</p>
                <p className="text-xs text-slate-500">Nasc. {fmtDate(p.birth_date)}</p>
              </div>
            </button>
            <RowMenu p={p} busy={isBusy?.(`patient:${p.id}`)} onEdit={onEdit} onOpen={() => navigate(`/pacientes/${p.id}`)}
              onDelete={onDelete} onAnon={onAnon} onArchive={onArchive} />
          </div>
          <p className="mt-2 text-xs text-slate-500">Última consulta: {fmtDate(p.last_consultation_date)}</p>
        </Card>
      ))}
    </div>
  );
}

function PatientFormDialog({ open, onOpenChange, editing, form, onFormChange, saving, onSubmit, currentTerm }) {
  // Quem já consentiu vê a cópia do texto que aceitou; os demais, o termo atual.
  const consentText = editing?.consent_terms ? editing.consent_terms_text : currentTerm;
  const consentDate = editing?.consent_terms && editing.consent_given_at
    ? new Date(editing.consent_given_at).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" })
    : "";
  // Segurança (varredura, item 2): limites iguais aos do servidor e aviso de
  // CPF/telefone inválido já no campo. Um paciente vindo do Google Forms pode
  // ter CPF digitado errado pelo próprio paciente: o aviso aparece ao editar.
  const cpfError = cpfProblem(form.cpf);
  const phoneError = phoneProblem(form.phone);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-head">{editing ? "Editar paciente" : "Novo paciente"}</DialogTitle>
          {/* Item 13: antes só o CPF (e o texto dizia "AES-256", que não é o algoritmo usado). */}
          <DialogDescription>Nome, documentos, contatos e observações são criptografados antes de serem salvos.</DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <Label>Nome completo *</Label>
            <Input
              required
              maxLength={LIMITS.patientName}
              value={form.full_name}
              onChange={onFormChange("full_name")}
              className="mt-1.5"
              data-testid="patient-name-input"
            />
          </div>

          <div>
            <Label htmlFor="patient-cpf">CPF</Label>
            <Input
              id="patient-cpf"
              inputMode="numeric"
              maxLength={LIMITS.cpf}
              value={form.cpf}
              onChange={onFormChange("cpf")}
              className="mt-1.5"
              aria-invalid={Boolean(cpfError)}
              aria-describedby={cpfError ? "patient-cpf-error" : undefined}
              data-testid="patient-cpf-input"
              placeholder="000.000.000-00"
            />
            {cpfError && <p id="patient-cpf-error" className="mt-1 text-xs text-red-600">{cpfError}</p>}
          </div>

          <div>
            <Label>RG</Label>
            <Input
              maxLength={LIMITS.rg}
              value={form.rg}
              onChange={onFormChange("rg")}
              className="mt-1.5"
              data-testid="patient-rg-input"
            />
          </div>

          <div>
            <Label>Data de nascimento</Label>
            <Input
              type="date"
              min="1900-01-01"
              max={todayISO()}
              value={form.birth_date}
              onChange={onFormChange("birth_date")}
              className="mt-1.5"
              data-testid="patient-birthdate-input"
            />
          </div>

          <div>
            <Label>Idade</Label>
            <Input
              type="number"
              min={0}
              max={130}
              value={form.age}
              onChange={onFormChange("age")}
              className="mt-1.5"
              data-testid="patient-age-input"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Escolaridade *</Label>
              <select
                value={form.education}
                onChange={onFormChange("education")}
                className="mt-1.5 flex h-10 w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-950"
              >
                <option value="">Selecione...</option>
                <option value="Fundamental - Incompleto">Fundamental - Incompleto</option>
                <option value="Fundamental - Completo">Fundamental - Completo</option>
                <option value="Médio - Incompleto">Médio - Incompleto</option>
                <option value="Médio - Completo">Médio - Completo</option>
                <option value="Superior - Incompleto">Superior - Incompleto</option>
                <option value="Superior - Completo">Superior - Completo</option>
                <option value="Pós Graduação - Completo">Pós Graduação - Completo</option>
              </select>
            </div>
            <div>
              <Label>Estado civil</Label>
              <select
                value={form.marital_status}
                onChange={onFormChange("marital_status")}
                className="mt-1.5 flex h-10 w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-950"
              >
                <option value="">Selecione...</option>
                <option value="Solteiro(a)">Solteiro(a)</option>
                <option value="Casado(a)">Casado(a)</option>
                <option value="Divorciado(a)">Divorciado(a)</option>
                <option value="Viúvo(a)">Viúvo(a)</option>
                <option value="União estável">União estável</option>
              </select>
            </div>
          </div>

          <div>
            <Label>Profissão</Label>
            <Input
              maxLength={LIMITS.profession}
              value={form.profession}
              onChange={onFormChange("profession")}
              className="mt-1.5"
              data-testid="patient-profession-input"
            />
          </div>

          <div>
            <Label htmlFor="patient-phone">Telefone</Label>
            <Input
              id="patient-phone"
              type="tel"
              inputMode="tel"
              maxLength={LIMITS.phone}
              value={form.phone}
              onChange={onFormChange("phone")}
              className="mt-1.5"
              aria-invalid={Boolean(phoneError)}
              aria-describedby={phoneError ? "patient-phone-error" : undefined}
              data-testid="patient-phone-input"
              placeholder="(11) 91234-5678"
            />
            {phoneError && <p id="patient-phone-error" className="mt-1 text-xs text-red-600">{phoneError}</p>}
          </div>

          <div>
            <Label>Email</Label>
            <Input
              type="email"
              maxLength={LIMITS.email}
              value={form.email}
              onChange={onFormChange("email")}
              className="mt-1.5"
              data-testid="patient-email-input"
            />
          </div>

          <div>
            <Label>Endereço</Label>
            <Input
              maxLength={LIMITS.address}
              value={form.address}
              onChange={onFormChange("address")}
              className="mt-1.5"
              data-testid="patient-address-input"
            />
          </div>

          <div>
            <Label>Contato de emergência</Label>
            <Input
              maxLength={LIMITS.emergencyContact}
              value={form.emergency_contact}
              onChange={onFormChange("emergency_contact")}
              className="mt-1.5"
              data-testid="patient-emergency-input"
            />
          </div>

          <div>
            <Label>Notas iniciais</Label>
            <Textarea
              maxLength={LIMITS.patientNotes}
              value={form.initial_notes}
              onChange={onFormChange("initial_notes")}
              className="mt-1.5"
              data-testid="patient-notes-input"
              rows={3}
            />
          </div>

          <div className="space-y-3 rounded-md border border-slate-200 bg-slate-50 p-4">
            <div>
              <p className="text-sm font-semibold text-[#0F172A]">Termo de consentimento livre e esclarecido</p>
              {consentText ? (
                <p className="psi-scroll mt-1 max-h-48 overflow-y-auto whitespace-pre-line text-xs leading-relaxed text-slate-600">{consentText}</p>
              ) : (
                <div className="flex justify-center py-3"><Loader2 className="h-4 w-4 animate-spin text-[#1E3A8A]" /></div>
              )}
              {consentDate && (
                <p className="mt-2 text-xs font-medium text-emerald-700">Consentimento registrado em {consentDate}.</p>
              )}
            </div>
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium text-[#0F172A]">O paciente concorda com o termo? *</legend>
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="radio"
                  name="consent_terms"
                  value="true"
                  checked={form.consent_terms === true}
                  onChange={onFormChange("consent_terms")}
                  required
                />
                Concordo
              </label>
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="radio"
                  name="consent_terms"
                  value="false"
                  checked={form.consent_terms === false}
                  onChange={onFormChange("consent_terms")}
                />
                Não concordo
              </label>
            </fieldset>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancelar</Button>
            <Button type="submit" disabled={saving} className="bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="save-patient-btn">
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Salvar"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function PatientConfirmDialog({ confirm, onConfirm, onOpenChange }) {
  return (
    <AlertDialog open={!!confirm} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {confirm?.type === "delete" ? "Excluir paciente?" : "Anonimizar dados?"}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {confirm?.type === "delete"
              ? "Pacientes sem prontuário são excluídos na hora e de forma permanente. Se houver prontuários, a exclusão só é permitida depois de 5 anos do último registro (guarda obrigatória do CFP); até lá, use Arquivar."
              : "Os dados pessoais serão removidos, mas os prontuários serão mantidos pelo período de guarda legal exigido pelo CFP."}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancelar</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}
            className={confirm?.type === "delete" ? "bg-[#EF4444] hover:bg-red-700" : "bg-[#1E3A8A] hover:bg-[#0F2C59]"}
            data-testid="confirm-action-btn">
            {confirm?.type === "delete" ? "Excluir" : "Anonimizar"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

// Item 15: o token não é mais carregado junto com a tela. O profissional gera
// um token aqui, copia o script na hora (o token não pode ser visto de novo)
// e pode trocá-lo ou revogá-lo a qualquer momento.
function IntegrationDialog({ open, onOpenChange, integration, newToken, tokenBusy, onGenerate, onRevoke,
  generatedScript, copySuccess, onCopyScript }) {
  const createdAt = integration?.created_at
    ? new Date(integration.created_at).toLocaleDateString("pt-BR")
    : "";
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="font-head text-xl">Integração com Google Forms</DialogTitle>
          <DialogDescription>
            Vincule os cadastros do Forms direto na sua plataforma de forma segura e criptografada.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 text-sm text-slate-700">
          {!integration ? (
            <div className="flex justify-center py-6"><Loader2 className="h-5 w-5 animate-spin text-[#1E3A8A]" /></div>
          ) : (
            <div className="rounded-md border border-slate-200 bg-slate-50 p-4">
              <div className="flex items-start gap-3">
                <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-[#1E3A8A]" />
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-[#0F172A]">
                    {integration.configured
                      ? `Integração ativa · token terminado em …${integration.hint}${createdAt ? ` · gerado em ${createdAt}` : ""}`
                      : "Nenhum token de integração ativo"}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    Por segurança, o token é exibido só no momento em que é gerado. Gerar um novo token desativa o anterior
                    (atualize o script no formulário depois disso).
                  </p>
                </div>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button size="sm" onClick={onGenerate} disabled={tokenBusy} className="bg-[#1E3A8A] hover:bg-[#0F2C59]"
                  data-testid="generate-webhook-token">
                  {tokenBusy
                    ? <Loader2 className="h-4 w-4 animate-spin" />
                    : integration.configured ? "Gerar novo token e script" : "Gerar token e script"}
                </Button>
                {integration.configured && (
                  <Button size="sm" variant="outline" onClick={onRevoke} disabled={tokenBusy}
                    className="border-red-200 text-red-600 hover:bg-red-50">
                    Revogar integração
                  </Button>
                )}
              </div>
            </div>
          )}

          {newToken && (
            <>
              <div className="space-y-2">
                <p><strong>Passo 1:</strong> No seu Google Forms, clique nos 3 pontos verticais (canto superior direito) e acesse <strong>Apps Script</strong>.</p>
                <p><strong>Passo 2:</strong> Apague o código padrão e cole o seu script de integração pessoal gerado abaixo. <strong>Copie agora:</strong> depois de fechar esta janela o token não poderá ser exibido de novo.</p>
              </div>

              <div className="relative rounded-md bg-slate-900 p-4">
                <Button
                  size="sm"
                  onClick={onCopyScript}
                  className="absolute right-2 top-2 bg-slate-700 hover:bg-slate-600 text-white"
                >
                  {copySuccess ? "Copiado!" : "Copiar Script"}
                </Button>
                <pre className="overflow-x-auto text-xs text-green-400">
                  <code>{generatedScript}</code>
                </pre>
              </div>

              <div className="space-y-2">
                <p><strong>Passo 3:</strong> No menu central, salve o projeto no <strong>Drive</strong>.</p>
                <p><strong>Passo 4:</strong> No menu lateral esquerdo do painel do Google, clique no ícone de <strong>Relógio (Acionadores)</strong>.</p>
                <p><strong>Passo 5:</strong> Clique em "Adicionar Acionador" e configure para rodar a função <code>onFormSubmit</code> no evento <strong>"Ao enviar o formulário"</strong> e, em Configurações de notificação de falha, escolha <strong>Receber notificações imediatamente</strong>.</p>
                <p className="mt-2 text-xs text-slate-500">* Atenção: este código contém seu token de segurança pessoal. Não o compartilhe publicamente. Se ele vazar, gere um novo token aqui.</p>
              </div>
            </>
          )}

          {/* Termo por profissional: o consentimento marcado no formulário é
              registrado com a cópia do termo configurado no sistema. */}
          <p className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
            Use no formulário o mesmo texto do seu Termo de consentimento (Privacidade &amp; LGPD). O consentimento
            marcado no formulário é registrado com esse texto e a data do envio.
          </p>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function RowMenu({ p, busy, onEdit, onOpen, onDelete, onAnon, onArchive }) {
  // Varredura, item 9: enquanto uma ação deste paciente está em andamento, o
  // menu mostra "carregando" e as ações que gravam ficam desabilitadas.
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="h-8 w-8" data-testid={`patient-menu-${p.id}`}
          aria-busy={busy || undefined}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin text-slate-500" /> : <MoreVertical className="h-4 w-4 text-slate-500" />}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={onOpen}><FileText className="mr-2 h-4 w-4" /> Prontuários</DropdownMenuItem>
        <DropdownMenuItem onClick={() => onEdit(p)}><Pencil className="mr-2 h-4 w-4" /> Editar</DropdownMenuItem>
        {/* Item 12: arquivar é a alternativa à exclusão durante a guarda do CFP. */}
        {p.archived ? (
          <DropdownMenuItem disabled={busy} onClick={() => onArchive(p, false)}><ArchiveRestore className="mr-2 h-4 w-4" /> Reativar</DropdownMenuItem>
        ) : (
          <DropdownMenuItem disabled={busy} onClick={() => onArchive(p, true)}><Archive className="mr-2 h-4 w-4" /> Arquivar</DropdownMenuItem>
        )}
        <DropdownMenuItem disabled={busy} onClick={() => onAnon(p)}><ShieldOff className="mr-2 h-4 w-4" /> Anonimizar</DropdownMenuItem>
        <DropdownMenuItem disabled={busy} onClick={() => onDelete(p)} className="text-[#EF4444]"><Trash2 className="mr-2 h-4 w-4" /> Excluir</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
