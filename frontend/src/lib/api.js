export const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Rotas em que 401 é resposta normal (senha errada / visitante sem login).
const PUBLIC_AUTH_PATHS = ["/auth/me", "/auth/login", "/auth/register", "/auth/google"];

async function doFetch(url, opts, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...opts, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function request(method, path, body) {
  const opts = { method, credentials: "include", headers: {} };
  // SEGURANÇA (varredura, item 10): proteção contra CSRF. O servidor recusa
  // escritas (POST/PUT/DELETE) sem este cabeçalho. Outro site não consegue
  // enviá-lo com o cookie do usuário: cabeçalho próprio exige licença do
  // servidor (preflight do CORS), dada só às origens permitidas.
  // Leituras (GET) não levam o cabeçalho: não alteram nada e, sem ele,
  // dispensam o preflight (uma ida e volta a menos em cada tela).
  if (method !== "GET") {
    opts.headers["X-Requested-With"] = "psigestao";
  }
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const url = `${API}${path}`;

  // CORREÇÃO (item 5 da auditoria): novas tentativas só para GET.
  // Antes, TODA requisição era repetida até 3x quando passava de 6s sem
  // resposta. Num POST/PUT, o timeout não significa que o servidor não gravou:
  // ele pode ter criado o paciente/prontuário/sessão e só demorado para
  // responder. Repetir gerava registros DUPLICADOS (e versões extras de
  // prontuário). GET é idempotente (só lê), então repetir é seguro.
  // Escritas agora têm 1 tentativa e um prazo maior (30s); se falharem,
  // o erro chega à tela e o usuário decide se tenta de novo.
  const isIdempotent = method === "GET";
  const maxAttempts = isIdempotent ? 3 : 1;
  const timeoutMs = isIdempotent ? 6000 : 30000;

  let res;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      res = await doFetch(url, opts, timeoutMs);
      break;
    } catch (e) {
      if (attempt === maxAttempts) throw e;
    }
  }

  let data = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (e) {
      data = text;
    }
  }
  if (!res.ok) {
    // Conformidade (item 16): se a Política de Privacidade mudar de versão
    // durante o uso, o backend recusa as rotas de dados com este código.
    // O AuthContext ouve o evento, recarrega o usuário e a tela de aceite
    // aparece sozinha, em vez de o usuário ver erros soltos em cada tela.
    if (res.status === 403 && data?.detail?.code === "terms_required") {
      window.dispatchEvent(new Event("psi:terms-required"));
    }
    // PRODUTO (bloqueio por inatividade): o servidor bloqueou a sessão (pode
    // ter sido em outra aba, ou porque o computador hibernou). O AuthContext
    // mostra a tela de bloqueio POR CIMA da tela atual, sem perder o que
    // estava aberto (ex.: uma anotação sendo escrita).
    if (res.status === 423 && data?.detail?.code === "session_locked") {
      window.dispatchEvent(new Event("psi:session-locked"));
    }
    // PRODUTO: sessão expirada/encerrada no meio do uso. Antes, cada tela só
    // mostrava "erro" e a pessoa ficava presa; agora o app volta ao login
    // lembrando a página de origem (e os rascunhos continuam guardados).
    // As rotas públicas de autenticação respondem 401 por senha errada e
    // não devem disparar isso.
    // Produto (assinatura): gravação recusada no modo somente leitura. O AuthContext
    // atualiza a situação da conta e o WriteAccessContext explica o motivo.
    // Análise da administração (AD2): a conta foi suspensa com a tela aberta. O
    // AuthContext recarrega a conta e a tela do modo restrito aparece sozinha.
    if (res.status === 403 && data?.detail?.code === "account_suspended") {
      window.dispatchEvent(new Event("psi:account-suspended"));
    }
    if (res.status === 402 && data?.detail?.code === "read_only") {
      window.dispatchEvent(new CustomEvent("psi:read-only", { detail: data.detail }));
    }
    if (res.status === 401 && !PUBLIC_AUTH_PATHS.includes(path)) {
      window.dispatchEvent(new Event("psi:session-expired"));
    }
    const err = new Error(`Request failed with status ${res.status}`);
    err.response = { status: res.status, data };
    throw err;
  }
  return { data, status: res.status };
}

const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body),
  put: (path, body) => request("PUT", path, body),
  delete: (path) => request("DELETE", path),
};

export function formatApiErrorDetail(detail) {
  if (detail == null) return "Algo deu errado. Tente novamente.";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((e) => (e && typeof e.msg === "string" ? e.msg : JSON.stringify(e)))
      .filter(Boolean)
      .join(" ");
  if (detail && typeof detail.msg === "string") return detail.msg;
  return String(detail);
}

export default api;
