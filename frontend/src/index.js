import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    },
  },
});

// 2. Configure a variável de ambiente correspondente ao seu Client ID do Google Cloud Console
// (se estiver a utilizar o Vite, será import.meta.env.VITE_GOOGLE_CLIENT_ID)
const GOOGLE_CLIENT_ID = process.env.REACT_APP_GOOGLE_CLIENT_ID || "COLOQUE_AQUI_O_SEU_CLIENT_ID";

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <QueryClientProvider client={queryClient}>
    {/* 3. Envolver o App com o Provider */}
    <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>
      <App />
    </GoogleOAuthProvider>
  </QueryClientProvider>,
);