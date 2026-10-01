"""
Simulador de Memória Virtual com Paginação -Emanuele Schlemmer Thomazzoni
Disciplina: Análise e Aplicação de Sistemas Operacionais
Universidade do Vale do Rio dos Sinos (UNISINOS)

Especificações do sistema Simulado:
  - Memória principal : 64 KB, 8 frames  de 8 KB cada
  - Memória virtual   : 1 MB,  128 páginas de 8 KB cada
  - Algoritmo de substituição: LRU (Least Recently Used)
  - Pelo menos 2 processos leves (threads)
"""

import sys
import threading
import random
import time
from collections import OrderedDict
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box
from rich.columns import Columns
from rich.rule import Rule

# UTF-8 no Windows para evitar erros de encoding com caracteres especiais
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# CONSTANTES
#o 1024 converte kb para bytes (computador trabalha em binario)
MEM_PRINCIPAL_BYTES = 64 * 1024        # 64 KB
MEM_VIRTUAL_BYTES   = 1024 * 1024      # 1 MB
TAMANHO_PAGINA      = 8 * 1024         # 8 KB

NUM_FRAMES  = MEM_PRINCIPAL_BYTES // TAMANHO_PAGINA   # 8 frames
NUM_PAGINAS = MEM_VIRTUAL_BYTES   // TAMANHO_PAGINA   # 128 páginas

console = Console(force_terminal=True)

# MEMÓRIA PRINCIPAL = representa os frames físicos
class MemoriaPrincipal:
    """
    Representa a RAM física de 64 KB, lista de 8 frames de 8 KB cada.
    Cada frame quando livre fica None, quando ocupado guarda um dicionário com pid, pagina e dados.
    O self lock garante que as duas thread nao acessem ao mesmo tempo
    """
    def __init__(self):
        #cria uma lista com 8 posicoes (frames) onde cada prosicao é um frame fisico na ram
        #quando frame esta livre o valor fica como none
        #quando frame esta ocupado guarda um dicionario com pid, pagina e dados
        self.frames: list[dict | None] = [None] * NUM_FRAMES
        #LOCK para concorrencia
        self._lock = threading.Lock()

    def frame_livre(self) -> int | None:
        #precorre os 8 frames e retorna o indice do primeiro vazio (none)
        #se nao houver frame livre, retorna None
        for i, f in enumerate(self.frames):
            if f is None:
                return i
        return None
    
    #carregamento de pagin da memoria virtual para a RAM colocando uma pagina em um frame especifico
    def carregar(self, frame_idx: int, pid: int, pagina: int, dados: bytes):
        with self._lock:
            self.frames[frame_idx] = {"pid": pid, "pagina": pagina, "dados": dados}

    #Remove o conteúdo de um frame (substituição de página).
    def despejar(self, frame_idx: int):
        with self._lock:
            antigo = self.frames[frame_idx]
            #bota o frame como livre = none
            self.frames[frame_idx] = None
            #retorna a vitima
            return antigo

    def ler(self, frame_idx: int, offset: int) -> int:
        with self._lock:
            f = self.frames[frame_idx]
            if f is None:
                raise RuntimeError(f"Frame {frame_idx} está vazio!")
            return f["dados"][offset]

    #retorna uma copia do estado atual dos 8 frames para visualização
    def snapshot(self) -> list[dict | None]:
        with self._lock:
            return list(self.frames)


# MEMÓRIA VIRTUAL = representa o espaço de endereçamento de 1 MB por processo
class MemoriaVirtual:
    """
    Simula o espaço virtual de 1 MB (128 páginas de 8 KB).
    Cada página armazena um array de bytes com conteúdo fictício.
    """
    def __init__(self, pid: int):
        #recebe o pid e guarda para gerar dados diferentes para cada processo
        self.pid = pid
        #cria uma lista com 128 posicoes (uma para cada pagina)
        #gera dados sinteticos para cada pagina usando o pid e o numero da pagina
        self.paginas: list[bytes] = [
            bytes([((pid * 17 + p * 13 + b) % 256) for b in range(TAMANHO_PAGINA)])
            for p in range(NUM_PAGINAS)
        ]
        #FORMULA
        #pid — garante que PID 1 e PID 2 têm dados diferentes
        #p — garante que cada página tem dados diferentes
        #b — garante que cada byte dentro da página é diferente
        #256 — garante que o byte está entre 0 e 255 (um byte válido)

    #retorna os dados de uma pagina especifica
    #a mmu usa esse metodo para ler os dados de uma pagina
    def ler_pagina(self, num_pagina: int) -> bytes:
        return self.paginas[num_pagina]

