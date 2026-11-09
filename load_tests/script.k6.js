// load_tests/script.k6.js
// Teste de carga/estresse do PsiGestão — SOMENTE ambiente local/staging.
//
// Uso:
//   docker run --rm -i --network <rede_do_compose> \
//     -v "$(pwd)/load_tests:/scripts" \
//     -e BASE_URL=http://api:5000 \
//     -e TEST_EMAIL=... -e TEST_PASSWORD=... \
//     grafana/k6 run /scripts/script.k6.js
//
// (rede_do_compose: ver `docker network ls | grep psigestao` depois do
// `docker compose -f docker-compose.prod.yml up -d`)

import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:5000";
const TEST_EMAIL = __ENV.TEST_EMAIL;
const TEST_PASSWORD = __ENV.TEST_PASSWORD;

// Cabeçalho exigido pelo CSRFMiddleware (backend/http_security.py) em toda
// escrita (POST/PUT/PATCH/DELETE) para /api/*. Sem ele a API responde 403
// antes de tocar no banco — não é o gargalo que queremos medir.
const WRITE_HEADERS = { "Content-Type": "application/json", "X-Requested-With": "psigestao" };

export const options = {
  scenarios: {
    // Fase 1, item 1: login é rota crítica — mede o custo do bcrypt + DB sob concorrência.
    login_stress: {
      executor: "ramping-vus",
      exec: "loginFlood",
      startVUs: 0,
      stages: [
        { duration: "1m", target: 40 },
        { duration: "2m", target: 40 },
        { duration: "30s", target: 0 },
      ],
    },
    // Rotas autenticadas: dashboard, listagem, busca e criação de evolução.
    browse_and_write: {
      executor: "ramping-vus",
      exec: "browseAndWrite",
      startVUs: 0,
      stages: [
        { duration: "1m", target: 200 },
        { duration: "2m", target: 200 },
        { duration: "30s", target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.05"],
    "http_req_duration{route:dashboard_stats}": ["p(95)<1000"],
    "http_req_duration{route:patients_list}": ["p(95)<800"],
  },
};

// setup() roda UMA vez (não por VU): loga com a conta de teste e cria um
// paciente dedicado ao teste de carga, reaproveitado por todas as VUs do
// cenário browse_and_write (evita acoplar o script ao fluxo de
// registro/confirmação por e-mail, que não serve para isto).
export function setup() {
  if (!TEST_EMAIL || !TEST_PASSWORD) {
    throw new Error("Defina TEST_EMAIL e TEST_PASSWORD (conta local já existente, com termos aceitos).");
  }

  const loginRes = http.post(`${BASE_URL}/api/auth/login`, JSON.stringify({
    email: TEST_EMAIL, password: TEST_PASSWORD,
  }), { headers: WRITE_HEADERS });

  if (loginRes.status !== 200) {
    throw new Error(`Login de setup falhou (${loginRes.status}): ${loginRes.body}`);
  }
  const cookie = extractCookie(loginRes);

  const patientRes = http.post(`${BASE_URL}/api/patients`, JSON.stringify({
    full_name: "Paciente Teste de Carga (k6)",
    consent_terms: true,
    // Cadastro exige TODOS os campos preenchidos (server.py: require_all_fields).
    cpf: "111.444.777-35", // CPF de teste com dígitos verificadores válidos
    rg: "12.345.678-9",
    birth_date: "1990-01-01",
    age: "36",
    education: "Ensino superior",
    marital_status: "Solteiro(a)",
    profession: "Testador(a) de carga",
    phone: "(11) 91234-5678",
    email: "paciente@psigestao-k6.com",
    address: "Rua de Teste, 123",
    emergency_contact: "Contato Teste (11) 90000-0000",
    initial_notes: "Paciente fictício criado pelo teste de carga k6.",
  }), { headers: { ...WRITE_HEADERS, Cookie: cookie } });

  if (patientRes.status !== 200 && patientRes.status !== 201) {
    throw new Error(`Criação do paciente de teste falhou (${patientRes.status}): ${patientRes.body}`);
  }
  const patientId = patientRes.json("id");

  return { cookie, patientId };
}

// Pega o valor bruto do cookie de sessão do header Set-Cookie, para reenviar
// manualmente (sem depender do cookie jar automático do k6, que respeita a
// flag Secure — e em ENVIRONMENT=production o cookie é Secure mesmo testando
// por HTTP simples dentro da rede Docker).
function extractCookie(res) {
  const raw = res.headers["Set-Cookie"];
  if (!raw) throw new Error("Resposta de login sem Set-Cookie.");
  return raw.split(";")[0];
}

export function loginFlood() {
  const res = http.post(`${BASE_URL}/api/auth/login`, JSON.stringify({
    email: TEST_EMAIL, password: TEST_PASSWORD,
  }), { headers: WRITE_HEADERS, tags: { route: "login" } });

  check(res, { "login: 200": (r) => r.status === 200 });
  sleep(1);
}

export function browseAndWrite(data) {
  const authHeaders = { Cookie: data.cookie };

  const dashboard = http.get(`${BASE_URL}/api/dashboard/stats`, {
    headers: authHeaders, tags: { route: "dashboard_stats" },
  });
  check(dashboard, { "dashboard: 200": (r) => r.status === 200 });

  const patients = http.get(`${BASE_URL}/api/patients?limit=20`, {
    headers: authHeaders, tags: { route: "patients_list" },
  });
  check(patients, { "patients: 200": (r) => r.status === 200 });

  const search = http.post(`${BASE_URL}/api/patients/search`, JSON.stringify({
    q: "Teste", limit: 20,
  }), { headers: { ...WRITE_HEADERS, ...authHeaders }, tags: { route: "patients_search" } });
  check(search, { "search: 200": (r) => r.status === 200 });

  // 1 em cada 5 iterações grava uma evolução (rota de escrita mais pesada: cifra + versiona + audita).
  if (Math.random() < 0.2) {
    const record = http.post(`${BASE_URL}/api/patients/${data.patientId}/records`, JSON.stringify({
      session_datetime: new Date().toISOString(),
      content: "Evolução gerada pelo teste de carga k6.",
    }), { headers: { ...WRITE_HEADERS, ...authHeaders }, tags: { route: "record_create" } });
    check(record, { "record create: 200": (r) => r.status === 200 });
  }

  sleep(1);
}
