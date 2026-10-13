// ============================================================================
// IMPORTS
// ============================================================================
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  Plus, Search, MoreVertical, Pencil, Trash2, ShieldOff, FileText, Users, Loader2,
  Archive, ArchiveRestore, Link2,
} from "lucide-react";

// API & Utils
import api, { formatApiErrorDetail } from "@/lib/api";
import { LIMITS } from "@/lib/validation";
// Produto (cadastro pelo paciente): regras e campos da ficha compartilhados com o link de cadastro.
import {
  EMPTY_FORM, applyFieldChange, errorFields, isMinorForm, payloadForSubmit, validatePatientForm,
} from "@/lib/patientForm";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { formatDateOnly, formatDateTime } from "@/lib/datetime";
import { countLabel } from "@/lib/utils";

// UI Components
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { useWriteAccess } from "@/context/WriteAccessContext";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from "@/components/ui/dialog";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  ConsentQuestion, ConsentTermText, FieldError, FormErrorSummary, FormSection, PatientFormFields, fieldA11y, invalidClass,
} from "@/components/PatientFormFields";
import IntakeLinksDialog from "@/components/IntakeLinksDialog";

// ============================================================================
// CONSTANTS & HELPERS
// ============================================================================
// Data sem hora tratada como texto, sem fuso (versão única em lib/datetime.js).
const fmtDate = formatDateOnly;

// Regras da ficha (campos obrigatórios, menores de idade, validação, máscaras):
// em lib/patientForm.js. O link de cadastro do paciente (pages/PatientIntake.js)
// usa as mesmas, e os campos do formulário ficam em components/PatientFormFields.js.

// Listas de seleção: o componente único NativeSelect (análise de UI, F4),
// com a mesma aparência dos campos de texto. Antes havia uma classe própria aqui.

// Termo por profissional: aqui existia uma CÓPIA do termo com o nome e o CRP de
// uma psicóloga fixos no código ("Fernanda Andrade Da Silva – CRP 06/228054"),
// mostrada no cadastro de pacientes de TODOS os profissionais. O texto agora
// vem do backend (GET /consent-term): o termo atual do profissional logado,
// ou, para quem já consentiu, a cópia exata do texto aceito.

// ESCALA: tamanho da página da lista de pacientes (o servidor aceita até 100).
const PAGE_SIZE = 50;

// Complementares (lista de pacientes): ordens oferecidas (o servidor ordena;
// o nome é criptografado, ver _patients_page em server.py).
const SORT_OPTIONS = [
  { id: "recent", label: "Cadastrados por último" },
  { id: "name", label: "Nome (A a Z)" },
  { id: "last_consultation", label: "Última consulta" },
];

