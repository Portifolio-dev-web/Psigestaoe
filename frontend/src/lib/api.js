export const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

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
