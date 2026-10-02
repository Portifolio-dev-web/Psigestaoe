import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { GoogleLogin } from "@react-oauth/google";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { clearUserDrafts, forgetDraftKey } from "@/lib/drafts";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { formatDateTime } from "@/lib/datetime";
import { downloadFromApi } from "@/lib/download";
import { countLabel } from "@/lib/utils";
import { PASSWORD_RULE, passwordProblem } from "@/lib/validation";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import PasswordInput from "@/components/auth/PasswordInput";
import TwoFactorCard from "@/components/settings/TwoFactorCard";
import ConnectedDevicesCard from "@/components/settings/ConnectedDevicesCard";
import CloseAccountCard from "@/components/settings/CloseAccountCard";
import { toast } from "sonner";
import {
  Shield, Lock, FileCheck, History, KeyRound, ScrollText, Loader2, CheckCircle2, UserRound,
  FileSignature, Archive, TimerReset, Mail, Download, ShieldCheck, UserX,
} from "lucide-react";

// Rótulos das ações gravadas pelo backend em log_audit(). Ações novas da etapa
// de conformidade: leituras (item 14), arquivamento (12), consentimento,
// aceite da política (16) e token da integração (15).
const ACTION_LABEL = {
  criar: "Criação", editar: "Edição", visualizar: "Visualização",
  excluir: "Exclusão", anonimizar: "Anonimização", exportar: "Exportação",
  arquivar: "Arquivamento", desarquivar: "Reativação",
  consentir: "Consentimento", revogar: "Revogação",
  aceitar: "Aceite", gerar_token: "Novo token",
  // Produto: bloqueio por inatividade.
  bloquear: "Bloqueio", desbloquear: "Desbloqueio", desbloquear_falha: "Senha incorreta", encerrar: "Encerramento",
  // Segurança (varredura, Fase 2): vínculo com o Google e troca de senha.
  vincular: "Vínculo Google", alterar_senha: "Troca de senha", alterar_senha_falha: "Senha incorreta",
  // Segurança alta (auditoria de login): cada acesso, tentativa recusada e saída.
  entrar: "Login", entrar_falha: "Login recusado", sair: "Logout",
  // Segurança média + backlog: pedido de nova senha por e-mail e verificação em duas etapas.
  solicitar_senha: "Pedido de nova senha", mfa_ativar: "Duas etapas ativada",
  mfa_desativar: "Duas etapas desativada", mfa_codigos: "Novos códigos",
};
const ENTITY_LABEL = {
  paciente: "Paciente", prontuario: "Prontuário", agenda: "Agenda", usuario: "Usuário",
  termos: "Política", termo: "Termo", integracao: "Integração", sessao: "Sessão",
  auditoria: "Auditoria",  // exportação da própria trilha (complementares)
};

// Correção (varredura de UI, defeito 6): alguns detalhes da trilha guardam a
// data em ISO ("Sessão 2026-09-24T13:00:00+00:00"), e a tela a mostrava crua,
// em UTC. A trilha é só de inclusão (registros antigos não podem ser
// alterados), então a correção é na exibição: toda data ISO do detalhe vira
// data e hora locais ("24/09/2026, 10:00"), inclusive nos registros antigos.
const ISO_INSTANT = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})/g;
const formatAuditDetail = (detail) => String(detail || "").replace(ISO_INSTANT, (iso) => formatDateTime(iso));

const fmtDateTime = (s) => (s
  ? new Date(s).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" })
  : "—");

