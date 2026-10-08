import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { CheckCircle2, Clock, Loader2, ShieldCheck, Stethoscope, TriangleAlert } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useLinkToken } from "@/components/auth/useLinkToken";
import { Button } from "@/components/ui/button";
import {
  ConsentQuestion, ConsentTermText, FormErrorSummary, FormSection, PatientFormFields,
} from "@/components/PatientFormFields";
import {
  EMPTY_FORM, applyFieldChange, errorFields, isMinorForm, payloadForSubmit, validatePatientForm,
} from "@/lib/patientForm";

// ===========================================================================
// PRODUTO: cadastro feito pelo PRÓPRIO paciente, num link temporário
// (substitui a integração com o Google Forms).
//
// Como funciona (as regras valem no servidor; a tela só as mostra):
//   1. a psicóloga gera o link e manda ao paciente. Abrir a página NÃO gasta
//      o link: a prévia automática do WhatsApp também "abre" o endereço;
//   2. o paciente lê quem recebe os dados e toca em "Começar". A partir daí
//      tem 10 minutos (cronômetro na tela) e o cadastro fica preso a ESTE
//      navegador (um segredo devolvido pelo servidor, ver "Guardado no aparelho");
//   3. o que ele digita é salvo sozinho, criptografado, a cada pausa. Recarregar
//      a página, perder a internet ou abrir o link de novo no mesmo celular volta
//      de onde parou: nada se perde;
//   4. nos últimos 3 minutos aparece "Preciso de mais tempo" (+10 minutos, até
//      10 vezes; WCAG 2.2.1: quem digita devagar ou usa leitor de tela não pode
//      ser expulso por um prazo fixo);
//   5. tempo esgotado: o formulário trava e o que foi salvo chega à psicóloga
//      como "cadastro incompleto", para ela completar com o paciente.
//
// Privacidade e segurança:
//   - o token vem no FRAGMENTO do link (#token=...), que o navegador não envia a
//     servidor nenhum, e sai da barra de endereço ao abrir (useLinkToken);
//   - esta página não carrega o Google (index.js) nem nada da conta da
//     psicóloga (App.js): o IP do paciente não vai para terceiros;
//   - os dados digitados NUNCA ficam no navegador: só no servidor, criptografados;
//   - noindex: buscadores não guardam a página.
// ===========================================================================

const FORM_OPTIONS = { includeNotes: false }; // "Notas iniciais" é anotação clínica da psicóloga
const SAVE_DELAY_MS = 1500;  // salva 1,5 s depois da última tecla
const RETRY_MS = 5000;       // sem internet: tenta salvar de novo a cada 5 s
const EXTEND_WINDOW_S = 180; // "Preciso de mais tempo" nos últimos 3 minutos (igual ao servidor)
const WARN_AT_S = [300, 120, 60, 30]; // avisos ao leitor de tela
// Campos do rascunho (os mesmos de IntakeDraftData no servidor).
const DRAFT_KEYS = [
  "full_name", "cpf", "rg", "birth_date", "education", "marital_status", "profession", "phone", "email",
  "address", "emergency_contact", "guardian_name", "guardian_cpf", "guardian_relationship", "guardian_phone",
  "consent_terms",
];
const draftData = (form) => Object.fromEntries(DRAFT_KEYS.map((key) => [key, form[key] ?? (key === "consent_terms" ? null : "")]));