#TABELA DE PAGINAS = cada processo tem a propria tabela com mapeamento de paginas virtuais para frames fisicos
class TabelaDePaginas:
    """
    Mantém o mapeamento de páginas virtuais para frames físicos.
    Cada entrada: { "frame": int, "presente": bool }
    """
    def __init__(self, pid: int):
        #recebe o pid do processo e guarda para identificar qual processo a tabela pertence
        self.pid = pid
        #cria uma lista com 128 posicoes (uma para cada pagina)
        #cada posicao é um dicionario com o frame e se a pagina está presente
        #true se esta na RAM
        self.entradas: list[dict] = [
            {"frame": None, "presente": False} for _ in range(NUM_PAGINAS)
        ]
        #estado inicial frame None e presente False

    #marca que uma pagina virtual esta em um frame fisico especifico
    #usa quando a MMU carrega uma pagina na RAM
    def mapear(self, pagina: int, frame: int):
        #atualiza a entrada com o num do frame e marca como presente
        self.entradas[pagina] = {"frame": frame, "presente": True}
        #exemplo mapear(0,5) pagina 0 no frame 5

    #usado quando a pagina é substituida no LRU
    #marca que a pagina não esta mais na RAM = volta para estado inicial
    def desmarcar(self, pagina: int):
        self.entradas[pagina] = {"frame": None, "presente": False}

    def consultar(self, pagina: int) -> dict:
        return self.entradas[pagina]


# Inicialização da MMU
class MMU:
    """
    Responsável por:
      1. Traduzir endereço virtual para endereço físico (frame + offset)
      2. Detectar page faults
      3. Carregar páginas na memória principal (frame livre ou substituição LRU)
      4. Manter o histórico LRU para seleção da vítima
    """
    def __init__(self, mem_principal: MemoriaPrincipal):
        #recebe a RAM fisica para acessar os frames
        self.mem_principal = mem_principal 
        # tabelas[pid] é onde guarda a tabela de paginas de cada processo
        self.tabelas: dict[int, TabelaDePaginas] = {}
        # LRU OrderedDict registra (pid, pagina) na ordem de último uso
        self.lru: OrderedDict[tuple[int, int], int] = OrderedDict()  
        # o lock é para não ter duas threads concorrendo
        self._lock = threading.Lock()

    def registrar_processo(self, pid: int) -> TabelaDePaginas:
        tp = TabelaDePaginas(pid)
        self.tabelas[pid] = tp
        return tp

    # metodo de acessar é chamado toda vez que um processo acessa um endereço
    def acessar(self, pid: int, end_virtual: int, mem_virtual: MemoriaVirtual) -> dict:
        """
        Acessa um endereço virtual de um processo.
        Retorna um dicionário com todo o log da operação.
        """
        with self._lock:
            #divide o endereço virtual em pagina e offset
            num_pagina = end_virtual // TAMANHO_PAGINA
            offset     = end_virtual  % TAMANHO_PAGINA
            #exemplo: 
            #endereço 0x00002200 = 8704 decimal
            #8704 // 8192 = 1 → página 1
            #8704 % 8192 = 512 → offset 512

            #cria um dicionario vazio para guardar as info do acesso
            resultado = {
                "pid":          pid,
                "end_virtual":  end_virtual,
                "num_pagina":   num_pagina,
                "offset":       offset,
                "page_fault":   False,
                "frame_livre":  None,
                "vitima":       None,      # página substituída (pid, pag, frame)
                "frame_usado":  None,
                "end_fisico":   None,
                "dado":         None,
                "algoritmo":    "LRU",
            }

            #pega a tabela de paginas do processo e consulta se a pagina ta presente na RAM
            tabela = self.tabelas[pid]
            entrada = tabela.consultar(num_pagina)
            
            #SE JA ESTIVER NA RAM (HIT)
            #pega o frame de onde ta e calcula o endereço fisico
            if entrada["presente"]:
                frame = entrada["frame"]
                resultado["frame_usado"] = frame
                #calcula endereco fisico 
                #exemplo Página 1 está no Frame 2, offset 512
                #Endereço físico = 2 × 8192(8kb) = 16384 + 512 = 16896
                #transforma em hexadecimal
                resultado["end_fisico"]  = frame * TAMANHO_PAGINA + offset
                resultado["dado"]  = self.mem_principal.ler(frame, offset)
                #atualiza LRU movendo a pagina para o fim (MAIS RECENTE)
                chave = (pid, num_pagina)
                if chave in self.lru:
                    self.lru.move_to_end(chave)
                return resultado

            # SE NAO ESTIVER NA RAM (MISS PAGE FAULT)
            #registra a falta da pagina
            resultado["page_fault"] = True
            #le os dados da pagina na memoria virtual
            dados_pagina = mem_virtual.ler_pagina(num_pagina)
            #ve se tem frame livre
            frame_idx = self.mem_principal.frame_livre()

            if frame_idx is not None:
                #se tiver frame livre registra o indice para carregar a pagina nesse frame
                resultado["frame_livre"] = frame_idx
            else:
                #SE NAO TIVER = SUBSTITUIÇAO LRU
                # O primeiro item (mais antigo) no OrderedDict é substituido pela nova pagina
                vitima_chave, vitima_frame = next(iter(self.lru.items()))
                #remove a pagina mais antiga do LRU da RAM
                self.lru.popitem(last=False)
                #guarda quem foi a vitima
                vitima_pid, vitima_pag = vitima_chave
                resultado["vitima"] = {
                    "pid":   vitima_pid,
                    "pagina": vitima_pag,
                    "frame":  vitima_frame,
                }

                # Desmarca a pagina vítima na tabela do processo dono
                self.tabelas[vitima_pid].desmarcar(vitima_pag)
                self.mem_principal.despejar(vitima_frame)
                frame_idx = vitima_frame

            # Carrega a nova pagina no frame escolhido
            self.mem_principal.carregar(frame_idx, pid, num_pagina, dados_pagina)
            tabela.mapear(num_pagina, frame_idx)

            resultado["frame_usado"] = frame_idx
            resultado["end_fisico"]  = frame_idx * TAMANHO_PAGINA + offset
            resultado["dado"]        = dados_pagina[offset]

            # Registra no LRU
            self.lru[(pid, num_pagina)] = frame_idx

            return resultado

