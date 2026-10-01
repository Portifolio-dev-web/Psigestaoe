import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { GoogleLogin } from "@react-oauth/google";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { clearUserDrafts, forgetDraftKey } from "@/lib/drafts";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";
import {
  Shield, Lock, FileCheck, History, KeyRound, ScrollText, Loader2, CheckCircle2, UserRound,
  FileSignature, Archive, TimerReset, LogOut,
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
};
const ENTITY_LABEL = {
  paciente: "Paciente", prontuario: "Prontuário", agenda: "Agenda", usuario: "Usuário",
  termos: "Política", termo: "Termo", integracao: "Integração", sessao: "Sessão",
};

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
// - Rascunhos: as anotações não salvas ficam criptografadas em cada aparelho.
//   Se um aparelho for perdido, trocar a chave torna os rascunhos dele
//   ilegíveis, mesmo para quem conseguir entrar na conta depois.
const LOCK_OPTIONS = [5, 10, 15, 30];

function SessionSecurity() {
  const { user, setUser } = useAuth();
  const [saving, setSaving] = useState(false);

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

  const invalidateDrafts = async () => {
    const confirmed = window.confirm(
      "Isto apaga a chave dos rascunhos: anotações não salvas em QUALQUER aparelho (inclusive este) deixam de poder ser recuperadas. Continuar?",
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
  };

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
          <p className="text-sm font-medium text-slate-700">Rascunhos de anotações</p>
          <p className="mt-1 text-xs leading-relaxed text-slate-500">
            Enquanto você escreve, a anotação é guardada criptografada neste aparelho, para não se perder se a
            sessão expirar ou a internet cair. Rascunhos somem ao salvar e vencem em 7 dias.
          </p>
          <Button variant="outline" size="sm" onClick={invalidateDrafts}
            className="mt-3 border-red-200 text-red-600 hover:bg-red-50" data-testid="invalidate-drafts-btn">
            Invalidar rascunhos em todos os aparelhos
          </Button>
        </div>
      </div>
    </Card>
  );
}

// SEGURANÇA (varredura, item 5): senha e sessões em outros aparelhos.
// Antes não havia como trocar a senha nem encerrar uma sessão esquecida em
// outro computador: se a senha vazasse, o profissional não tinha o que fazer.
// - Trocar a senha encerra as sessões de TODOS os outros aparelhos (este continua).
// - Quem só entra pelo Google define a primeira senha confirmando com o Google:
//   assim, quem encontrar o computador logado não consegue tomar a conta.
// - "Sair dos outros dispositivos": para um computador esquecido logado ou
//   suspeita de acesso indevido.

// Mesmas regras do servidor (validate_password_strength em server.py). Aqui
// servem só para avisar antes de enviar; quem decide é o servidor.
function passwordProblem(password) {
  if (password.length < 8) return "A senha deve ter pelo menos 8 caracteres.";
  if (new TextEncoder().encode(password).length > 72) return "A senha é longa demais (máximo de 72 caracteres).";
  if (!/\p{L}/u.test(password) || !/\d/.test(password)) return "A senha deve conter letras e números.";
  return "";
}

const EMPTY_PASSWORD_FORM = { current: "", next: "", confirm: "" };

