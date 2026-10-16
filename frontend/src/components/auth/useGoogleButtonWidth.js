import { useCallback, useEffect, useState } from "react";

// Correção (varredura de UI, defeito 8): o botão do Google tinha largura fixa
// de 384 px e, no celular, passava das bordas do formulário. O Google só
// aceita largura em pixels (entre 200 e 400), não "100%", então medimos a
// área do botão e acompanhamos mudanças de tamanho (girar o celular, etc.).
// Complementares: virou um hook para o cadastro usar o mesmo cálculo do login.
//
// DESIGN (cartão de login/cadastro, AuthCard): login e cadastro passaram a
// dividir o mesmo componente, trocando de modo sem desmontar a página — mas
// o DIV do botão do Google de cada modo ainda desmonta/remonta ao trocar
// (só um existe por vez). Com `useRef`, o efeito (que roda uma vez só, ao
// montar o Login) continuava observando o elemento ANTIGO depois de um
// remonte, e o botão novo ficava com a largura inicial (384px) até a janela
// mudar de tamanho. Virou callback ref: cada vez que o nó muda (inclusive
// remontando), o efeito roda de novo e observa o nó certo.
export function useGoogleButtonWidth() {
  const [box, setBox] = useState(null);
  const [width, setWidth] = useState(384);

  useEffect(() => {
    if (!box) return undefined;
    const update = () => setWidth(Math.max(200, Math.min(400, Math.floor(box.clientWidth))));
    update();
    const observer = new ResizeObserver(update);
    observer.observe(box);
    return () => observer.disconnect();
  }, [box]);

  const boxRef = useCallback((node) => setBox(node), []);
  return [boxRef, width];
}