#visualizacao funcoes
def exibir_cabecalho():
    console.print()
    console.print(Panel.fit(
        "[bold cyan]Simulador de Memória Virtual com Paginação[/bold cyan]\n"
        "[dim]Memória Principal: 64 KB  |  Memória Virtual: 1 MB  |  Páginas/Frames: 8 KB[/dim]\n"
        "[dim]Algoritmo de Substituição: LRU (Least Recently Used)[/dim]",
        border_style="cyan"
    ))


def exibir_resultado(res: dict, contador: int):
    """Exibe o log formatado de um acesso à memória."""
    console.print()
    console.print(Rule(f"[bold]Acesso #{contador}[/bold]", style="dim"))

    # Linha principal
    pf_tag = "[bold red]PAGE FAULT[/bold red]" if res["page_fault"] else "[bold green]HIT[/bold green]"
    console.print(
        f"  PID [bold yellow]{res['pid']}[/bold yellow]  |  "
        f"End. Virtual [bold]{res['end_virtual']:#010x}[/bold]  →  "
        f"Página [bold]{res['num_pagina']}[/bold] + Offset [bold]{res['offset']}[/bold]  |  "
        + pf_tag
    )

    if res["page_fault"]:
        if res["frame_livre"] is not None:
            console.print(
                f"  [cyan]>> Frame livre encontrado:[/cyan] Frame {res['frame_livre']}"
            )
        else:
            v = res["vitima"]
            console.print(
                f"  [red]>> Memoria cheia! Substituicao LRU:[/red] "
                f"Vitima: PID {v['pid']} / Pagina {v['pagina']} (Frame {v['frame']})"
            )
        console.print(
            f"  [cyan]>> Pagina {res['num_pagina']} carregada no Frame {res['frame_usado']}[/cyan]"
        )

    console.print(
        f"  [bold green]Endereço Físico:[/bold green] {res['end_fisico']:#010x}  |  "
        f"[bold green]Dado lido:[/bold green] [bold]{res['dado']}[/bold] "
        f"(0x{res['dado']:02X})"
    )