function AccountSecurity() {
  const { user, setUser } = useAuth();
  const hasPassword = Boolean(user?.has_password);
  const [form, setForm] = useState(EMPTY_PASSWORD_FORM);
  const [saving, setSaving] = useState(false);
  const [otherSessions, setOtherSessions] = useState(null);
  const [ending, setEnding] = useState(false);

  useEffect(() => {
    api.get("/auth/sessions")
      .then(({ data }) => setOtherSessions(data.other_sessions))
      .catch(() => setOtherSessions(null));
  }, []);

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
      setOtherSessions(0);
      const ended = data.ended_sessions
        ? ` ${data.ended_sessions} sessão(ões) em outros aparelhos foram encerradas.`
        : "";
      toast.success(`${hasPassword ? "Senha alterada" : "Senha definida"}.${ended}`);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    } finally {
      setSaving(false);
    }
  };

  const endOtherSessions = async () => {
    if (!window.confirm("Encerrar a sessão em todos os outros aparelhos e navegadores? Este continua conectado.")) return;
    setEnding(true);
    try {
      // Corpo JSON obrigatório: o servidor recusa a rota sem ele (proteção contra CSRF).
      const { data } = await api.post("/auth/sessions/end-others", { confirm: true });
      setOtherSessions(0);
      toast.success(data.ended_sessions
        ? `${data.ended_sessions} sessão(ões) encerrada(s) em outros aparelhos.`
        : "Não havia outras sessões abertas.");
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    } finally {
      setEnding(false);
    }
  };

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
              <Input id="current-password" type="password" autoComplete="current-password" className="mt-1.5"
                value={form.current} onChange={setField("current")} data-testid="current-password-input" />
            </div>
          ) : (
            <p className="rounded-md bg-slate-50 px-3 py-2 text-xs leading-relaxed text-slate-600">
              Você entra com o Google. Defina uma senha para também entrar com e-mail e senha; para confirmar que
              é você, o Google será usado.
            </p>
          )}
          <div>
            <Label htmlFor="new-password" className="text-slate-700">{hasPassword ? "Nova senha" : "Senha"}</Label>
            <Input id="new-password" type="password" autoComplete="new-password" maxLength={72} className="mt-1.5"
              value={form.next} onChange={setField("next")} aria-invalid={Boolean(problem)}
              aria-describedby="password-rules" data-testid="new-password-input" />
          </div>
          <div>
            <Label htmlFor="confirm-password" className="text-slate-700">Repita a senha</Label>
            <Input id="confirm-password" type="password" autoComplete="new-password" maxLength={72} className="mt-1.5"
              value={form.confirm} onChange={setField("confirm")} aria-invalid={Boolean(mismatch)}
              aria-describedby="password-rules" data-testid="confirm-password-input" />
          </div>
          <p id="password-rules" role={hint ? "alert" : undefined}
            className={`text-xs ${hint ? "text-red-600" : "text-slate-500"}`}>
            {hint || "Mínimo de 8 caracteres, com letras e números. Os outros aparelhos serão desconectados."}
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
          <p className="text-sm font-medium text-slate-700">Outros aparelhos</p>
          <p className="mt-1 text-xs leading-relaxed text-slate-500" data-testid="other-sessions-count">
            {otherSessions === null
              ? "Verificando sessões abertas…"
              : otherSessions === 0
                ? "Nenhuma outra sessão aberta: só este navegador está conectado."
                : `${otherSessions} outra(s) sessão(ões) aberta(s) em outros aparelhos ou navegadores.`}
          </p>
          <p className="mt-1 text-xs leading-relaxed text-slate-500">
            Use se esqueceu a conta aberta em outro computador ou se suspeita de acesso indevido.
          </p>
          <Button variant="outline" size="sm" onClick={endOtherSessions} disabled={ending || !otherSessions}
            className="mt-3 gap-2 border-red-200 text-red-600 hover:bg-red-50" data-testid="end-other-sessions-btn">
            {ending ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogOut className="h-4 w-4" />}
            Sair dos outros dispositivos
          </Button>
        </div>
      </div>
    </Card>
  );
}