// Item 6 da auditoria: o termo de consentimento dos pacientes passou a usar o
// nome e o CRP de quem está logado (antes era o nome de uma psicóloga fixo no
// backend). Este formulário grava esses dados via PUT /api/auth/profile.
function ProfessionalProfile() {
  const { user, setUser } = useAuth();
  const [form, setForm] = useState({ name: user?.name || "", crp: user?.crp || "" });
  const [saving, setSaving] = useState(false);

  const save = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      const { data } = await api.put("/auth/profile", form);
      setUser(data);
      toast.success("Dados profissionais atualizados.");
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="border-slate-200 p-6 shadow-sm">
      <div className="flex items-center gap-2">
        <UserRound className="h-4 w-4 text-[#1E3A8A]" />
        <h2 className="font-head text-lg font-bold text-[#0F172A]">Dados profissionais</h2>
      </div>
      <p className="mt-1 text-sm text-slate-500">
        Nome e CRP aparecem no Termo de Consentimento exibido e exportado para os seus pacientes.
      </p>
      {!user?.crp && (
        <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800" role="status">
          Seu CRP ainda não foi informado. Os termos dos pacientes mostram "CRP não informado".
        </p>
      )}
      <form onSubmit={save} className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-[1fr_180px_auto] sm:items-end">
        <div>
          <Label htmlFor="profile-name" className="text-slate-700">Nome completo</Label>
          <Input id="profile-name" required maxLength={120} className="mt-1.5" value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })} data-testid="profile-name-input" />
        </div>
        <div>
          <Label htmlFor="profile-crp" className="text-slate-700">CRP</Label>
          <Input id="profile-crp" placeholder="06/123456" pattern="\d{2}/\d{1,6}"
            title="Formato: região/número, ex.: 06/123456" className="mt-1.5" value={form.crp}
            onChange={(e) => setForm({ ...form, crp: e.target.value })} data-testid="profile-crp-input" />
        </div>
        <Button type="submit" disabled={saving} className="bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="profile-save-btn">
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Salvar"}
        </Button>
      </form>
    </Card>
  );
}

// Termo de consentimento POR PROFISSIONAL.
// Cada profissional pode adaptar o corpo do termo (ex.: atendimento online,
// faltas). Com a identificação do profissional ligada (ver server.py,
// SHOW_PROFESSIONAL_IDENTIFICATION), o sistema acrescenta nome e CRP no topo.
// Mudanças aqui valem só para NOVOS consentimentos: cada paciente guarda a
// cópia exata do texto que aceitou (backend: apply_consent).
function ConsentTermEditor() {
  const { user } = useAuth();
  const showIdentification = Boolean(user?.features?.professional_identification);
  const [term, setTerm] = useState(null);
  const [body, setBody] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.get("/consent-term")
      .then(({ data }) => { setTerm(data); setBody(data.body); })
      .catch(() => setTerm(false));
  }, []);

  const save = async (nextBody) => {
    setSaving(true);
    try {
      const { data } = await api.put("/consent-term", { body: nextBody });
      setTerm(data);
      setBody(data.body);
      toast.success(data.is_default ? "Modelo padrão restaurado." : "Termo de consentimento atualizado.");
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    } finally {
      setSaving(false);
    }
  };

  if (term === false) return null;

  return (
    <Card className="border-slate-200 p-6 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <FileSignature className="h-4 w-4 text-[#1E3A8A]" />
        <h2 className="font-head text-lg font-bold text-[#0F172A]">Termo de consentimento</h2>
        {term && (
          <Badge variant="secondary" className="text-[11px]">{term.is_default ? "Modelo padrão" : "Personalizado"}</Badge>
        )}
      </div>
      <p className="mt-1 text-sm text-slate-500">
        {showIdentification && "O cabeçalho com seu nome e CRP é incluído automaticamente. "}
        Alterações valem para novos consentimentos; os já registrados guardam a cópia do texto que o paciente aceitou.
      </p>
      {!term ? (
        <div className="flex justify-center py-6"><Loader2 className="h-5 w-5 animate-spin text-[#1E3A8A]" /></div>
      ) : (
        <>
          <Label htmlFor="consent-body" className="sr-only">Corpo do termo de consentimento</Label>
          <Textarea id="consent-body" value={body} onChange={(e) => setBody(e.target.value)} rows={10} maxLength={10000}
            className="psi-scroll mt-4 text-sm leading-relaxed" data-testid="consent-term-input" />
          <details className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-3">
            <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-slate-500">
              Ver como o paciente vê
            </summary>
            <p className="mt-2 whitespace-pre-line text-xs leading-relaxed text-slate-600">{term.text}</p>
          </details>
          <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" disabled={saving || term.is_default} onClick={() => save("")}>
              Restaurar modelo padrão
            </Button>
            <Button type="button" disabled={saving || body === term.body} onClick={() => save(body)}
              className="bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="consent-term-save">
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Salvar termo"}
            </Button>
          </div>
        </>
      )}
    </Card>
  );
}

