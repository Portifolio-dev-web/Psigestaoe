// ---------------------------------------------------------------------------
// Acessibilidade (análise de UI, A1): título de cada tela (aba do navegador,
// histórico e o que o leitor de tela anuncia ao trocar de tela).
// Antes TODAS as telas tinham o mesmo título ("PsiGestão — Prontuários de
// Psicologia"), e quem usa leitor de tela não sabia em que tela estava.
//
// PRIVACIDADE: o título NUNCA leva o nome do paciente ("Ficha do paciente", e
// não "Mariana Souza"). O título aparece na aba, na barra de tarefas, no
// histórico do navegador e em capturas de tela compartilhadas.
// Mantenha os nomes iguais aos títulos (h1) das telas.
// ---------------------------------------------------------------------------
const APP_NAME = "PsiGestão";

const PAGES = [
  [/^\/login\/?$/, "Entrar"],
  [/^\/register\/?$/, "Criar conta"],
  [/^\/esqueci-senha\/?$/, "Esqueci minha senha"],
  [/^\/criar-senha\/?$/, "Criar sua senha"],
  [/^\/redefinir-senha\/?$/, "Criar nova senha"],
  [/^\/privacidade\/?$/, "Política de Privacidade"],
  [/^\/termos\/?$/, "Termos de Uso"],  // análise da administração (AD1)
  [/^\/cadastro\/?$/, "Cadastro do paciente"],  // produto: link de cadastro (página do paciente)
  [/^\/dashboard\/?$/, "Painel"],  // F3: em português (o endereço continua /dashboard)
  [/^\/pacientes\/?$/, "Pacientes"],
  [/^\/pacientes\/[^/]+\/?$/, "Ficha do paciente"],
  [/^\/agenda\/?$/, "Agenda"],
  [/^\/configuracoes\/?$/, "Configurações"],
  [/^\/ajuda\/?$/, "Ajuda"],  // análise de UI, O2
  [/^\/assinatura\/simulacao\/?$/, "Pagamento simulado"],  // produto: assinatura (desenvolvimento)
  [/^\/administracao\/?$/, "Administração"],  // produto: administração
  // DEPLOY (varredura, item D2): precisa ser o ÚLTIMO (qualquer endereço que
  // não bateu em nenhum padrão acima) — a página 404 em si.
  [/^\/.*/, "Página não encontrada"],
];

/** { name: "Pacientes", full: "Pacientes · PsiGestão" } para o endereço dado. */
export function pageTitle(pathname) {
  const match = PAGES.find(([pattern]) => pattern.test(pathname));
  const name = match ? match[1] : "";
  return { name, full: name ? `${name} · ${APP_NAME}` : `${APP_NAME} — Prontuários de Psicologia` };
}
