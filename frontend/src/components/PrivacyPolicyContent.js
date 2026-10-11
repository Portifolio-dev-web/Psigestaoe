import { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import { Loader2 } from "lucide-react";

// Documentos legais (Política de Privacidade e Termos de Uso): busca e exibição.
//
// O texto e a versão vêm do backend (backend/legal.py), que é a fonte ÚNICA.
// Assim a versão que o usuário lê aqui é exatamente a que o servidor exige no
// aceite (POST /auth/accept-terms). Não copie os textos para o frontend: duas
// cópias acabam divergindo, e o aceite deixa de provar o que foi lido.
// Análise da administração (AD1): os Termos de Uso entraram com a MESMA versão
// da política; um aceite cobre os dois.
export const LEGAL_DOCUMENTS = {
  privacy: { path: "/legal/privacy-policy", name: "a Política de Privacidade" },
  terms: { path: "/legal/terms-of-use", name: "os Termos de Uso" },
};

export function useLegalDocument(kind) {
  const [content, setContent] = useState(null);
  const [error, setError] = useState(false);

  const reload = useCallback(async () => {
    setError(false);
    try {
      const { data } = await api.get(LEGAL_DOCUMENTS[kind].path);
      setContent(data);
    } catch (e) {
      setError(true);
    }
  }, [kind]);

  useEffect(() => { reload(); }, [reload]);

  return { policy: content, error, reload };
}

export const usePrivacyPolicy = () => useLegalDocument("privacy");
export const useTermsOfUse = () => useLegalDocument("terms");

// `idPrefix`: dois documentos na mesma tela (aceite) não podem repetir os ids dos títulos.
export function PolicySections({ policy, error, documentName = "a Política de Privacidade", idPrefix = "policy" }) {
  if (error) {
    return <p className="py-8 text-center text-sm text-red-600">Não foi possível carregar {documentName}. Tente novamente.</p>;
  }
  if (!policy) {
    return <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin text-[#1E3A8A]" /></div>;
  }
  return (
    <div className="space-y-6">
      <p className="text-xs text-slate-500">
        Versão {policy.version} · atualizada em {policy.updated_at}
      </p>
      {policy.sections.map((section, index) => (
        <section key={section.title} aria-labelledby={`${idPrefix}-section-${index}`}>
          <h2 id={`${idPrefix}-section-${index}`} className="font-head text-base font-bold text-[#0F172A]">
            {section.title}
          </h2>
          <div className="mt-2 space-y-2">
            {section.paragraphs.map((paragraph, i) => (
              <p key={i} className="text-sm leading-relaxed text-slate-600">{paragraph}</p>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
