import { useEffect, useRef, useState } from "react";

// Correção (varredura de UI, defeito 8): o botão do Google tinha largura fixa
// de 384 px e, no celular, passava das bordas do formulário. O Google só
// aceita largura em pixels (entre 200 e 400), não "100%", então medimos a
// área do botão e acompanhamos mudanças de tamanho (girar o celular, etc.).
// Complementares: virou um hook para o cadastro usar o mesmo cálculo do login.
export function useGoogleButtonWidth() {
  const boxRef = useRef(null);
  const [width, setWidth] = useState(384);
  useEffect(() => {
    const box = boxRef.current;
    if (!box) return undefined;
    const update = () => setWidth(Math.max(200, Math.min(400, Math.floor(box.clientWidth))));
    update();
    const observer = new ResizeObserver(update);
    observer.observe(box);
    return () => observer.disconnect();
  }, []);
  return [boxRef, width];
}
