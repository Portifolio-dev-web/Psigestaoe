import { useEffect, useState } from "react";

// ---------------------------------------------------------------------------
// Celular (UX): a tela casa com a media query? Acompanha giros do aparelho e
// redimensionamento da janela (antes a agenda só olhava a largura ao abrir).
// Ex.: useMediaQuery("(min-width: 768px)") -> true no computador/tablet.
// ---------------------------------------------------------------------------
const matchesNow = (query) => (typeof window !== "undefined" && window.matchMedia
  ? window.matchMedia(query).matches
  : false);

export function useMediaQuery(query) {
  const [matches, setMatches] = useState(() => matchesNow(query));

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return undefined;
    const media = window.matchMedia(query);
    const onChange = () => setMatches(media.matches);
    onChange(); // a query pode ter mudado entre o render e o efeito
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}

// Mesmo limite do Tailwind `md`: abaixo disso é celular.
export const WIDE_SCREEN = "(min-width: 768px)";
