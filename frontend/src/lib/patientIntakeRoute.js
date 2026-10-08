// ---------------------------------------------------------------------------
// Produto (cadastro pelo paciente): a página do PACIENTE (/cadastro, aberta pelo
// link temporário) carrega sem o resto do sistema:
//   - sem o provedor do Google (index.js): o script de login do Google seria
//     baixado e entregaria ao Google o IP de quem só veio preencher a ficha;
//   - sem a conta da psicóloga (App.js): nada de /auth/me, bloqueio por
//     inatividade ou aceite da política, que são da psicóloga e não do paciente.
// ---------------------------------------------------------------------------
export const PATIENT_INTAKE_PATH = "/cadastro";

export const isPatientIntakePage = (pathname = window.location.pathname) => /^\/cadastro\/?$/.test(pathname);