def exibir_tabela_frames(mem: MemoriaPrincipal):
    """Exibe o estado atual dos frames da memória principal."""
    table = Table(
        title="Estado da Memória Principal (64 KB / 8 frames de 8 KB)",
        box=box.ROUNDED,
        show_lines=True,
    )
    table.add_column("Frame", style="bold cyan", justify="center", width=7)
    table.add_column("Endereço Físico", style="dim", justify="center", width=18)
    table.add_column("PID", justify="center", width=6)
    table.add_column("Página Virtual", justify="center", width=15)
    table.add_column("Status", justify="center", width=10)

    for i, f in enumerate(mem.snapshot()):
        end_inicio = f"{i * TAMANHO_PAGINA:#010x}"
        end_fim    = f"{(i + 1) * TAMANHO_PAGINA - 1:#010x}"
        if f is None:
            table.add_row(
                str(i), f"{end_inicio} – {end_fim}", "—", "—",
                "[dim]Livre[/dim]"
            )
        else:
            table.add_row(
                str(i), f"{end_inicio} – {end_fim}",
                str(f["pid"]), str(f["pagina"]),
                "[bold green]Ocupado[/bold green]"
            )

    console.print()
    console.print(table)


def exibir_tabela_paginas(tabela: TabelaDePaginas, max_pags: int = 20):
    """Exibe as primeiras entradas da tabela de páginas de um processo."""
    t = Table(
        title=f"Tabela de Páginas — PID {tabela.pid} (primeiras {max_pags} entradas)",
        box=box.SIMPLE_HEAD,
    )
    t.add_column("Página", style="bold", justify="center", width=8)
    t.add_column("Frame", justify="center", width=8)
    t.add_column("Presente", justify="center", width=10)

    for i in range(max_pags):
        e = tabela.entradas[i]
        presente = "[green]Sim[/green]" if e["presente"] else "[red]Não[/red]"
        frame    = str(e["frame"]) if e["frame"] is not None else "—"
        t.add_row(str(i), frame, presente)

    console.print(t)


# PROCESSOS LEVES = simulam threads acessando memória
class ProcessoLeve(threading.Thread):
    """
    Thread que gera acessos a endereços virtuais aleatórios.
    Cada acesso é delegado à MMU e o resultado é exibido.
    """
    def __init__(self, pid: int, mmu: MMU, mem_virtual: MemoriaVirtual,
                 acessos: list[int], lock_print: threading.Lock,
                 contador_global: list[int]):
        super().__init__(name=f"Processo-{pid}", daemon=True)
        self.pid             = pid
        self.mmu             = mmu
        self.mem_virtual     = mem_virtual
        self.acessos         = acessos        # lista de endereços virtuais
        self.lock_print      = lock_print
        self.contador_global = contador_global  # lista de 1 int (mutável)

    def run(self):
        for end in self.acessos:
            # Garante que apenas uma thread acessa a MMU e exibe resultado por vez
            with self.lock_print:
                res = self.mmu.acessar(self.pid, end, self.mem_virtual)
                self.contador_global[0] += 1
                exibir_resultado(res, self.contador_global[0])


