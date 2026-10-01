// ---------------------------------------------------------------------------
// SEGURANÇA (varredura, item 2): limites, máscaras e conferências dos formulários.
//
// Antes os formulários não tinham tamanho máximo nem máscara, e um CPF ou
// telefone digitado errado só era percebido (quando era) no servidor.
// Os valores abaixo ESPELHAM os do servidor (backend/server.py: PatientInput,
// RecordInput, SessionInput...). Aqui eles orientam quem digita; quem decide
// é sempre o servidor, que recusa o que passar daqui (alguém pode chamar a
// API sem usar esta tela). Ao mudar um limite, mude nos dois lugares.
// ---------------------------------------------------------------------------

export const LIMITS = {
  patientName: 150,
  cpf: 14, // 000.000.000-00
  rg: 20,
  profession: 100,
  phone: 20,
  email: 254,
  address: 300,
  emergencyContact: 200,
  patientNotes: 10_000,
  recordContent: 50_000,
  recordDiagnosis: 5_000,
  professionalName: 120,
  loginPassword: 256,
  search: 120,
};

export const onlyDigits = (value) => String(value || "").replace(/\D/g, "");

// Formata o CPF enquanto a pessoa digita: 12345678909 -> 123.456.789-09.
export function maskCPF(value) {
  const digits = onlyDigits(value).slice(0, 11);
  let out = [digits.slice(0, 3), digits.slice(3, 6), digits.slice(6, 9)].filter(Boolean).join(".");
  if (digits.length > 9) out += `-${digits.slice(9)}`;
  return out;
}

// Telefone brasileiro: (11) 1234-5678 ou (11) 91234-5678. Número que começa
// com "+" (internacional) não é reformatado, só limpo de caracteres estranhos.
export function maskPhone(value) {
  const raw = String(value || "");
  if (raw.trim().startsWith("+")) return raw.replace(/[^\d\s()+-]/g, "").slice(0, LIMITS.phone);
  const digits = onlyDigits(raw).slice(0, 11);
  if (digits.length <= 2) return digits ? `(${digits}` : "";
  if (digits.length <= 6) return `(${digits.slice(0, 2)}) ${digits.slice(2)}`;
  if (digits.length <= 10) return `(${digits.slice(0, 2)}) ${digits.slice(2, 6)}-${digits.slice(6)}`;
  return `(${digits.slice(0, 2)}) ${digits.slice(2, 7)}-${digits.slice(7)}`;
}

// Dígitos verificadores do CPF (mesma regra do servidor: cpf_is_valid).
export function isValidCPF(value) {
  const digits = onlyDigits(value);
  if (digits.length !== 11 || /^(\d)\1{10}$/.test(digits)) return false;
  for (const size of [9, 10]) {
    let total = 0;
    for (let i = 0; i < size; i += 1) total += Number(digits[i]) * (size + 1 - i);
    if (((total * 10) % 11) % 10 !== Number(digits[size])) return false;
  }
  return true;
}

// Mensagens de problema ("" = tudo certo). Campo vazio é aceito: são opcionais.
export const cpfProblem = (value) => (!value || isValidCPF(value) ? "" : "CPF inválido. Confira os 11 números.");

export function phoneProblem(value) {
  if (!value) return "";
  const count = onlyDigits(value).length;
  return count >= 10 && count <= 15 ? "" : "Telefone inválido. Informe o DDD e o número, ex.: (11) 91234-5678.";
}

// Data de hoje no fuso local (AAAA-MM-DD), para o `max` dos campos de data.
export function todayISO() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
}
