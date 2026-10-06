import { useEffect, useRef, useState } from "react";
import { Check, ChevronsUpDown, Loader2 } from "lucide-react";
import api from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Command, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { LIMITS } from "@/lib/validation";

// ESCALA: seletor de paciente com busca NO SERVIDOR.
//
// Antes, a agenda baixava TODOS os pacientes ao abrir, só para montar uma
// lista suspensa. Agora:
//   - ao abrir, mostra os 20 cadastrados mais recentemente;
//   - ao digitar 2+ letras, busca pelo índice cego (POST /patients/search,
//     com o nome no corpo da requisição, nunca na URL). Análise de UI (P5):
//     o mesmo campo aceita telefone e CPF.
// `value` é o id escolhido; `valueLabel`, o nome a exibir (vem da própria
// sessão ao editar, sem precisar buscar o paciente de novo).
// `id`: liga o seletor ao <Label htmlFor> de quem o usa (acessibilidade, varredura de UI).
export default function PatientPicker({ id, value, valueLabel, onChange }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const requestSeq = useRef(0); // descarta respostas que chegam fora de ordem

  useEffect(() => {
    if (!open) return undefined;
    const term = query.trim();
    const timer = setTimeout(async () => {
      const seq = ++requestSeq.current;
      setLoading(true);
      try {
        const { data } = term.length >= 2
          ? await api.post("/patients/search", { q: term, limit: 20 })
          : await api.get("/patients?limit=20");
        if (seq === requestSeq.current) setResults(data.items);
      } catch (err) {
        if (seq === requestSeq.current) setResults([]);
      } finally {
        if (seq === requestSeq.current) setLoading(false);
      }
    }, term ? 300 : 0); // espera a pessoa parar de digitar antes de consultar
    return () => clearTimeout(timer);
  }, [query, open]);

  const choose = (patient) => {
    onChange(patient);
    setOpen(false);
    setQuery("");
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        {/* Acessibilidade (varredura de UI): um "combobox" não usa o texto de dentro
            como nome; sem id ligado ao rótulo, o leitor de tela anunciava só "caixa de combinação". */}
        <Button id={id} type="button" variant="outline" role="combobox" aria-expanded={open}
          className="mt-1.5 w-full justify-between font-normal" data-testid="session-patient-select">
          <span className="truncate">{value ? valueLabel || "Paciente selecionado" : "Sem paciente"}</span>
          <ChevronsUpDown className="h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[var(--radix-popover-trigger-width)] p-0" align="start">
        <Command shouldFilter={false}>
          {/* Segurança (varredura, item 2): o servidor aceita buscas de até 120 caracteres. */}
          <CommandInput placeholder="Nome, telefone ou CPF..." value={query} onValueChange={setQuery} maxLength={LIMITS.search} />
          <CommandList>
            <CommandGroup>
              <CommandItem value="__sem_paciente__" onSelect={() => choose(null)}>
                <Check className={`mr-2 h-4 w-4 ${value ? "opacity-0" : "opacity-100"}`} /> Sem paciente
              </CommandItem>
              {results.map((patient) => (
                <CommandItem key={patient.id} value={patient.id} onSelect={() => choose(patient)}>
                  <Check className={`mr-2 h-4 w-4 ${value === patient.id ? "opacity-100" : "opacity-0"}`} />
                  {patient.full_name}
                </CommandItem>
              ))}
            </CommandGroup>
            {loading && (
              <div className="flex justify-center py-3"><Loader2 className="h-4 w-4 animate-spin text-[#1E3A8A]" /></div>
            )}
            {!loading && query.trim().length >= 2 && results.length === 0 && (
              <p className="py-3 text-center text-sm text-slate-500">Nenhum paciente encontrado.</p>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