# MODO DEMO = demonstração automática de todos os cenários
def modo_demo():
    console.print(Panel(
        "[bold yellow]MODO DEMONSTRAÇÃO AUTOMÁTICA[/bold yellow]\n"
        "Dois processos leves acessam endereços virtuais gerando\n"
        "HIT, page fault com frame livre e substituição LRU.",
        border_style="yellow"
    ))

    mem_principal = MemoriaPrincipal()
    mmu           = MMU(mem_principal)

    # Cria dois processos com suas memórias virtuais e tabelas de páginas
    pids = [1, 2]
    mem_virtuais = {}
    for pid in pids:
        mv  = MemoriaVirtual(pid)
        mem_virtuais[pid] = mv
        mmu.registrar_processo(pid)

    # Sequência de acessos projetada para demonstrar todos os cenários:
    # Páginas 0-7 preenchem os 8 frames (nenhuma substituição ainda)
    # Páginas 8+ forçam substituição LRU
    acessos_p1 = [
        0 * TAMANHO_PAGINA,          # pág 0  — page fault, frame livre
        1 * TAMANHO_PAGINA,          # pág 1  — page fault, frame livre
        2 * TAMANHO_PAGINA,          # pág 2  — page fault, frame livre
        3 * TAMANHO_PAGINA,          # pág 3  — page fault, frame livre
        0 * TAMANHO_PAGINA + 100,    # pág 0  — HIT (acessa novamente)
        8 * TAMANHO_PAGINA,          # pág 8  — page fault, substituição LRU
        9 * TAMANHO_PAGINA,          # pág 9  — page fault, substituição LRU
        1 * TAMANHO_PAGINA + 512,    # pág 1  — pode ser HIT ou fault
    ]

    acessos_p2 = [
        4 * TAMANHO_PAGINA,          # pág 4  — page fault, frame livre
        5 * TAMANHO_PAGINA,          # pág 5  — page fault, frame livre
        6 * TAMANHO_PAGINA,          # pág 6  — page fault, frame livre
        7 * TAMANHO_PAGINA,          # pág 7  — page fault, frame livre (último!)
        4 * TAMANHO_PAGINA + 200,    # pág 4  — HIT
        10 * TAMANHO_PAGINA,         # pág 10 — page fault, substituição LRU
        5 * TAMANHO_PAGINA,          # pág 5  — pode ser HIT ou fault
    ]

    lock_print      = threading.Lock()
    contador_global = [0]

    t1 = ProcessoLeve(1, mmu, mem_virtuais[1], acessos_p1, lock_print, contador_global)
    t2 = ProcessoLeve(2, mmu, mem_virtuais[2], acessos_p2, lock_print, contador_global)

    console.print("\n[bold]Iniciando processos leves (threads)...[/bold]")
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    console.print()
    console.print(Rule("[bold cyan]Estado Final da Memória[/bold cyan]"))
    exibir_tabela_frames(mem_principal)

    console.print()
    console.print(Rule("[bold cyan]Tabelas de Páginas[/bold cyan]"))
    for pid in pids:
        exibir_tabela_paginas(mmu.tabelas[pid])

    console.print()
    console.print(Panel(
        f"[bold]Resumo:[/bold] {contador_global[0]} acessos realizados.\n"
        f"Frames utilizados: {sum(1 for f in mem_principal.snapshot() if f is not None)}/{NUM_FRAMES}",
        border_style="green"
    ))


# MODO DEMO SEQUENCIAL = executa sequência específica com pausas para mostrar frames
def modo_demo_sequencial():
    console.print(Panel(
        "[bold yellow]MODO DEMO SEQUENCIAL[/bold yellow]\n"
        "Executa sequência predefinida demonstrando todos os cenários:\n"
        "1. Page fault com frame livre\n"
        "2. HIT\n"
        "3. Preenchimento do resto\n"
        "4. Substituição LRU",
        border_style="yellow"
    ))

    mem_principal = MemoriaPrincipal()
    mmu           = MMU(mem_principal)

    pids = [1, 2]
    mem_virtuais = {}
    for pid in pids:
        mv  = MemoriaVirtual(pid)
        mem_virtuais[pid] = mv
        mmu.registrar_processo(pid)

    contador = 0

    def executar_acesso(pid, endereco):
        nonlocal contador
        contador += 1
        res = mmu.acessar(pid, endereco, mem_virtuais[pid])
        exibir_resultado(res, contador)

    console.print()
    console.print(Rule("[bold cyan]CENÁRIO 1: Page Fault com Frame Livre[/bold cyan]"))
    
    executar_acesso(1, 0x00000000)  # Página 0
    executar_acesso(1, 0x00002000)  # Página 1
    executar_acesso(1, 0x00004000)  # Página 2

    console.print()
    console.print(Rule("[bold cyan]Estado dos Frames após Cenário 1[/bold cyan]"))
    exibir_tabela_frames(mem_principal)

    console.print()
    console.print(Rule("[bold cyan]CENÁRIO 2: HIT[/bold cyan]"))
    
    executar_acesso(1, 0x00000064)  # Página 0, offset 100

    console.print()
    console.print(Rule("[bold cyan]CENÁRIO 3: Preenchimento do Resto[/bold cyan]"))
    
    executar_acesso(2, 0x00008000)  # Página 4
    executar_acesso(2, 0x0000A000)  # Página 5
    executar_acesso(2, 0x0000C000)  # Página 6
    executar_acesso(2, 0x0000E000)  # Página 7
    executar_acesso(2, 0x00010000)  # Página 8

    console.print()
    console.print(Rule("[bold cyan]Estado dos Frames ANTES da Substituição (8 páginas carregadas)[/bold cyan]"))
    exibir_tabela_frames(mem_principal)

    executar_acesso(2, 0x00012000)  # Página 9

    console.print()
    console.print(Rule("[bold cyan]CENÁRIO 4: Substituição LRU[/bold cyan]"))
    
    executar_acesso(1, 0x00014000)  # Página 10

    console.print()
    console.print(Rule("[bold cyan]Estado dos Frames DEPOIS da Substituição[/bold cyan]"))
    exibir_tabela_frames(mem_principal)

    console.print()
    console.print(Panel(
        f"[bold]Resumo:[/bold] {contador} acessos realizados.\n"
        f"Frames utilizados: {sum(1 for f in mem_principal.snapshot() if f is not None)}/{NUM_FRAMES}",
        border_style="green"
    ))


