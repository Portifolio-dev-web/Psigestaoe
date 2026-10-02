import { clsx } from "clsx";
import { twMerge } from "tailwind-merge"

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

/**
 * "1 sessão" / "3 sessões": número + palavra no singular ou no plural.
 * Correção (varredura de UI, defeito 9): os textos usavam "sessão(ões)",
 * "outra(s)", "encerrada(s)", que soam como formulário de cartório. Passe a
 * expressão inteira em cada forma: countLabel(n, "sessão encerrada", "sessões encerradas").
 */
export function countLabel(count, singular, plural) {
  return `${count} ${count === 1 ? singular : plural}`;
}