export default function Settings() {
  const { user } = useAuth();
  const [logs, setLogs] = useState(null);
  // ESCALA: a trilha é paginada. Antes só os 100 últimos registros apareciam,
  // sem como ver os anteriores (e a trilha só cresce, pois inclui leituras).
  const [logsCursor, setLogsCursor] = useState(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    api.get("/audit")
      .then(({ data }) => { setLogs(data.items); setLogsCursor(data.next_cursor); })
      .catch(() => setLogs([]));
  }, []);

  const loadMoreLogs = async () => {
    if (!logsCursor) return;
    setLoadingMore(true);
    try {
      const { data } = await api.get(`/audit?${new URLSearchParams({ cursor: logsCursor })}`);
      setLogs((current) => [...current, ...data.items]);
      setLogsCursor(data.next_cursor);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    } finally {
      setLoadingMore(false);
    }
  };

  // Descrições corrigidas para refletir o que o sistema FAZ. A versão anterior
  // dizia "AES-256-GCM" (o sistema usa Fernet: AES-128-CBC + HMAC-SHA256) e
  // chamava de "imutável" uma trilha que podia ser apagada em cascata.
  const items = [
    { icon: Lock, title: "Criptografia em repouso", desc: "Nome, CPF, RG, contatos, endereço, observações, prontuários e agenda são cifrados (Fernet: AES-128 + HMAC-SHA256) antes de gravar no banco." },
    { icon: Shield, title: "Criptografia em trânsito", desc: "Toda comunicação ocorre via HTTPS/TLS." },
    { icon: History, title: "Imutabilidade & versionamento", desc: "Prontuários não são sobrescritos: cada edição gera uma nova versão arquivada." },
    { icon: KeyRound, title: "Multi-tenancy seguro", desc: "Cada profissional acessa apenas os próprios dados, com sessão isolada." },
    { icon: FileCheck, title: "Direitos do titular (LGPD)", desc: "Exportação (PDF/JSON), correção, anonimização e registro ou revogação do consentimento, com data." },
    { icon: Archive, title: "Guarda legal (CFP)", desc: "Prontuários não podem ser excluídos antes de 5 anos do último registro. Até lá, o paciente pode ser arquivado." },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-head text-3xl font-extrabold tracking-tight text-[#0F172A]">Privacidade & LGPD</h1>
        <p className="mt-1 text-sm text-slate-500">Conformidade, criptografia e trilha de auditoria</p>
      </div>

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

      {/* PRODUTO (primeira versão): o cadastro de nome/CRP só aparece com a
          identificação do profissional ligada no servidor. Nesta versão o
          sistema é usado só pelos profissionais; volta ao abrir para pacientes. */}
      {user?.features?.professional_identification && <ProfessionalProfile />}
      <ConsentTermEditor />
      <SessionSecurity />
      <AccountSecurity />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {items.map((it) => (
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

      <Card className="border-slate-200 p-6 shadow-sm">
        <div className="flex flex-wrap items-center gap-2">
          <ScrollText className="h-4 w-4 text-[#1E3A8A]" />
          <h2 className="font-head text-lg font-bold text-[#0F172A]">Trilha de auditoria</h2>
          {/* Item 14: agora é de fato somente inclusão (gatilho no banco) e inclui leituras e IP. */}
          <span className="text-xs text-slate-400">(somente inclusão · inclui visualizações e IP)</span>
        </div>
        <Separator className="my-4" />
        {logs === null ? (
          <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin text-[#1E3A8A]" /></div>
        ) : logs.length === 0 ? (
          <p className="py-8 text-center text-sm text-slate-400">Nenhum registro de auditoria ainda.</p>
        ) : (
          <div className="max-h-[420px] space-y-1 overflow-y-auto psi-scroll" data-testid="audit-log">
            {logs.map((l) => (
              <div key={l.log_id} className="flex items-center gap-3 rounded-md px-2 py-2 text-sm hover:bg-slate-50">
                <Badge variant="secondary" className="w-28 shrink-0 justify-center text-[11px]">{ACTION_LABEL[l.action] || l.action}</Badge>
                <span className="text-slate-600">{ENTITY_LABEL[l.entity_type] || l.entity_type}</span>
                <span className="truncate text-slate-400">{l.detail}</span>
                <span className="ml-auto flex shrink-0 items-center gap-3">
                  <span className="hidden font-mono text-[11px] text-slate-400 md:inline" title="Endereço IP da ação">
                    {l.ip_address || "—"}
                  </span>
                  <span className="text-xs text-slate-400">
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
    </div>
  );
}