export default function Patients() {
  // ========================================================================
  // STATE MANAGEMENT
  // ========================================================================
  const navigate = useNavigate();
  // Análise de UI (O1): ?novo=1 (link "Cadastre o primeiro paciente") abre o cadastro.
  const [searchParams, setSearchParams] = useSearchParams();
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
  // A4: erros por campo ({ cpf: "CPF inválido..." }) e erro devolvido pelo servidor.
  const [formErrors, setFormErrors] = useState({});
  const [submitError, setSubmitError] = useState("");
  const [confirm, setConfirm] = useState(null); // {type, patient}
  // Segurança (varredura, item 9): arquivar, anonimizar e excluir não travavam
  // durante o envio; um clique repetido mandava a ação de novo. Agora cada
  // paciente aceita uma ação por vez (chave `patient:<id>`).
  const inFlight = useSingleFlight();
  // Item 12: pacientes arquivados ficam fora da lista ativa, em um filtro próprio.
  const [showArchived, setShowArchived] = useState(false);
  // Complementares: ordem da lista e total de pacientes (com o filtro/busca atual).
  const [sort, setSort] = useState("recent");
  const [total, setTotal] = useState(null);
  // Termo atual do profissional (para novos consentimentos).
  const [currentTerm, setCurrentTerm] = useState("");

  // Produto (cadastro pelo paciente): janela dos links de cadastro (substitui a
  // integração com o Google Forms, que mandava os dados do paciente por fora,
  // num script com token fixo) e o link cujo cadastro incompleto está sendo
  // completado na ficha (null = cadastro manual ou edição).
  const [intakeOpen, setIntakeOpen] = useState(false);
  const [completingIntake, setCompletingIntake] = useState(null);
  // Muda quando um cadastro incompleto é completado: a lista de links se atualiza.
  const [intakeRefresh, setIntakeRefresh] = useState(0);

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
      return api.post("/patients/search", { q: term, archived: showArchived, cursor, limit: PAGE_SIZE, sort });
    }
    const params = new URLSearchParams({ archived: showArchived, limit: PAGE_SIZE, sort });
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
      setTotal(data.total ?? null);
      // Item 15: antes este load também buscava /auth/me só para pegar o token
      // do webhook, deixando o token em memória em toda visita à tela.
    } catch (err) {
      if (seq !== requestSeq.current) return;
      setPatients([]);
      setNextCursor(null);
      // Varredura, item 15: a busca agora tem limite por minuto. Sem este
      // aviso a pessoa veria só "Nenhum paciente encontrado".
      if (err.response?.status === 429) toast.warning(formatApiErrorDetail(err.response.data?.detail));
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
  useEffect(() => { setPatients(null); load(); }, [showArchived, debouncedQuery, sort]);

  // ========================================================================
  // HANDLERS - DIALOG
  // ========================================================================
  // Produto (assinatura): paciente NOVO exige gravação; no somente leitura aparece a
  // explicação (com "Ver planos"). Editar o cadastro de quem já existe continua
  // liberado (correção, LGPD art. 18, III), como no servidor.
  const { guard, canWrite } = useWriteAccess();
  // AS2/RS4: no somente leitura, editar ou completar o cadastro continua liberado,
  // mas as "Notas iniciais" (texto clínico) ficam travadas como estão (o servidor
  // aplica a mesma regra: _check_initial_notes).
  const notesLocked = !canWrite && Boolean(editing || completingIntake);
  const formOptions = { includeNotes: !notesLocked };
  const openNew = guard(() => {
    setEditing(null);
    setCompletingIntake(null);
    setForm(EMPTY_FORM);
    setFormErrors({}); // A4: formulário novo começa sem erros
    setSubmitError("");
    loadCurrentTerm(); // sempre o texto mais recente (nome/CRP/modelo podem ter mudado)
    setDialogOpen(true);
  });

  // O1: chegou com ?novo=1 -> abre o cadastro; o parâmetro sai da URL (recarregar não reabre).
  // RS2: ?cadastros=1 (aviso do Painel e e-mail dos incompletos) abre os links de cadastro.
  useEffect(() => {
    if (searchParams.get("cadastros") === "1") {
      setIntakeOpen(true);
      setSearchParams({}, { replace: true });
      return;
    }
    if (searchParams.get("novo") !== "1") return;
    openNew();
    setSearchParams({}, { replace: true });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  // ESCALA: a lista agora traz só um resumo (nome e datas). A edição busca a
  // ficha completa antes de abrir o formulário: sem isso, salvar enviaria
  // CPF, endereço, observações etc. VAZIOS e apagaria esses dados.
  const openEdit = async (p) => {
    try {
      const { data } = await api.get(`/patients/${p.id}`);
      setEditing(data);
      setCompletingIntake(null);
      setFormErrors({}); // A4
      setSubmitError("");
      setForm({ ...EMPTY_FORM, ...data });
      loadCurrentTerm();
      setDialogOpen(true);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    }
  };

  // Produto (cadastro pelo paciente): o tempo do link acabou com o cadastro pela
  // metade. A ficha abre com o que o paciente preencheu; a psicóloga completa (de
  // preferência com ele) e salva. A leitura fica na trilha de auditoria (servidor).
  // RS4: liberado também no somente leitura (são dados que o paciente forneceu, e o
  // prazo de 60 dias apagaria esses dados sem que a psicóloga pudesse salvá-los).
  const reviewIncomplete = async (intake) => {
    try {
      const { data } = await api.get(`/intake-links/${intake.id}/draft`);
      const draft = data.data || {};
      setEditing(null);
      setCompletingIntake(intake);
      setFormErrors({});
      setSubmitError("");
      // A idade acompanha a data de nascimento (o rascunho guarda só a data).
      setForm(applyFieldChange({ ...EMPTY_FORM, ...draft }, "birth_date", draft.birth_date || ""));
      loadCurrentTerm();
      setIntakeOpen(false);
      setDialogOpen(true);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    }
  };

  // Segurança (varredura, item 2): CPF e telefone (também os do responsável, C3)
  // ganham máscara enquanto a pessoa digita, e a idade acompanha a data de
  // nascimento. As mesmas regras do link do paciente (applyFieldChange, lib/patientForm.js).
  const upd = (k) => (e) => {
    setForm(applyFieldChange(form, k, e.target.value));
    // A4: a pessoa mexeu no campo, a mensagem de erro dele sai (volta a ser
    // conferido no próximo "Salvar"). CPF e telefone continuam com o aviso ao vivo.
    setFormErrors((current) => {
      if (!current[k]) return current;
      const next = { ...current };
      delete next[k];
      return next;
    });
  };

  // ========================================================================
  // HANDLERS - FORM SUBMISSION
  // ========================================================================
  const save = async (e) => {
    e.preventDefault();
    // Segurança (varredura, item 2): avisa antes de enviar. O servidor confere
    // de novo (e é ele quem decide), mas assim a pessoa corrige sem esperar.
    // PRODUTO: nenhum campo em branco. Lista tudo o que falta e leva o foco ao
    // primeiro campo com problema, em vez de a pessoa descobrir um erro de cada vez.
    // A4: os erros ficam no formulário (resumo + mensagem em cada campo), não num aviso que some.
    const errors = validatePatientForm(form, formOptions);  // AS2: notas travadas não são exigidas
    setFormErrors(errors);
    setSubmitError("");
    const firstInvalid = errorFields(form, formOptions).find(([key]) => errors[key]);
    if (firstInvalid) {
      document.getElementById(firstInvalid[2])?.focus();
      return;
    }
    setSaving(true);
    // C3: para menores, estado civil e profissão não se aplicam (campos escondidos).
    const payload = payloadForSubmit(form);
    try {
      if (completingIntake) {
        // Produto (cadastro pelo paciente): completa o cadastro incompleto do link
        // (vira paciente e o rascunho do link é apagado no servidor).
        await api.post(`/intake-links/${completingIntake.id}/complete`, payload);
        toast.success("Cadastro completado. O paciente já está na lista.");
        setCompletingIntake(null);
        setIntakeRefresh((n) => n + 1);
      } else if (editing) {
        await api.put(`/patients/${editing.id}`, payload);
        toast.success("Paciente atualizado.");
      } else {
        await api.post("/patients", payload);
        toast.success("Paciente cadastrado.");
      }
      setDialogOpen(false);
      load();
    } catch (err) {
      // A4: o erro do servidor (ex.: dado recusado) também fica no formulário, no
      // resumo do topo, que recebe o foco para ser lido; antes era um aviso que sumia.
      setSubmitError(formatApiErrorDetail(err.response?.data?.detail) || err.message);
      requestAnimationFrame(() => document.getElementById("patient-form-errors")?.focus());
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
  // DERIVED STATE
  // ========================================================================
  // ESCALA: a busca já vem filtrada do servidor (índice cego); não há mais
  // filtro local sobre a lista inteira.
  const filtered = patients || [];

  // ========================================================================
  // RENDER
  // ========================================================================

  return (
    <div className="space-y-6">
      <PatientHeader onNew={openNew} onIntake={() => setIntakeOpen(true)} />
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <PatientSearch query={query} onQueryChange={setQuery} />
        <div className="flex flex-wrap items-center gap-2">
          <SortSelect sort={sort} onChange={setSort} />
          <ArchiveFilter showArchived={showArchived} onChange={setShowArchived} />
        </div>
      </div>
      {/* Complementares: total de pacientes (antes a tela não dizia quantos eram). */}
      {total !== null && patients !== null && (
        <p className="-mt-2 text-sm text-slate-500" role="status" data-testid="patients-total">
          {countLabel(total, showArchived ? "paciente arquivado" : "paciente", showArchived ? "pacientes arquivados" : "pacientes")}
          {/* Concordância: "1 paciente encontrado", "3 pacientes encontrados". */}
          {debouncedQuery.trim().length >= 2 ? (total === 1 ? " encontrado na busca" : " encontrados na busca") : ""}
        </p>
      )}
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
        form={form} onFormChange={upd} saving={saving} onSubmit={save} currentTerm={currentTerm}
        errors={formErrors} submitError={submitError} completing={Boolean(completingIntake)}
        notesLocked={notesLocked} />

      <PatientConfirmDialog confirm={confirm} onConfirm={doAction} onOpenChange={(o) => !o && setConfirm(null)} />

      {/* Produto (cadastro pelo paciente): links de cadastro (antes, "Integrar Google Forms"). */}
      <IntakeLinksDialog open={intakeOpen} onOpenChange={setIntakeOpen} onReviewIncomplete={reviewIncomplete}
        refreshKey={intakeRefresh} />
    </div>
  );
}

// ============================================================================
// SUB-COMPONENTS
// ============================================================================

function PatientHeader({ onNew, onIntake }) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 className="font-head text-3xl font-extrabold tracking-tight text-[#0F172A]">Pacientes</h1>
        <p className="mt-1 text-sm text-slate-500">Gerencie cadastros e acesse prontuários</p>
      </div>
      {/* Acessibilidade (análise de UI, A5): flex-wrap. Em 320 px (celular pequeno ou
          zoom de 400%) os dois botões não cabem lado a lado, e "Novo paciente", a ação
          principal da tela, ficava cortado para fora. Agora ele desce para a linha de baixo. */}
      <div className="flex flex-wrap gap-2">
        {/* Produto (cadastro pelo paciente): o paciente preenche a própria ficha num
            link temporário (substitui a integração com o Google Forms). */}
        <Button onClick={onIntake} variant="outline" className="gap-2 border-slate-300" data-testid="intake-links-btn">
          <Link2 className="h-4 w-4" aria-hidden="true" /> Link de cadastro
        </Button>
        <Button onClick={onNew} className="gap-2 bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="new-patient-btn">
          <Plus className="h-4 w-4" /> Novo paciente
        </Button>
      </div>
    </div>
  );
}

function PatientSearch({ query, onQueryChange }) {
  // Correção (varredura de UI, defeito 7): o campo não tinha largura própria e,
  // na linha ao lado do filtro Ativos/Arquivados, encolhia até cortar o texto de
  // ajuda ("Buscar por nome (mín. 2"). Agora ocupa a largura disponível, até ~380 px.
  // Análise de UI (P5): a busca aceita também telefone (com ou sem DDD) e CPF.
  return (
    <div className="relative w-full sm:max-w-sm">
      <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" aria-hidden="true" />
      <Input
        maxLength={LIMITS.search} // varredura, item 2: o servidor aceita até 120 caracteres
        aria-label="Buscar paciente por nome, telefone ou CPF"
        placeholder="Buscar por nome, telefone ou CPF..."
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        className="pl-9"
        data-testid="patient-search-input"
      />
    </div>
  );
}

// Complementares: ordem da lista. Lista nativa (<select>): leve e com o
// comportamento de acessibilidade do próprio navegador.
function SortSelect({ sort, onChange }) {
  return (
    <div className="flex items-center gap-2">
      <Label htmlFor="patients-sort" className="text-sm text-slate-600">Ordem</Label>
      {/* F4: a mesma lista de seleção de todo o sistema. */}
      <NativeSelect id="patients-sort" value={sort} onChange={(e) => onChange(e.target.value)} data-testid="patients-sort">
        {SORT_OPTIONS.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
      </NativeSelect>
    </div>
  );
}

// Complementares: próxima sessão marcada (não cancelada) de cada paciente.
const fmtNextSession = (iso) => (iso ? formatDateTime(iso) : "—");

// Item 12: alterna entre a lista ativa e os pacientes arquivados.
function ArchiveFilter({ showArchived, onChange }) {
  const option = (active) =>
    `rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
      // Acessibilidade (varredura de UI): opção inativa slate-500 sobre slate-100 = 4,3:1 -> slate-600.
      active ? "bg-white text-[#0F172A] shadow-sm" : "text-slate-600 hover:text-[#0F172A]"
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
          {/* P5: explica as três formas de busca (o telefone precisa de 8 números ou mais). */}
          {searching
            ? "A busca considera o início de cada palavra do nome, o telefone (pelo menos 8 números, com ou sem DDD) e o CPF."
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
            <TableHead>Próxima sessão</TableHead>
            {/* A11: a coluna do menu tem nome para o leitor de tela (antes cabeçalho vazio). */}
            <TableHead className="w-10"><span className="sr-only">Ações</span></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {filtered.map((p) => (
            <TableRow key={p.id} className="cursor-pointer hover:bg-slate-50"
              onClick={() => navigate(`/pacientes/${p.id}`)} data-testid={`patient-row-${p.id}`}>
              <TableCell className="font-medium text-[#0F172A]">
                <div className="flex items-center gap-3">
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[#1E3A8A] text-xs font-semibold text-white"
                    aria-hidden="true">
                    {(p.full_name || "?")[0]}
                  </div>
                  {/* Acessibilidade (análise de UI, A11): o nome é um LINK para a ficha.
                      Antes só a linha inteira era clicável (com mouse): pelo teclado a ficha
                      só abria pelo menu "⋮", e não dava para abrir em nova aba para comparar
                      pacientes. stopPropagation: o clique no link não dispara também o da linha. */}
                  <Link to={`/pacientes/${p.id}`} onClick={(e) => e.stopPropagation()}
                    className="rounded-sm hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]"
                    data-testid={`patient-link-${p.id}`}>
                    {p.full_name}
                  </Link>
                </div>
              </TableCell>
              <TableCell className="text-slate-600">{fmtDate(p.birth_date)}</TableCell>
              <TableCell className="text-slate-600">{fmtDate(p.last_consultation_date)}</TableCell>
              <TableCell className="text-slate-600">{fmtNextSession(p.next_session)}</TableCell>
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
            {/* A11: link (e não botão) para a ficha: abre também em nova aba. */}
            <Link to={`/pacientes/${p.id}`} className="flex items-center gap-3 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]">
              <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[#1E3A8A] text-sm font-semibold text-white"
                aria-hidden="true">
                {(p.full_name || "?")[0]}
              </div>
              <div>
                <p className="font-medium text-[#0F172A]">{p.full_name}</p>
                <p className="text-xs text-slate-500">Nasc. {fmtDate(p.birth_date)}</p>
              </div>
            </Link>
            <RowMenu p={p} busy={isBusy?.(`patient:${p.id}`)} onEdit={onEdit} onOpen={() => navigate(`/pacientes/${p.id}`)}
              onDelete={onDelete} onAnon={onAnon} onArchive={onArchive} />
          </div>
          <p className="mt-2 text-xs text-slate-500">
            Última consulta: {fmtDate(p.last_consultation_date)}
            {p.next_session && <> · Próxima: {fmtNextSession(p.next_session)}</>}
          </p>
        </Card>
      ))}
    </div>
  );
}

// `completing`: a ficha veio de um link de cadastro incompleto (produto: cadastro pelo paciente).
function PatientFormDialog({ open, onOpenChange, editing, form, onFormChange, saving, onSubmit, currentTerm,
  errors = {}, submitError = "", completing = false, notesLocked = false }) {
  // Quem já consentiu vê a cópia do texto que aceitou; os demais, o termo atual.
  const consentText = editing?.consent_terms ? editing.consent_terms_text : currentTerm;
  const consentDate = editing?.consent_terms && editing.consent_given_at
    ? new Date(editing.consent_given_at).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" })
    : "";
  // C3: paciente menor de 18 anos: quem responde ao termo é o responsável legal.
  // (Os avisos de CPF/telefone e os limites dos campos estão em PatientFormFields.)
  const minor = isMinorForm(form);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* UX (cadastro de paciente): mais largo no computador (duas colunas). */}
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="font-head">{completing ? "Completar cadastro" : editing ? "Editar paciente" : "Novo paciente"}</DialogTitle>
          {/* Item 13: antes só o CPF (e o texto dizia "AES-256", que não é o algoritmo usado). */}
          <DialogDescription>
            {/* Produto: campos com * são obrigatórios. C3: para menores, alguns ficam opcionais. */}
            Campos com * são obrigatórios. Nome, documentos, contatos e observações são criptografados antes de serem salvos.
          </DialogDescription>
        </DialogHeader>
        {/* Acessibilidade (varredura de UI): cada rótulo agora aponta para o seu campo
            (htmlFor/id). Antes 9 campos e as 2 listas de seleção não tinham nome para o
            leitor de tela, e clicar no rótulo não levava ao campo.
            UX (cadastro de paciente): antes eram 13 campos numa coluna só. Agora são
            seções (identificação, contato, informações clínicas, consentimento), em
            duas colunas no computador e uma no celular.
            autoComplete="off": o navegador sugeria os dados do PRÓPRIO profissional
            (nome, e-mail, endereço) nos campos do paciente. */}
        {/* noValidate (A4): os erros são mostrados pelo próprio formulário (ver validatePatientForm). */}
        <form onSubmit={onSubmit} className="space-y-6" autoComplete="off" noValidate>
          {/* Produto (cadastro pelo paciente): de onde vieram os dados já preenchidos. */}
          {completing && (
            <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900" data-testid="intake-completing-note">
              O tempo do link acabou antes de o paciente terminar. Os campos preenchidos vieram dele: confira
              com o paciente o que falta (inclusive a resposta sobre o termo) antes de salvar.
            </p>
          )}
          <FormErrorSummary errors={errors} submitError={submitError}
            fields={errorFields(form, { includeNotes: !notesLocked })} />
          <PatientFormFields form={form} errors={errors} onFormChange={onFormChange} />

          <FormSection title="Informações clínicas">
            <div className="sm:col-span-2">
              <Label htmlFor="patient-notes">Notas iniciais{notesLocked ? "" : " *"}</Label>
              <Textarea
                id="patient-notes"
                {...fieldA11y(errors, "initial_notes", "patient-notes", notesLocked ? "patient-notes-locked" : undefined)}
                required={!notesLocked}
                readOnly={notesLocked}
                maxLength={LIMITS.patientNotes}
                value={form.initial_notes}
                onChange={onFormChange("initial_notes")}
                className={`mt-1.5 ${invalidClass(errors, "initial_notes")}`}
                placeholder="Motivo da procura, encaminhamento, observações da triagem"
                data-testid="patient-notes-input"
                rows={3}
              />
              <FieldError id="patient-notes" message={errors.initial_notes} />
              {/* AS2: por que o campo não aceita texto (somente leitura). */}
              {notesLocked && (
                <p id="patient-notes-locked" className="mt-1 text-xs text-slate-600" data-testid="patient-notes-locked">
                  Conta em somente leitura: as notas iniciais são registro clínico e não podem ser alteradas sem
                  assinatura. O restante do cadastro pode ser corrigido normalmente.
                </p>
              )}
            </div>
          </FormSection>

          <FormSection title="Consentimento">
            <div className="space-y-3 rounded-md border border-slate-200 bg-slate-50 p-4 sm:col-span-2">
              <ConsentTermText text={consentText} consentDate={consentDate} />
              {/* C3: para menores, quem concorda é o responsável legal. */}
              <ConsentQuestion form={form} errors={errors} onFormChange={onFormChange}
                legend={minor ? "O responsável legal concorda com o termo? *" : "O paciente concorda com o termo? *"} />
            </div>
          </FormSection>

          {/* UX (celular): os botões ficam sempre à vista, presos ao pé do diálogo.
              Antes "Salvar" só aparecia depois de rolar o formulário inteiro. */}
          {/* -bottom-6: o diálogo tem 24 px de espaçamento interno; sem isso o rodapé
              parava 24 px acima da borda, com o formulário aparecendo por baixo dele. */}
          <div className="sticky -bottom-6 z-10 -mx-6 -mb-6 flex justify-end gap-2 border-t border-slate-200 bg-background px-6 py-3">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancelar</Button>
            <Button type="submit" disabled={saving} className="bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="save-patient-btn">
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Salvar"}
            </Button>
          </div>
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

function RowMenu({ p, busy, onEdit, onOpen, onDelete, onAnon, onArchive }) {
  // Varredura, item 9: enquanto uma ação deste paciente está em andamento, o
  // menu mostra "carregando" e as ações que gravam ficam desabilitadas.
  return (
    // Acessibilidade (varredura de UI):
    // - o botão ⋮ só tinha ícone: o leitor de tela anunciava apenas "botão".
    //   Agora diz de quem é o menu ("Ações de Mariana Souza");
    // - modal={false}: o menu aberto escondia o resto da página dos leitores de
    //   tela (aria-hidden) sem impedir o foco de chegar lá, um estado
    //   inconsistente. Um menu de ações não precisa bloquear a página.
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="h-8 w-8" data-testid={`patient-menu-${p.id}`}
          aria-label={`Ações de ${p.full_name}`} aria-busy={busy || undefined}>
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
        <DropdownMenuItem disabled={busy} onClick={() => onDelete(p)} className="text-red-600"><Trash2 className="mr-2 h-4 w-4" /> Excluir</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
