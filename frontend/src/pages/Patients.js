// ============================================================================
// IMPORTS
// ============================================================================
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  Plus, Search, MoreVertical, Pencil, Trash2, ShieldOff, FileText, Users, Loader2,
} from "lucide-react";

// API & Utils
import api, { formatApiErrorDetail } from "@/lib/api";

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
const fmtDate = (s) => (s ? new Date(s).toLocaleDateString("pt-BR") : "—");

const EMPTY_FORM = { 
  full_name: "", cpf: "", rg: "", birth_date: "", age: "", 
  education: "", profession: "", phone: "", email: "", 
  address: "", emergency_contact: "", initial_notes: "", consent_terms: false 
};

export default function Patients() {
  // ========================================================================
  // STATE MANAGEMENT
  // ========================================================================
  const navigate = useNavigate();
  const [patients, setPatients] = useState(null);
  const [query, setQuery] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [confirm, setConfirm] = useState(null); // {type, patient}

  // NOVOS ESTADOS PARA O WEBHOOK
  const [integrationOpen, setIntegrationOpen] = useState(false);
  const [copySuccess, setCopySuccess] = useState(false);
  const [userToken, setUserToken] = useState("");

  // ========================================================================
  // HANDLERS - DATA LOADING
  // ========================================================================
  const load = async () => {
    try {
      // 1. Busca os pacientes
      const resPatients = await api.get("/patients");
      setPatients(resPatients.data);
      
      // 2. Busca os dados do usuário logado (que agora traz o webhook_token)
      const resUser = await api.get("/auth/me");
      setUserToken(resUser.data.webhook_token); 
      
    } catch (err) {
      setPatients([]);
      console.error("Erro ao carregar dados:", err);
    }
  };

  useEffect(() => { load(); }, []);

  // ========================================================================
  // HANDLERS - DIALOG
  // ========================================================================
  const openNew = () => { 
    setEditing(null); 
    setForm(EMPTY_FORM); 
    setDialogOpen(true); 
  };

  const openEdit = (p) => { 
    setEditing(p); 
    setForm({ ...EMPTY_FORM, ...p }); 
    setDialogOpen(true); 
  };

  const upd = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  // ========================================================================
  // HANDLERS - FORM SUBMISSION
  // ========================================================================
  const save = async (e) => {
    e.preventDefault();
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
  const doAction = async () => {
    if (!confirm) return;
    try {
      if (confirm.type === "delete") {
        await api.delete(`/patients/${confirm.patient.id}`);
        toast.success("Paciente excluído definitivamente.");
      } else {
        await api.post(`/patients/${confirm.patient.id}/anonymize`);
        toast.success("Dados anonimizados (prontuários mantidos p/ guarda legal).");
      }
      setConfirm(null);
      load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    }
  };

  // ========================================================================
  // HANDLERS - INTEGRATION
  // ========================================================================
  const copyScript = () => {
    navigator.clipboard.writeText(generatedScript);
    setCopySuccess(true);
    setTimeout(() => setCopySuccess(false), 2000);
  };

  // ========================================================================
  // DERIVED STATE
  // ========================================================================
  const filtered = (patients || []).filter((p) =>
    p.full_name.toLowerCase().includes(query.toLowerCase()));

  const generatedScript = `
const WEBHOOK_URL = "${process.env.WEBHOOK_URL}";
const SECURITY_TOKEN = "${userToken}";

function onFormSubmit(e) {
  const response = e.response;
  const itemResponses = response.getItemResponses();
  
  let patientData = {
    full_name: "", 
    cpf: "", 
    rg: "", 
    birth_date: "", 
    age: "",
    education: "", 
    profession: "", 
    phone: "", 
    email: "", 
    address: "", 
    emergency_contact: "", 
    initial_notes: "Cadastrado via Google Forms.", 
    consent_terms: false
  };
  
  itemResponses.forEach(itemResponse => {
    const question = itemResponse.getItem().getTitle();
    const answer = itemResponse.getResponse();
    
    if (question.includes("Nome Completo")) patientData.full_name = answer;
    else if (question.includes("CPF")) patientData.cpf = answer;
    else if (question.includes("RG")) patientData.rg = answer;
    else if (question.includes("Data de Nascimento")) patientData.birth_date = answer;
    else if (question.includes("Idade")) patientData.age = answer;
    else if (question.includes("Escolaridade")) patientData.education = answer;
    else if (question.includes("Profissão")) patientData.profession = answer;
    else if (question.includes("Contato Telefônico")) patientData.phone = answer;
    else if (question.includes("E-mail")) patientData.email = answer;
    else if (question.includes("Endereço Completo")) patientData.address = answer;
    else if (question.includes("risco iminente")) patientData.emergency_contact = answer;
    else if (question.includes("TERMO DE CONSENTIMENTO")) {
      patientData.consent_terms = (answer === "Estou Ciente e concordo.");
    }
    else if (question.includes("Estado Civil")) {
      patientData.initial_notes += " | Estado Civil: " + answer;
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
      <PatientHeader onNew={openNew} onIntegration={() => setIntegrationOpen(true)} />
      <PatientSearch query={query} onQueryChange={setQuery} />
      <PatientContent patients={patients} filtered={filtered} navigate={navigate}
        onEdit={openEdit} onDelete={(p) => setConfirm({ type: "delete", patient: p })}
        onAnon={(p) => setConfirm({ type: "anon", patient: p })} />
      
      <PatientFormDialog open={dialogOpen} onOpenChange={setDialogOpen} editing={editing}
        form={form} onFormChange={upd} saving={saving} onSubmit={save} />
      
      <PatientConfirmDialog confirm={confirm} onConfirm={doAction} onOpenChange={(o) => !o && setConfirm(null)} />
      
      <IntegrationDialog open={integrationOpen} onOpenChange={setIntegrationOpen} 
        userToken={userToken} generatedScript={generatedScript} copySuccess={copySuccess} 
        onCopyScript={copyScript} />
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
        placeholder="Buscar por nome..." 
        value={query} 
        onChange={(e) => onQueryChange(e.target.value)}
        className="pl-9" 
        data-testid="patient-search-input" 
      />
    </div>
  );
}

function PatientContent({ patients, filtered, navigate, onEdit, onDelete, onAnon }) {
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
        <p className="mt-4 font-head text-lg font-bold text-[#0F172A]">Nenhum paciente encontrado</p>
        <p className="mt-1 text-sm text-slate-500">Cadastre seu primeiro paciente para começar.</p>
      </Card>
    );
  }

  return (
    <>
      <PatientTable filtered={filtered} navigate={navigate} onEdit={onEdit}
        onDelete={onDelete} onAnon={onAnon} />
      <PatientMobileCards filtered={filtered} navigate={navigate} onEdit={onEdit}
        onDelete={onDelete} onAnon={onAnon} />
    </>
  );
}

function PatientTable({ filtered, navigate, onEdit, onDelete, onAnon }) {
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
                <RowMenu p={p} onEdit={onEdit} onOpen={() => navigate(`/pacientes/${p.id}`)}
                  onDelete={onDelete} onAnon={onAnon} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}

function PatientMobileCards({ filtered, navigate, onEdit, onDelete, onAnon }) {
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
            <RowMenu p={p} onEdit={onEdit} onOpen={() => navigate(`/pacientes/${p.id}`)}
              onDelete={onDelete} onAnon={onAnon} />
          </div>
          <p className="mt-2 text-xs text-slate-500">Última consulta: {fmtDate(p.last_consultation_date)}</p>
        </Card>
      ))}
    </div>
  );
}

function PatientFormDialog({ open, onOpenChange, editing, form, onFormChange, saving, onSubmit }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-head">{editing ? "Editar paciente" : "Novo paciente"}</DialogTitle>
          <DialogDescription>O CPF é criptografado (AES-256) antes de ser salvo.</DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <Label>Nome completo *</Label>
            <Input 
              required 
              value={form.full_name} 
              onChange={onFormChange("full_name")} 
              className="mt-1.5" 
              data-testid="patient-name-input" 
            />
          </div>

          <div>
            <Label>CPF</Label>
            <Input 
              value={form.cpf} 
              onChange={onFormChange("cpf")} 
              className="mt-1.5" 
              data-testid="patient-cpf-input"
              placeholder="000.000.000-00"
            />
          </div>

          <div>
            <Label>RG</Label>
            <Input 
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
              <Label>Profissão</Label>
              <Input 
                value={form.profession} 
                onChange={onFormChange("profession")} 
                className="mt-1.5"
                data-testid="patient-profession-input"
              />
            </div>
          </div>

          <div>
            <Label>Telefone</Label>
            <Input 
              value={form.phone} 
              onChange={onFormChange("phone")} 
              className="mt-1.5"
              data-testid="patient-phone-input"
              placeholder="(XX) XXXX-XXXX"
            />
          </div>

          <div>
            <Label>Email</Label>
            <Input 
              type="email" 
              value={form.email} 
              onChange={onFormChange("email")} 
              className="mt-1.5"
              data-testid="patient-email-input"
            />
          </div>

          <div>
            <Label>Endereço</Label>
            <Input 
              value={form.address} 
              onChange={onFormChange("address")} 
              className="mt-1.5"
              data-testid="patient-address-input"
            />
          </div>

          <div>
            <Label>Contato de emergência</Label>
            <Input 
              value={form.emergency_contact} 
              onChange={onFormChange("emergency_contact")} 
              className="mt-1.5"
              data-testid="patient-emergency-input"
            />
          </div>

          <div>
            <Label>Notas iniciais</Label>
            <Textarea 
              value={form.initial_notes} 
              onChange={onFormChange("initial_notes")} 
              className="mt-1.5"
              data-testid="patient-notes-input"
              rows={3}
            />
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
              ? "Esta ação é permanente e remove o paciente e seus prontuários."
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

function IntegrationDialog({ open, onOpenChange, userToken, generatedScript, copySuccess, onCopyScript }) {
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
          <div className="space-y-2">
            <p><strong>Passo 1:</strong> No seu Google Forms, clique nos 3 pontos verticais (canto superior direito) e acesse <strong>Apps Script</strong>.</p>
            <p><strong>Passo 2:</strong> Apague o código padrão e cole o seu script de integração pessoal gerado abaixo:</p>
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
            <p><strong>Passo 5:</strong> Clique em "Adicionar Acionador" e configure para rodar a função <code>onFormSubmit</code> no evento <strong>"Ao enviar o formulário" <strong> e na Configurações de notificação de falha </strong> Receber Notificaçções Imediatamente</strong>.</p>
            <p className="mt-2 text-xs text-slate-500">* Atenção: Este código contém seu Token de Segurança Pessoal. Não o compartilhe publicamente.</p>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function RowMenu({ p, onEdit, onOpen, onDelete, onAnon }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="h-8 w-8" data-testid={`patient-menu-${p.id}`}>
          <MoreVertical className="h-4 w-4 text-slate-500" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={onOpen}><FileText className="mr-2 h-4 w-4" /> Prontuários</DropdownMenuItem>
        <DropdownMenuItem onClick={() => onEdit(p)}><Pencil className="mr-2 h-4 w-4" /> Editar</DropdownMenuItem>
        <DropdownMenuItem onClick={onAnon}><ShieldOff className="mr-2 h-4 w-4" /> Anonimizar</DropdownMenuItem>
        <DropdownMenuItem onClick={onDelete} className="text-[#EF4444]"><Trash2 className="mr-2 h-4 w-4" /> Excluir</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