# MODO INTERATIVO = usuário digita endereços virtuais
def modo_interativo():
    console.print(Panel(
        "[bold blue]MODO INTERATIVO[/bold blue]\n"
        "Digite endereços virtuais hexadecimais ou decimais.\n"
        "Comandos: [bold]frames[/bold] | [bold]tabela[/bold] | [bold]sair[/bold]",
        border_style="blue"
    ))

    mem_principal = MemoriaPrincipal()
    mmu           = MMU(mem_principal)

    pids        = [1, 2]
    mem_virtuais = {}
    for pid in pids:
        mv  = MemoriaVirtual(pid)
        mem_virtuais[pid] = mv
        mmu.registrar_processo(pid)

    contador = 0
    lock = threading.Lock()

    console.print(
        f"\n[dim]Sistema: {NUM_FRAMES} frames  |  {NUM_PAGINAS} páginas virtuais  |  "
        f"Tamanho de página: {TAMANHO_PAGINA} bytes (8 KB)[/dim]"
    )
    console.print(
        f"[dim]Endereços virtuais válidos: 0x00000000 – 0x{MEM_VIRTUAL_BYTES - 1:08X}[/dim]"
    )
    console.print("[dim]Exemplos de endereços:[/dim]")
    console.print("[dim]  0x00000000 (pág 0) | 0x00002000 (pág 1) | 0x00010000 (pág 8)[/dim]")
    console.print("[dim]  65536 (decimal) | 8192 (decimal) | 0x00000064 (pág 0, offset 100)[/dim]\n")

    while True:
        try:
            entrada = console.input(
                "[bold cyan]PID (1 ou 2)[/bold cyan] > "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            break

        if entrada.lower() == "sair":
            break
        if entrada.lower() == "frames":
            exibir_tabela_frames(mem_principal)
            continue
        if entrada.lower() == "tabela":
            for pid in pids:
                exibir_tabela_paginas(mmu.tabelas[pid])
            continue

        try:
            pid = int(entrada)
            if pid not in pids:
                console.print("[red]PID deve ser 1 ou 2.[/red]")
                continue
        except ValueError:
            console.print("[red]Entrada inválida. Digite 1 ou 2 para PID.[/red]")
            continue

        try:
            end_str = console.input(
                f"[bold cyan]Endereço virtual (hex/dec)[/bold cyan] > "
            ).strip()
            end_virtual = int(end_str, 0)
            if not (0 <= end_virtual < MEM_VIRTUAL_BYTES):
                console.print(
                    f"[red]Endereço fora do espaço virtual (0 – {MEM_VIRTUAL_BYTES - 1}).[/red]"
                )
                continue
        except (ValueError, EOFError, KeyboardInterrupt):
            console.print("[red]Endereço inválido.[/red]")
            continue

        res = mmu.acessar(pid, end_virtual, mem_virtuais[pid])
        contador += 1
        exibir_resultado(res, contador)


# PONTO DE ENTRADA
def main():
    exibir_cabecalho()

    console.print(
        "\n[bold]Escolha o modo de execução:[/bold]\n"
        "  [bold cyan]1[/bold cyan] – Demo sequencial (passo a passo)\n"
        "  [bold cyan]2[/bold cyan] – Demo automática (concorrente)\n"
        "  [bold cyan]3[/bold cyan] – Interativo\n"
    )

    try:
        opcao = console.input("[bold cyan]Opção[/bold cyan] > ").strip()
    except (EOFError, KeyboardInterrupt):
        opcao = "1"

    if opcao == "1":
        modo_demo_sequencial()
    elif opcao == "2":
        modo_demo()
    elif opcao == "3":
        modo_interativo()
    else:
        console.print("[red]Opção inválida. Rodando demo automática.[/red]")
        modo_demo()


if __name__ == "__main__":
    main()
