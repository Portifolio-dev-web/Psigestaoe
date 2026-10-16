import React from "react";
import ReactDOM from "react-dom/client";
// 1. Importar o provedor do Google OAuth
import { GoogleOAuthProvider } from "@react-oauth/google";
// Complementares (privacidade): fontes servidas pelo PRÓPRIO app (pacotes
// @fontsource). Antes vinham do Google Fonts, e cada abertura do sistema
// entregava ao Google o IP de quem estava usando. Só os pesos usados na tela.
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/manrope/400.css";
import "@fontsource/manrope/600.css";
import "@fontsource/manrope/700.css";
import "@fontsource/manrope/800.css";
import "@/index.css";
import App from "@/App";
import ErrorBoundary from "@/components/ErrorBoundary";
import { isPatientIntakePage } from "@/lib/patientIntakeRoute";
import { initSentry } from "@/lib/sentry";

// DEPLOY (varredura, item I2): captura erros do navegador (desligado sem
// REACT_APP_SENTRY_DSN; ver lib/sentry.js). Chamado antes do render para
// pegar também falhas bem no início; o import do pacote é dinâmico, então
// sem DSN configurado nem o código do Sentry entra no bundle carregado.
initSentry();

// Desempenho (análise de componentes sem uso): o @tanstack/react-query envolvia
// o app inteiro (QueryClientProvider), mas nenhuma tela usava useQuery nem
// useMutation: os dados vêm direto da lib/api.js. Saiu o provedor e o pacote,
// 7,8 KB comprimidos a menos no carregamento de TODA tela, inclusive o login.

// 2. Configure a variável de ambiente correspondente ao seu Client ID do Google Cloud Console
// (se estiver a utilizar o Vite, será import.meta.env.VITE_GOOGLE_CLIENT_ID)
const GOOGLE_CLIENT_ID = process.env.REACT_APP_GOOGLE_CLIENT_ID || "COLOQUE_AQUI_O_SEU_CLIENT_ID";

const root = ReactDOM.createRoot(document.getElementById("root"));
// Produto (cadastro pelo paciente): a página do link de cadastro (/cadastro) abre SEM
// o provedor do Google. Ele baixa o script de login do Google assim que a página
// carrega, e o IP do paciente iria para o Google sem motivo (o paciente não faz login).
// DEPLOY (varredura, item D1): por fora de tudo — se o provedor do Google ou
// o próprio App quebrarem no meio do render, ainda cai na tela de erro, não
// numa página em branco (ver components/ErrorBoundary.jsx).
root.render(
  <ErrorBoundary>
    {isPatientIntakePage() ? (
      <App />
    ) : (
      // 3. Envolver o App com o Provider
      <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>
        <App />
      </GoogleOAuthProvider>
    )}
  </ErrorBoundary>,
);