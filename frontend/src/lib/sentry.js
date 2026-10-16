// DEPLOY (varredura, item I2): o backend já tinha Sentry (observability.py),
// mas nenhum erro do NAVEGADOR era capturado — uma falha de JS na tela da
// paciente só aparecia se alguém reclamasse. Este módulo espelha as mesmas
// regras do backend: desligado por padrão (sem DSN, não faz nada) e, quando
// ligado, nunca manda dado pessoal.
//
// REACT_APP_SENTRY_DSN vazio (padrão) = Sentry desligado, nem o pacote carrega
// de verdade (import dinâmico). Preencha no .env do frontend só depois de
// criar um projeto no Sentry (sentry.io, região UE de preferência — ver
// lgpd.md item 11, transferência internacional) e liste o serviço na Política
// de Privacidade (backend/legal.py, seção de terceiros).
import { isPatientIntakePage } from "@/lib/patientIntakeRoute";

const SENSITIVE_HEADERS = new Set(["authorization", "cookie", "set-cookie"]);

// Mesma função do backend (observability.py: scrub_sentry_event), adaptada:
// tira cookies/cabeçalhos sensíveis, corta a query string da URL (pode ter
// termo de busca) e nunca manda e-mail/IP do usuário.
function scrubSentryEvent(event) {
  const request = event.request;
  if (request && typeof request === "object") {
    delete request.cookies;
    delete request.data;
    if (typeof request.url === "string") request.url = request.url.split("?")[0];
    if (request.headers && typeof request.headers === "object") {
      for (const name of Object.keys(request.headers)) {
        if (SENSITIVE_HEADERS.has(name.toLowerCase())) request.headers[name] = "[removido]";
      }
    }
  }
  if (event.user && typeof event.user === "object") {
    delete event.user.email;
    delete event.user.ip_address;
  }
  return event;
}

let initialized = false;

/** Liga o Sentry só se REACT_APP_SENTRY_DSN estiver definido; senão, não faz nada. */
export async function initSentry() {
  const dsn = (process.env.REACT_APP_SENTRY_DSN || "").trim();
  // Produto (cadastro pelo paciente): a página do link de cadastro não carrega
  // nenhum script de terceiro por padrão (ver index.js, o mesmo motivo do
  // GoogleOAuthProvider) — um erro nela ainda é reportável, mas sem o SDK
  // inicializado ANTES de saber se é essa página evitaríamos um carregamento
  // condicional sem necessidade real; manter simples: mesma regra do Google.
  if (!dsn || initialized || isPatientIntakePage()) return;
  initialized = true;

  const Sentry = await import("@sentry/react");
  Sentry.init({
    dsn,
    environment: process.env.REACT_APP_SENTRY_ENVIRONMENT || process.env.NODE_ENV || "production",
    release: process.env.REACT_APP_RELEASE || undefined,
    // Rastreamento de desempenho desligado por padrão (0); ligar é escolha
    // consciente (REACT_APP_SENTRY_TRACES_SAMPLE_RATE), como no backend.
    tracesSampleRate: Number(process.env.REACT_APP_SENTRY_TRACES_SAMPLE_RATE || 0),
    sendDefaultPii: false,
    beforeSend: scrubSentryEvent,
    beforeSendTransaction: scrubSentryEvent,
  });
}

// DEPLOY (varredura, item D1): usado pelo ErrorBoundary (components/ErrorBoundary.jsx)
// para reportar um erro de render sem travar se o Sentry não estiver ligado
// (sem DSN) ou não tiver terminado de carregar ainda — nunca é motivo para a
// tela de erro falhar também. `info` é só o componentStack (nomes de
// componentes, nunca dado do formulário/estado) que o React já devolve.
export async function captureException(error, info) {
  if (!initialized) return;
  try {
    const Sentry = await import("@sentry/react");
    Sentry.captureException(error, info ? { contexts: { react: { componentStack: info.componentStack } } } : undefined);
  } catch {
    // Sentry não carregou (rede, bloqueador de terceiros etc.) — a tela de
    // erro já apareceu para a pessoa; isso aqui é só telemetria.
  }
}
