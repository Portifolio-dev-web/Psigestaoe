import { Loader2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { LIMITS, cpfProblem, phoneProblem, todayISO } from "@/lib/validation";
import {
  EDUCATION_OPTIONS, EMPTY_MESSAGE, GUARDIAN_RELATIONSHIPS, MARITAL_STATUS_OPTIONS, OPTIONAL_FOR_MINORS, isMinorForm,
} from "@/lib/patientForm";

// ---------------------------------------------------------------------------
// Campos da ficha do paciente, compartilhados (produto: cadastro pelo paciente).
//
// Moravam dentro do diálogo de pages/Patients.js. O link de cadastro
// (pages/PatientIntake.js) mostra os MESMOS campos ao paciente: mesmos rótulos,
// máscaras, limites, regras para menores de idade e mensagens de erro.
//
// Acessibilidade (análise de UI, A4): erros do cadastro NO PRÓPRIO FORMULÁRIO.
// Antes os erros apareciam só num aviso que sumia em 4 s ("Faltando: Nome,
// CPF, RG..."), e os campos não ficavam marcados. Agora:
//   - um resumo fixo no topo do formulário lista o que corrigir, com atalho
//     para cada campo (e é lido pelo leitor de tela ao aparecer);
//   - cada campo com problema fica marcado (borda vermelha + aria-invalid) e
//     tem a própria mensagem, ligada a ele (aria-describedby);
//   - a mensagem some assim que a pessoa mexe no campo.
// A validação do navegador fica desligada (noValidate): ela mostrava um balão
// só para o PRIMEIRO campo vazio, que some sozinho. Os `required` continuam,
// para o leitor de tela anunciar "obrigatório". Quem decide é sempre o servidor.
// ---------------------------------------------------------------------------

// UX (cadastro de paciente): uma seção do formulário. <fieldset> + <legend>:
// o leitor de tela anuncia o grupo ("Contato") ao entrar nos campos dele.
// Duas colunas no computador, uma no celular; `sm:col-span-2` ocupa a linha toda.
export function FormSection({ title, children }) {
  return (
    <fieldset>
      <legend className="mb-3 w-full border-b border-slate-200 pb-1.5 text-xs font-semibold uppercase tracking-wide text-[#1E3A8A]">
        {title}
      </legend>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">{children}</div>
    </fieldset>
  );
}

// A4: mensagem de erro de um campo. O id ("<campo>-error") é o que o campo
// aponta em aria-describedby, para o leitor de tela ler a mensagem junto.
export function FieldError({ id, message }) {
  return message ? <p id={`${id}-error`} className="mt-1 text-xs text-red-600">{message}</p> : null;
}

// A4: resumo dos erros no topo do formulário. role="alert": o leitor de tela lê
// o resumo assim que ele aparece. Cada item leva o foco ao campo correspondente.
// `action`: o verbo do botão ("salvar" na ficha da psicóloga, "enviar" no link do paciente).
export function FormErrorSummary({ errors, submitError, fields, action = "salvar" }) {
  const entries = fields.filter(([key]) => errors[key]);
  if (!entries.length && !submitError) return null;
  return (
    <div id="patient-form-errors" role="alert" tabIndex={-1}
      className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500"
      data-testid="patient-form-errors">
      {submitError ? (
        <p>{submitError}</p>
      ) : (
        <>
          <p className="font-medium">
            {entries.length === 1 ? `Corrija 1 campo para ${action}:` : `Corrija ${entries.length} campos para ${action}:`}
          </p>
          <ul className="mt-1.5 grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-2">
            {entries.map(([key, label, id]) => (
              <li key={key}>
                <a href={`#${id}`} className="underline underline-offset-2 hover:text-red-950"
                  onClick={(e) => { e.preventDefault(); document.getElementById(id)?.focus(); }}>
                  {label}
                </a>
                {errors[key] !== EMPTY_MESSAGE && <span>: {errors[key]}</span>}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

// A4: atributos de um campo com erro: marcado como inválido e com a mensagem
// ligada a ele. `hintId`: um texto de ajuda que já descreve o campo (ex.: a idade).
export const fieldA11y = (errors, key, id, hintId) => ({
  "aria-invalid": errors[key] ? true : undefined,
  "aria-describedby": [hintId, errors[key] ? `${id}-error` : null].filter(Boolean).join(" ") || undefined,
});
export const invalidClass = (errors, key) => (errors[key] ? "border-red-500 focus-visible:ring-red-500" : "");

/** Identificação, responsável legal (menores) e contato.
 *  `selfFill`: quem preenche é o próprio paciente (link de cadastro). */
export function PatientFormFields({ form, errors = {}, onFormChange, selfFill = false }) {
  // Segurança (varredura, item 2): limites iguais aos do servidor e aviso de
  // CPF/telefone inválido já no campo. Um paciente que se cadastrou pelo link
  // pode ter digitado o CPF errado: o aviso aparece ao editar.
  // A4: o aviso ao vivo (enquanto digita) ou o erro do "Salvar" (ex.: campo vazio).
  const cpfError = cpfProblem(form.cpf) || errors.cpf;
  const phoneError = phoneProblem(form.phone) || errors.phone;
  // C3: paciente menor de 18 anos (pela data de nascimento digitada).
  const minor = isMinorForm(form);
  const guardianCpfError = cpfProblem(form.guardian_cpf) || errors.guardian_cpf;
  const guardianPhoneError = phoneProblem(form.guardian_phone) || errors.guardian_phone;
  // "*" nos obrigatórios; "(opcional)" no que o menor pode não ter.
  const mark = (key) => (minor && OPTIONAL_FOR_MINORS.includes(key) ? " (opcional)" : " *");
  // Acessibilidade (WCAG 1.3.5): no link do paciente, os campos dizem ao navegador o que
  // pedem (nome, telefone...) e ele oferece os dados do PRÓPRIO paciente. Na ficha da
  // psicóloga não: o navegador sugeriria os dados dela (o formulário tem autoComplete="off").
  const auto = (token) => (selfFill ? { autoComplete: token } : {});
  return (
    <>
      <FormSection title="Identificação">
        <div className="sm:col-span-2">
          <Label htmlFor="patient-name">Nome completo *</Label>
          <Input
            id="patient-name"
            {...auto("name")}
            {...fieldA11y(errors, "full_name", "patient-name")}
            required
            maxLength={LIMITS.patientName}
            value={form.full_name}
            onChange={onFormChange("full_name")}
            className={`mt-1.5 ${invalidClass(errors, "full_name")}`}
            data-testid="patient-name-input"
          />
          <FieldError id="patient-name" message={errors.full_name} />
        </div>

        <div>
          <Label htmlFor="patient-cpf">CPF *</Label>
          <Input
            id="patient-cpf"
            required
            inputMode="numeric"
            maxLength={LIMITS.cpf}
            value={form.cpf}
            onChange={onFormChange("cpf")}
            className={`mt-1.5 ${cpfError ? "border-red-500 focus-visible:ring-red-500" : ""}`}
            aria-invalid={Boolean(cpfError)}
            aria-describedby={cpfError ? "patient-cpf-error" : undefined}
            data-testid="patient-cpf-input"
            placeholder="000.000.000-00"
          />
          <FieldError id="patient-cpf" message={cpfError} />
        </div>

        <div>
          <Label htmlFor="patient-rg">RG{mark("rg")}</Label>
          <Input
            id="patient-rg"
            {...fieldA11y(errors, "rg", "patient-rg")}
            required={!minor}
            maxLength={LIMITS.rg}
            value={form.rg}
            onChange={onFormChange("rg")}
            className={`mt-1.5 ${invalidClass(errors, "rg")}`}
            data-testid="patient-rg-input"
          />
          <FieldError id="patient-rg" message={errors.rg} />
        </div>

        <div>
          <Label htmlFor="patient-birth-date">Data de nascimento *</Label>
          <Input
            id="patient-birth-date"
            {...auto("bday")}
            {...fieldA11y(errors, "birth_date", "patient-birth-date", "patient-age-hint")}
            required
            type="date"
            min="1900-01-01"
            max={todayISO()}
            value={form.birth_date}
            onChange={onFormChange("birth_date")}
            className={`mt-1.5 ${invalidClass(errors, "birth_date")}`}
            data-testid="patient-birthdate-input"
          />
          <FieldError id="patient-birth-date" message={errors.birth_date} />
          {/* PRODUTO: a idade é calculada pela data de nascimento. Antes havia um
              campo "Idade" (primeiro digitado, depois só de leitura) que ocupava
              uma linha inteira para repetir a data; agora ela aparece aqui. */}
          <p id="patient-age-hint" className="mt-1 text-xs text-slate-500" aria-live="polite" data-testid="patient-age-hint">
            {form.age !== "" && form.age != null ? `Idade: ${form.age} ${Number(form.age) === 1 ? "ano" : "anos"}` : "A idade é calculada pela data."}
          </p>
        </div>

        {/* C3: estado civil e profissão não se aplicam a menores de 18 anos. */}
        {!minor && (
        <div>
          <Label htmlFor="patient-marital-status">Estado civil *</Label>
          <NativeSelect
            id="patient-marital-status"
            {...fieldA11y(errors, "marital_status", "patient-marital-status")}
            required
            value={form.marital_status}
            onChange={onFormChange("marital_status")}
            className="mt-1.5" selectClassName={invalidClass(errors, "marital_status")}
          >
            <option value="">Selecione...</option>
            {MARITAL_STATUS_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
          </NativeSelect>
          <FieldError id="patient-marital-status" message={errors.marital_status} />
        </div>
        )}

        <div>
          {/* Correção (varredura de UI, defeito 5): o "*" aparecia sem o campo ser
              exigido. Agora TODOS os campos são obrigatórios (aqui e no servidor). */}
          <Label htmlFor="patient-education">Escolaridade *</Label>
          <NativeSelect
            id="patient-education"
            {...fieldA11y(errors, "education", "patient-education")}
            required
            value={form.education}
            onChange={onFormChange("education")}
            className="mt-1.5" selectClassName={invalidClass(errors, "education")}
          >
            <option value="">Selecione...</option>
            {EDUCATION_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
          </NativeSelect>
          <FieldError id="patient-education" message={errors.education} />
        </div>

        {!minor && (
        <div>
          <Label htmlFor="patient-profession">Profissão *</Label>
          <Input
            id="patient-profession"
            {...fieldA11y(errors, "profession", "patient-profession")}
            required
            maxLength={LIMITS.profession}
            value={form.profession}
            onChange={onFormChange("profession")}
            className={`mt-1.5 ${invalidClass(errors, "profession")}`}
            data-testid="patient-profession-input"
          />
          <FieldError id="patient-profession" message={errors.profession} />
        </div>
        )}
      </FormSection>

      {/* C3: responsável legal, obrigatório para menores de 18 anos. */}
      {minor && (
        <FormSection title="Responsável legal">
          <p className="rounded-md bg-blue-50 px-3 py-2 text-sm text-[#0F2C59] sm:col-span-2" data-testid="patient-minor-note">
            Paciente menor de 18 anos: o responsável legal é o contato de referência e quem consente o atendimento.
          </p>
          <div className="sm:col-span-2">
            <Label htmlFor="patient-guardian-name">Nome do responsável *</Label>
            <Input id="patient-guardian-name" {...fieldA11y(errors, "guardian_name", "patient-guardian-name")} required
              maxLength={LIMITS.patientName} value={form.guardian_name} onChange={onFormChange("guardian_name")}
              className={`mt-1.5 ${invalidClass(errors, "guardian_name")}`} data-testid="patient-guardian-name-input" />
            <FieldError id="patient-guardian-name" message={errors.guardian_name} />
          </div>
          <div>
            <Label htmlFor="patient-guardian-cpf">CPF do responsável *</Label>
            <Input id="patient-guardian-cpf" required inputMode="numeric" maxLength={LIMITS.cpf}
              value={form.guardian_cpf} onChange={onFormChange("guardian_cpf")} placeholder="000.000.000-00"
              aria-invalid={Boolean(guardianCpfError)} aria-describedby={guardianCpfError ? "patient-guardian-cpf-error" : undefined}
              className={`mt-1.5 ${guardianCpfError ? "border-red-500 focus-visible:ring-red-500" : ""}`}
              data-testid="patient-guardian-cpf-input" />
            <FieldError id="patient-guardian-cpf" message={guardianCpfError} />
          </div>
          <div>
            <Label htmlFor="patient-guardian-relationship">Parentesco *</Label>
            <NativeSelect id="patient-guardian-relationship" {...fieldA11y(errors, "guardian_relationship", "patient-guardian-relationship")}
              required value={form.guardian_relationship} onChange={onFormChange("guardian_relationship")}
              className="mt-1.5" selectClassName={invalidClass(errors, "guardian_relationship")}>
              <option value="">Selecione...</option>
              {GUARDIAN_RELATIONSHIPS.map((option) => <option key={option} value={option}>{option}</option>)}
            </NativeSelect>
            <FieldError id="patient-guardian-relationship" message={errors.guardian_relationship} />
          </div>
          <div>
            <Label htmlFor="patient-guardian-phone">Telefone do responsável *</Label>
            <Input id="patient-guardian-phone" required type="tel" inputMode="tel" maxLength={LIMITS.phone}
              value={form.guardian_phone} onChange={onFormChange("guardian_phone")} placeholder="(11) 91234-5678"
              aria-invalid={Boolean(guardianPhoneError)} aria-describedby={guardianPhoneError ? "patient-guardian-phone-error" : undefined}
              className={`mt-1.5 ${guardianPhoneError ? "border-red-500 focus-visible:ring-red-500" : ""}`}
              data-testid="patient-guardian-phone-input" />
            <FieldError id="patient-guardian-phone" message={guardianPhoneError} />
          </div>
        </FormSection>
      )}

      <FormSection title="Contato">
        <div>
          <Label htmlFor="patient-phone">Telefone{mark("phone")}</Label>
          <Input
            id="patient-phone"
            {...auto("tel")}
            required={!minor}
            type="tel"
            inputMode="tel"
            maxLength={LIMITS.phone}
            value={form.phone}
            onChange={onFormChange("phone")}
            className={`mt-1.5 ${phoneError ? "border-red-500 focus-visible:ring-red-500" : ""}`}
            aria-invalid={Boolean(phoneError)}
            aria-describedby={phoneError ? "patient-phone-error" : undefined}
            data-testid="patient-phone-input"
            placeholder="(11) 91234-5678"
          />
          <FieldError id="patient-phone" message={phoneError} />
        </div>

        <div>
          <Label htmlFor="patient-email">E-mail{mark("email")}</Label>
          <Input
            id="patient-email"
            {...auto("email")}
            {...fieldA11y(errors, "email", "patient-email")}
            required={!minor}
            type="email"
            inputMode="email"
            maxLength={LIMITS.email}
            value={form.email}
            onChange={onFormChange("email")}
            className={`mt-1.5 ${invalidClass(errors, "email")}`}
            data-testid="patient-email-input"
          />
          <FieldError id="patient-email" message={errors.email} />
        </div>

        <div className="sm:col-span-2">
          <Label htmlFor="patient-address">Endereço *</Label>
          <Input
            id="patient-address"
            {...auto("street-address")}
            {...fieldA11y(errors, "address", "patient-address")}
            required
            maxLength={LIMITS.address}
            value={form.address}
            onChange={onFormChange("address")}
            className={`mt-1.5 ${invalidClass(errors, "address")}`}
            placeholder="Rua, número, bairro, cidade"
            data-testid="patient-address-input"
          />
          <FieldError id="patient-address" message={errors.address} />
        </div>

        <div className="sm:col-span-2">
          <Label htmlFor="patient-emergency">Contato de emergência *</Label>
          <Input
            id="patient-emergency"
            {...fieldA11y(errors, "emergency_contact", "patient-emergency")}
            required
            maxLength={LIMITS.emergencyContact}
            value={form.emergency_contact}
            onChange={onFormChange("emergency_contact")}
            className={`mt-1.5 ${invalidClass(errors, "emergency_contact")}`}
            placeholder="Nome, parentesco e telefone"
            data-testid="patient-emergency-input"
          />
          <FieldError id="patient-emergency" message={errors.emergency_contact} />
        </div>
      </FormSection>
    </>
  );
}

/** Texto do termo de consentimento (com rolagem própria) e a data do aceite, se houver. */
export function ConsentTermText({ text, consentDate = "" }) {
  return (
    <div>
      <p className="text-sm font-semibold text-[#0F172A]">Termo de consentimento livre e esclarecido</p>
      {text ? (
        // Acessibilidade (varredura de UI): texto com rolagem precisa receber foco
        // (tabIndex), senão quem usa só o teclado não consegue rolar e ler o termo inteiro.
        <p tabIndex={0} role="region" aria-label="Texto do termo de consentimento"
          className="psi-scroll mt-1 max-h-48 overflow-y-auto whitespace-pre-line text-xs leading-relaxed text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]">{text}</p>
      ) : (
        <div className="flex justify-center py-3"><Loader2 className="h-4 w-4 animate-spin text-[#1E3A8A]" /></div>
      )}
      {consentDate && (
        <p className="mt-2 text-xs font-medium text-emerald-700">Consentimento registrado em {consentDate}.</p>
      )}
    </div>
  );
}

/** A pergunta "concorda com o termo?" (Concordo / Não concordo, sem opção pré-marcada). */
export function ConsentQuestion({ form, errors = {}, onFormChange, legend }) {
  // PRODUTO: a resposta é obrigatória e explícita. Antes "Não concordo" já
  // vinha marcado, e a ficha podia ser salva sem a pergunta ser respondida.
  // Celular: opções com área de toque maior (linha inteira, 44 px).
  return (
    <fieldset className="space-y-1">
      <legend className="text-sm font-medium text-[#0F172A]">{legend}</legend>
      <FieldError id="patient-consent" message={errors.consent_terms} />
      <label className="flex min-h-[44px] items-center gap-2 text-sm text-slate-700 sm:min-h-0 sm:py-1">
        <input
          id="patient-consent-yes"
          type="radio"
          name="consent_terms"
          value="true"
          checked={form.consent_terms === true}
          onChange={onFormChange("consent_terms")}
          required
          {...fieldA11y(errors, "consent_terms", "patient-consent")}
        />
        Concordo
      </label>
      <label className="flex min-h-[44px] items-center gap-2 text-sm text-slate-700 sm:min-h-0 sm:py-1">
        <input
          type="radio"
          name="consent_terms"
          value="false"
          checked={form.consent_terms === false}
          onChange={onFormChange("consent_terms")}
          {...fieldA11y(errors, "consent_terms", "patient-consent")}
        />
        Não concordo
      </label>
    </fieldset>
  );
}
