import { ageFromBirthDate } from "@/lib/datetime";
import { cpfProblem, maskCPF, maskPhone, phoneProblem, todayISO } from "@/lib/validation";

// ---------------------------------------------------------------------------
// Regras da ficha do paciente, compartilhadas (produto: cadastro pelo paciente).
//
// Moravam em pages/Patients.js. O link de cadastro (pages/PatientIntake.js) usa
// as MESMAS regras: a psicóloga e o paciente veem os mesmos campos obrigatórios
// e as mesmas mensagens, e nenhuma cópia fica para trás quando uma regra muda.
// O servidor confere tudo de novo (PatientInput / IntakeSubmitData em server.py).
// Diferença do paciente: "Notas iniciais" é anotação clínica da psicóloga e não
// aparece no link ({ includeNotes: false }).
// ---------------------------------------------------------------------------

// PRODUTO: TODOS os campos do cadastro são obrigatórios. Mesma lista do
// servidor (PATIENT_REQUIRED_LABELS em backend/server.py). [campo, rótulo, id do campo]
// A idade não está aqui: é calculada pela data de nascimento.
// Na mesma ordem da tela: o foco vai para o primeiro campo vazio que a pessoa vê.
export const REQUIRED_FIELDS = [
  ["full_name", "Nome completo", "patient-name"],
  ["cpf", "CPF", "patient-cpf"],
  ["rg", "RG", "patient-rg"],
  ["birth_date", "Data de nascimento", "patient-birth-date"],
  ["marital_status", "Estado civil", "patient-marital-status"],
  ["education", "Escolaridade", "patient-education"],
  ["profession", "Profissão", "patient-profession"],
  ["phone", "Telefone", "patient-phone"],
  ["email", "E-mail", "patient-email"],
  ["address", "Endereço", "patient-address"],
  ["emergency_contact", "Contato de emergência", "patient-emergency"],
  ["initial_notes", "Notas iniciais", "patient-notes"],
];

// Fluxo clínico (C3): paciente MENOR de 18 anos (mesmas regras do servidor,
// PATIENT_MINOR_REQUIRED_LABELS): saem estado civil e profissão; RG, e-mail e
// telefone do menor ficam opcionais; entram os dados do responsável legal.
export const ADULT_AGE = 18;
export const GUARDIAN_FIELDS = [
  ["guardian_name", "Nome do responsável", "patient-guardian-name"],
  ["guardian_cpf", "CPF do responsável", "patient-guardian-cpf"],
  ["guardian_relationship", "Parentesco do responsável", "patient-guardian-relationship"],
  ["guardian_phone", "Telefone do responsável", "patient-guardian-phone"],
];
export const OPTIONAL_FOR_MINORS = ["rg", "phone", "email"];
export const HIDDEN_FOR_MINORS = ["marital_status", "profession"];
export const GUARDIAN_RELATIONSHIPS = ["Mãe", "Pai", "Avó", "Avô", "Tutor(a) legal", "Guardião(ã) legal", "Outro"];
// Listas de seleção (as mesmas da ficha da psicóloga).
export const MARITAL_STATUS_OPTIONS = ["Solteiro(a)", "Casado(a)", "Divorciado(a)", "Viúvo(a)", "União estável"];
export const EDUCATION_OPTIONS = [
  "Fundamental - Incompleto", "Fundamental - Completo", "Médio - Incompleto", "Médio - Completo",
  "Superior - Incompleto", "Superior - Completo", "Pós Graduação - Completo",
];

/** O paciente do formulário é menor de idade (pela data de nascimento)? */
export const isMinorForm = (form) => {
  const age = ageFromBirthDate(form.birth_date);
  return age !== null && age !== undefined && age < ADULT_AGE;
};

/** Campos obrigatórios na ORDEM DA TELA (o foco vai para o primeiro com problema). */
export function requiredFields(form, { includeNotes = true } = {}) {
  const base = includeNotes ? REQUIRED_FIELDS : REQUIRED_FIELDS.filter(([key]) => key !== "initial_notes");
  if (!isMinorForm(form)) return base;
  const identification = base.filter(([key]) => ["full_name", "cpf", "birth_date", "education"].includes(key));
  const rest = base.filter(([key]) => ["address", "emergency_contact", "initial_notes"].includes(key));
  return [...identification, ...GUARDIAN_FIELDS, ...rest];
}