// PRODUTO: segurança da sessão neste e em outros aparelhos.
// - Bloqueio por inatividade: tempo até a tela travar e pedir a senha. Vale
//   no navegador e no servidor (a sessão fica bloqueada de verdade).
// - Rascunhos: as evoluções não salvas ficam criptografadas em cada aparelho.
//   Se um aparelho for perdido, trocar a chave torna os rascunhos dele
//   ilegíveis, mesmo para quem conseguir entrar na conta depois.
const LOCK_OPTIONS = [5, 10, 15, 30];

function SessionSecurity() {
  const { user, setUser } = useAuth();
  const [saving, setSaving] = useState(false);
  // Segurança (varredura, item 9): o botão não travava durante o envio.
  const inFlight = useSingleFlight();

  const changeLockMinutes = async (value) => {
    setSaving(true);
    try {
      const { data } = await api.put("/auth/lock-settings", { minutes: Number(value) });
      setUser((current) => ({ ...current, ...data }));
      toast.success(`A tela será bloqueada após ${value} minutos sem uso.`);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    } finally {
      setSaving(false);
    }
  };

  const invalidateDrafts = () => inFlight.run("drafts", async () => {
    const confirmed = window.confirm(
      "Isto apaga a chave dos rascunhos: evoluções não salvas em QUALQUER aparelho (inclusive este) deixam de poder ser recuperadas. Continuar?",
    );
    if (!confirmed) return;
    try {
      await api.delete("/auth/draft-key");
      forgetDraftKey();
      clearUserDrafts(user.user_id);
      toast.success("Rascunhos invalidados em todos os dispositivos.");
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    }
  });

  return (
    <Card className="border-slate-200 p-6 shadow-sm">
      <div className="flex items-center gap-2">
        <TimerReset className="h-4 w-4 text-[#1E3A8A]" />
        <h2 className="font-head text-lg font-bold text-[#0F172A]">Sessão e rascunhos</h2>
      </div>
      <div className="mt-4 grid grid-cols-1 gap-6 md:grid-cols-2">
        <div>
          <Label htmlFor="lock-minutes" className="text-slate-700">Bloquear a tela após</Label>
          <Select value={String(user?.lock_minutes || 10)} onValueChange={changeLockMinutes} disabled={saving}>
            <SelectTrigger id="lock-minutes" className="mt-1.5 w-full sm:w-56" data-testid="lock-minutes-select">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LOCK_OPTIONS.map((minutes) => (
                <SelectItem key={minutes} value={String(minutes)}>{minutes} minutos sem uso</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="mt-2 text-xs leading-relaxed text-slate-500">
            Depois desse tempo a tela trava e pede a senha (ou o Google). O que estava aberto continua lá após
            desbloquear. Use também o botão "Bloquear" no topo ao sair da sala.
          </p>
        </div>
        <div>
          {/* Terminologia (UX): o rascunho é de uma evolução (antes "anotação"). */}
          <p className="text-sm font-medium text-slate-700">Rascunhos de evoluções</p>
          <p className="mt-1 text-xs leading-relaxed text-slate-500">
            Enquanto você escreve, a evolução é guardada criptografada neste aparelho, para não se perder se a
            sessão expirar ou a internet cair. Rascunhos somem ao salvar e vencem em 7 dias.
          </p>
          <Button variant="outline" size="sm" onClick={invalidateDrafts} disabled={inFlight.isBusy("drafts")}
            className="mt-3 border-red-200 text-red-600 hover:bg-red-50" data-testid="invalidate-drafts-btn">
            Invalidar rascunhos em todos os aparelhos
          </Button>
        </div>
      </div>
    </Card>
  );
}

// SEGURANÇA (varredura, item 5): troca de senha.
// Antes não havia como trocar a senha: se ela vazasse, o profissional não
// tinha o que fazer.
// - Trocar a senha encerra as sessões de TODOS os outros aparelhos (este continua).
// - Quem só entra pelo Google define a primeira senha confirmando com o Google:
//   assim, quem encontrar o computador logado não consegue tomar a conta.
// Segurança média: "Outros aparelhos" virou o cartão "Aparelhos conectados"
// (components/settings/ConnectedDevicesCard.js), com cada sessão listada; e
// quem esqueceu a senha atual recebe aqui mesmo o link de redefinição no e-mail.
// A regra de senha é a de lib/validation.js (antes havia uma cópia aqui).

const EMPTY_PASSWORD_FORM = { current: "", next: "", confirm: "" };

function AccountSecurity() {
  const { user, setUser } = useAuth();
  const hasPassword = Boolean(user?.has_password);
  const [form, setForm] = useState(EMPTY_PASSWORD_FORM);
  const [saving, setSaving] = useState(false);
  const inFlight = useSingleFlight();

  const problem = form.next ? passwordProblem(form.next) : "";
  const mismatch = form.confirm && form.confirm !== form.next ? "As senhas não conferem." : "";
  const ready = Boolean(form.next) && !problem && form.confirm === form.next && (!hasPassword || Boolean(form.current));

  // `proof`: a senha atual (quem tem senha) ou o token do Google (quem não tem).
  const submit = async (proof) => {
    setSaving(true);
    try {
      const { data } = await api.post("/auth/password", { ...proof, new_password: form.next });
      setUser((current) => ({ ...current, ...data.user }));
      setForm(EMPTY_PASSWORD_FORM);
      const ended = data.ended_sessions
        // Varredura de UI, defeito 9: plural de verdade, sem "sessão(ões)".
        ? ` ${countLabel(data.ended_sessions, "sessão em outro aparelho foi encerrada", "sessões em outros aparelhos foram encerradas")}.`
        : "";
      toast.success(`${hasPassword ? "Senha alterada" : "Senha definida"}.${ended}`);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    } finally {
      setSaving(false);
    }
  };

  // Segurança média: esqueceu a senha atual estando logado -> o mesmo link
  // de "Esqueci minha senha", enviado ao e-mail da conta.
  const sendResetLink = () => inFlight.run("reset-link", async () => {
    try {
      await api.post("/auth/password/forgot", { email: user.email });
      toast.success(`Enviamos um link para ${user.email}. Ele vale por 1 hora.`);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    }
  });

  const setField = (field) => (e) => setForm((current) => ({ ...current, [field]: e.target.value }));
  const hint = mismatch || problem;

  return (
    <Card className="border-slate-200 p-6 shadow-sm">
      <div className="flex items-center gap-2">
        <KeyRound className="h-4 w-4 text-[#1E3A8A]" />
        <h2 className="font-head text-lg font-bold text-[#0F172A]">Senha e acesso</h2>
      </div>
      <div className="mt-4 grid grid-cols-1 gap-6 md:grid-cols-2">
        <form className="space-y-3" data-testid="password-form"
          onSubmit={(e) => { e.preventDefault(); if (ready && hasPassword) submit({ current_password: form.current }); }}>
          {/* Campo oculto: ajuda o gerenciador de senhas a saber de qual conta é a senha nova. */}
          <input type="text" name="username" autoComplete="username" value={user?.email || ""} readOnly
            className="sr-only" tabIndex={-1} aria-hidden="true" />
          {hasPassword ? (
            <div>
              <Label htmlFor="current-password" className="text-slate-700">Senha atual</Label>
              <div className="mt-1.5">
                <PasswordInput id="current-password" autoComplete="current-password"
                  value={form.current} onChange={setField("current")} data-testid="current-password-input" />
              </div>
            </div>
          ) : (
            <p className="rounded-md bg-slate-50 px-3 py-2 text-xs leading-relaxed text-slate-600">
              Você entra com o Google. Defina uma senha para também entrar com e-mail e senha; para confirmar que
              é você, o Google será usado.
            </p>
          )}
          <div>
            <Label htmlFor="new-password" className="text-slate-700">{hasPassword ? "Nova senha" : "Senha"}</Label>
            <div className="mt-1.5">
              <PasswordInput id="new-password" autoComplete="new-password" maxLength={72}
                value={form.next} onChange={setField("next")} aria-invalid={Boolean(problem)}
                aria-describedby="password-rules" data-testid="new-password-input" />
            </div>
          </div>
          <div>
            <Label htmlFor="confirm-password" className="text-slate-700">Repita a senha</Label>
            <div className="mt-1.5">
              <PasswordInput id="confirm-password" autoComplete="new-password" maxLength={72}
                value={form.confirm} onChange={setField("confirm")} aria-invalid={Boolean(mismatch)}
                aria-describedby="password-rules" data-testid="confirm-password-input" />
            </div>
          </div>
          <p id="password-rules" role={hint ? "alert" : undefined}
            className={`text-xs ${hint ? "text-red-600" : "text-slate-500"}`}>
            {hint || `${PASSWORD_RULE}. Os outros aparelhos serão desconectados.`}
          </p>
          {hasPassword ? (
            <Button type="submit" disabled={!ready || saving} className="bg-[#1E3A8A] hover:bg-[#0F2C59]"
              data-testid="change-password-btn">
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Alterar senha"}
            </Button>
          ) : ready && !saving ? (
            <GoogleLogin
              onSuccess={(response) => submit({ credential: response.credential })}
              onError={() => toast.error("Não foi possível confirmar com o Google. Tente novamente.")}
              shape="rectangular" text="continue_with"
            />
          ) : saving ? (
            <Loader2 className="h-4 w-4 animate-spin text-[#1E3A8A]" />
          ) : null}
        </form>

        <div>
          <p className="text-sm font-medium text-slate-700">{hasPassword ? "Esqueceu a senha atual?" : "Prefere criar a senha pelo e-mail?"}</p>
          <p className="mt-1 text-xs leading-relaxed text-slate-500">
            Enviaremos um link para <b className="text-slate-700">{user?.email}</b>. Ele vale por 1 hora, e usar o
            link encerra as sessões de todos os aparelhos, inclusive esta.
          </p>
          <Button variant="outline" size="sm" onClick={sendResetLink} disabled={inFlight.isBusy("reset-link")}
            className="mt-3 gap-2 border-slate-300" data-testid="send-reset-link-btn">
            {inFlight.isBusy("reset-link") ? <Loader2 className="h-4 w-4 animate-spin" /> : <Mail className="h-4 w-4" aria-hidden="true" />}
            Enviar link para o meu e-mail
          </Button>
        </div>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Trilha de auditoria (aba "Auditoria").
// Só é buscada quando a aba é aberta (antes vinha junto com a página inteira,
// mesmo para quem só queria trocar a senha), e de novo a cada abertura, para
// mostrar os registros mais recentes.
// ---------------------------------------------------------------------------
// Complementares (auditoria): filtros por período, ação e tipo, e exportação.
// Antes a trilha só podia ser rolada do mais recente para trás: achar "quem
// acessou este prontuário em março" exigia carregar página por página. As
// datas são do dia LOCAL ("Até 10/03" inclui o dia 10 inteiro); o servidor
// recebe o intervalo em ISO com fuso, fim exclusivo.
const EMPTY_AUDIT_FILTERS = { from: "", to: "", action: "", entity: "" };
// Duas ações têm o mesmo rótulo na lista ("Senha incorreta"); no filtro, diferenciadas.
const ACTION_FILTER_LABEL = {
  ...ACTION_LABEL,
  desbloquear_falha: "Senha incorreta (desbloqueio)",
  alterar_senha_falha: "Senha incorreta (troca de senha)",
};
const FILTER_SELECT_CLASS = "mt-1 flex h-9 w-full rounded-md border border-slate-200 bg-white px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]";

function auditParams(filters) {
  const params = new URLSearchParams();
  if (filters.from) params.set("start", new Date(`${filters.from}T00:00`).toISOString());
  if (filters.to) {
    const end = new Date(`${filters.to}T00:00`);
    end.setDate(end.getDate() + 1);
    params.set("end", end.toISOString());
  }
  if (filters.action) params.set("action", filters.action);
  if (filters.entity) params.set("entity", filters.entity);
  return params;
}

function AuditTrail() {
  const [logs, setLogs] = useState(null);
  // ESCALA: a trilha é paginada. Antes só os 100 últimos registros apareciam,
  // sem como ver os anteriores (e a trilha só cresce, pois inclui leituras).
  const [logsCursor, setLogsCursor] = useState(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [filters, setFilters] = useState(EMPTY_AUDIT_FILTERS);
  const inFlight = useSingleFlight();
  const filtered = Object.values(filters).some(Boolean);

  useEffect(() => {
    setLogs(null);
    api.get(`/audit?${auditParams(filters)}`)
      .then(({ data }) => { setLogs(data.items); setLogsCursor(data.next_cursor); })
      .catch(() => setLogs([]));
  }, [filters]);

  const setFilter = (key) => (e) => setFilters((current) => ({ ...current, [key]: e.target.value }));

  const exportCsv = () => inFlight.run("export", async () => {
    try {
      await downloadFromApi(`/audit/export?${auditParams(filters)}`, "trilha_auditoria.csv");
      toast.success("Trilha exportada em CSV.");
    } catch (err) {
      toast.error(err.message);
    }
  });

  const loadMoreLogs = async () => {
    if (!logsCursor) return;
    setLoadingMore(true);
    try {
      const params = auditParams(filters);
      params.set("cursor", logsCursor);
      const { data } = await api.get(`/audit?${params}`);
      setLogs((current) => [...current, ...data.items]);
      setLogsCursor(data.next_cursor);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <Card className="border-slate-200 p-6 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <ScrollText className="h-4 w-4 text-[#1E3A8A]" />
        <h2 className="font-head text-lg font-bold text-[#0F172A]">Trilha de auditoria</h2>
        {/* Item 14: agora é de fato somente inclusão (gatilho no banco) e inclui leituras e IP. */}
        {/* Acessibilidade (varredura de UI): textos da trilha em slate-400 (2,6:1) -> slate-500 (4,8:1). */}
        <span className="text-xs text-slate-500">(somente inclusão · inclui visualizações e IP)</span>
        <Button type="button" variant="outline" size="sm" onClick={exportCsv} disabled={inFlight.isBusy("export")}
          className="ml-auto gap-1.5 border-slate-300" data-testid="audit-export">
          {inFlight.isBusy("export") ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" aria-hidden="true" />}
          Exportar CSV
        </Button>
      </div>
      {/* Complementares: filtros (os mesmos valem para a exportação). */}
      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-[repeat(4,minmax(0,1fr))_auto] md:items-end"
        role="group" aria-label="Filtros da trilha" data-testid="audit-filters">
        <div>
          <Label htmlFor="audit-from" className="text-xs text-slate-600">De</Label>
          <Input id="audit-from" type="date" value={filters.from} max={filters.to || undefined}
            onChange={setFilter("from")} className="mt-1 h-9" />
        </div>
        <div>
          <Label htmlFor="audit-to" className="text-xs text-slate-600">Até</Label>
          <Input id="audit-to" type="date" value={filters.to} min={filters.from || undefined}
            onChange={setFilter("to")} className="mt-1 h-9" />
        </div>
        <div>
          <Label htmlFor="audit-action" className="text-xs text-slate-600">Ação</Label>
          <select id="audit-action" value={filters.action} onChange={setFilter("action")} className={FILTER_SELECT_CLASS}>
            <option value="">Todas</option>
            {Object.entries(ACTION_FILTER_LABEL).sort((a, b) => a[1].localeCompare(b[1], "pt-BR")).map(([code, label]) => (
              <option key={code} value={code}>{label}</option>
            ))}
          </select>
        </div>
        <div>
          <Label htmlFor="audit-entity" className="text-xs text-slate-600">Tipo</Label>
          <select id="audit-entity" value={filters.entity} onChange={setFilter("entity")} className={FILTER_SELECT_CLASS}>
            <option value="">Todos</option>
            {Object.entries(ENTITY_LABEL).sort((a, b) => a[1].localeCompare(b[1], "pt-BR")).map(([code, label]) => (
              <option key={code} value={code}>{label}</option>
            ))}
          </select>
        </div>
        <Button type="button" variant="ghost" size="sm" disabled={!filtered} onClick={() => setFilters(EMPTY_AUDIT_FILTERS)}
          className="col-span-2 text-[#1E3A8A] md:col-span-1">
          Limpar filtros
        </Button>
      </div>
      <Separator className="my-4" />
      {logs === null ? (
        <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin text-[#1E3A8A]" /></div>
      ) : logs.length === 0 ? (
        <p className="py-8 text-center text-sm text-slate-500">
          {filtered ? "Nenhum registro com estes filtros." : "Nenhum registro de auditoria ainda."}
        </p>
      ) : (
        // Acessibilidade (varredura de UI): lista com rolagem alcançável pelo teclado.
        // Com a aba só para ela, a lista pode ser mais alta (antes 420px).
        <div tabIndex={0} role="region" aria-label="Registros da trilha de auditoria"
          className="max-h-[560px] space-y-1 overflow-y-auto psi-scroll focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]"
          data-testid="audit-log">
          {logs.map((l) => (
            // Celular: o detalhe desce para uma segunda linha (antes a hora era cortada na borda da tela).
            <div key={l.log_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md px-2 py-2 text-sm hover:bg-slate-50">
              {/* sm:w-36: no computador, largura fixa (coluna alinhada) que cabe "Duas etapas
                  desativada" numa linha (antes w-28); no celular, só a largura do texto. */}
              <Badge variant="secondary" className="shrink-0 justify-center text-[11px] sm:w-36">{ACTION_LABEL[l.action] || l.action}</Badge>
              <span className="text-slate-600">{ENTITY_LABEL[l.entity_type] || l.entity_type}</span>
              <span className="order-last w-full truncate text-slate-500 sm:order-none sm:w-auto sm:min-w-0 sm:flex-1">{formatAuditDetail(l.detail)}</span>
              <span className="ml-auto flex shrink-0 items-center gap-3">
                <span className="hidden font-mono text-[11px] text-slate-500 md:inline" title="Endereço IP da ação">
                  {l.ip_address || "—"}
                </span>
                <span className="text-xs text-slate-500">
                  {new Date(l.timestamp).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}
                </span>
              </span>
            </div>
          ))}
          {logsCursor && (
            <div className="flex justify-center pt-2">
              <Button variant="outline" size="sm" onClick={loadMoreLogs} disabled={loadingMore}
                className="gap-2 border-slate-300" data-testid="audit-load-more">
                {loadingMore && <Loader2 className="h-4 w-4 animate-spin" />} Carregar registros anteriores
              </Button>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

// Descrições corrigidas para refletir o que o sistema FAZ. A versão anterior
// dizia "AES-256-GCM" (o sistema usa Fernet: AES-128-CBC + HMAC-SHA256) e
// chamava de "imutável" uma trilha que podia ser apagada em cascata.
const PRIVACY_FEATURES = [
  { icon: Lock, title: "Criptografia em repouso", desc: "Nome, CPF, RG, contatos, endereço, observações, prontuários e agenda são cifrados (Fernet: AES-128 + HMAC-SHA256) antes de gravar no banco." },
  { icon: Shield, title: "Criptografia em trânsito", desc: "Toda comunicação ocorre via HTTPS/TLS." },
  // Varredura de UI (defeito 4): "Imutabilidade" sugeria que nada podia ser editado.
  // UX: agora as versões podem ser vistas na ficha ("Ver histórico de versões").
  { icon: History, title: "Histórico de versões", desc: "Evoluções podem ser corrigidas, mas nada se perde: cada edição guarda a versão anterior, que pode ser consultada na ficha do paciente." },
  { icon: KeyRound, title: "Multi-tenancy seguro", desc: "Cada profissional acessa apenas os próprios dados, com sessão isolada." },
  { icon: FileCheck, title: "Direitos do titular (LGPD)", desc: "Exportação (PDF/JSON), correção, anonimização e registro ou revogação do consentimento, com data." },
  { icon: Archive, title: "Guarda legal (CFP)", desc: "Prontuários não podem ser excluídos antes de 5 anos do último registro. Até lá, o paciente pode ser arquivado." },
  // Segurança média + backlog: recursos novos, descritos como de fato funcionam.
  { icon: ShieldCheck, title: "Verificação em duas etapas", desc: "Opcional: além da senha, o login pede um código do aplicativo autenticador do celular. Ative em Minha conta." },
  { icon: UserX, title: "Encerramento de conta", desc: "Exportação completa antes de sair; o que não está sob guarda é apagado na hora e o restante, no fim do prazo legal." },
];

// ---------------------------------------------------------------------------
// UX (Configurações em abas): antes era uma página única e longa, chamada
// "Privacidade & LGPD", onde trocar a senha exigia rolar por termo,
// criptografia e auditoria. Agora são quatro abas:
//   Minha conta   - senha, duas etapas, aparelhos, bloqueio, rascunhos,
//                   encerrar a conta (e nome/CRP);
//   Termo         - termo de consentimento dos pacientes;
//   Privacidade   - conformidade, política e o que o sistema protege;
//   Auditoria     - trilha de auditoria (carregada só ao abrir a aba).
// A aba fica na URL (?aba=termo), para abrir direto por um link.
// "Minha conta" e "Termo" continuam montadas quando escondidas: um termo em
// edição ou uma senha digitada não se perdem ao trocar de aba.
// ---------------------------------------------------------------------------
const TABS = [
  { id: "conta", label: "Minha conta", icon: UserRound },
  { id: "termo", label: "Termo de consentimento", icon: FileSignature },
  { id: "privacidade", label: "Privacidade", icon: Shield },
  { id: "auditoria", label: "Auditoria", icon: ScrollText },
];
const TAB_IDS = TABS.map((t) => t.id);
// Aba montada mas escondida (forceMount): o Radix só marca data-state=inactive.
const KEEP_MOUNTED = "data-[state=inactive]:hidden";

export default function Settings() {
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = TAB_IDS.includes(searchParams.get("aba")) ? searchParams.get("aba") : "conta";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-head text-3xl font-extrabold tracking-tight text-[#0F172A]">Configurações</h1>
        <p className="mt-1 text-sm text-slate-500">Sua conta, o termo de consentimento, privacidade e auditoria</p>
      </div>

      {/* `replace`: trocar de aba não enche o histórico do navegador. */}
      <Tabs value={tab} onValueChange={(next) => setSearchParams({ aba: next }, { replace: true })}>
        {/* Celular: as abas quebram em duas linhas em vez de sair da tela. */}
        <TabsList className="h-auto flex-wrap justify-start gap-1 bg-slate-100 text-slate-600">
          {TABS.map(({ id, label, icon: Icon }) => (
            <TabsTrigger key={id} value={id} className="gap-1.5 data-[state=active]:text-[#1E3A8A]" data-testid={`settings-tab-${id}`}>
              <Icon className="h-4 w-4" aria-hidden="true" /> {label}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="conta" forceMount className={`mt-6 space-y-6 ${KEEP_MOUNTED}`}>
          {/* PRODUTO (primeira versão): o cadastro de nome/CRP só aparece com a
              identificação do profissional ligada no servidor. Nesta versão o
              sistema é usado só pelos profissionais; volta ao abrir para pacientes. */}
          {user?.features?.professional_identification && <ProfessionalProfile />}
          <AccountSecurity />
          {/* Segurança média + backlog: verificação em duas etapas, aparelhos e encerramento. */}
          <TwoFactorCard />
          <ConnectedDevicesCard />
          <SessionSecurity />
          <CloseAccountCard />
        </TabsContent>

        <TabsContent value="termo" forceMount className={`mt-6 ${KEEP_MOUNTED}`}>
          <ConsentTermEditor />
        </TabsContent>

        <TabsContent value="privacidade" className="mt-6 space-y-6">
          <Card className="border-slate-200 p-6 shadow-sm">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600"><CheckCircle2 className="h-5 w-5" /></div>
                <div className="min-w-0">
                  <p className="font-head font-bold text-[#0F172A]">Conta em conformidade</p>
                  <p className="truncate text-sm text-slate-500">{user?.name} · {user?.email}</p>
                  {/* Item 16: evidência do aceite (versão + data), não um "Termos aceitos" fixo. */}
                  <p className="text-xs text-slate-500">
                    Política de Privacidade versão {user?.terms_version} aceita em {fmtDateTime(user?.terms_accepted_at)}
                  </p>
                </div>
              </div>
              <Link to="/privacidade" className="text-sm font-medium text-[#1E3A8A] hover:underline">Ler a política</Link>
            </div>
          </Card>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {PRIVACY_FEATURES.map((it) => (
              <Card key={it.title} className="border-slate-200 p-5 shadow-sm">
                <div className="flex items-start gap-3">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#F0F4F8] text-[#1E3A8A]"><it.icon className="h-4 w-4" /></div>
                  <div>
                    <div className="flex items-center gap-2">
                      <p className="font-medium text-[#0F172A]">{it.title}</p>
                      <Badge className="bg-emerald-100 text-emerald-700 hover:bg-emerald-100">Ativo</Badge>
                    </div>
                    <p className="mt-1 text-sm leading-relaxed text-slate-500">{it.desc}</p>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        </TabsContent>

        <TabsContent value="auditoria" className="mt-6">
          <AuditTrail />
        </TabsContent>
      </Tabs>
    </div>
  );
}