const mmss = (seconds) => `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
const clock = (ms) => new Date(ms).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
const minutesText = (seconds) => {
  const minutes = Math.ceil(seconds / 60);
  return seconds <= 60 ? `${seconds} segundos` : `${minutes} minutos`;
};

// ---------------------------------------------------------------------------
// Guardado no aparelho (nunca dados pessoais):
//   - o token do link, na sessionStorage: a página tira o token da barra de
//     endereço, e sem esta cópia recarregar a página perderia o link;
//   - o segredo de preenchimento, na localStorage, com validade (o prazo do
//     servidor + 1 minuto). Assim, se o paciente fechar a aba e abrir o link de
//     novo pelo WhatsApp (outra aba), continua de onde parou. O segredo sozinho
//     não abre nada (precisa do link) e deixa de valer quando o prazo acaba.
//     A chave é uma marca do token (hash), não o token.
// Tudo é apagado ao enviar, ao vencer e quando o link deixa de valer.
// ---------------------------------------------------------------------------
const TOKEN_KEY = "psi-intake-token";
const SECRET_KEY = "psi-intake-fill";

const tokenTag = (token) => {
  let hash = 0x811c9dc5; // FNV-1a: só para reconhecer o link, não é proteção
  for (let i = 0; i < token.length; i += 1) {
    hash ^= token.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16);
};
const storage = {
  readToken: () => { try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } },
  writeToken: (token) => { try { sessionStorage.setItem(TOKEN_KEY, token); } catch { /* aba anônima: só não sobrevive ao recarregar */ } },
  readSecret: (token) => {
    try {
      const saved = JSON.parse(localStorage.getItem(SECRET_KEY) || "null");
      if (saved && saved.tag === tokenTag(token) && saved.until > Date.now()) return saved.secret;
      localStorage.removeItem(SECRET_KEY);
    } catch { /* armazenamento bloqueado */ }
    return "";
  },
  writeSecret: (token, secret, secondsLeft) => {
    try {
      localStorage.setItem(SECRET_KEY, JSON.stringify({ tag: tokenTag(token), secret, until: Date.now() + (secondsLeft + 60) * 1000 }));
    } catch { /* sem armazenamento: continua valendo nesta aba (memória) */ }
  },
  clear: () => {
    try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* nada a fazer */ }
    try { localStorage.removeItem(SECRET_KEY); } catch { /* nada a fazer */ }
  },
};

export default function PatientIntake() {
  const urlToken = useLinkToken();
  const [token] = useState(() => urlToken || storage.readToken());
  // checking | intro | filling | done | closed | other_device | offline
  const [phase, setPhase] = useState("checking");
  const [info, setInfo] = useState(null); // { professional_name, start_until, fill_minutes, ... }
  const [closedMessage, setClosedMessage] = useState({ code: "", msg: "" });
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [formErrors, setFormErrors] = useState({});
  const [submitError, setSubmitError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [extensionsLeft, setExtensionsLeft] = useState(0);
  const [extending, setExtending] = useState(false);
  const [saveState, setSaveState] = useState("idle"); // idle | pending | saving | saved | error
  const [savedAt, setSavedAt] = useState(null);
  const [announcement, setAnnouncement] = useState("");

  // Referências: os temporizadores e os eventos da página precisam do valor ATUAL.
  const secretRef = useRef("");
  const deadlineRef = useRef(0);      // horário-limite NESTE relógio (Date.now() + segundos do servidor)
  const formRef = useRef(form);
  const dirtyRef = useRef(false);     // há digitação ainda não salva
  const savingRef = useRef(false);
  const saveTimer = useRef(null);
  const phaseRef = useRef(phase);
  const announcedRef = useRef(new Set());
  const expiringRef = useRef(false);
  const submittingRef = useRef(false);
  formRef.current = form;
  phaseRef.current = phase;

  // noindex (buscadores não guardam a página) e o token sobrevivendo ao recarregar.
  useEffect(() => {
    const meta = document.createElement("meta");
    meta.name = "robots";
    meta.content = "noindex, nofollow";
    document.head.appendChild(meta);
    return () => meta.remove();
  }, []);
  useEffect(() => { if (token) storage.writeToken(token); }, [token]);
  // Outro link colado na barra com esta página aberta: o navegador só troca o
  // "#token=..." (sem recarregar) e a tela continuaria no link anterior.
  // Recarrega para abrir o link novo (o token novo está no endereço).
  useEffect(() => {
    const onHashChange = () => { if (/token=/.test(window.location.hash)) window.location.reload(); };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  // O prazo vem SEMPRE do servidor (segundos restantes): o relógio do celular
  // pode estar errado, e o que vale é o do servidor.
  const syncDeadline = useCallback((seconds) => {
    const previous = deadlineRef.current;
    deadlineRef.current = Date.now() + seconds * 1000;
    setSecondsLeft(Math.max(0, Math.round(seconds)));
    if (deadlineRef.current > previous + 30000) announcedRef.current = new Set(); // ganhou tempo: avisos de novo
    if (secretRef.current) storage.writeSecret(token, secretRef.current, seconds);
  }, [token]);

  // O link deixou de valer (enviado, vencido, cancelado) ou está em outro aparelho.
  const closeWith = useCallback((detail, nextPhase = "closed") => {
    clearTimeout(saveTimer.current);
    dirtyRef.current = false;
    storage.clear();
    setClosedMessage({ code: detail?.code || "", msg: formatApiErrorDetail(detail) });
    setPhase(nextPhase);
  }, []);

  /** Trata as respostas que encerram o preenchimento. Devolve true se tratou. */
  const handleFillError = useCallback((err) => {
    const status = err.response?.status;
    const detail = err.response?.data?.detail;
    if (status === 410) {
      closeWith(detail);
      return true;
    }
    if (detail?.code === "intake_other_device") {
      closeWith(detail, "other_device");
      return true;
    }
    return false;
  }, [closeWith]);

  // ---------------------------------------------------------------------------
  // 1. Abrir: confere o link (sem começar o tempo) e retoma se já começou aqui.
  // ---------------------------------------------------------------------------
  const resume = useCallback(async (secret) => {
    const { data } = await api.post("/intake/resume", { token, fill_secret: secret });
    secretRef.current = secret;
    setForm(applyFieldChange({ ...EMPTY_FORM, ...data.data }, "birth_date", data.data?.birth_date || ""));
    setExtensionsLeft(data.extensions_left);
    syncDeadline(data.seconds_left);
    if (data.data && Object.keys(data.data).length) setSaveState("saved");
    setPhase("filling");
  }, [token, syncDeadline]);

  const openLink = useCallback(async () => {
    if (!token) {
      closeWith({ code: "intake_invalid", msg: "Este link de cadastro não é válido ou já expirou. Peça um novo link à sua psicóloga." });
      return;
    }
    setPhase("checking");
    try {
      const { data } = await api.post("/intake/open", { token });
      setInfo(data);
      if (data.status === "in_progress") {
        const secret = storage.readSecret(token);
        if (!secret) {
          closeWith({ code: "intake_other_device", msg: "Este cadastro foi começado em outro aparelho ou navegador. Continue nele, ou peça um novo link à sua psicóloga." }, "other_device");
          return;
        }
        await resume(secret);
        return;
      }
      setPhase("intro");
    } catch (err) {
      if (handleFillError(err)) return;
      if (err.response?.status === 429) {
        setClosedMessage({ code: "", msg: formatApiErrorDetail(err.response.data?.detail) });
      } else {
        setClosedMessage({ code: "", msg: "" });
      }
      setPhase("offline");
    }
  }, [token, resume, handleFillError, closeWith]);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { openLink(); }, []);

  // ---------------------------------------------------------------------------
  // 2. Começar: o tempo de preenchimento começa aqui.
  // ---------------------------------------------------------------------------
  const start = async () => {
    setBusy(true);
    try {
      const { data } = await api.post("/intake/start", { token });
      secretRef.current = data.fill_secret;
      setExtensionsLeft(data.extensions_left);
      syncDeadline(data.seconds_left);
      setPhase("filling");
      // Leva o foco ao primeiro campo (o botão "Começar" sumiu da tela).
      requestAnimationFrame(() => document.getElementById("patient-name")?.focus());
    } catch (err) {
      if (!handleFillError(err)) toast.error(err.response ? formatApiErrorDetail(err.response.data?.detail) : "Sem conexão com a internet. Confira e tente de novo.");
    } finally {
      setBusy(false);
    }
  };

  // ---------------------------------------------------------------------------
  // 3. Salvamento automático (rascunho criptografado no servidor).
  // ---------------------------------------------------------------------------
  const saveDraftRef = useRef(null);
  const scheduleSave = useCallback((delay = SAVE_DELAY_MS) => {
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => saveDraftRef.current?.(), delay);
  }, []);

  const saveDraft = useCallback(async () => {
    clearTimeout(saveTimer.current);
    if (!dirtyRef.current || phaseRef.current !== "filling" || !secretRef.current) return;
    if (savingRef.current) { scheduleSave(); return; } // um salvamento por vez
    savingRef.current = true;
    dirtyRef.current = false;
    setSaveState("saving");
    try {
      const { data } = await api.put("/intake/draft", { token, fill_secret: secretRef.current, data: draftData(formRef.current) });
      setSavedAt(data.saved_at);
      setSaveState("saved");
      syncDeadline(data.seconds_left);
    } catch (err) {
      dirtyRef.current = true; // não salvou: continua pendente
      if (!handleFillError(err)) {
        setSaveState("error");
        scheduleSave(RETRY_MS);
      }
    } finally {
      // O que foi digitado DURANTE este salvamento já agendou o próximo (onFormChange).
      savingRef.current = false;
    }
  }, [token, syncDeadline, handleFillError, scheduleSave]);
  saveDraftRef.current = saveDraft;

  const onFormChange = (key) => (e) => {
    setForm((current) => applyFieldChange(current, key, e.target.value));
    setFormErrors((current) => {
      if (!current[key]) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
    dirtyRef.current = true;
    setSaveState("pending");
    scheduleSave();
  };

  // Trocou de aplicativo (ex.: foi ao WhatsApp ver o CPF) ou vai fechar: salva na hora.
  // Fechar a aba com algo não salvo: o navegador pergunta antes.
  useEffect(() => {
    if (phase !== "filling") return undefined;
    const onHide = () => { if (document.visibilityState === "hidden") saveDraftRef.current?.(); };
    const onBeforeUnload = (event) => {
      if (!dirtyRef.current) return;
      saveDraftRef.current?.();
      event.preventDefault();
      event.returnValue = "";
    };
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("beforeunload", onBeforeUnload);
    };
  }, [phase]);

  // ---------------------------------------------------------------------------
  // 4. Cronômetro. Conta pelo horário-limite (não por "menos 1 a cada segundo"):
  // celular bloqueado ou aba em segundo plano não atrasam a contagem.
  // ---------------------------------------------------------------------------
  const timeIsUp = useCallback(async () => {
    expiringRef.current = true;
    // Última chance de salvar (o servidor aceita alguns segundos de atraso da rede).
    if (dirtyRef.current) await saveDraftRef.current?.();
    if (phaseRef.current !== "filling") return;
    try {
      // O servidor diz o que aconteceu com os dados (incompleto ou sem nada salvo).
      const { data } = await api.post("/intake/resume", { token, fill_secret: secretRef.current });
      syncDeadline(data.seconds_left); // relógio do celular adiantado: ainda havia tempo
      expiringRef.current = false;
    } catch (err) {
      if (!handleFillError(err)) {
        closeWith({
          code: "intake_expired",
          msg: savedAt
            ? `O tempo para preencher acabou. O que você preencheu até ${clock(Date.parse(savedAt))} foi enviado à sua psicóloga, que vai completar o cadastro com você.`
            : "O tempo para preencher acabou. Peça um novo link à sua psicóloga.",
        });
      }
    }
  }, [token, savedAt, syncDeadline, handleFillError, closeWith]);

  useEffect(() => {
    if (phase !== "filling") return undefined;
    const tick = () => {
      const left = Math.max(0, Math.round((deadlineRef.current - Date.now()) / 1000));
      setSecondsLeft(left);
      const warning = WARN_AT_S.find((at) => left <= at && left > at - 5 && !announcedRef.current.has(at));
      if (warning) {
        announcedRef.current.add(warning);
        setAnnouncement(`Faltam ${minutesText(left)} para terminar o cadastro.`
          + (left <= EXTEND_WINDOW_S ? " Se precisar, use o botão Preciso de mais tempo." : ""));
      }
      if (left <= 0 && !expiringRef.current && !submittingRef.current) timeIsUp();
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [phase, timeIsUp]);

  const extend = async () => {
    setExtending(true);
    try {
      const { data } = await api.post("/intake/extend", { token, fill_secret: secretRef.current });
      syncDeadline(data.seconds_left);
      setExtensionsLeft(data.extensions_left);
      const message = `Pronto: mais ${info?.extension_minutes || 10} minutos. Agora você tem até ${clock(deadlineRef.current)}.`;
      setAnnouncement(message);
      toast.success(message);
    } catch (err) {
      if (!handleFillError(err)) {
        toast.error(err.response ? formatApiErrorDetail(err.response.data?.detail) : "Sem conexão com a internet. Confira e tente de novo.");
      }
    } finally {
      setExtending(false);
    }
  };

  // ---------------------------------------------------------------------------
  // 5. Enviar. O servidor confere tudo de novo e o envio é idempotente: se a
  // resposta se perder na rede, enviar de novo não cria outro cadastro.
  // ---------------------------------------------------------------------------
  const submit = async (e) => {
    e.preventDefault();
    const errors = validatePatientForm(form, FORM_OPTIONS);
    setFormErrors(errors);
    setSubmitError("");
    const firstInvalid = errorFields(form, FORM_OPTIONS).find(([key]) => errors[key]);
    if (firstInvalid) {
      document.getElementById(firstInvalid[2])?.focus();
      return;
    }
    setSubmitting(true);
    submittingRef.current = true;
    clearTimeout(saveTimer.current);
    try {
      await api.post("/intake/submit", { token, fill_secret: secretRef.current, data: payloadForSubmit(form) });
      dirtyRef.current = false;
      storage.clear();
      setPhase("done");
    } catch (err) {
      if (!handleFillError(err)) {
        setSubmitError(err.response
          ? formatApiErrorDetail(err.response.data?.detail)
          : "Sem conexão com a internet. O que você preencheu está salvo: confira a conexão e toque em Enviar cadastro de novo.");
        requestAnimationFrame(() => document.getElementById("patient-form-errors")?.focus());
        dirtyRef.current = true; // mantém o rascunho em dia enquanto isso
        scheduleSave();
      }
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Tela
  // ---------------------------------------------------------------------------
  const professional = info?.professional_name || "sua psicóloga";
  const minor = isMinorForm(form);
  const lowTime = secondsLeft <= 60;
  const canExtend = phase === "filling" && secondsLeft <= EXTEND_WINDOW_S && secondsLeft > 0 && extensionsLeft > 0;

  return (
    // tabIndex -1: recebe o foco ao abrir (RouteAnnouncer).
    <main tabIndex={-1} className="min-h-screen bg-[#F8FAFC] pb-16 focus:outline-none" data-testid="patient-intake-page">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-2xl items-center gap-2.5 px-4 py-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#1E3A8A]">
            <Stethoscope className="h-4 w-4 text-white" aria-hidden="true" />
          </div>
          <span className="font-head text-base font-bold text-[#0F172A]">PsiGestão</span>
        </div>
      </header>

      {/* Acessibilidade: avisos do cronômetro para o leitor de tela (a contagem
          visível não é lida a cada segundo, o que seria insuportável). */}
      <div className="sr-only" aria-live="polite" role="status" data-testid="intake-announcer">{announcement}</div>

      {phase === "filling" && (
        <div className={`sticky top-0 z-20 border-b ${lowTime ? "border-red-200 bg-red-50" : "border-slate-200 bg-white"}`}
          data-testid="intake-timer-bar">
          <div className="mx-auto flex max-w-2xl flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-2.5">
            <p className="flex items-center gap-2 text-sm text-slate-700">
              <Clock className={`h-4 w-4 ${lowTime ? "text-red-700" : "text-[#1E3A8A]"}`} aria-hidden="true" />
              <span>Tempo restante:</span>
              <span role="timer" className={`font-head text-lg font-bold tabular-nums ${lowTime ? "text-red-700" : "text-[#0F172A]"}`}
                data-testid="intake-countdown">{mmss(secondsLeft)}</span>
            </p>
            <div className="flex items-center gap-3">
              <SaveStatus state={saveState} savedAt={savedAt} />
              {canExtend && (
                <Button type="button" size="sm" onClick={extend} disabled={extending} className="bg-[#1E3A8A] hover:bg-[#0F2C59]"
                  data-testid="intake-extend">
                  {extending && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}Preciso de mais tempo
                </Button>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="mx-auto max-w-2xl px-4 pt-6">
        {phase === "checking" && (
          <div className="flex justify-center py-16" role="status" aria-label="Conferindo o link">
            <Loader2 className="h-6 w-6 animate-spin text-[#1E3A8A]" />
          </div>
        )}

        {phase === "intro" && info && (
          <section aria-labelledby="intake-title" data-testid="intake-intro">
            <h1 id="intake-title" className="font-head text-2xl font-bold text-[#0F172A]">Cadastro do paciente</h1>
            <p className="mt-2 text-sm leading-relaxed text-slate-600">
              <b className="text-[#0F172A]">{professional}</b> pediu que você preencha seu cadastro para o atendimento.
            </p>
            <ul className="mt-5 space-y-2.5 text-sm leading-relaxed text-slate-700">
              <li>• Depois de tocar em <b>Começar</b>, você tem <b>{info.fill_minutes} minutos</b> para terminar. Se precisar, dá para pedir mais tempo.</li>
              <li>• O que você digita é salvo sozinho, com criptografia. Se a internet cair ou a página recarregar, você continua de onde parou.</li>
              <li>• Se o tempo acabar antes do envio, o que você já preencheu vai para {professional}, que completa o cadastro com você.</li>
              <li>• Use este aparelho até o fim: o cadastro fica preso a este navegador.</li>
              <li>• Tenha à mão: CPF, RG, endereço e um contato de emergência. Para menores de 18 anos, também os dados do responsável legal.</li>
            </ul>
            <PrivacyNotice professional={professional} />
            <Button type="button" onClick={start} disabled={busy} className="mt-6 h-12 w-full bg-[#1E3A8A] text-base hover:bg-[#0F2C59] sm:w-auto sm:px-8"
              data-testid="intake-start">
              {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Começar
            </Button>
            <p className="mt-3 text-xs text-slate-500">Este link vale para começar até {clock(Date.parse(info.start_until))}.</p>
          </section>
        )}

        {phase === "filling" && (
          <section aria-labelledby="intake-form-title">
            <h1 id="intake-form-title" className="font-head text-2xl font-bold text-[#0F172A]">Cadastro do paciente</h1>
            <p className="mt-1 text-sm text-slate-600">
              Campos com * são obrigatórios. Seus dados vão somente para {professional} e são criptografados antes de serem salvos.
            </p>
            {/* noValidate: os erros aparecem no próprio formulário (mesmo padrão da ficha da psicóloga). */}
            <form onSubmit={submit} noValidate className="mt-6 space-y-6" data-testid="intake-form">
              <FormErrorSummary errors={formErrors} submitError={submitError} fields={errorFields(form, FORM_OPTIONS)} action="enviar" />
              {/* Travado durante o envio: nada muda depois de conferido. */}
              <fieldset disabled={submitting} className="space-y-6">
                <PatientFormFields form={form} errors={formErrors} onFormChange={onFormChange} selfFill />
                <FormSection title="Consentimento">
                  <div className="space-y-3 rounded-md border border-slate-200 bg-white p-4 sm:col-span-2">
                    <ConsentTermText text={info?.consent_text} />
                    <ConsentQuestion form={form} errors={formErrors} onFormChange={onFormChange}
                      legend={minor ? "O responsável legal concorda com o termo? *" : "Você concorda com o termo? *"} />
                  </div>
                </FormSection>
              </fieldset>
              <PrivacyNotice professional={professional} compact />
              <Button type="submit" disabled={submitting} className="h-12 w-full bg-[#1E3A8A] text-base hover:bg-[#0F2C59]"
                data-testid="intake-submit">
                {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Enviar cadastro
              </Button>
            </form>
          </section>
        )}

        {phase === "done" && (
          <Outcome icon="ok" title="Cadastro enviado" testid="intake-done">
            Obrigado! Seu cadastro chegou para {professional}. Você já pode fechar esta página.
            Se precisar corrigir algo, fale com a sua psicóloga.
          </Outcome>
        )}

        {phase === "closed" && (
          <Outcome icon={closedMessage.code === "intake_done" ? "ok" : "warn"}
            title={closedMessage.code === "intake_done" ? "Cadastro já enviado" : closedMessage.code === "intake_expired" ? "O tempo acabou" : "Link indisponível"}
            testid="intake-closed">
            {closedMessage.msg}
          </Outcome>
        )}

        {phase === "other_device" && (
          <Outcome icon="warn" title="Cadastro aberto em outro aparelho" testid="intake-other-device">
            {closedMessage.msg}
          </Outcome>
        )}

        {phase === "offline" && (
          <Outcome icon="warn" title="Não foi possível abrir o cadastro" testid="intake-offline">
            {closedMessage.msg || "Sem conexão com o servidor. Confira a internet e tente de novo."}
            <Button type="button" onClick={openLink} className="mt-4 block bg-[#1E3A8A] hover:bg-[#0F2C59]">Tentar de novo</Button>
          </Outcome>
        )}
      </div>
    </main>
  );
}

/** Situação do salvamento automático, em texto (o ícone só reforça). */
function SaveStatus({ state, savedAt }) {
  const text = {
    pending: "Salvando…",
    saving: "Salvando…",
    saved: savedAt ? `Salvo às ${clock(Date.parse(savedAt))}` : "Salvo",
    error: "Sem conexão: tentando salvar de novo",
  }[state];
  if (!text) return null;
  return (
    <span className={`text-xs ${state === "error" ? "font-medium text-red-700" : "text-slate-500"}`} data-testid="intake-save-status">
      {text}
    </span>
  );
}

// LGPD (art. 9º): quem recebe os dados, para quê, o que fica registrado e onde
// ler mais. A política abre em outra aba para não perder o preenchimento.
// (<section> e não <aside>: dentro do <main>, um <aside> vira um ponto de referência
// solto, que o leitor de tela lista fora do conteúdo.)
function PrivacyNotice({ professional, compact = false }) {
  return (
    <section className={`rounded-md border border-slate-200 bg-white p-4 text-xs leading-relaxed text-slate-600 ${compact ? "" : "mt-6"}`}
      aria-label="Como seus dados são tratados">
      <p className="flex items-center gap-1.5 font-semibold text-[#0F172A]">
        <ShieldCheck className="h-4 w-4 text-emerald-700" aria-hidden="true" /> Seus dados
      </p>
      <p className="mt-1.5">
        Quem recebe: <b>{professional}</b>, para o seu atendimento psicológico. Os dados ficam guardados com criptografia e
        não são usados para publicidade nem vendidos. A data e o endereço IP do envio ficam registrados como prova da sua
        resposta ao termo de consentimento. Se o tempo acabar antes do envio, o que já foi preenchido fica com {professional}{" "}
        para completar o cadastro com você.{" "}
        <a href="/privacidade" target="_blank" rel="noopener noreferrer" className="font-medium text-[#1E3A8A] underline underline-offset-2">
          Política de Privacidade (abre em nova aba)
        </a>
      </p>
    </section>
  );
}

/** Tela final (enviado, vencido, indisponível...). O título recebe o foco para ser lido. */
function Outcome({ icon, title, testid, children }) {
  const headingRef = useRef(null);
  useEffect(() => { headingRef.current?.focus(); }, [title]);
  const ok = icon === "ok";
  return (
    <section className="py-6" data-testid={testid}>
      <div className={`flex h-11 w-11 items-center justify-center rounded-lg ${ok ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>
        {ok ? <CheckCircle2 className="h-5 w-5" aria-hidden="true" /> : <TriangleAlert className="h-5 w-5" aria-hidden="true" />}
      </div>
      <h1 ref={headingRef} tabIndex={-1} className="mt-4 font-head text-2xl font-bold text-[#0F172A] focus:outline-none">{title}</h1>
      <div className="mt-2 text-sm leading-relaxed text-slate-600">{children}</div>
    </section>
  );
}
