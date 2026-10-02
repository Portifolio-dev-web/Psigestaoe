// ---------------------------------------------------------------------------
// Complementares (evoluções): modelos de texto para a evolução.
// Formatos de registro comuns na prática clínica. O modelo só preenche os
// títulos; o profissional completa (e apaga o que não usar). Não muda nada no
// servidor: o texto continua livre e criptografado como antes.
// ---------------------------------------------------------------------------
export const RECORD_TEMPLATES = [
  {
    id: "soap",
    label: "SOAP",
    description: "Subjetivo, Objetivo, Avaliação, Plano",
    text: "S (Subjetivo): \n\nO (Objetivo): \n\nA (Avaliação): \n\nP (Plano): ",
  },
  {
    id: "dap",
    label: "DAP",
    description: "Dados, Avaliação, Plano",
    text: "D (Dados): \n\nA (Avaliação): \n\nP (Plano): ",
  },
  {
    id: "roteiro",
    label: "Roteiro da sessão",
    description: "Demanda, intervenções, observações, encaminhamentos",
    text: "Demanda / tema trazido: \n\nIntervenções realizadas: \n\nObservações clínicas: \n\nEncaminhamentos / próxima sessão: ",
  },
];

/** Texto com o modelo: no campo vazio, o próprio modelo; senão, acrescentado ao fim (nada é apagado). */
export function applyTemplate(current, template) {
  return current.trim() ? `${current.replace(/\s+$/, "")}\n\n${template.text}` : template.text;
}