// Espaços em branco contam como vazio (o `required` do navegador aceitaria "   ").
const missingFields = (form, options) => requiredFields(form, options).filter(([key]) => !String(form[key] ?? "").trim());

export const CONSENT_FIELD = ["consent_terms", "Termo de consentimento", "patient-consent-yes"];
/** Ordem da tela para levar o foco ao primeiro problema (C3: muda para menores). */
export const errorFields = (form, options) => [...requiredFields(form, options), CONSENT_FIELD];
export const EMPTY_MESSAGE = "Preencha este campo.";
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** { campo: mensagem } com TODOS os problemas do formulário (vazio = nenhum). */
export function validatePatientForm(form, options) {
  const errors = {};
  missingFields(form, options).forEach(([key]) => { errors[key] = EMPTY_MESSAGE; });
  if (!errors.cpf && cpfProblem(form.cpf)) errors.cpf = cpfProblem(form.cpf);
  if (!errors.phone && phoneProblem(form.phone)) errors.phone = phoneProblem(form.phone);
  // C3: documentos e telefone do responsável seguem as mesmas regras.
  if (!errors.guardian_cpf && cpfProblem(form.guardian_cpf)) errors.guardian_cpf = cpfProblem(form.guardian_cpf);
  if (!errors.guardian_phone && phoneProblem(form.guardian_phone)) errors.guardian_phone = phoneProblem(form.guardian_phone);
  // C3: e-mail é opcional para menores; preenchido, precisa ser válido.
  const email = String(form.email || "").trim();
  if (!errors.email && email && !EMAIL_PATTERN.test(email)) errors.email = "E-mail inválido. Ex.: nome@exemplo.com.";
  if (!errors.birth_date && (form.birth_date < "1900-01-01" || form.birth_date > todayISO())) {
    errors.birth_date = "Data de nascimento inválida.";
  }
  // PRODUTO: a pergunta sobre o termo precisa de resposta (não há opção pré-marcada).
  if (typeof form.consent_terms !== "boolean") errors.consent_terms = "Escolha “Concordo” ou “Não concordo”.";
  return errors;
}

// consent_terms começa SEM resposta (null): a pessoa precisa escolher
// "Concordo" ou "Não concordo" (antes "Não concordo" já vinha marcado).
export const EMPTY_FORM = {
  full_name: "", cpf: "", rg: "", birth_date: "", age: "",
  education: "", marital_status: "", profession: "", phone: "", email: "",
  address: "", emergency_contact: "", initial_notes: "", consent_terms: null,
  // C3: responsável legal (menores de 18 anos).
  guardian_name: "", guardian_cpf: "", guardian_relationship: "", guardian_phone: "",
};

// Segurança (varredura, item 2): CPF e telefone ganham máscara enquanto a pessoa digita.
export const FIELD_MASKS = { cpf: maskCPF, phone: maskPhone, guardian_cpf: maskCPF, guardian_phone: maskPhone };

/** Valor digitado já com máscara (e a idade acompanhando a data de nascimento). */
export function applyFieldChange(form, key, rawValue) {
  return {
    ...form,
    [key]: key === "consent_terms" ? rawValue === "true" || rawValue === true : (FIELD_MASKS[key] ? FIELD_MASKS[key](rawValue) : rawValue),
    // PRODUTO: a idade acompanha a data de nascimento (não é mais digitada).
    ...(key === "birth_date" ? { age: String(ageFromBirthDate(rawValue) ?? "") } : {}),
  };
}

/** Para menores, estado civil e profissão não se aplicam (campos escondidos): vão vazios. */
export const payloadForSubmit = (form) =>
  (isMinorForm(form) ? { ...form, ...Object.fromEntries(HIDDEN_FOR_MINORS.map((key) => [key, ""])) } : form);
